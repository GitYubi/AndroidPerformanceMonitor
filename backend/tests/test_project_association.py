"""Project association across the performance and Monkey entry points."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main, monkey
from app.models import StartSessionRequest
from app.ui_testing import UIManager


def test_start_endpoints_validate_project_and_persist_server_name(tmp_path):
    ui = UIManager(tmp_path / 'ui')
    project = ui.store.images.create_project('车型测试')
    calls = []
    cleanup_calls = []
    async def clean_stale_temp(serial):
        cleanup_calls.append(serial)
    async def performance_start(value):
        assert cleanup_calls == [value.serial]
        calls.append(('performance', value))
        return 'session'
    async def monkey_start(config, project_name='默认项目'):
        calls.append(('monkey', config, project_name))
        return {'id': 'monkey'}
    monitor = SimpleNamespace(start=performance_start, active={}, store=SimpleNamespace(get_session=lambda _: None))
    monkey_manager = SimpleNamespace(start=monkey_start, clean_stale_temp=clean_stale_temp)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=ui, manager=monitor, monkey=monkey_manager)))

    async def scenario():
        value = StartSessionRequest(serial='device', project_id=project['id'], project_name='伪造名称')
        await main.start_session(value, request)
        assert calls[-1][1].project_id == project['id']
        assert calls[-1][1].project_name == '车型测试'
        job = monkey.MonkeyRequest(serial='device', mode='none', project_id=project['id'])
        await monkey.start(job, request)
        assert calls[-1][1].project_id == project['id'] and calls[-1][2] == '车型测试'
        with pytest.raises(HTTPException):
            await main.start_session(StartSessionRequest(serial='device', project_id='a' * 32), request)
        with pytest.raises(HTTPException):
            await monkey.start(monkey.MonkeyRequest(serial='device', mode='none', project_id='a' * 32), request)
        assert len(calls) == 2
        assert cleanup_calls == ['device']
    asyncio.run(scenario())


def test_record_deletion_respects_active_tasks_and_cleans_files(tmp_path):
    from app.storage import SessionStore
    from app.monkey_records import MonkeyRecords
    from app import ui_testing as ui_module

    ui = UIManager(tmp_path / 'ui')
    monitor = SimpleNamespace(store=SessionStore(tmp_path / 'performance'), active={})
    monkey_manager = SimpleNamespace(store=MonkeyRecords(tmp_path / 'monkey'), active={})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=ui, manager=monitor, monkey=monkey_manager)))
    ui_id = 'a' * 32
    ui_dir = tmp_path / 'reports' / ui_id
    ui_dir.mkdir(parents=True)
    (ui_dir / 'report.html').write_text('report')
    case = {'id': 'test', 'name': '测试用例', 'version': 1, 'projectId': 'default', 'steps': []}
    ui.store.put('runs', {'id': ui_id, 'state': 'passed', 'testCase': case, 'outputDir': str(ui_dir)})
    ui.store.event(ui_id, {'type': 'step'})
    writer = monitor.store.create_session('performance-test', {'session_id': 'performance-test', 'serial': 'dev', 'created_at_ms': 1, 'duration_seconds': 60, 'interval_ms': 1000, 'enabled_metrics': {'cpu': True, 'memory': False, 'fps': False}})
    writer.finish('completed')
    session_dir = monitor.store.database_path('performance-test').parent
    monkey_id = 'b' * 32
    run = {'id': monkey_id, 'serial': 'dev', 'state': 'completed', 'created_at_ms': 2, 'config': {'duration_hours': 1, 'interval_ms': 1000, 'fps': False}}
    monkey_manager.store.put(run)
    monkey_manager.store.initialize(run)
    monkey_dir = monkey_manager.store.directory(monkey_id)

    async def scenario():
        listed = await ui_module.runs_list(request)
        assert listed[0]['testCase']['projectId'] == 'default'
        ui.tasks[ui_id] = None
        with pytest.raises(HTTPException, match='运行中'):
            await ui_module.delete_ui_run(ui_id, request)
        ui.tasks.clear()
        with ui.store.connect() as conn:
            conn.execute("CREATE TRIGGER fail_ui BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT, 'failed'); END")
        with pytest.raises(HTTPException, match='删除运行记录失败'):
            await ui_module.delete_ui_run(ui_id, request)
        assert ui_dir.exists() and ui.store.get('runs', ui_id)['id'] == ui_id
        with ui.store.connect() as conn:
            conn.execute('DROP TRIGGER fail_ui')
        assert (await ui_module.delete_ui_run(ui_id, request))['cleanupErrors'] == []
        assert not ui_dir.exists() and ui.store.events(ui_id) == []
        with pytest.raises(KeyError): ui.store.get('runs', ui_id)
        monitor.active['performance-test'] = object()
        with pytest.raises(HTTPException, match='运行中'):
            await main.delete_session('performance-test', request)
        monitor.active.clear()
        assert (await main.delete_session('performance-test', request))['cleanupErrors'] == []
        assert not session_dir.exists()
        monkey_manager.active[monkey_id] = object()
        with pytest.raises(HTTPException, match='运行中'):
            await monkey.delete_run(monkey_id, request)
        monkey_manager.active.clear()
        assert (await monkey.delete_run(monkey_id, request))['cleanupErrors'] == []
        assert not monkey_dir.exists()
        with pytest.raises(KeyError): monkey_manager.store.get(monkey_id)
    asyncio.run(scenario())
