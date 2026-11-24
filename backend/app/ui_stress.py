"""Sequential UI stress batches with bounded, structured reports."""

import asyncio
import base64
from io import BytesIO
import logging
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from PIL import Image

from .ui_map import Device


STATES = ('passed', 'failed', 'needs_review', 'error', 'cancelled', 'interrupted')
MIN_FREE_BYTES = 128 * 1024 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024
MAX_BATCH_EVIDENCE_BYTES = 250 * 1024 * 1024
MAX_FAILURE_STEPS = 5
MAX_ACTIVE_BATCHES = 4


class UIStress:
    def __init__(self, manager):
        self.manager = manager
        self.store = manager.store
        self.tasks = {}
        self.projects = {}
        self.active = {}
        self.store.recover_stress()
        self.workspace_root = self.store.root / '.stress-work'
        self.workspace_root.mkdir(exist_ok=True)
        for path in self.workspace_root.iterdir():
            if path.name.startswith('batch-') and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)

    def evidence_dir(self, batch_id):
        return self.store.root / 'stress-evidence' / batch_id

    async def capture_timeout_evidence(self, serial):
        try:
            async with asyncio.timeout(4):
                frame = await Device(serial).frame()
                return await asyncio.to_thread(self.encode_evidence, frame['image'])
        except (OSError, ValueError, TimeoutError, Image.DecompressionBombError):
            return b''

    @staticmethod
    def encode_evidence(data_url):
        with Image.open(BytesIO(base64.b64decode(data_url.split(',', 1)[1])), formats=['PNG']) as frame:
            image = frame.convert('RGB')
        for size in (1024, 720, 480):
            image.thumbnail((size, size))
            output = BytesIO()
            image.save(output, 'JPEG', quality=65)
            if output.tell() <= MAX_EVIDENCE_BYTES:
                return output.getvalue()
        return b''

    def save_evidence(self, batch_id, number, failure_number, image, remaining):
        if not image.startswith(b'\xff\xd8\xff') or len(image) > min(remaining, MAX_EVIDENCE_BYTES):
            return 0
        try:
            folder = self.evidence_dir(batch_id)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'{number}-{failure_number}.jpg'
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_bytes(image)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return len(image)
        except OSError:
            return 0

    async def start(self, request, repetitions, stop_on_failure):
        manager = self.manager
        case = request.testCase
        serial, can_steps = manager.run_target(request)
        async with manager.lock, manager.device_lock(serial):
            if len(self.tasks) >= MAX_ACTIVE_BATCHES:
                raise ValueError('最多同时运行 4 个压测任务')
            baselines, parameters, can_session = await manager.prepare_run(request, serial, can_steps)
            if shutil.disk_usage(self.store.root).free < MIN_FREE_BYTES:
                raise ValueError('剩余空间不足 128 MiB，请先清理测试记录')
            batch_id = uuid.uuid4().hex
            sensitive = any(p.secret for p in case.parameters)
            batch = dict(id=batch_id, projectId=case.projectId, caseId=case.id, caseName=case.name,
                         caseVersion=case.version, serial=serial, requested=repetitions, completed=0,
                         counts=dict.fromkeys(STATES, 0), state='running', stopOnFailure=stop_on_failure,
                         createdAt=int(time.time() * 1000), endedAt=None)
            # Keep one input snapshot for the entire batch. Use an isolated temp
            # directory and discard any incidental SDK output between rounds.
            workspace = tempfile.TemporaryDirectory(prefix='batch-', dir=self.workspace_root)
            try:
                job = manager.make_job(request, serial, parameters, baselines, Path(workspace.name),
                                       sensitive, compact=True)
                env = manager.environment()
                self.store.put_stress(batch)
            except BaseException:
                workspace.cleanup()
                raise
            manager.external_busy[serial] = batch_id
            if manager.monitor and serial:
                for runtime in manager.monitor.active.values():
                    if runtime.request.serial == serial:
                        runtime.preserve_device_logs = True
            self.projects[batch_id] = {case.projectId, *(key.split('/')[0] for key in baselines if '/' in key)}
            self.active[batch_id] = batch
            self.tasks[batch_id] = asyncio.create_task(
                self.execute(batch, job, env, request.timeoutSeconds, can_steps, can_session, workspace))
            return batch.copy()

    async def execute(self, batch, job, env, timeout, can_steps, can_session, workspace):
        manager = self.manager
        batch_id = batch['id']
        names = {step['id']: step.get('name') or step['type'] for step in job['testCase']['steps']}
        remaining = MAX_BATCH_EVIDENCE_BYTES
        try:
            for number in range(1, batch['requested'] + 1):
                if batch_id in manager.cancelled:
                    break
                if shutil.disk_usage(self.store.root).free < MIN_FREE_BYTES:
                    batch.update(state='error', message='剩余空间不足 128 MiB，已停止压测')
                    break
                start = time.monotonic()
                failures = []
                pending = None

                def collect(event):
                    nonlocal pending
                    if event.get('type') != 'step':
                        return
                    state = event.get('state')
                    if state == 'running':
                        pending = event
                    elif state in {'passed', 'failed', 'error', 'needs_review'}:
                        pending = None
                        if state == 'error' and len(failures) >= MAX_FAILURE_STEPS:
                            failures.pop()
                        if state in {'failed', 'error'} and len(failures) < MAX_FAILURE_STEPS:
                            failures.append(event)

                # A fresh process resets variables and its agent, not the device UI.
                with tempfile.TemporaryDirectory(prefix='round-', dir=workspace.name) as output:
                    run = dict(serial=batch['serial'], state='queued', sensitive=job['sensitive'])
                    await manager._execute_run(batch_id, {**job, 'outputDir': output}, env, timeout,
                                               can_steps, can_session, summary=(run, collect))
                    if run['state'] == 'error' and pending is not None:
                        collect({**pending, 'state': 'error', 'timedOut': True, 'message': run.get('message')})
                    steps = []
                    for index, event in enumerate(failures, 1):
                        step_id = str(event.get('stepId'))
                        step = dict(step=names.get(step_id, '未知步骤')[:120],
                                    stepId=step_id[:64], state=event['state'],
                                    iterations=[i for i in (event.get('iterations') or [])[:100]
                                                if type(i) is int],
                                    message='' if job['sensitive'] else str(event.get('message') or '')[:240])
                        if (remaining > 0 and not job['sensitive'] and batch_id not in manager.cancelled
                                and run['state'] in {'failed', 'error'}):
                            image = b''
                            slot = event.get('failureImage')
                            if type(slot) is int and 1 <= slot <= MAX_FAILURE_STEPS + 1:
                                try:
                                    with (Path(output) / f'failure-{slot}.jpg').open('rb') as source:
                                        image = source.read(MAX_EVIDENCE_BYTES + 1)
                                except OSError:
                                    pass
                            elif event.get('timedOut') and batch['serial']:
                                image = await self.capture_timeout_evidence(batch['serial'])
                            size = (self.save_evidence(batch_id, number, index, image, remaining)
                                    if batch_id not in manager.cancelled else 0)
                            if size:
                                remaining -= size
                                step['screenshot'] = index
                        steps.append(step)
                manager.processes.pop(batch_id, None)
                state = run['state']
                result = dict(number=number, state=state, durationMs=round((time.monotonic() - start) * 1000),
                              failures=steps,
                              message='' if job['sensitive'] else str(run.get('message') or '')[:240])
                batch['completed'] = number
                batch['counts'][state] += 1
                self.store.put_stress(batch, result)
                if state in {'error', 'interrupted', 'cancelled'} or state == 'failed' and batch['stopOnFailure']:
                    batch.update(state=state, message=result['message'] or '已停止后续轮次')
                    break
                await asyncio.sleep(0)
            if batch_id in manager.cancelled and batch['state'] in {'running', 'stopping', 'cancelled'}:
                batch.update(state='cancelled', message='已手动停止')
            elif batch['state'] == 'running':
                batch['state'] = 'completed'
        except asyncio.CancelledError:
            batch.update(state='interrupted', message='后端关闭，压测中断')
        except Exception:
            logging.getLogger(__name__).exception('UI stress batch failed: %s', batch_id)
            batch.update(state='error', message='压测记录或临时目录写入失败')
        finally:
            try:
                batch['endedAt'] = int(time.time() * 1000)
                self.store.put_stress(batch)
            finally:
                self.tasks.pop(batch_id, None)
                self.projects.pop(batch_id, None)
                self.active.pop(batch_id, None)
                manager.processes.pop(batch_id, None)
                manager.cancelled.discard(batch_id)
                if manager.external_busy.get(batch['serial']) == batch_id:
                    manager.external_busy.pop(batch['serial'], None)
                await asyncio.to_thread(workspace.cleanup)

    async def stop(self, batch_id):
        batch = self.store.get('stress_batches', batch_id)
        task = self.tasks.get(batch_id)
        if task and batch['state'] == 'running' and batch_id not in self.manager.cancelled:
            self.manager.cancelled.add(batch_id)
            batch = self.active[batch_id]
            batch.update(state='stopping')
            self.store.put_stress(batch)
            if process := self.manager.processes.get(batch_id):
                await self.manager.terminate(process)
        return self.store.get('stress_batches', batch_id)

    async def close(self):
        # Let newly scheduled tasks enter their cleanup scope before cancelling.
        await asyncio.sleep(0)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
