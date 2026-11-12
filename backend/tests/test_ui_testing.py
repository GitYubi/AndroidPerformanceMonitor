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


def can_step(kind, key, message, signals=None, period=None):
    options = {'dbcKey': key, 'channel': 0, 'message': message}
    if signals is not None:
        options['signals'] = signals
    if period is not None:
        options['cycleMs'] = period
    return {'id': kind, 'type': kind, 'prompt': kind, 'options': options}


def test_can_steps_use_selected_dbc_and_share_the_bus(monkeypatch, tmp_path):
    if not (ui.RUNNER.parent / 'node_modules' / '@midscene' / 'test').is_dir():
        pytest.skip('需要安装 UI 执行器依赖')
    monkeypatch.setenv('ZCAN_DATA_DIR', str(tmp_path / 'zcan'))
    from app.can_service import ROOT

    async def no_adb():
        pytest.fail('纯 CAN 用例不应读取 ADB 设备')
    monkeypatch.setattr(ui, 'list_devices', no_adb)

    async def scenario():
        bus = ui.CanService()
        manager = ui.UIManager(tmp_path / 'ui', can=bus)
        dbc = str(ROOT / 'config' / 'Example.dbc')
        def request(key):
            return ui.RunRequest(serial='', testCase=case(parameters=[], steps=[
                {'id': 'loop', 'type': 'for', 'prompt': 'For 循环', 'options': {'count': 2}},
                {**can_step('can_send_once', key, 'TestButtons', {'PressA': {'raw': 1}, 'PressB': 1}), 'parentId': 'loop'},
                can_step('can_send_loop', key, 'TestClassicA', {'Value': 17}, 20),
                {'id': 'delay', 'type': 'delay', 'prompt': '等待时间', 'options': {'durationMs': 100}},
                can_step('can_stop', key, 'TestClassicA'),
            ]))

        with pytest.raises(ValueError, match='打开 DBC'):
            await manager.start(request('0' * 64))
        bus.load_dbc(dbc)
        with pytest.raises(ValueError, match='打开设备'):
            await manager.start(request(bus.dbc_key))
        bus.open('simulation', dbc, [0])
        try:
            with pytest.raises(ValueError, match='DBC 与步骤配置不同'):
                await manager.start(request('0' * 64))
            run = await manager.start(request(bus.dbc_key))
            await asyncio.wait_for(manager.tasks[run['id']], 10)
            result = manager.store.get('runs', run['id'])
            assert result['state'] == 'needs_review'
            passed = [event for event in manager.store.events(run['id'])
                      if event['type'] == 'step' and event['state'] == 'passed']
            assert [event['stepId'] for event in passed] == [
                'loop', 'can_send_once', 'can_send_once', 'can_send_loop', 'delay', 'can_stop']
            await asyncio.sleep(.15)
            frames = list(bus.frames)
            once = [frame for frame in frames if frame['arbitration_id'] == 0x287]
            assert len(once) == 2 and all(frame['data'][0] == 3 and frame['is_fd'] for frame in once)
            repeated = [frame for frame in frames if frame['arbitration_id'] == 6]
            assert len(repeated) > 1 and all(frame['data'][0] == 17 and not frame['is_fd'] for frame in repeated)
            assert not any(task['active'] for task in bus.status()['tasks'])
            await asyncio.sleep(.1)
            assert len(bus.frames) == len(frames)
        finally:
            bus.close()
    asyncio.run(scenario())


def test_cancel_can_loop_keeps_unrelated_sends(monkeypatch, tmp_path):
    setup_worker(monkeypatch, tmp_path, """
      import { createInterface } from 'node:readline';
      const input = createInterface({ input: process.stdin });
      const lines = input[Symbol.asyncIterator]();
      const job = JSON.parse((await lines.next()).value);
      console.log(JSON.stringify({ protocol: 1, type: 'step', state: 'running', stepId: job.testCase.steps[0].id }));
      console.log(JSON.stringify({ protocol: 1, type: 'can_request', stepId: job.testCase.steps[0].id }));
      const reply = JSON.parse((await lines.next()).value);
      if (!reply.ok) process.exit(2);
      setInterval(() => {}, 1000);
    """)
    monkeypatch.setenv('ZCAN_DATA_DIR', str(tmp_path / 'zcan'))

    async def scenario():
        bus = ui.CanService()
        bus.open('simulation', '', [0, 1])
        try:
            bus.send_msg_by_name(1, 'TestButtons', {'PressB': 1})
            manager = ui.UIManager(tmp_path / 'ui', can=bus)
            request = ui.RunRequest(serial='', testCase=case(parameters=[], steps=[
                can_step('can_send_loop', bus.dbc_key, 'TestButtons', {'PressA': 1}, 25),
            ]))
            run = await manager.start(request)
            for _ in range(100):
                if any(frame['channel'] == 0 for frame in bus.frames):
                    break
                await asyncio.sleep(.02)
            else:
                pytest.fail('循环发送未开始')
            result = await manager.cancel(run['id'])
            assert result['state'] == 'cancelled'
            assert [(task['channel'], task['name']) for task in bus.status()['tasks'] if task['active']] == [(1, 'TestButtons')]
        finally:
            bus.close()
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
        assert ui.ui_project_images('default', request)[0]['source'] == 'legacy'
        value = ui.TestCase(name='图像断言', steps=[ui.Step(id='image', type='image_assert', prompt='图像断言',
                            options={'baselineId': uploaded['id']})])
        await ui.case_save(value, request)
        response = await ui.cases_export(ui.ExportRequest(ids=[value.id]), request)
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            assert archive.read(f"baselines/{uploaded['id']}.png") == data
            saved = json.loads(archive.read(f'cases/{value.id}.json'))
            assert saved['steps'][0]['options']['minSimilarity'] == 99

    asyncio.run(scenario())


def test_project_images_are_scoped_and_export_with_cases(tmp_path):
    import io
    import zipfile
    from fastapi import UploadFile
    from PIL import Image

    manager = ui.UIManager(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=manager)))
    first = ui.create_ui_project(ui.ProjectName(name='车型一'), request)
    second = ui.create_ui_project(ui.ProjectName(name='车型二'), request)
    assert ui.ui_projects(request)[0]['id'] == 'default'
    with pytest.raises(ui.HTTPException, match='项目名称已存在'):
        ui.create_ui_project(ui.ProjectName(name=' 车型一 '), request)

    png = io.BytesIO()
    Image.new('RGB', (3, 2), 'blue').save(png, 'PNG')
    data = png.getvalue()

    async def scenario():
        async def upload(project, name, source, raw=data):
            return await ui.save_ui_project_image(
                project['id'], request, name, source,
                UploadFile(filename='reference.png', file=io.BytesIO(raw)), region=None)

        with pytest.raises(ui.HTTPException, match='PNG 图片无法读取'):
            await upload(first, '损坏图片', 'roi', data[:35])
        roi = await upload(first, '框选按钮', 'roi')
        assert ui.ui_project_images(second['id'], request) == []
        element = await upload(second, '元素截图', 'element')
        assert await upload(first, '框选按钮', 'roi') == roi
        named_copy = await upload(first, '按钮的另一名称', 'roi')
        coordinates = {'left': 5, 'top': 8, 'right': 8, 'bottom': 10}
        with_coords = await ui.save_ui_project_image(first['id'], request, '带坐标', 'roi',
                            UploadFile(filename='reference.png', file=io.BytesIO(data)), region=json.dumps(coordinates))
        assert with_coords['region'] == coordinates
        assert ui.ui_project_images(first['id'], request)[0]['region'] == coordinates
        assert roi['id'] != named_copy['id']
        assert ui.ui_project_images(first['id'], request) == [with_coords, named_copy, roi]
        assert ui.ui_project_images(second['id'], request) == [element]
        assert (roi['width'], roi['height'], roi['source']) == (3, 2, 'roi')
        path = tmp_path / 'projects' / first['id'] / 'images' / f"{roi['id']}.png"
        assert path.read_bytes() == data
        assert ui.ui_project_image(first['id'], roi['id'], request).path == path
        value = ui.TestCase(name='车型用例', projectId=first['id'], steps=[
            ui.Step(id='image', type='image_assert', prompt='图像断言',
                    options={'baselineId': roi['id'], 'baselineProjectId': first['id']})
        ])
        saved = await ui.case_save(value, request)
        assert saved['projectId'] == first['id']
        assert manager.store.validate_baselines(value) == {f"{first['id']}/{roi['id']}": str(path.resolve())}
        response = await ui.cases_export(ui.ExportRequest(ids=[value.id]), request)
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            assert archive.read(f"baselines/{roi['id']}.png") == data
            options = json.loads(archive.read(f'cases/{value.id}.json'))['steps'][0]['options']
            assert options['baselineProjectId'] == first['id']

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


def test_screenshot_comparison_references_prior_steps_and_survives_draft_creation():
    first = {'id': 'before', 'type': 'screenshot', 'prompt': '截图',
             'options': {'captureName': '图片1'}}
    second = {'id': 'after', 'type': 'screenshot', 'prompt': '旧用例截图'}
    compare = {'id': 'compare', 'type': 'screenshot_assert', 'prompt': '前后截图对比',
               'options': {'firstStepId': 'before', 'secondStepId': 'after', 'relation': 'different'}}
    original = case(parameters=[], steps=[first, second, compare])
    assert original.steps[-1].options.minSimilarity == 100
    assert original.steps[-1].options.pixelTolerance == 0
    assert original.steps[1].options is None
    for steps in ([first, compare, second], [first, {**second, 'enabled': False}, compare]):
        with pytest.raises(ValidationError, match='前面已启用'):
            case(parameters=[], steps=steps)
    draft = ui.draft_from_run({'testCase': original.model_dump()}, [])['testCase']
    first, second, assertion = draft['steps']
    assert first['id'] != 'before'
    assert assertion['options']['firstStepId'] == first['id']
    assert assertion['options']['secondStepId'] == second['id']


def test_control_flow_validates_branch_scopes_and_remaps_drafts():
    loop = {'id': 'loop', 'type': 'for', 'prompt': '循环',
            'options': {'count': 2, 'indexVariable': 'round'}}
    condition = {'id': 'if', 'type': 'variable_assert', 'prompt': '变量比较',
                 'options': {'variable': 'round', 'expectedValue': '1'}}
    check = {'id': 'if', 'type': 'if', 'prompt': '判断', 'parentId': 'loop', 'condition': condition}
    roi = {'id': 'roi', 'type': 'screenshot', 'prompt': 'ROI', 'parentId': 'if',
           'options': {'captureName': '区域', 'mode': 'roi',
                       'roi': {'left': 1, 'top': 1, 'right': 5, 'bottom': 4}}}
    element = {'id': 'element', 'type': 'element_screenshot', 'prompt': '元素', 'parentId': 'if',
               'selector': {'resourceId': 'demo:id/control'}, 'options': {'captureName': '按钮'}}
    compare = {'id': 'compare', 'type': 'screenshot_assert', 'prompt': '对比', 'parentId': 'if',
               'options': {'firstStepId': 'roi', 'secondStepId': 'element'}}
    test_case = case(parameters=[], steps=[loop, check, roi, element, compare])
    saved = ui.draft_from_run({'testCase': test_case.model_dump()}, [])['testCase']['steps']
    assert saved[1]['parentId'] == saved[0]['id']
    assert saved[4]['parentId'] == saved[1]['id']
    assert saved[4]['options']['secondStepId'] == saved[3]['id']
    with pytest.raises(ValidationError, match='当前分支或外层'):
        case(parameters=[], steps=[loop, check, roi, element, {**compare, 'branch': 'else'}])
    with pytest.raises(ValidationError, match='不可见的变量'):
        case(parameters=[], steps=[loop, check, roi, element, compare, {**condition, 'id': 'outside'}])
    with pytest.raises(ValidationError, match='定位条件'):
        case(parameters=[], steps=[loop, check, {**element, 'selector': None}])
    fallback = {'id': 'fallback', 'type': 'else', 'prompt': 'Else 分支', 'parentId': 'if', 'branch': 'else'}
    action = {'id': 'no', 'type': 'delay', 'prompt': '等待', 'options': {'durationMs': 1}}
    case(parameters=[], steps=[loop, check, {**action, 'parentId': 'if', 'branch': 'else'}])
    test_case = case(parameters=[], steps=[loop, check, fallback, {**action, 'parentId': 'fallback'}])
    draft = ui.draft_from_run({'testCase': test_case.model_dump()}, [])['testCase']['steps']
    assert draft[2]['parentId'] == draft[1]['id'] and draft[3]['parentId'] == draft[2]['id']
    with pytest.raises(ValidationError, match='Else 必须归属于 If'):
        case(parameters=[], steps=[{**fallback, 'parentId': None, 'branch': None}])
    with pytest.raises(ValidationError, match='只能有一个 Else'):
        case(parameters=[], steps=[loop, check, fallback, {**fallback, 'id': 'duplicate'}])


def test_named_variable_assignments_validate_sources_and_remap_screenshot_ids():
    capture = {'id': 'capture', 'type': 'screenshot', 'name': '采集画面', 'prompt': '截图'}
    assignment = {'id': 'saved', 'type': 'assign', 'name': '保存画面', 'prompt': '赋值',
                  'options': {'variable': 'shot', 'valueType': 'image', 'sourceStepId': 'capture'}}
    compare = {'id': 'compare', 'type': 'screenshot_assert', 'prompt': '对比',
               'options': {'firstStepId': 'capture', 'secondVariable': 'shot'}}
    original = case(parameters=[], steps=[capture, assignment, compare])
    draft = ui.draft_from_run({'testCase': original.model_dump()}, [])['testCase']['steps']
    assert [item['name'] for item in draft] == ['采集画面', '保存画面', '']
    assert draft[1]['options']['sourceStepId'] == draft[0]['id']
    assert draft[2]['options']['firstStepId'] == draft[0]['id']
    assert draft[2]['options']['secondVariable'] == 'shot'
    with pytest.raises(ValidationError, match='图片变量须在前面赋值'):
        case(parameters=[], steps=[capture, compare, assignment])
    with pytest.raises(ValidationError):
        ui.Step(**{**assignment, 'options': {'variable': 'flag', 'valueType': 'boolean', 'booleanValue': 'false'}})


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
    device.nodes += device.node.replace('[1,1][4,3]', '[2,2][6,6]')
    with pytest.raises(ElementNotUnique):
        capture_element(device, selector)
    assert capture_element(device, {**selector, 'xpath': '/hierarchy/node[2]'}).size == (4, 4)
