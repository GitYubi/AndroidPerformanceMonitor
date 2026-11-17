import asyncio
import base64
import struct
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app import monkey_multiscreen as multi
from app.monkey import MonkeyRequest, display_report, remote_paths, runner_script


SCREEN = {
    'display_id': 2, 'fingerprint': 'b' * 64, 'user_id': 10,
    'width': 200, 'height': 100, 'rotation': 0, 'input_supported': True, 'reason': '',
    'identity': 'local:123', 'physical_id': '123',
}


def config(**changes):
    return multi.MultiScreen(targets=[{'display_id':2, 'fingerprint':'b'*64, 'packages':['test.app'], **changes}])


def test_legacy_and_multiscreen_runner_contract():
    paths = remote_paths('a' * 32)
    legacy = MonkeyRequest(serial='test', mode='whitelist', packages=['test.app'])
    assert '/system/framework/monkey.jar' in runner_script(legacy, paths)
    request = MonkeyRequest(serial='test', mode='none', multi_screen=config())
    script = runner_script(request, paths)
    assert 'acpm.monkey.Runner run' in script and paths['gate'] in script and paths['halt'] in script
    assert '/system/framework/monkey.jar' not in script
    with pytest.raises(ValidationError):
        MonkeyRequest(serial='test', mode='whitelist', packages=['test.app'], multi_screen=config())


def test_blacklist_rejects_allowed_switch_or_area_for_excluded_package():
    with pytest.raises(ValidationError, match='黑名单中的应用'):
        config(mode='blacklist', launch_packages=['test.app'])
    with pytest.raises(ValidationError, match='黑名单中的应用'):
        config(mode='blacklist', areas=[dict(package='test.app',left=20,top=2,right=60,bottom=20)])


def test_prepare_rejects_stale_identity_and_out_of_screen_roi(monkeypatch):
    monkeypatch.setattr(multi, 'discover', AsyncMock(return_value=[SCREEN]))
    plan, inventory = asyncio.run(multi.prepare('test', config(mode='blacklist',launch_packages=['launch.app']), 42,300))
    assert '\tscreen\t' not in plan
    assert 'screen\t2\t10\t200\t100\t0\tlocal:123\tblacklist' in plan
    assert 'launch\t2\tlaunch.app' in plan and inventory==[SCREEN]
    with pytest.raises(ValueError, match='配置已过期'):
        asyncio.run(multi.prepare('test', config(fingerprint='c'*64), 42,300))
    with pytest.raises(ValueError, match='区域超出画面'):
        asyncio.run(multi.prepare('test', config(areas=[dict(package='system.app',left=20,top=2,right=201,bottom=20)]), 42,300))


def test_frame_uses_physical_id_and_checks_geometry(monkeypatch):
    monkeypatch.setattr(multi, 'discover', AsyncMock(return_value=[SCREEN]))
    raw = b'\x89PNG\r\n\x1a\n'+bytes(8)+struct.pack('>II',200,100)
    command = AsyncMock(return_value=raw)
    monkeypatch.setattr(multi.Device, 'command', command)
    frame = asyncio.run(multi.capture('test', 2, 'b'*64))
    command.assert_awaited_once_with('exec-out','screencap','-p','-d','123')
    assert base64.b64decode(frame['image'].split(',')[1]) == raw


def test_summary_keeps_failure_context_and_marks_unselected(monkeypatch, tmp_path):
    unselected = {**SCREEN, 'display_id':0}
    display_only = {**SCREEN, 'display_id':3, 'input_supported':False, 'reason':'不可触控'}
    summary = multi.ScreenEvidence(tmp_path, [unselected, SCREEN, display_only], config(), 42)
    summary.add('@ACPM\tPLAN\t1\t2\ttap')
    summary.add('@ACPM\tRESULT\t1\t2\ttap\tsuccess\ttest.app\t[[[40, 50]]]')
    assert summary.anomaly('anr',10,'test.app')
    assert not summary.anomaly('crash',0,'test.app')
    summary.add('@ACPM\tPLAN\t2\t2\tpinch')
    summary.add('@ACPM\tRESULT\t2\t2\tpinch\tfailed\t\t<script>')
    data = multi.read_summary(tmp_path)
    assert data['selected_coverage'] and not data['all_input_coverage']
    assert data['failures'][0]['display_id'] is None
    assert data['failures'][1]['preceding_events'][0]['sequence']==1
    assert data['screens'][2]['reason']=='不可触控'
    html = display_report(data)
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_app_launch_does_not_count_as_touch_coverage(tmp_path):
    evidence = multi.ScreenEvidence(tmp_path, [SCREEN], config(), 42)
    evidence.add('@ACPM\tPLAN\t1\t2\tapp_switch')
    evidence.add('@ACPM\tRESULT\t1\t2\tapp_switch\tsuccess\ttest.app/.Main\t已发出应用启动指令')
    data = multi.read_summary(tmp_path)
    assert data['screens'][0]['actions']['app_switch']['success'] == 1
    assert data['screens'][0]['injected'] == 0
    assert not data['selected_coverage'] and not data['all_input_coverage']
