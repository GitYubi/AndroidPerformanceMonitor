"""Multiscreen Monkey contracts and the host side of the Android runner."""
from __future__ import annotations

import asyncio
import base64
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import struct
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adb import AdbError, run_adb
from .ui_hierarchy_capture import physical_display
from .ui_map import Device

RUNNER = Path(__file__).resolve().parents[2] / 'tools/monkey/multiscreen-runner.jar'
MAIN_CLASS = 'acpm.monkey.Runner'
PREFIX = '@ACPM\t'
ACTIONS = ('tap', 'long_press', 'swipe', 'pinch', 'app_switch')
LABELS = dict(zip(ACTIONS, ('点击', '长按', '滑动', '双指缩放', '应用切换')))
PackageName = Annotated[str, Field(min_length=1, max_length=255, pattern=r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')]
Pixel = Annotated[int, Field(ge=0, le=32768, strict=True)]
Percent = Annotated[int, Field(ge=0, le=100, strict=True)]


class SystemArea(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package: PackageName
    left: Pixel
    top: Pixel
    right: Pixel
    bottom: Pixel

    @model_validator(mode='after')
    def check_rect(self):
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError('系统区域必须有有效的像素边界')
        return self


class ScreenTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_id: int = Field(ge=0, le=32768, strict=True)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    mode: Literal['whitelist', 'blacklist'] = 'whitelist'
    packages: list[PackageName] = Field(default_factory=list, max_length=256)
    launch_packages: list[PackageName] = Field(default_factory=list, max_length=30)
    areas: list[SystemArea] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def scope(self):
        if self.mode=='whitelist' and not self.packages and not self.areas:
            raise ValueError('每块参与屏幕需配置应用或系统区域')
        if self.mode=='blacklist' and not self.packages:
            raise ValueError('请配置此屏幕要排除的应用')
        if len(set(self.packages)) != len(self.packages) or len(set(self.launch_packages)) != len(self.launch_packages):
            raise ValueError('一块屏幕不能重复配置同一应用')
        if self.mode=='blacklist' and any(p in self.packages for p in
                [*self.launch_packages, *(area.package for area in self.areas)]):
            raise ValueError('黑名单中的应用不能配置为切换目标或系统区域')
        if self.mode=='whitelist' and self.launch_packages:
            raise ValueError('白名单模式直接使用名单中的应用作为切换候选')
        return self


class ScreenActions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tap: Percent = 45
    long_press: Percent = 10
    swipe: Percent = 30
    pinch: Percent = 15
    app_switch: Percent = 0

    @model_validator(mode='after')
    def total(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError('多屏动作占比之和必须为 100%')
        return self


class MultiScreen(BaseModel):
    model_config = ConfigDict(extra='forbid')
    targets: list[ScreenTarget] = Field(min_length=1, max_length=16)
    actions: ScreenActions = Field(default_factory=ScreenActions)

    @model_validator(mode='after')
    def unique_screens(self):
        if len({t.display_id for t in self.targets}) != len(self.targets):
            raise ValueError('屏幕 ID 不得重复')
        if self.actions.app_switch and not any((t.packages if t.mode=='whitelist' else t.launch_packages) for t in self.targets):
            raise ValueError('应用切换需要至少配置一个应用')
        return self


def command(jar, *args):
    return f'CLASSPATH={shlex.quote(jar)} ' + shlex.join(['app_process', '/system/bin', MAIN_CLASS, *args])


async def discover(serial):
    """List displays even when the targeted injection API is unavailable."""
    if not RUNNER.is_file():
        raise ValueError('缺少多屏 Monkey 设备端运行包')
    remote = f'/data/local/tmp/acpm-displays-{uuid4().hex}.jar'
    try:
        await run_adb('push', str(RUNNER), remote, serial=serial, timeout_seconds=15)
        output = await run_adb('shell', command(remote, 'inspect'), serial=serial, timeout_seconds=20)
        dump = await run_adb('shell', 'dumpsys', 'display', serial=serial)
    finally:
        try:
            await run_adb('shell', 'rm', '-f', remote, serial=serial, timeout_seconds=5)
        except AdbError:
            pass
    displays = []
    for line in output.splitlines():
        if not line.startswith(PREFIX+'DISPLAY\t'):
            continue
        _, _, id_, user, width, height, rotation, state, identity, supported, reason, activity = line.split('\t')
        id_, user, width, height, rotation, state = map(int, (id_, user, width, height, rotation, state))
        try:
            physical_id = physical_display(dump, id_)
        except ValueError:
            physical_id = None
        fingerprint = hashlib.sha256(json.dumps([id_, user, width, height, rotation, state, identity]).encode()).hexdigest()
        displays.append({'display_id': id_, 'user_id': user if user>=0 else None, 'width': width, 'height': height,
                         'rotation': rotation, 'identity': identity, 'fingerprint': fingerprint, 'state': state,
                         'input_supported': supported=='true', 'reason': reason, 'activity': activity,
                         'physical_id': physical_id})
    if not displays:
        raise ValueError('Android 未返回可识别的屏幕')
    return displays


async def capture(serial, display_id, fingerprint=None):
    displays = await discover(serial)
    screen = next((screen for screen in displays if screen['display_id']==display_id), None)
    if not screen or (fingerprint and screen['fingerprint']!=fingerprint):
        raise ValueError('屏幕已变化，请重新扫描')
    if screen['physical_id'] is None:
        raise ValueError('此屏没有可确认的物理截图来源')
    raw = await Device(serial).command('exec-out','screencap','-p','-d',screen['physical_id'])
    if len(raw)<24 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('该屏幕未返回 PNG 截图')
    if struct.unpack('>II',raw[16:24])!=(screen['width'],screen['height']):
        raise ValueError('截图尺寸与逻辑屏幕不一致，请刷新后重试')
    return {'screen': screen, 'image': 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii')}


async def prepare(serial, multi, seed, throttle):
    screens = await discover(serial)
    by_id = {item['display_id']: item for item in screens}
    lines = ['\t'.join(map(str, ['options', seed, throttle, *multi.actions.model_dump().values()]))]
    for t in multi.targets:
        s = by_id.get(t.display_id)
        if not s or s['fingerprint'] != t.fingerprint:
            raise ValueError(f'屏幕 {t.display_id} 配置已过期，请重新扫描')
        if not s['input_supported']:
            raise ValueError(f"屏幕 {t.display_id} 不可注入：{s['reason']}")
        lines.append('\t'.join(map(str, ['screen',t.display_id,s['user_id'],s['width'],s['height'],s['rotation'],s['identity'],t.mode])))
        for package in t.packages:
            lines.append(f'app\t{t.display_id}\t{package}')
        for package in t.launch_packages:
            lines.append(f'launch\t{t.display_id}\t{package}')
        for area in t.areas:
            if area.right>s['width'] or area.bottom>s['height']:
                raise ValueError(f'屏幕 {t.display_id} 的系统区域超出画面')
            lines.append('\t'.join(map(str,['area',t.display_id,area.package,area.left,area.top,area.right,area.bottom])))
    return '\n'.join(lines)+'\n', screens


def read_summary(directory):
    try:
        return json.loads((directory/'display-summary.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


class ScreenEvidence:
    """Bounded per-action counts, recent events, and failure context."""
    def __init__(self, directory, screens, scope, seed):
        self.directory = directory
        self.screens = {item['display_id']: item for item in screens}
        self.targets = {t.display_id:t for t in scope.targets}
        self.seed = seed
        self.counts = {n:{action:Counter() for action in ACTIONS} for n in self.targets}
        self.recent = deque(maxlen=200)
        self.failures = deque(maxlen=20)
        self.reasons = {n: Counter() for n in self.targets}
        self.fail_counts = Counter()
        self.observed_packages = {n: set() for n in self.targets}
        self.flush()

    def add(self, line):
        fields = line.split('\t')
        if len(fields)<5 or fields[0]!='@ACPM' or fields[1] not in ('PLAN','RESULT'):
            return
        kind, seq, display, action = fields[1:5]
        display = int(display)
        counter = self.counts[display][action]
        if kind=='PLAN':
            counter['planned'] += 1
        elif len(fields)==8 and fields[5] in ('success','skipped','failed'):
            status, package, detail = fields[5:]
            counter[status] += 1
            event = {'sequence':int(seq),'display_id':display,'action':action,'status':status,'package':package,'detail':detail[:600]}
            self.recent.append(event)
            if status == 'success' and package:
                self.observed_packages[display].add(package.split('/',1)[0])
            if status != 'success':
                if detail in self.reasons[display] or len(self.reasons[display])<50:
                    self.reasons[display][detail[:200]] += 1
                else:
                    self.reasons[display]['其他原因'] += 1
            if status=='failed':
                self.failures.append({**event,'preceding_events':list(self.recent)[-21:-1]})
        self.flush()

    def anomaly(self, kind, user, package):
        package = package.split(':',1)[0]
        matches = [id_ for id_, target in self.targets.items()
                   if self.screens[id_]['user_id']==user
                   and ((target.mode=='whitelist' and package in target.packages)
                        or package in self.observed_packages[id_] or package in target.launch_packages
                        or any(a.package==package for a in target.areas))]
        if not matches:
            return False
        # logcat does not carry a display ID; only a single configured screen is attributable.
        display = matches[0] if len(matches)==1 and self.attribution_is_unique(matches[0], package) else None
        self.fail_counts[kind] += 1
        self.failures.append({'kind':kind,'user_id':user,'package':package,'display_id':display,
                              'preceding_events':list(self.recent)[-20:]})
        self.flush()
        return True

    def attribution_is_unique(self, display, package):
        # Shared system processes and apps visible on another display cannot be assigned by logcat alone.
        return len(self.screens)==1 and package in self.targets[display].packages

    def flush(self):
        results = []
        for id_, screen in self.screens.items():
            actions = self.counts.get(id_, {})
            count = sum(row['success'] for action, row in actions.items() if action != 'app_switch')
            results.append({'display_id':id_,'selected':id_ in self.targets,'input_supported':screen['input_supported'],
                            'reason':screen['reason'],'injected':count,'actions':actions,'reasons':self.reasons.get(id_,{})})
        selected = [screen for screen in results if screen['selected']]
        eligible = [screen for screen in results if screen['input_supported']]
        body = {'seed':self.seed,'screens':results,'selected_coverage':bool(selected) and all(s['injected'] for s in selected),
                'all_input_coverage':bool(eligible) and all(s['selected'] and s['injected'] for s in eligible),
                'recent_events':list(self.recent),'failures':list(self.failures),'anomalies':self.fail_counts}
        temporary = self.directory/'display-summary.tmp'
        temporary.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8')
        temporary.replace(self.directory/'display-summary.json')


ANOMALY = re.compile(r'^\s*(\d+(?:\.\d+)?)\b.*\bam_(crash|anr):\s*\[(\d+),\d+,([^,\]]+)')

async def watch_anomalies(serial, evidence, on_fault, device_epoch):
    adb = shutil.which('adb')
    if not adb:
        raise AdbError('未找到 adb')
    proc = await asyncio.create_subprocess_exec(adb,'-s',serial,'logcat','-b','events','-v','epoch','-T','1',
        'am_crash:I','am_anr:I','*:S',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        while line := await proc.stdout.readline():
            found = ANOMALY.match(line.decode(errors='replace'))
            if not found or float(found.group(1))<device_epoch:
                continue
            _, kind, user, package = found.groups()
            if evidence.anomaly(kind,int(user),package) and on_fault(kind,package):
                return
        raise AdbError('Crash / ANR 监控中断')
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            await proc.wait()
