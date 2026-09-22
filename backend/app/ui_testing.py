"""UI 用例、配置和独立 Node 执行任务；与性能会话分开存储。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import signal
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adb import list_devices
from .ui_navigation import NavigationStore

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'tools' / 'ui-test' / 'runner.mjs'
TERMINAL = {'passed', 'failed', 'error', 'cancelled', 'interrupted', 'needs_review'}
NAME = r'^[a-zA-Z0-9_-]{1,64}$'
REFERENCE = re.compile(r'\$\{([a-zA-Z0-9_]+)\}')


class ExploreScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allowedControls: list[str] = Field(default_factory=list, max_length=10)
    allowedNavigation: list[str] = Field(default_factory=list, max_length=100)
    allowedPackages: list[str] = Field(default_factory=lambda: ['com.android.settings'], min_length=1, max_length=10)
    maxActions: int = Field(default=20, ge=2, le=100)
    maxDepth: int = Field(default=2, ge=0, le=5)
    maxPages: int = Field(default=10, ge=1, le=50)
    autoNavigation: bool = False
    visualAssertion: str = Field(default='', max_length=2000)


class Selector(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package: str = Field(default='', max_length=200)
    className: str = Field(default='', max_length=200)
    resourceId: str = Field(default='', max_length=500)
    label: str = Field(default='', max_length=2000)


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=NAME)
    type: Literal['action', 'tap', 'input', 'wait', 'assert', 'screenshot', 'explore', 'scan', 'settings', 'back']
    prompt: str = Field(min_length=1, max_length=8000)
    value: str = Field(default='', max_length=8000)
    timeoutMs: int = Field(default=60000, ge=1000, le=300000)
    enabled: bool = True
    onFailure: Literal['stop', 'continue'] = 'stop'
    exploration: ExploreScope | None = None
    selector: Selector | None = None

    @model_validator(mode='after')
    def validate_exploration(self):
        if self.selector and (self.type != 'tap' or not any(self.selector.model_dump().values())):
            raise ValueError('结构定位条件仅用于点击步骤且不能为空')
        if self.type in {'explore', 'scan'}:
            if not self.exploration or any(not name.strip() or len(name) > 200 for name in self.exploration.allowedControls):
                raise ValueError('探索步骤需要有效的范围配置')
            if len(set(self.exploration.allowedControls)) != len(self.exploration.allowedControls):
                raise ValueError('探索开关名称不可重复')
            if self.type == 'scan' and (self.exploration.allowedControls or self.exploration.visualAssertion):
                raise ValueError('结构扫描不能操作开关或调用模型断言')
        return self


class Parameter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-zA-Z0-9_]{1,64}$')
    secret: bool = False
    default: str = Field(default='', max_length=8000)


class TestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schemaVersion: Literal[1] = 1
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=NAME)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=160)
    group: str = Field(default='', max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    parameters: list[Parameter] = Field(default_factory=list, max_length=50)
    steps: list[Step] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_steps(self):
        if not self.name.strip() or any(not s.prompt.strip() for s in self.steps):
            raise ValueError('名称和步骤描述不能为空白')
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError('步骤 ID 不可重复')
        names = {p.name for p in self.parameters}
        if len(names) != len(self.parameters):
            raise ValueError('参数名不可重复')
        if any(p.secret and p.default for p in self.parameters):
            raise ValueError('敏感参数不可保存默认值，请在执行时输入')
        for step in self.steps:
            references = REFERENCE.findall(step.prompt + ' ' + step.value)
            if any(name not in names for name in references):
                raise ValueError('步骤引用了未声明的参数')
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    serial: str = Field(pattern=r'^[a-zA-Z0-9._:-]{1,128}$')
    testCase: TestCase
    parameters: dict[str, str] = Field(default_factory=dict, max_length=50)
    timeoutSeconds: int = Field(default=900, ge=10, le=3600)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    baseUrl: str = 'https://api.deepseek.com'
    model: str = Field(default='deepseek-flash', min_length=1, max_length=128)
    family: str = Field(default='deepseek', min_length=1, max_length=64)
    reportRoot: str = Field(default='', max_length=1024)
    apiKey: str | None = Field(default=None, max_length=4096)

    @model_validator(mode='after')
    def validate_url(self):
        from urllib.parse import urlsplit
        url = urlsplit(self.baseUrl)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('模型接口必须是无凭据、无查询参数的 HTTP(S) 地址')
        return self


class UIStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / 'ui.db'
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, body TEXT NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq)')

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def all(self, table: str):
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(f'SELECT body FROM {table} ORDER BY rowid DESC LIMIT 500')]

    def get(self, table: str, key: str):
        with self.connect() as conn:
            row = conn.execute(f'SELECT body FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def put(self, table: str, body: dict):
        with self.connect() as conn:
            conn.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (body['id'], json.dumps(body, ensure_ascii=False)))

    def event(self, run_id: str, body: dict):
        with self.connect() as conn:
            conn.execute('INSERT INTO events(run_id,body) VALUES (?,?)', (run_id, json.dumps(body, ensure_ascii=False)))

    def events(self, run_id: str, after: int = 0):
        with self.connect() as conn:
            return [{'seq': row[0], **json.loads(row[1])} for row in conn.execute('SELECT seq,body FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 10000', (run_id, after))]

    def recover(self):
        with self.connect() as conn:
            rows = conn.execute('SELECT body FROM runs').fetchall()
        for row in rows:
            run = json.loads(row[0])
            if run['state'] not in TERMINAL:
                run.update(state='interrupted', endedAt=int(time.time() * 1000))
                self.put('runs', run)


def draft_from_run(run: dict, events: list[dict]) -> dict:
    """只展开有语义定位信息的已完成动作；不把坐标或失败动作伪装成稳定脚本。"""
    result = json.loads(json.dumps(run['testCase']))
    result.update(id=uuid.uuid4().hex, version=1, name=f"{result['name']} · 执行草稿"[:160])
    for parameter in result.get('parameters', []):
        if not parameter['secret']:
            parameter['default'] = run.get('parameters', {}).get(parameter['name'], parameter['default'])
    steps, warnings = [], []
    for original in result['steps']:
        if original['type'] == 'explore':
            observations = [e for e in events if e.get('type') == 'exploration' and e.get('stepId') == original['id']]
            controls = list(dict.fromkeys(e.get('control') for e in observations if e.get('control')))
            regression = []
            for control in controls:
                history = [e for e in observations if e.get('control') == control]
                began = next((e for e in history if e.get('state') == 'running' and isinstance(e.get('original'), bool)), None)
                if not began or not any(e.get('state') == 'passed' for e in history) or history[-1].get('state') != 'restored':
                    continue
                initial = '开启' if began['original'] else '关闭'
                opposite = '关闭' if began['original'] else '开启'
                for kind, prompt in [('assert', f'“{control}”开关处于{initial}状态'), ('tap', f'完整标签为“{control}”的开关本身'), ('assert', f'“{control}”开关处于{opposite}状态'), ('tap', f'完整标签为“{control}”的开关本身'), ('assert', f'“{control}”开关处于{initial}状态')]:
                    regression.append({**original, 'id': uuid.uuid4().hex, 'type': kind, 'prompt': prompt, 'exploration': None})
            if regression and len(steps) + len(regression) + len(result['steps']) <= 100:
                steps.extend(regression)
                warnings.append('探索已转为回归步骤；请核对初始状态断言和恢复步骤。未验证成功的控件不纳入草稿。')
                continue
        actions = [e for e in events if e.get('type') == 'action' and e.get('stepId') == original['id']]
        converted = []
        for action in actions:
            target = action.get('target', '').strip()
            if action.get('action') == 'Tap' and target:
                converted.append({**original, 'id': uuid.uuid4().hex, 'type': 'tap', 'prompt': target, 'value': ''})
            else:
                converted = []
                break
        # A partially failed action may have performed only half the intended work.
        completed = any(e.get('type') == 'step' and e.get('stepId') == original['id'] and e.get('state') == 'passed' for e in events)
        if original['type'] == 'action' and converted and completed and len(steps) + len(converted) + len(result['steps']) <= 100:
            steps.extend(converted)
        else:
            steps.append({**original, 'id': uuid.uuid4().hex})
            if original['type'] == 'action':
                warnings.append('部分动作缺少完整语义定位或未完成，保留了原自然语言步骤，请检查前置页面。')
    result['steps'] = steps
    return {'testCase': TestCase.model_validate(result).model_dump(), 'warnings': list(dict.fromkeys(warnings))}


class UIManager:
    def __init__(self, root: Path, monitor=None):
        self.store = UIStore(root)
        self.navigation = NavigationStore(root)
        self.monitor = monitor
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.cancelled: set[str] = set()
        self.lock = asyncio.Lock()
        self.device_locks: dict[str, asyncio.Lock] = {}
        self.store.recover()

    def device_lock(self, serial):
        return self.device_locks.setdefault(serial, asyncio.Lock())

    def has_active_device(self, serial):
        return any(self.store.get('runs', key)['serial'] == serial for key in self.tasks)

    def link_performance(self, serial, session_id):
        for key in self.tasks:
            run = self.store.get('runs', key)
            if run['serial'] == serial:
                run['performanceSessionId'] = run.get('performanceSessionId') or session_id
                self.store.put('runs', run)
                self.store.event(key, {'type': 'performance', 'time': int(time.time() * 1000), 'sessionId': session_id})

    def settings(self, public=True):
        path = self.store.root / 'settings.json'
        data = Settings().model_dump()
        if path.exists():
            data.update(json.loads(path.read_text()))
        if public:
            key = data.pop('apiKey', None)
            data['hasKey'] = bool(key or os.environ.get('MIDSCENE_MODEL_API_KEY'))
            data['envFileExists'] = (RUNNER.parent / '.env').exists()
        return data

    def save_settings(self, value: Settings):
        data = value.model_dump()
        if value.apiKey is None:
            data['apiKey'] = self.settings(False).get('apiKey')
        directory = Path(value.reportRoot).expanduser() if value.reportRoot else self.store.root / 'runs'
        if not directory.is_absolute():
            raise ValueError('报告路径必须为主机上的绝对路径')
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f'.write-test-{uuid.uuid4().hex}'
        try:
            probe.write_text('')
        finally:
            probe.unlink(missing_ok=True)
        data['reportRoot'] = str(directory.resolve())
        path = self.store.root / 'settings.json'
        temp = path.with_suffix('.tmp')
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(data, file)
        os.replace(temp, path)
        return self.settings()

    def environment(self):
        env = os.environ.copy()
        settings = self.settings(False)
        for name, field in [('MIDSCENE_MODEL_BASE_URL', 'baseUrl'), ('MIDSCENE_MODEL_NAME', 'model'), ('MIDSCENE_MODEL_FAMILY', 'family'), ('MIDSCENE_MODEL_API_KEY', 'apiKey')]:
            if settings.get(field):
                env[name] = settings[field]
        return env

    async def start(self, request: RunRequest):
        async with self.lock, self.device_lock(request.serial):
            if self.has_active_device(request.serial):
                raise ValueError('该设备已有 UI 测试运行中')
            devices = await list_devices()
            if not any(d.serial == request.serial and d.state == 'device' for d in devices):
                raise ValueError('指定设备未连接或未授权')
            parameters = {p.name: p.default for p in request.testCase.parameters}
            if set(request.parameters) - set(parameters):
                raise ValueError('存在未声明的执行参数')
            parameters.update(request.parameters)
            if any(p.secret and not parameters[p.name] for p in request.testCase.parameters):
                raise ValueError('请填写敏感参数')
            if any(len(v) > 8000 for v in parameters.values()):
                raise ValueError('参数值过长')
            run_id = uuid.uuid4().hex
            settings = self.settings()
            output = (Path(settings['reportRoot']) if settings['reportRoot'] else self.store.root / 'runs') / run_id
            output.mkdir(parents=True, exist_ok=True)
            performance_id = None
            if self.monitor:
                for runtime in self.monitor.active.values():
                    if runtime.request.serial == request.serial:
                        performance_id = runtime.session_id
                        runtime.preserve_device_logs = True
            run = {
                'id': run_id, 'serial': request.serial, 'state': 'queued',
                'createdAt': int(time.time() * 1000), 'endedAt': None,
                'testCase': request.testCase.model_dump(), 'performanceSessionId': performance_id,
                'outputDir': str(output.resolve()), 'report': None,
                'model': settings['model'], 'sensitive': any(p.secret for p in request.testCase.parameters),
                'parameters': {p.name: parameters[p.name] for p in request.testCase.parameters if not p.secret},
                'kind': 'scan' if any(s.type == 'scan' for s in request.testCase.steps) else 'test',
            }
            self.store.put('runs', run)
            job = {'serial': request.serial, 'testCase': request.testCase.model_dump(), 'parameters': parameters, 'outputDir': run['outputDir'], 'sensitive': run['sensitive'], 'timeoutSeconds': request.timeoutSeconds}
            if not run['sensitive']:
                try:
                    job['navigation'] = self.navigation.load(request.serial)
                except (ValueError, OSError, sqlite3.Error):
                    pass  # Optional knowledge must not prevent normal Midscene execution.
            self.tasks[run_id] = asyncio.create_task(self.execute(run_id, job, self.environment(), request.timeoutSeconds))
            return run

    async def terminate(self, process):
        if process.returncode is not None and os.name != 'posix':
            return
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            else:
                killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            await asyncio.wait_for(process.wait(), 3)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        finally:
            # The worker may have exited on its own deadline while ADB children
            # are still alive. Terminate its process group even in that case.
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    async def execute(self, run_id, job, env, timeout):
        run = self.store.get('runs', run_id)
        process = None
        try:
            if run_id in self.cancelled:
                return
            process = await asyncio.create_subprocess_exec('node', str(RUNNER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=os.name == 'posix', limit=1024 * 1024)
            self.processes[run_id] = process
            if run_id in self.cancelled:
                await self.terminate(process)
                return
            run['state'] = 'running'
            self.store.put('runs', run)
            async with asyncio.timeout(timeout):
                process.stdin.write(json.dumps(job).encode())
                await process.stdin.drain()
                process.stdin.close()
                async for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(event, dict) or event.get('protocol') != 1:
                        continue
                    if event.get('type') == 'navigation_update':
                        if not run['sensitive']:
                            try:
                                self.navigation.save(run['serial'], event.get('knowledge'))
                            except (ValueError, OSError, sqlite3.Error):
                                pass
                        continue
                    self.store.event(run_id, event)
                    if event.get('type') == 'finished' and event.get('state') in TERMINAL:
                        run['state'] = event['state']
                        run['report'] = event.get('report')
                        run['message'] = event.get('message')
                code = await process.wait()
                if run['state'] not in TERMINAL or (code != 0 and run['state'] == 'passed'):
                    run.update(state='error', message='执行进程异常退出')
        except asyncio.TimeoutError:
            run.update(state='error', message='任务达到总超时，已终止执行进程')
        except asyncio.CancelledError:
            run.update(state='interrupted', message='后端关闭，任务中断')
        except Exception:
            run.update(state='error', message='无法执行 UI 任务，请检查 Node.js、依赖和本机配置')
        finally:
            if process:
                await self.terminate(process)
            if run_id in self.cancelled:
                run['state'] = 'cancelled'
            if run['state'] not in TERMINAL:
                run['state'] = 'interrupted'
            controls = {}
            for event in self.store.events(run_id):
                if event.get('type') == 'exploration' and event.get('control'):
                    controls[event['control']] = event
            pending = [name for name, event in controls.items() if event.get('state') in {'running', 'passed', 'failed', 'needs_review'}]
            if pending:
                run['message'] = '探索控件未确认恢复，请检查：' + '、'.join(pending)
            run['endedAt'] = int(time.time() * 1000)
            run['performanceSessionId'] = self.store.get('runs', run_id).get('performanceSessionId')
            self.store.put('runs', run)
            self.store.event(run_id, {'type': 'task_end', 'state': run['state'], 'time': run['endedAt'], 'message': run.get('message')})
            self.processes.pop(run_id, None)
            self.tasks.pop(run_id, None)
            self.cancelled.discard(run_id)

    async def cancel(self, run_id):
        self.store.get('runs', run_id)
        task = self.tasks.get(run_id)
        if task:
            self.cancelled.add(run_id)
            process = self.processes.get(run_id)
            if process:
                await self.terminate(process)
            await task
        return self.store.get('runs', run_id)

    async def close(self):
        pending = list(self.tasks)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in pending:
            run = self.store.get('runs', key)
            if run['state'] not in TERMINAL:
                run.update(state='interrupted', endedAt=int(time.time() * 1000))
                self.store.put('runs', run)
            self.tasks.pop(key, None)


router = APIRouter(prefix='/api/ui')


def manager(request: Request) -> UIManager:
    return request.app.state.ui


def get_run(request, run_id):
    try:
        return manager(request).store.get('runs', run_id)
    except KeyError:
        raise HTTPException(404, '未找到任务')


@router.get('/settings')
async def settings_get(request: Request):
    return manager(request).settings()


@router.post('/settings')
async def settings_save(value: Settings, request: Request):
    try:
        return manager(request).save_settings(value)
    except (ValueError, OSError) as error:
        raise HTTPException(400, '配置无效或报告目录不可写') from error


@router.get('/doctor')
async def doctor(request: Request):
    try:
        process = await asyncio.create_subprocess_exec('node', str(RUNNER.parent / 'doctor.mjs'), env=manager(request).environment(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 25)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(504, '环境检查超时')
        return json.loads(output)
    except (OSError, ValueError):
        raise HTTPException(503, '环境检查失败，请检查 Node.js')


@router.get('/cases')
async def cases_list(request: Request):
    return manager(request).store.all('cases')


@router.post('/cases')
async def case_save(value: TestCase, request: Request):
    store = manager(request).store
    body = value.model_dump()
    try:
        previous = store.get('cases', value.id)
        if previous['version'] != value.version:
            raise HTTPException(409, '用例已被修改，请重新加载后编辑')
        body['version'] = previous['version'] + 1
    except KeyError:
        body['version'] = 1
    store.put('cases', body)
    return body


@router.post('/cases/{case_id}/delete')
async def case_delete(case_id: str, request: Request):
    with manager(request).store.connect() as conn:
        conn.execute('DELETE FROM cases WHERE id=?', (case_id,))
    return {'deleted': True}


class ExportRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class ScanRequest(BaseModel):
    serial: str = Field(pattern=r'^[a-zA-Z0-9._:-]{1,128}$')
    scope: ExploreScope = Field(default_factory=lambda: ExploreScope(autoNavigation=True))
    openSettings: bool = True


@router.post('/scans', status_code=201)
async def scan_start(value: ScanRequest, request: Request):
    if value.scope.allowedControls or value.scope.visualAssertion:
        raise HTTPException(400, '扫描阶段只收集结构，不操作开关或调用模型')
    steps = []
    if value.openSettings:
        steps.append(Step(id='open-settings', type='settings', prompt='打开系统设置入口'))
    steps.append(Step(id='scan', type='scan', prompt='扫描页面结构', timeoutMs=300000, exploration=value.scope))
    try:
        return await manager(request).start(RunRequest(serial=value.serial, testCase=TestCase(name='页面结构扫描', steps=steps), timeoutSeconds=360))
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@router.post('/cases/export')
async def cases_export(value: ExportRequest, request: Request):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for key in dict.fromkeys(value.ids):
            try:
                case = manager(request).store.get('cases', key)
            except KeyError:
                raise HTTPException(404, '导出用例不存在')
            archive.writestr(f"cases/{case['id']}.json", json.dumps(case, ensure_ascii=False, indent=2))
            archive.writestr(f"parameters/{case['id']}.json", json.dumps({p['name']: '' if p['secret'] else p['default'] for p in case['parameters']}, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', '格式版本 1。需要本项目 tools/ui-test 执行器及 npm ci 安装依赖。\n用例 JSON 作为 runner 输入的 testCase，参数 JSON 作为 parameters；另需指定 serial。\n密钥通过本机 .env 或环境变量配置。敏感参数默认留空。\n')
    return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="ui-cases.zip"'})


@router.get('/runs')
async def runs_list(request: Request):
    runs = manager(request).store.all('runs')
    return [{**run, 'testCase': {k: run['testCase'][k] for k in ('id', 'name', 'version')}} for run in runs]


@router.post('/runs', status_code=201)
async def run_start(value: RunRequest, request: Request):
    try:
        return await manager(request).start(value)
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@router.get('/runs/{run_id}')
async def run_get(run_id: str, request: Request):
    return get_run(request, run_id)


@router.get('/runs/{run_id}/events')
async def run_events(run_id: str, request: Request, after: int = 0):
    get_run(request, run_id)
    return manager(request).store.events(run_id, after)


@router.get('/runs/{run_id}/report')
async def structured_report(run_id: str, request: Request):
    run = get_run(request, run_id)
    # Parameters are never stored in a run; the snapshot contains references only.
    body = {'run': run, 'events': manager(request).store.events(run_id)}
    return Response(json.dumps(body, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="ui-report-{run_id}.json"'})


@router.get('/runs/{run_id}/draft')
async def execution_draft(run_id: str, request: Request):
    run = get_run(request, run_id)
    if run['state'] not in TERMINAL:
        raise HTTPException(409, '执行结束后才能生成草稿')
    return draft_from_run(run, manager(request).store.events(run_id))


def step_metrics(run: dict, events: list[dict], performance_store) -> list[dict]:
    session_ids = list(dict.fromkeys([run.get('performanceSessionId')] + [e.get('sessionId') for e in events if e.get('type') == 'performance']))
    output = []
    for step in run['testCase']['steps']:
        progress = [e for e in events if e.get('type') == 'step' and e.get('stepId') == step['id']]
        starts = [e['time'] for e in progress if e.get('state') == 'running']
        if not starts:
            continue
        start = min(starts)
        ends = [e['time'] for e in progress if e.get('state') in TERMINAL]
        end = max(ends) if ends else run.get('endedAt') or int(time.time() * 1000)
        for session_id in filter(None, session_ids):
            path = performance_store.database_path(session_id)
            if not path.exists():
                continue
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute('SELECT COUNT(*) AS sampleCount, AVG(cpu_total_pct) AS cpuAverage, MAX(cpu_total_pct) AS cpuPeak, AVG(pss_kb)/1024.0 AS memoryAverageMb, AVG(fps) AS fpsAverage FROM sample WHERE ts_ms>=? AND ts_ms<=?', (start, end)).fetchone()
            if row['sampleCount']:
                output.append({'stepId': step['id'], 'sessionId': session_id, 'start': start, 'end': end, **dict(row)})
    return output


@router.get('/runs/{run_id}/metrics')
async def run_metrics(run_id: str, request: Request):
    run = get_run(request, run_id)
    return step_metrics(run, manager(request).store.events(run_id), request.app.state.manager.store)


@router.post('/runs/{run_id}/cancel')
async def run_cancel(run_id: str, request: Request):
    get_run(request, run_id)
    return await manager(request).cancel(run_id)


@router.get('/runs/{run_id}/artifacts/{filename}')
async def artifact(run_id: str, filename: str, request: Request):
    run = get_run(request, run_id)
    root = Path(run['outputDir']).resolve()
    path = Path(run['report']) if filename == 'report.html' and run.get('report') else root / filename
    path = path.resolve()
    if root not in path.parents or not path.is_file() or path.suffix not in {'.png', '.html', '.json'} or run['sensitive']:
        raise HTTPException(404, '证据不存在')
    headers = {'Content-Security-Policy': "sandbox allow-scripts", 'X-Content-Type-Options': 'nosniff'} if path.suffix == '.html' else {}
    return FileResponse(path, headers=headers)
