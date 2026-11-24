import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app import ui_testing as ui


def request_for(manager):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=manager)))


def save_case(manager, **fields):
    case = ui.TestCase(name='压测样例', parameters=[{'name': 'state', 'default': 'ok'}], steps=[
        ui.Step(id='check', type='variable_assert', prompt='比较变量',
                options={'variable': 'state', 'expectedValue': 'ok'})
    ], **fields)
    manager.store.put('cases', case.model_dump())
    return case


def test_2000_round_history_is_bounded_and_paginated(tmp_path, monkeypatch):
    async def scenario():
        manager = ui.UIManager(tmp_path)
        case = save_case(manager)
        calls = 0

        async def execute(_, job, *args, summary):
            nonlocal calls
            calls += 1
            assert job['compactReport'] is True
            run, collect = summary
            # Simulate large runner messages and incidental output every round.
            (Path(job['outputDir']) / 'unexpected.html').write_text('temporary')
            if calls % 2 == 0:
                for _ in range(7):
                    collect({'type': 'step', 'stepId': 'check', 'state': 'failed', 'message': 'x' * 8000})
            run.update(state='passed' if calls % 2 else 'failed')

        monkeypatch.setattr(manager, '_execute_run', execute)
        request = request_for(manager)
        batch = await ui.stress_start(ui.StressRequest(caseId=case.id, repetitions=2000), request)
        await manager.stress.tasks[batch['id']]
        batch = manager.store.get('stress_batches', batch['id'])
        assert batch['state'] == 'completed' and batch['completed'] == 2000
        assert batch['counts']['passed'] == batch['counts']['failed'] == 1000
        detail = ui.stress_detail(batch['id'], request, offset=25, limit=25, failures=True)
        assert detail['total'] == 1000 and [r['number'] for r in detail['items']] == list(range(52, 102, 2))
        assert all(len(r['failures']) == 5 and len(r['failures'][0]['message']) == 240 for r in detail['items'])
        report = ui.stress_report(batch['id'], request)
        assert len(json.loads(report.body)['rounds']) == 2000
        assert manager.store.all('runs') == [] and manager.store.events(batch['id']) == []
        assert not list(manager.stress.workspace_root.iterdir())
        assert not (tmp_path / 'stress-evidence').exists()
        print(f"2000 compressed rows: SQLite {manager.store.db.stat().st_size} bytes, JSON {len(report.body)} bytes")
        assert manager.store.db.stat().st_size < 5 * 1024 * 1024
        assert not manager.external_busy
        await ui.stress_delete(batch['id'], request)
        assert manager.store.stress_batches('default') == []
        assert manager.store.stress_rounds(batch['id'])['total'] == 0
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='fixture uses a POSIX adb wrapper')
def test_real_runner_retains_only_failed_step_screenshots(tmp_path, monkeypatch):
    async def scenario():
        root = tmp_path / 'store'
        manager = ui.UIManager(root)
        for name, color in [('actual', 'orange'), ('different', 'blue')]:
            Image.new('RGB', (20, 20), color).save(tmp_path / f'{name}.png')
        counter = tmp_path / 'count'
        counter.write_text('0')
        adb = tmp_path / 'adb'
        adb.write_text(f"""#!/bin/sh
n=$(cat "{counter}")
echo $((n+1)) > "{counter}"
case "$n" in
    0|1|2) exec cat "{tmp_path / 'actual.png'}" ;;
    *) exec cat "{tmp_path / 'different.png'}" ;;
esac
""")
        adb.chmod(0o755)
        monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))

        async def connected():
            return [SimpleNamespace(serial='fixture-device', state='device')]
        monkeypatch.setattr(ui, 'list_devices', connected)
        request = request_for(manager)
        for source, state in [('actual', 'passed'), ('different', 'failed')]:
            image = manager.store.images.save('default', source, 'roi', (tmp_path / f'{source}.png').read_bytes())
            orange = manager.store.images.save('default', '橙色', 'roi', (tmp_path / 'actual.png').read_bytes())
            steps = [ui.Step(id='compare', type='image_assert', prompt='对比画面',
                             onFailure='continue',
                             options={'baselineId': image['id'], 'baselineProjectId': 'default',
                                      'mode': 'screen', 'colorMode': 'color'})]
            if state == 'failed':
                steps.append(ui.Step(id='compare2', type='image_assert', prompt='第二次对比',
                                     options={'baselineId': orange['id'], 'baselineProjectId': 'default',
                                              'mode': 'screen', 'colorMode': 'color'}))
            case = ui.TestCase(name='图像对比', steps=steps)
            manager.store.put('cases', case.model_dump())
            batch = await ui.stress_start(ui.StressRequest(caseId=case.id, serial='fixture-device',
                                                           repetitions=2, stopOnFailure=True), request)
            await manager.stress.tasks[batch['id']]
            batch = manager.store.get('stress_batches', batch['id'])
            assert batch['counts'][state] == (2 if state == 'passed' else 1), batch
            rows = manager.store.stress_rounds(batch['id'])['items']
            folder = manager.stress.evidence_dir(batch['id'])
            if state == 'failed':
                first, second = rows[0]['failures']
                assert [(r['stepId'], r['screenshot']) for r in (first, second)] == [('compare', 1), ('compare2', 2)]
                for step in (first, second):
                    response = ui.stress_screenshot(batch['id'], 1, request, step=step['screenshot'])
                    with Image.open(response.path) as screenshot:
                        red, green, blue = screenshot.getpixel((5, 5))
                        if step == first:
                            assert red > 220 and green > 120 and blue < 50
                        else:
                            assert red < 50 and blue > 220
                    assert Path(response.path).stat().st_size <= 128 * 1024
                assert sorted(p.name for p in folder.iterdir()) == ['1-1.jpg', '1-2.jpg']
                await ui.stress_delete(batch['id'], request)
                assert not folder.exists()
            else:
                assert rows[0]['failures'] == []
                assert not folder.exists()
        assert not list(manager.stress.workspace_root.iterdir())
    asyncio.run(scenario())


def test_step_timeout_gets_its_own_screenshot_and_respects_budget(tmp_path, monkeypatch):
    from io import BytesIO
    from app import ui_stress

    async def scenario():
        manager = ui.UIManager(tmp_path)
        case = ui.TestCase(name='超时截图', steps=[ui.Step(
            id='capture', type='screenshot', prompt='读取画面')])
        manager.store.put('cases', case.model_dump())
        async def connected():
            return [SimpleNamespace(serial='fixture-device', state='device')]
        monkeypatch.setattr(ui, 'list_devices', connected)
        async def execute(_, job, *args, summary):
            run, collect = summary
            for index in range(5):
                collect(dict(type='step', state='failed', stepId='capture', iterations=[index + 1]))
            collect(dict(type='step', state='running', stepId='capture', iterations=[3, 2]))
            run.update(state='error', message='任务达到总超时')
        monkeypatch.setattr(manager, '_execute_run', execute)
        frame = BytesIO()
        Image.new('RGB', (20, 20), 'green').save(frame, 'JPEG')
        async def capture(serial):
            assert serial == 'fixture-device'
            return frame.getvalue()
        monkeypatch.setattr(manager.stress, 'capture_timeout_evidence', capture)
        request = request_for(manager)
        for budget in (1024, 1):
            monkeypatch.setattr(ui_stress, 'MAX_BATCH_EVIDENCE_BYTES', budget)
            batch = await ui.stress_start(ui.StressRequest(caseId=case.id, serial='fixture-device', repetitions=1), request)
            await manager.stress.tasks[batch['id']]
            row, = manager.store.stress_rounds(batch['id'])['items']
            assert len(row['failures']) == 5
            failure = row['failures'][-1]
            assert failure['state'] == 'error'
            assert failure['stepId'] == 'capture' and failure['iterations'] == [3, 2]
            if budget == 1:
                assert 'screenshot' not in failure
            else:
                assert Path(ui.stress_screenshot(batch['id'], 1, request, step=5).path).read_bytes() == frame.getvalue()
    asyncio.run(scenario())


def test_stop_and_restart_release_batch_and_keep_results(tmp_path, monkeypatch):
    async def scenario():
        manager = ui.UIManager(tmp_path)
        case = save_case(manager)
        request = request_for(manager)
        second = asyncio.Event()
        proceed = asyncio.Event()

        async def execute(batch_id, job, *args, summary):
            run, _ = summary
            if manager.store.stress_rounds(batch_id)['total'] == 0:
                run['state'] = 'passed'
            else:
                second.set()
                await proceed.wait()
                run['state'] = 'cancelled'
        monkeypatch.setattr(manager, '_execute_run', execute)
        batch = await ui.stress_start(ui.StressRequest(caseId=case.id, repetitions=500), request)
        task = manager.stress.tasks[batch['id']]
        await asyncio.wait_for(second.wait(), 2)
        assert manager.has_active_device('')
        with pytest.raises(ui.HTTPException, match='本机用例运行中'):
            await ui.stress_start(ui.StressRequest(caseId=case.id, repetitions=1), request)
        assert (await ui.stress_stop(batch['id'], request))['state'] == 'stopping'
        proceed.set()
        await task
        detail = ui.stress_detail(batch['id'], request, offset=0, limit=25)
        assert detail['batch']['state'] == 'cancelled'
        assert [row['state'] for row in detail['items']] == ['passed', 'cancelled']
        assert not manager.has_active_device('')
        manager.store.put_stress({**detail['batch'], 'state': 'running'})
        reopened = ui.UIManager(tmp_path)
        assert reopened.store.get('stress_batches', batch['id'])['state'] == 'interrupted'
        assert reopened.store.stress_rounds(batch['id'])['total'] == 2
    asyncio.run(scenario())
