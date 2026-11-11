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


def test_new_step_schema_rejects_missing_coordinates_and_empty_selector():
    with pytest.raises(ValidationError):
        ui.Step(id='tap', type='adb_tap', prompt='ADB 点击', options={'x': 25})
    with pytest.raises(ValidationError):
        ui.Step(id='element', type='element_assert', prompt='元素断言', selector={}, options={'expected': 'exists'})
    swipe = ui.Step(id='swipe', type='adb_swipe', prompt='ADB 滑动',
                    options={'startX': 25, 'startY': 40, 'endX': 25, 'endY': 10})
    assert swipe.options.durationMs == 300


def test_image_baseline_is_local_and_included_in_case_export(tmp_path):
    import base64
    import io
    import zipfile
    from fastapi import UploadFile

    data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
    manager = ui.UIManager(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=manager)))

    async def scenario():
        uploaded = await ui.upload_baseline(request, UploadFile(filename='reference.png', file=io.BytesIO(data)))
        value = ui.TestCase(name='图像断言', steps=[ui.Step(id='image', type='image_assert', prompt='图像断言',
                            options={'baselineId': uploaded['id']})])
        await ui.case_save(value, request)
        response = await ui.cases_export(ui.ExportRequest(ids=[value.id]), request)
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            assert archive.read(f"baselines/{uploaded['id']}.png") == data
            saved = json.loads(archive.read(f'cases/{value.id}.json'))
            assert saved['steps'][0]['options']['minSimilarity'] == 99

    asyncio.run(scenario())


def test_image_assertion_modes_validate_regions_and_preserve_legacy_default():
    base = {'id': 'compare', 'type': 'image_assert', 'prompt': '图像断言'}
    baseline = {'baselineId': 'a' * 64}
    assert ui.Step(**base, options=baseline).options.mode == 'screen'
    with pytest.raises(ValidationError):
        ui.Step(**base, options={**baseline, 'mode': 'element'})
    assert ui.Step(**base, selector={'label': '设置'}, options={**baseline, 'mode': 'element'}).selector.label == '设置'
    with pytest.raises(ValidationError):
        ui.Step(**base, options={**baseline, 'mode': 'roi', 'roi': {'left': 20, 'top': 10, 'right': 20, 'bottom': 50}})
    step = ui.Step(**base, options={**baseline, 'mode': 'roi', 'roi': {'left': 20, 'top': 10, 'right': 40, 'bottom': 50}})
    assert step.options.roi.right == 40


def test_uiautomator2_element_capture_uses_exact_unique_bounds():
    from PIL import Image
    from uiautomator2.xpath import XPathEntry
    from app.ui_element_capture import ElementNotFound, ElementNotUnique, capture_element

    class FakeDevice:
        wait_timeout = 5
        node = '<node text="" content-desc="就绪" resource-id="demo:id/status" class="android.widget.TextView" package="demo" bounds="[1,1][4,3]" />'
        def __init__(self):
            self.xpath = XPathEntry(self)
            self.nodes = self.node

        def dump_hierarchy(self):
            return '<hierarchy>' + self.nodes + '</hierarchy>'

        def screenshot(self):
            return Image.new('RGB', (8, 8), (1, 2, 3))

    device = FakeDevice()
    selector = {'resourceId': 'demo:id/status', 'label': '就绪', 'className': 'android.widget.TextView', 'package': 'demo'}
    assert capture_element(device, selector).size == (3, 2)
    with pytest.raises(ElementNotFound):
        capture_element(device, {**selector, 'label': '不匹配'})
    device.nodes *= 2
    with pytest.raises(ElementNotUnique):
        capture_element(device, selector)
