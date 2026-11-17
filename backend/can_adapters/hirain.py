"""TestBaseVCI adapter using the separately installed vendor python-can plugin.

The existing application owns periodic scheduling; the vendor native periodic API
is deliberately not used until its execution location and cleanup are verified.
"""
from copy import copy
import ctypes
from importlib.metadata import PackageNotFoundError, version
import platform

from zlg.backend import validate_channel


def _sdk():
    if platform.system() not in ('Windows', 'Linux'):
        raise RuntimeError('恒润 TestBaseVCI 适配器仅提供 Windows/Linux 支持；当前系统不可用。')
    try:
        from can_hirain import hrcan
        return hrcan
    except (ImportError, OSError) as exc:
        raise RuntimeError(f'无法加载恒润 SDK：请安装 python-can-hirain==0.3.0 和 TestBaseVCI 驱动。{exc}') from exc


def _driver_channels(sdk):
    """Unlike the vendor convenience wrapper, preserve enumeration failures."""
    config = sdk.BasicStructure.H3DriverConfig()
    result = sdk.dll.H3GetDriverConfig(ctypes.byref(config), ctypes.c_char_p())
    if result != 0:
        raise RuntimeError(f'恒润驱动枚举失败，错误码：{result}。')
    if config.nChannelCount > len(config.h3Channels):
        raise RuntimeError('恒润驱动返回的通道数量超出结构容量。')
    result = []
    for channel in config.h3Channels[:config.nChannelCount]:
        result.append({'bus_capabilities': channel.ucBusCapabilities,
                       'hw_name': channel.szHwName.decode('utf-8', errors='replace').rstrip('\0'),
                       'channel_index': channel.ucChannelIndex,
                       'hw_channel_no': channel.ucHwChannelNo})
    return result


def discover():
    sdk = _sdk()
    groups = {}
    for entry in _driver_channels(sdk):
        if entry['bus_capabilities'] != 3:
            continue
        name = entry['hw_name']
        if not name:
            continue  # selecting a device without a stable SDK identifier is unsafe
        group = groups.setdefault(name, [])
        if any(ch['hw_channel_no'] == entry['hw_channel_no'] for ch in group):
            raise RuntimeError(f'恒润设备标识 {name!r} 重复，无法安全区分设备。')
        group.append(entry)
    try:
        sdk_version = version('python-can-hirain')
    except PackageNotFoundError:
        sdk_version = None
    return [dict(vendor='hirain', model=None, device_id=name, connected=True,
                 channels=[{'number': i, 'sdk_channel_index': ch['channel_index'],
                            'hardware_channel': ch['hw_channel_no']} for i, ch in enumerate(channels)],
                 capabilities={'can': True, 'can_fd': True, 'periodic': 'UNVERIFIED',
                               'active_scheduler': 'SOFTWARE_SCHEDULED'}, sdk_version=sdk_version)
            for name, channels in groups.items()]


class HirainCanBackend:
    supports_diagnostics = False
    supports_hardware_periodic = False
    continuous_rx = True
    receive_interval = .01

    def __init__(self, device_id=None, *, bitrate=500000, data_bitrate=2000000, fd=True, resistance=True):
        self._sdk = _sdk()
        devices = discover()
        if device_id is None:
            if len(devices) != 1:
                raise RuntimeError(f'请选择恒润设备：当前发现 {len(devices)} 台。')
            device_id = devices[0]['device_id']
        matches = [d for d in devices if d['device_id'] == device_id]
        if len(matches) != 1:
            raise RuntimeError('所选恒润设备不存在或标识不唯一，请刷新。')
        self.device_id = device_id
        self._available = len(matches[0]['channels'])
        self._buses = {}
        self._closed = False
        self._config = {'resistance': int(resistance), 'listenFlag': 0}
        bs = self._sdk.BasicStructure
        if fd:
            self._config.update(mode=bs.TestBaseVCI_ChlMode.CANFD, abrBaudrate=bitrate,
                                dbrBaudrate=data_bitrate, abrSamplePoint=bs.TestBaseVCI_SAMPLEPOINT.SAMPLE_80,
                                dbrSamplePoint=bs.TestBaseVCI_SAMPLEPOINT.SAMPLE_80)
        else:
            self._config.update(mode=bs.TestBaseVCI_ChlMode.CAN, baudrate=bitrate,
                                samplePoint=bs.TestBaseVCI_SAMPLEPOINT.SAMPLE_75)

    @property
    def channels(self):
        return tuple(sorted(self._buses))

    def open_channel(self, channel):
        validate_channel(channel)
        if self._closed or channel >= self._available:
            raise RuntimeError('设备已关闭或通道不可用。')
        if channel in self._buses:
            return
        try:
            bus = self._sdk.HRCANBus(channel=channel, hwserial=self.device_id, configs=[self._config.copy()])
            if not hasattr(bus, 'port_handle') or not bus.port_handle:
                raise RuntimeError('恒润通道未能初始化。')
            self._buses[channel] = bus
        except Exception as exc:
            raise RuntimeError(f'恒润通道 {channel} 初始化失败：{exc}') from exc

    def close_channel(self, channel):
        bus = self._buses.pop(channel, None)
        if bus:
            try:
                bus.shutdown()
            except Exception as exc:
                raise RuntimeError(f'恒润通道关闭失败：{exc}') from exc

    def send(self, message):
        if message.channel not in self._buses:
            raise RuntimeError('恒润通道未打开。')
        if message.is_remote_frame or message.is_error_frame:
            raise RuntimeError('恒润适配器暂不支持发送远程帧或错误帧。')
        try:
            self._buses[message.channel].send(message)
        except Exception as exc:
            raise RuntimeError(f'恒润发送失败：{exc}') from exc

    def poll(self, limit):
        frames = []
        for channel, bus in self._buses.items():
            while len(frames) < limit:
                try:
                    frame = bus.recv(timeout=0)
                except Exception as exc:
                    raise RuntimeError(f'恒润接收失败：{exc}') from exc
                if frame is None:
                    break
                frame = copy(frame)
                frame.channel = channel
                frames.append(frame)
        return frames

    def clear(self, channel):
        if channel not in self._buses:
            raise RuntimeError('恒润通道未打开。')
        # Drain a bounded number; do not loop forever on a busy bus.
        for _ in range(4096):
            if self._buses[channel].recv(timeout=0) is None:
                break

    def is_online(self):
        if self._closed:
            return False
        return any(d['device_id'] == self.device_id for d in discover())

    def close(self):
        errors = []
        for channel in list(self._buses):
            try:
                self.close_channel(channel)
            except RuntimeError as exc:
                errors.append(str(exc))
        self._closed = True
        if errors:
            raise RuntimeError('; '.join(errors))
