"""Process-local CAN service owned by the application lifecycle."""
from __future__ import annotations
from contextlib import contextmanager
import os
import json
import hashlib
import configparser
import time
from math import isfinite
from pathlib import Path
from threading import RLock, Event, Thread
from collections import deque
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query
from cantools.database.errors import Error as DbcError
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('ZCAN_DATA_DIR', str(ROOT / 'data' / 'zcan'))
from zlg.zlgcan import zlgCan, DBC
from zlg.wake import resolve_signal_value, encode_wake_message

class CanService:
    def __init__(self):
        self.lock = RLock()
        self._lifecycle_lock = RLock()
        self.device = None
        self._pending_close = None
        self._ui_owners = {}
        self._manual_inflight = {}
        self.frames = deque(maxlen=10000)
        self.seq = 0
        self.receive_session = ''
        self.error = None
        self.stop = Event()
        self.worker = None
        self.dbc_path = None
        self.dbc_key = None
        self.catalog = []

    def open(self, backend, dbc, channels, device_id=None):
        with self._lifecycle_lock, self.lock:
            if self.device: raise ValueError('设备已打开')
            if self._pending_close is not None:
                self._close_device(self._pending_close)
            if backend not in ('simulation', 'mac-usb', 'hardware', 'hirain'): raise ValueError('无效后端')
            path = Path(dbc).expanduser().resolve() if dbc else self.dbc_path or ROOT / 'config' / 'Example.dbc'
            if not path.is_file(): raise ValueError('DBC 文件不存在')
            self.load_dbc(str(path))
            os.environ['ZCAN_BACKEND'] = backend
            device = zlgCan(0, str(path), device_id=device_id)
            try:
                for channel in channels: device.chn_open(channel)
            except BaseException as error:
                try:
                    self._close_device(device)
                except Exception as cleanup:
                    raise RuntimeError(f'{error}；{cleanup}') from error
                raise
            self.device = device
            self.frames.clear(); self.seq = 0; self.error = None
            self.receive_session = uuid4().hex
            self.stop.clear()
            self.worker = Thread(target=self._receive, daemon=True); self.worker.start()

    def load_dbc(self, path):
        with self.lock:
            if self.device: raise ValueError('请先断开设备再切换 DBC')
            path = Path(path).expanduser().resolve()
            if path.suffix.lower() != '.dbc': raise ValueError('请选择 DBC 文件')
            key = hashlib.sha256(path.read_bytes()).hexdigest()
            with DBC(str(path)) as db:
                catalog = []
                for m in db.db.messages:
                    catalog.append({'name': m.name, 'id': m.frame_id, 'length': m.length,
                        'can_fd': m.is_fd, 'extended': m.is_extended_frame, 'cycle_time': m.cycle_time or 100,
                        'signals': [{'name': v.name, 'minimum': v.minimum, 'maximum': v.maximum,
                            'scale': v.scale, 'offset': v.offset, 'unit': v.unit, 'initial': v.initial,
                            'choices': [{'raw': k, 'label': str(label)} for k, label in (v.choices or {}).items()],
                            'multiplexer': v.multiplexer_signal, 'multiplexer_ids': v.multiplexer_ids,
                            'is_multiplexer': v.is_multiplexer, 'comment': v.comment} for v in m.signals]})
            self.dbc_path, self.dbc_key, self.catalog = path, key, catalog
            return self.dbc_info()

    def dbc_info(self):
        return {'path': str(self.dbc_path) if self.dbc_path else '', 'key': self.dbc_key,
                'messages': self.catalog, 'message_count': len(self.catalog)}

    def profile_path(self):
        if not self.dbc_key: raise ValueError('请先选择 DBC')
        directory = Path(os.environ['ZCAN_DATA_DIR']) / 'settings'
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f'wake-{self.dbc_key}.json'

    def wake_profile(self):
        path = self.profile_path()
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def save_wake_profile(self, profile):
        with self.lock:
            if not profile: raise ValueError('唤醒配置为空，未覆盖已保存的配置')
            if self.device and self.device.wake_active: raise ValueError('请先取消唤醒再修改配置')
            if not self.dbc_path: raise ValueError('请先选择 DBC')
            if hashlib.sha256(self.dbc_path.read_bytes()).hexdigest() != self.dbc_key:
                raise ValueError('DBC 文件已改变，请重新加载')
            with DBC(str(self.dbc_path)) as db:
                for name, selected in profile.items():
                    encode_wake_message(db.get_message_by_name(name), selected)
            path = self.profile_path()
            current = path.read_text(encoding='utf-8') if path.is_file() else None
            if current is not None:
                try:
                    previous = json.loads(current)
                except ValueError:
                    previous = None
                if previous == profile:
                    return profile
                if isinstance(previous, dict) and previous:
                    self._write_profile_file(path.with_suffix('.bak.json'), current)
            self._write_profile_file(path, json.dumps(profile, ensure_ascii=False, allow_nan=False))
            return profile

    @staticmethod
    def _write_profile_file(path, contents):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(contents, encoding='utf-8')
        temporary.replace(path)

    def start_wake(self, channel, profile=None, *, update=False):
        with self.lock:
            d = self.require()
            if hashlib.sha256(self.dbc_path.read_bytes()).hexdigest() != self.dbc_key:
                raise ValueError('DBC 文件已改变，请关闭设备并重新加载')
            if profile is None:
                profile = self.wake_profile()
            for name in profile:
                self._check_message_owner(d, (channel, name))
            d.start_wake(channel, profile, background=True, update=update)

    def stop_wake(self):
        with self.lock:
            self.require().stop_wake()

    def _receive(self):
        next_health_check = time.monotonic() + 1.0
        while not self.stop.wait(.05):
            device = self.device
            if not device:
                continue
            try:
                if time.monotonic() >= next_health_check:
                    next_health_check = time.monotonic() + 1.0
                    if not device.is_online():
                        raise ConnectionError('CAN 设备已断开。')
                frames = device.get_receive_data()
                with self.lock:
                    if self.device is device:
                        for frame in frames:
                            self.seq += 1
                            self.frames.append({'sequence': self.seq, **frame})
            except Exception as exc:
                with self.lock:
                    if self.device is device:
                        self.stop.set()
                        self.error = str(exc)
                        try:
                            self._close_device(device)
                        except Exception as close_exc:
                            self.error = f'{exc}；{close_exc}'
                return

    def _close_device(self, device):
        self._ui_owners.clear()
        self._manual_inflight.clear()
        try:
            device.close()
        except Exception as exc:
            self._pending_close = device
            self.error = f'CAN 资源释放待确认；可重试关闭：{exc}'
            raise RuntimeError(self.error) from exc
        else:
            self._pending_close = None
        finally:
            self.device = None

    def close(self):
        with self._lifecycle_lock:
            with self.lock:
                self.stop.set()
                worker = self.worker
            if worker:
                worker.join()
            with self.lock:
                self.worker = None
                device = self.device or self._pending_close
                if device is not None:
                    self._close_device(device)
                    self.error = None

    def require(self):
        if self.device is None or self.stop.is_set(): raise ValueError('请先打开设备')
        return self.device

    def signal_series(self, message, signal, channel, after, session):
        with self.lock:
            device = self.require()
            if channel not in device.channels:
                raise ValueError('所选通道未连接')
            definition = device.db.get_message_by_name(message)
            definition.get_signal_by_name(signal)
            current_session = self.receive_session
            latest = self.seq
            if session != current_session:
                after = 0
            candidates = [
                frame for frame in reversed(self.frames)
                if frame['sequence'] > after
                and frame['channel'] == channel
                and frame['arbitration_id'] == definition.frame_id
                and frame['is_extended_id'] == definition.is_extended_frame
                and frame['is_rx']
                and not frame['is_error_frame']
                and not frame['is_remote_frame']
            ]

        points = []
        for frame in candidates:
            try:
                decoded = definition.decode(bytes(frame['data']), decode_choices=False)
            except DbcError:
                continue
            value = decoded.get(signal)
            if isinstance(value, (float, int)) and isfinite(value):
                points.append({'timestamp': frame['timestamp'], 'value': value})
                if len(points) == 600:
                    break
        return {'session': current_session, 'latest': latest, 'points': points[::-1]}

    def status(self):
        d = self.device
        if not d: return {'open': False, 'error': self.error, 'tasks': [], 'wake_active': False, 'dbc_path': str(self.dbc_path or '')}
        with d._state_lock:
            tasks = [{'channel': ch, 'name': name, 'signals': task['data'], 'active': task['func'].is_alive(), 'wake': bool(task.get('wake')), 'error': getattr(task['func'], 'error', None)} for (ch, name), task in d.thread_messages.items()]
            wake_active = any(task['wake'] and task['active'] for task in tasks)
        return {'open': True, 'channels': list(d.channels), 'recording': d.custom_log is not None, 'tasks': tasks, 'error': self.error or d.receive_error, 'hardware_periodic': d.hardware_periodic, 'wake_active': wake_active, 'dbc_path': str(self.dbc_path or '')}

    def _ui_message(self, device, options):
        if options['dbcKey'] != self.dbc_key:
            raise ValueError('当前 DBC 与步骤配置不同，请在总线页面打开对应 DBC')
        channel, name = options['channel'], options['message']
        if channel not in device.channels:
            raise ValueError(f'请先在总线页面打开通道 {channel}')
        try:
            return device.db.get_message_by_name(name)
        except KeyError as exc:
            raise ValueError(f'当前 DBC 中没有报文 {name}') from exc

    def _check_message_owner(self, device, key, owner=None, *, ui=False):
        current = self._ui_owners.get(key)
        if current is not None and current is not owner:
            raise ValueError(f'通道 {key[0]} 的 {key[1]} 正由其他 UI 用例发送')
        task = device.thread_messages.get(key)
        if ui and current is None and (key in self._manual_inflight or (task and task['func'].is_alive())):
            raise ValueError(f'通道 {key[0]} 的 {key[1]} 正由手动操作发送')

    @contextmanager
    def _message_access(self, key, owner=None):
        with self.lock:
            device = self.require()
            with device._state_lock:
                self._check_message_owner(device, key, owner, ui=owner is not None)
                yield device

    def prepare_ui_run(self, steps):
        with self.lock:
            if not self.dbc_key:
                raise ValueError('请先在总线页面打开 DBC')
            if self.device is None or self.stop.is_set():
                raise ValueError('请先在总线页面打开设备和通道')
            device = self.device
            with device._state_lock:
                for _, options in steps:
                    message = self._ui_message(device, options)
                    self._check_message_owner(device, (options['channel'], message.name), ui=True)
                    for name, value in options.get('signals', {}).items():
                        try:
                            signal = message.get_signal_by_name(name)
                        except KeyError as exc:
                            raise ValueError(f'{message.name} 没有信号 {name}') from exc
                        resolve_signal_value(signal, value, message.name)
            return self.receive_session

    def execute_ui_step(self, kind, options, session, owned):
        with self.lock:
            device = self.device
            if device is None or self.stop.is_set() or session != self.receive_session:
                raise ValueError('总线连接已改变，请重新执行用例')
            with device._state_lock:
                message = self._ui_message(device, options)
                key = (options['channel'], message.name)
                if kind == 'can_stop':
                    self._check_message_owner(device, key, owned, ui=True)
                    if self._ui_owners.get(key) is not owned:
                        return f'通道 {key[0]} 的 {message.name} 无本次循环任务'
                    self.break_msg_by_name(*key, _owner=owned)
                    self._ui_owners.pop(key, None)
                    owned.pop(key, None)
                    return f'已停止通道 {key[0]} 的 {message.name}'
                looping = kind == 'can_send_loop'
                period = options['cycleMs'] if looping else None
                period = period or message.cycle_time or 100
                self.send_msg_by_name(*key, options['signals'], can_fd=message.is_fd,
                                      cycle_time=period, send_count=-1 if looping else 1, _owner=owned)
                if looping:
                    self._ui_owners[key] = owned
                    owned[key] = device.thread_messages[key]
                    return f'已启动循环发送 · 通道 {key[0]} · {message.name} · {period} ms'
                self._ui_owners.pop(key, None)
                owned.pop(key, None)
                return f'已发送 1 帧 · 通道 {key[0]} · {message.name}'

    def stop_ui_sends(self, session, owned):
        errors = []
        with self.lock:
            device = self.device
            if device is None or session != self.receive_session:
                return errors
            with device._state_lock:
                for key, task in list(owned.items()):
                    if self._ui_owners.get(key) is not owned:
                        continue
                    if device.thread_messages.get(key) is task:
                        try:
                            self.break_msg_by_name(*key, _owner=owned)
                        except (RuntimeError, OSError, ValueError) as exc:
                            errors.append(f'通道 {key[0]} 的 {key[1]}：{exc}')
                            continue
                    self._ui_owners.pop(key, None)
                    owned.pop(key, None)
        return errors

    def send_msg_by_name(self, channel, name, signals, can_fd=True, fd_brs=False, cycle_time=100, send_count=-1, *, _owner=None):
        with self._message_access((channel, name), _owner) as d:
            message = d.db.get_message_by_name(name)
            values = {key: resolve_signal_value(message.get_signal_by_name(key), value, name) for key, value in signals.items()}
            existing = d.thread_messages.get((channel, name))
            merged = {signal.name: 0 for signal in message.signals}
            if existing:
                merged.update(existing["data"])
            merged.update(values)
            raw = {signal.name: signal.scaled_to_raw(merged[signal.name]) for signal in message.signals}
            active = message.gather_signals(raw)
            if inactive := values.keys() - active.keys():
                raise ValueError(f"复用选择值未启用这些信号：{', '.join(sorted(inactive))}")
            d.single_send(channel, name, values, can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time, send_count=send_count)
        return 1
    def send_msg_by_id(self, channel, id, signals, **kwargs):
        return self.send_msg_by_name(channel, self.require().db.get_message_by_id(id).name, signals, **kwargs)
    def break_msg_by_name(self, channel, name, *, _owner=None):
        with self._message_access((channel, name), _owner) as device:
            device.single_send(channel, name, state=False)
        return 1
    def break_msg_by_id(self, channel, id):
        return self.break_msg_by_name(channel, self.require().db.get_message_by_id(id).name)

    def trigger_message(self, channel, name, signals, *, can_fd, fd_brs, cycle_time, stop_after):
        key = (channel, name)
        with self._message_access(key) as device:
            if key in self._manual_inflight:
                raise ValueError(f'通道 {channel} 的 {name} 正在触发发送')
            message = device.db.get_message_by_name(name)
            values = {signal: resolve_signal_value(message.get_signal_by_name(signal), value, name)
                      for signal, value in signals.items()}
            self._manual_inflight[key] = device
        try:
            return device.send_timed_trigger(channel, name, values, can_fd=can_fd, fd_brs=fd_brs,
                                             cycle_time=cycle_time, stop_after=stop_after)
        finally:
            with self.lock:
                if self._manual_inflight.get(key) is device:
                    self._manual_inflight.pop(key)

    def pulse_message(self, channel, name, signals, count, *, can_fd):
        with self._message_access((channel, name)) as device:
            device.start_pulse(channel, name, signals, count, can_fd=can_fd)

    def send_once_msg_by_name(self, channel, name, single_name, single_value, isBreak=True, can_fd=True, fd_brs=False, cycle_time=100):
        return int(self.trigger_message(channel, name, {single_name: single_value}, can_fd=can_fd,
                                        fd_brs=fd_brs, cycle_time=cycle_time, stop_after=isBreak))
    def send_once_msg_by_name_two_singal(self, channel, name, single_name, single_value, single_name1, single_value1, isBreak=True, can_fd=True, fd_brs=False, cycle_time=100):
        return int(self.trigger_message(channel, name, {single_name: single_value, single_name1: single_value1},
                                        can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time, stop_after=isBreak))

router=APIRouter(prefix='/api/can')
class Open(BaseModel):
    backend: str = 'simulation'
    dbc: str = ''
    device_id: str | None = None
    channels: list[int] = Field(default_factory=lambda:[0])
class Send(BaseModel):
    channel: int = 0
    name: str
    signals: dict = Field(default_factory=dict)
    can_fd: bool = True
    fd_brs: bool = False
    cycle_time: int = 100
    send_count: int = -1
class Trigger(Send):
    stop_after: bool = True
class Pulse(Send):
    count: int = 1
class Record(BaseModel):
    name: str = ''
def service(request: Request): return request.app.state.can

def call(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except (ValueError, RuntimeError, OSError, KeyError, DbcError, configparser.Error, UnicodeError) as e: raise HTTPException(400, str(e)) from e
@router.get('/devices')
def devices():
    from can_adapters.device_manager import CanDeviceManager
    return CanDeviceManager().discover()
@router.get('/status')
def status(request: Request): return service(request).status()
@router.post('/open')
def open_device(p: Open, request: Request):
    call(service(request).open,p.backend,p.dbc,p.channels,p.device_id); return service(request).status()
@router.post('/close')
def close_device(request: Request):
    call(service(request).close); return service(request).status()
@router.post('/send')
def send(p: Send, request: Request):
    call(service(request).send_msg_by_name,**p.model_dump()); return service(request).status()
@router.post('/trigger')
def trigger(p: Trigger, request: Request):
    if not p.signals: raise HTTPException(400, "请至少指定一个信号")
    result=call(service(request).trigger_message,p.channel,p.name,p.signals,can_fd=p.can_fd,fd_brs=p.fd_brs,cycle_time=p.cycle_time,stop_after=p.stop_after)
    return {'completed': result}
@router.post('/pulse')
def pulse(p: Pulse, request: Request):
    call(service(request).pulse_message,p.channel,p.name,p.signals,p.count,can_fd=p.can_fd)
    return service(request).status()
@router.post('/stop')
def stop_send(p: Send, request: Request):
    call(service(request).break_msg_by_name,p.channel,p.name); return service(request).status()
@router.get('/frames')
def frames(request: Request, after: int = 0):
    s=service(request)
    with s.lock: return {'frames':[x for x in s.frames if x['sequence']>after], 'latest':s.seq}

@router.get('/signal-series')
def signal_series(request: Request, message: str, signal: str, channel: int = 0,
                  after: int = Query(default=0, ge=0), session: str = ''):
    return call(service(request).signal_series, message, signal, channel, after, session)
@router.post('/record/start')
def record_start(p: Record, request: Request):
    if p.name and Path(p.name).name != p.name: raise HTTPException(400,'文件名无效')
    call(call(service(request).require).blf_start,p.name); return service(request).status()
@router.post('/record/stop')
def record_stop(request: Request):
    call(call(service(request).require).blf_stop); return service(request).status()


class DbcLoad(BaseModel):
    path: str
class WakeProfile(BaseModel):
    profile: dict[str, dict]
class WakeStart(BaseModel):
    channel: int = 0
    profile: dict[str, dict] | None = None
class WakeUpdate(BaseModel):
    channel: int = 0
    profile: dict[str, dict]

@router.get('/files')
def browse_files(path: str = ''):
    def browse():
        directory = Path(path).expanduser().resolve() if path else ROOT.parent
        if not directory.is_dir(): raise ValueError('文件夹不存在')
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        return {'path': str(directory), 'parent': str(directory.parent),
                'entries': [{'name': p.name, 'path': str(p), 'directory': p.is_dir()} for p in entries
                            if not p.name.startswith('.') and (p.is_dir() or p.suffix.lower() == '.dbc')]}
    return call(browse)

@router.post('/dbc/load')
def load_dbc(p: DbcLoad, request: Request):
    return call(service(request).load_dbc, p.path)
@router.get('/dbc')
def dbc_info(request: Request):
    return service(request).dbc_info()
@router.get('/wake/profile')
def wake_profile(request: Request):
    return call(service(request).wake_profile)
@router.post('/wake/profile')
def save_profile(p: WakeProfile, request: Request):
    return call(service(request).save_wake_profile, p.profile)
@router.post('/wake/start')
def start_wake(p: WakeStart, request: Request):
    call(service(request).start_wake, p.channel, p.profile)
    return service(request).status()
@router.post('/wake/update')
def update_wake(p: WakeUpdate, request: Request):
    call(service(request).start_wake, p.channel, p.profile, update=True)
    return service(request).status()
@router.post('/wake/stop')
def stop_wake(request: Request):
    call(service(request).stop_wake)
    return service(request).status()


@router.post('/wake/import')
def import_wake_profile(request: Request, file: UploadFile = File(...)):
    def load():
        s = service(request)
        if not s.dbc_key: raise ValueError('请先选择 DBC')
        text = file.file.read(4 * 1024 * 1024).decode('utf-8-sig')
        if (file.filename or '').lower().endswith('.ini'):
            settings = configparser.ConfigParser(interpolation=None)
            settings.read_string(text)
            if not settings.has_option('dbc', s.dbc_key): raise ValueError('该配置中没有当前 DBC 的唤醒配置')
            profile = json.loads(settings['dbc'][s.dbc_key])
            if isinstance(profile, str): profile = json.loads(profile)
        else:
            profile = json.loads(text)
        if not isinstance(profile, dict) or any(not isinstance(v, dict) for v in profile.values()):
            raise ValueError('唤醒配置格式无效')
        return s.save_wake_profile(profile)
    return call(load)
