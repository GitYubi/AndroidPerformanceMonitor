"""验证用例快照、敏感值持久化边界及真实子进程生命周期（不调用模型）。"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import ui_testing as ui
from app.device_logs import DeviceLogConfig, export_and_clean_device_logs


def case(**updates):
    return ui.TestCase.model_validate({
        'name': 'Wi-Fi 测试',
        'parameters': [{'name': 'PASSWORD', 'secret': True}],
        'steps': [{'id': 'input', 'type': 'input', 'prompt': '密码框', 'value': '${PASSWORD}'}],
        **updates,
    })


def test_rejects_duplicate_steps_and_secret_defaults():
    with pytest.raises(ValidationError):
        case(steps=[{'id': 'same', 'type': 'tap', 'prompt': 'a'}] * 2)
    with pytest.raises(ValidationError):
        case(parameters=[{'name': 'PASSWORD', 'secret': True, 'default': 'secret'}])
    with pytest.raises(ValidationError):
        case(parameters=[])


def test_store_recovers_all_unfinished_runs(tmp_path):
    store = ui.UIStore(tmp_path)
    store.put('runs', {'id': 'a', 'state': 'running'})
    store.put('runs', {'id': 'b', 'state': 'passed'})
    store.recover()
    assert store.get('runs', 'a')['state'] == 'interrupted'
    assert store.get('runs', 'b')['state'] == 'passed'


def test_settings_never_return_key_and_preserve_it_on_update(tmp_path):
    manager = ui.UIManager(tmp_path)
    response = manager.save_settings(ui.Settings(apiKey='test-private-value'))
    assert response['hasKey'] is True
    assert 'test-private-value' not in json.dumps(response)
    manager.save_settings(ui.Settings(model='another-model'))
    assert manager.settings(False)['apiKey'] == 'test-private-value'
    if os.name == 'posix':
        assert (tmp_path / 'settings.json').stat().st_mode & 0o777 == 0o600


def setup_worker(monkeypatch, tmp_path, source):
    path = tmp_path / 'fake-worker.mjs'
    path.write_text(source)
    monkeypatch.setattr(ui, 'RUNNER', path)

    async def devices():
        return [SimpleNamespace(serial='test-device', state='device')]
    monkeypatch.setattr(ui, 'list_devices', devices)


def test_real_worker_snapshot_secret_omission_and_device_lock(monkeypatch, tmp_path):
    setup_worker(monkeypatch, tmp_path, """
      import { readFileSync } from 'node:fs';
      const job = JSON.parse(readFileSync(0, 'utf8'));
      setTimeout(() => console.log(JSON.stringify({ protocol: 1, type: 'finished', state: job.parameters.PASSWORD === 'private-password' ? 'passed' : 'failed' })), 150);
    """)

    async def scenario():
        manager = ui.UIManager(tmp_path / 'data')
        request = ui.RunRequest(serial='test-device', testCase=case(), parameters={'PASSWORD': 'private-password'})
        run = await manager.start(request)
        task = manager.tasks[run['id']]
        request.testCase.name = 'later edit'
        with pytest.raises(ValueError, match='已有'):
            await manager.start(request)
        await task
        saved = manager.store.get('runs', run['id'])
        assert saved['state'] == 'passed'
        assert saved['testCase']['name'] == 'Wi-Fi 测试'
        assert 'private-password' not in json.dumps(saved)
        assert 'private-password' not in json.dumps(manager.store.events(run['id']))
        assert not manager.tasks and not manager.processes
    asyncio.run(scenario())


def test_cancel_and_abnormal_exit_have_terminal_state(monkeypatch, tmp_path):
    setup_worker(monkeypatch, tmp_path, "setInterval(() => {}, 1000);")

    async def scenario():
        manager = ui.UIManager(tmp_path / 'data')
        request = ui.RunRequest(serial='test-device', testCase=case(), parameters={'PASSWORD': 'x'})
        run = await manager.start(request)
        await asyncio.sleep(0.1)
        result = await manager.cancel(run['id'])
        assert result['state'] == 'cancelled'
        assert not manager.processes and not manager.tasks
        # Cancellation before process creation must also release the device.
        run = await manager.start(request)
        assert (await manager.cancel(run['id']))['state'] == 'cancelled'
        ui.RUNNER.write_text('process.exit(3);')
        run = await manager.start(request)
        await manager.tasks[run['id']]
        assert manager.store.get('runs', run['id'])['state'] == 'error'
    asyncio.run(scenario())


def test_log_export_preserves_device_files_when_ui_overlaps(monkeypatch, tmp_path):
    commands = []

    async def adb(*args, **kwargs):
        commands.append(args)
        return 'trace.txt\n' if args[0] == 'shell' and args[1] == 'ls' else ''

    monkeypatch.setattr('app.device_logs.run_adb', adb)
    results = asyncio.run(export_and_clean_device_logs('test', DeviceLogConfig(anr='/data/anr'), tmp_path, lambda *args: None, preserve=True))
    assert results['ANR']['exported'] == 1
    assert results['ANR']['cleaned'] is False
    assert not any('rm' in command for command in commands)


def test_draft_uses_completed_semantic_actions_and_keeps_unresolved_steps():
    original = case(steps=[{'id': 'open', 'type': 'action', 'prompt': '打开设置'}, {'id': 'check', 'type': 'assert', 'prompt': '设置可见'}])
    run = {'testCase': original.model_dump()}
    events = [{'type': 'action', 'stepId': 'open', 'action': 'Tap', 'target': '设置图标'}, {'type': 'step', 'stepId': 'open', 'state': 'passed'}]
    draft = ui.draft_from_run(run, events)
    assert draft['testCase']['steps'][0]['type'] == 'tap'
    assert draft['testCase']['steps'][0]['prompt'] == '设置图标'
    assert draft['testCase']['steps'][1]['type'] == 'assert'
    assert draft['testCase']['id'] != original.id
    assert not draft['warnings']
    # No completion evidence means retain the original intent, not half a route.
    draft = ui.draft_from_run(run, events[:1])
    assert draft['testCase']['steps'][0]['type'] == 'action'
    assert draft['warnings']


def test_case_requires_explicit_exploration_scope():
    with pytest.raises(ValidationError):
        case(steps=[{'id': 'explore', 'type': 'explore', 'prompt': '设置'}])


def test_step_metrics_only_include_samples_inside_step_window(tmp_path):
    import sqlite3
    path = tmp_path / 'perf.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE sample (ts_ms INTEGER, cpu_total_pct REAL, pss_kb REAL, fps REAL)')
        conn.executemany('INSERT INTO sample VALUES (?,?,?,?)', [(1, 99, 1024, 5), (10, 20, 2048, 30), (20, 40, 4096, 60), (50, 99, 1024, 5)])
    run = {'testCase': {'steps': [{'id': 'check'}]}, 'performanceSessionId': 'perf', 'endedAt': 50}
    events = [{'type': 'step', 'stepId': 'check', 'time': 10, 'state': 'running'}, {'type': 'step', 'stepId': 'check', 'time': 20, 'state': 'passed'}]
    result = ui.step_metrics(run, events, SimpleNamespace(database_path=lambda _: path))[0]
    assert result['sampleCount'] == 2
    assert result['cpuAverage'] == 30
    assert result['memoryAverageMb'] == 3
    assert result['fpsAverage'] == 45


def test_exploration_draft_contains_initial_and_restored_state_assertions():
    test = case(steps=[{'id': 'explore', 'type': 'explore', 'prompt': '显示设置', 'exploration': {'allowedControls': ['自动旋转屏幕'], 'maxActions': 2}}])
    events = [{'type': 'exploration', 'stepId': 'explore', 'control': '自动旋转屏幕', 'state': state, 'original': False} for state in ['running', 'passed', 'restored']]
    draft = ui.draft_from_run({'testCase': test.model_dump()}, events)
    assert [s['type'] for s in draft['testCase']['steps']] == ['assert', 'tap', 'assert', 'tap', 'assert']
    assert '关闭' in draft['testCase']['steps'][0]['prompt']
    assert '关闭' in draft['testCase']['steps'][-1]['prompt']


def test_scan_rejects_switches_and_model_assertions():
    for scope in [{'allowedControls': ['蓝牙']}, {'visualAssertion': '页面正常'}]:
        with pytest.raises(ValidationError, match='结构扫描'):
            ui.Step(id='scan', type='scan', prompt='结构扫描', exploration=scope)
    step = ui.Step(id='scan', type='scan', prompt='结构扫描', exploration={})
    assert step.exploration.allowedPackages == ['com.android.settings']


def test_selector_cannot_silently_fall_back_to_model():
    with pytest.raises(ValidationError, match='结构定位'):
        ui.Step(id='tap', type='tap', prompt='点击', selector={})
    with pytest.raises(ValidationError, match='结构定位'):
        ui.Step(id='input', type='input', prompt='输入', selector={'label': '测试'})
