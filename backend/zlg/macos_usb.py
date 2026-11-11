"""Native USB backend for the verified USBCANFD-200U hardware profile."""

from array import array
from collections import deque
import platform
from struct import Struct, pack
import time

from can import Message
from zlg.backend import validate_channel
from zlg.logutil import Logs
from zlg.paths import resource_path
from zlg.ucfd_identity import COMMAND, crc16, encode_packet, exchange_command, query_identity, query_version

PROFILE = (0x0119, "USBCANFD-200U", 0x0300, 0x0235, 2)
FRAME = Struct("<IIIHBB")
FD_LENGTHS = frozenset((*range(9), 12, 16, 20, 24, 32, 48, 64))
RX_CAPACITY = 16_384
MIN_TX_INTERVAL = 0.05
PERIODIC_SLOTS = 100
MAX_PERIOD_MS = 0xffffffff // 10
# The USB frame info has separate bits for requesting an echo and marking a returned TX frame.
TX_ECHO_REQUEST = 0x4000
TX_ECHO_FRAME = 0x2000

# 80 MHz controller. 500 kbit/s: 160 quanta at BRP 1.
# 2 Mbit/s: 20 quanta at BRP 2. Both use an 80% sample point.
CHANNEL_CONFIG = pack("<IIBBBBHBBBBH", 80_000_000, 0, 126, 31, 0, 0, 0, 14, 3, 0, 0, 1)


def _tx_packet(message):
    flags = (1 | TX_ECHO_REQUEST | (int(message.is_fd) << 4) | (int(message.is_remote_frame) << 8)
             | (int(message.is_extended_id) << 9) | (int(message.bitrate_switch) << 11))
    wire_id = message.arbitration_id | (int(message.is_extended_id) << 31)
    wire_id |= int(message.is_remote_frame) << 30
    record = FRAME.pack(0, wire_id, flags, 0, message.channel, message.dlc)
    return encode_packet(0xf2, message.channel, 0, b"\x01\x00\x00\x00" + record
                         + bytes(message.data).ljust(64, b"\x00"))


def _periodic_payload(message, slot, period_ms, enabled):
    # The vendor USB driver uses a 100 us period, a zero start delay,
    # a slot byte, an enable byte, and an 80-byte CAN/CAN FD record.
    flags = (int(message.is_fd) << 4) | (int(message.is_remote_frame) << 8)
    flags |= (int(message.is_extended_id) << 9) | (int(message.bitrate_switch) << 11)
    if enabled:
        flags |= TX_ECHO_REQUEST
    record = FRAME.pack(0, message.arbitration_id, flags, 0, message.channel, message.dlc)
    return pack("<IHBB", period_ms * 10, 0, slot, int(enabled)) + record + bytes(message.data).ljust(64, b"\x00")


class _RxDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.epoch = None

    def feed(self, chunk):
        received_at = time.time() if self.epoch is None else None
        self.buffer.extend(chunk)
        result = []
        while len(self.buffer) >= COMMAND.size:
            marker, kind, _, _, length = COMMAND.unpack_from(self.buffer)
            if marker != 0x55 or kind not in (0xf1, 0xf2, 0xf3, 0xf4):
                self.buffer.clear()
                raise RuntimeError("USB 接收包头不匹配。")
            total = COMMAND.size + length + 2
            if length < 4 or total > RX_CAPACITY:
                self.buffer.clear()
                raise RuntimeError("USB 接收包长度不合法。")
            if len(self.buffer) < total:
                break
            packet = bytes(self.buffer[:total])
            del self.buffer[:total]
            if packet[-2:] != crc16(packet[:-2]):
                raise RuntimeError("USB 接收包校验失败。")
            if kind in (0xf1, 0xf2, 0xf3):
                result.extend(self._decode_records(kind, packet[COMMAND.size:-2]))
        if result:
            if received_at is not None:
                # Only the newest frame can have arrived near the USB read time.
                self.epoch = received_at - max(frame.timestamp for frame in result)
            for frame in result:
                frame.timestamp += self.epoch
        return result

    def _decode_records(self, kind, body):
        slot = FRAME.size + (64 if kind == 0xf2 else 8)
        count = int.from_bytes(body[:2], "little")
        if len(body) != 4 + count * slot:
            raise RuntimeError("USB 接收帧数与包长度不匹配。")
        for offset in range(4, len(body), slot):
            ticks, wire_id, flags, high_ticks, channel, size = FRAME.unpack_from(body, offset)
            is_fd = bool(flags & 0xf0)
            is_remote = bool(flags & 0x100)
            is_extended = bool(flags & 0x200)
            is_error = kind == 0xf3 or bool(flags & 0x400)
            if channel not in (0, 1) or size > slot - FRAME.size:
                raise RuntimeError("USB 接收帧通道或长度不合法。")
            if (is_fd and (size not in FD_LENGTHS or is_remote)) or (not is_fd and size > 8):
                raise RuntimeError("USB 接收帧类型与长度不匹配。")
            wrap = (flags >> 16) & 0xf
            if flags & (1 << 20):
                micros = ticks | (high_ticks << 32) | (wrap << 48)
            else:
                micros = (ticks | (wrap << 32)) * 100
            start = offset + FRAME.size
            yield Message(
                timestamp=micros / 1_000_000, channel=channel,
                arbitration_id=wire_id & (0x1fffffff if is_extended else 0x7ff),
                is_extended_id=is_extended, is_remote_frame=is_remote, is_error_frame=is_error,
                is_fd=is_fd, bitrate_switch=is_fd and bool(flags & 0x800),
                error_state_indicator=is_fd and bool(flags & 0x1000),
                is_rx=not bool(flags & TX_ECHO_FRAME), dlc=size,
                data=b"" if is_remote else body[start:start + size],
            )


class MacUsbCanBackend:
    supports_diagnostics = False
    supports_hardware_periodic = True
    periodic_slots = PERIODIC_SLOTS
    max_period_ms = MAX_PERIOD_MS
    continuous_rx = True
    receive_interval = 0.005

    def __init__(self):
        if platform.system() != "Darwin":
            raise RuntimeError("mac-usb 后端需要 macOS。")
        try:
            from usb import core
            from usb.backend import libusb1
        except ImportError as exc:
            raise RuntimeError("请先安装 requirements-macos-usb.txt。") from exc

        self._usb_error = core.USBError
        bundled = resource_path("libusb-1.0.0.dylib")
        self._usb = libusb1.get_backend(find_library=lambda _: str(bundled)) if bundled.is_file() else libusb1.get_backend()
        if self._usb is None:
            raise RuntimeError("找不到 ARM64 libusb。")
        self._channels = set()
        self._periodic_channels = set()
        self._pending = deque()
        self._decoder = _RxDecoder()
        self._handle = None
        self._claimed = False
        self._opened = False
        self._last_tx = float("-inf")
        self._log = Logs()
        try:
            self._open(core.Device)
        except (OSError, ValueError, RuntimeError) as exc:
            self._log.error(f"Mac USB 打开失败：{exc}")
            self._log.close()
            raise
        self._log.info("Mac USB 设备已打开。")

    def _open(self, device_type):
        matches = []
        for reference in self._usb.enumerate_devices():
            descriptor = self._usb.get_device_descriptor(reference)
            if (descriptor.idVendor, descriptor.idProduct) == (0x3068, 0x0009):
                matches.append(reference)
        if len(matches) != 1:
            raise RuntimeError(f"mac-usb 需要连接一台 3068:0009 设备，当前找到 {len(matches)} 台。")

        reference = matches[0]
        device = device_type(reference, self._usb)
        try:
            config = device[0]
            interface = config[(0, 0)]
        except (IndexError, KeyError) as exc:
            raise RuntimeError("设备未提供配置 1 的 USB 接口 0。") from exc
        endpoints = {endpoint.bEndpointAddress: endpoint.bmAttributes & 3 for endpoint in interface}
        if config.bConfigurationValue != 1 or interface.bInterfaceClass != 0xff or endpoints != {
            0x01: 2, 0x02: 2, 0x81: 2, 0x82: 2, 0x83: 2,
        }:
            raise RuntimeError("USB 描述符与已验证的 200U 不匹配。")

        self._handle = self._usb.open_device(reference)
        try:
            active = self._usb.get_configuration(self._handle)
            if active == 0:
                self._usb.set_configuration(self._handle, 1)
                active = self._usb.get_configuration(self._handle)
            if active != 1:
                raise RuntimeError("无法确认 USB 配置 1。")
            self._usb.claim_interface(self._handle, 0)
            self._claimed = True
            version = query_version(self._usb, self._handle)
            name, hw, fw, _, can_ports, _ = query_identity(self._usb, self._handle)
            if (version, name, hw, fw, can_ports) != PROFILE:
                raise RuntimeError(f"未验证的设备版本：{name!r}, USB 协议 {version:#06x}, "
                                   f"硬件 {hw:#06x}, 固件 {fw:#06x}, CAN 通道 {can_ports}。")
            self._command(0x01)
            self._opened = True
        except BaseException:
            self._release()
            raise

    def _command(self, opcode, channel=0, payload=b""):
        try:
            return exchange_command(self._usb, self._handle, opcode, channel, payload)
        except (OSError, RuntimeError) as exc:
            self._log.error(f"Mac USB 命令 0x{opcode:02x} 通道 {channel} 失败：{exc}")
            raise

    @property
    def channels(self):
        return tuple(sorted(self._channels))

    def open_channel(self, channel):
        validate_channel(channel)
        if not self._opened:
            raise RuntimeError("Mac USB 设备已关闭。")
        if channel in self._channels:
            return
        self._command(0x11, channel, CHANNEL_CONFIG)
        try:
            self._command(0x44, payload=pack("<I", 20))
            # Use an external termination plan on the bench.
            self._command(0x18, channel, b"\xaa")
            if not self._channels:
                self._discard_stale_rx()
            self._command(0x12, channel)
        except (OSError, RuntimeError):
            try:
                self._command(0x13, channel)
            except (OSError, RuntimeError):
                pass
            raise
        self._channels.add(channel)
        self._log.info(f"Mac USB 通道 {channel} 已启动（500 kbit/s / 2 Mbit/s）。")

    def close_channel(self, channel):
        validate_channel(channel)
        if channel in self._channels:
            if channel in self._periodic_channels:
                try:
                    self._command(0x17, channel, b"\xaa")
                except (OSError, RuntimeError):
                    # Still stop the CAN channel when clearing its slots fails.
                    self._log.warning(f"无法清除通道 {channel} 的定时任务，尝试停止通道。")
            self._command(0x13, channel)
            self._periodic_channels.discard(channel)
            self._channels.remove(channel)
            self.clear(channel)
            if not self._channels:
                self._decoder = _RxDecoder()
            self._log.info(f"Mac USB 通道 {channel} 已停止。")

    def set_periodic(self, message, slot, period_ms, *, enabled):
        channel = message.channel
        if channel not in self._channels:
            raise RuntimeError(f"通道 {channel} 未打开。")
        if not 0 <= slot < PERIODIC_SLOTS or not 1 <= period_ms <= MAX_PERIOD_MS:
            raise ValueError("硬件定时发送的任务编号或周期超出范围。")
        first = channel not in self._periodic_channels
        if first:
            # Track the channel even if the command reply is lost. Closing
            # must still attempt to clear any slots installed on the device.
            self._periodic_channels.add(channel)
            self._command(0x17, channel, b"\xaa")
        self._command(0x16, channel, _periodic_payload(message, slot, period_ms, enabled))
        if first and enabled:
            self._command(0x17, channel, b"\x55")

    def send(self, message):
        if message.channel not in self._channels:
            raise RuntimeError(f"通道 {message.channel} 未打开。")
        if message.is_fd and message.dlc not in FD_LENGTHS:
            raise ValueError("CAN FD 帧数据长度无对应 DLC。")
        now = time.monotonic()
        # The common device layer serializes USB I/O, including these waits.
        delay = MIN_TX_INTERVAL - (now - self._last_tx)
        if delay > 0:
            time.sleep(delay)
        packet = array("B", _tx_packet(message))
        self._last_tx = time.monotonic()
        try:
            self._exchange_tx(packet, message.channel)
        except (OSError, RuntimeError) as exc:
            self._log.error(f"Mac USB 通道 {message.channel} 发送失败：{exc}")
            raise

    def _exchange_tx(self, packet, channel):
        try:
            written = self._usb.bulk_write(self._handle, 0x02, 0, packet, 500)
        except OSError as exc:
            raise RuntimeError(f"USB 发送端点 0x02 写入失败：{exc}") from exc
        if written != len(packet):
            raise RuntimeError(f"USB 发送端点 0x02 写入不完整：{written}/{len(packet)} 字节。")

        reply = array("B", bytes(8))
        try:
            received = self._usb.bulk_read(self._handle, 0x82, 0, reply, 500)
        except OSError as exc:
            raise RuntimeError(f"USB 应答端点 0x82 读取失败：{exc}") from exc
        if received != len(reply):
            raise RuntimeError(f"USB 发送应答长度异常：{received}/{len(reply)} 字节。")
        header = bytes(reply[:6])
        if bytes(reply[6:]) != crc16(header):
            raise RuntimeError("USB 发送应答 CRC 校验失败。")
        marker, opcode, extension, sequence, status, sent = header
        if (marker, opcode, extension, sequence) != (0x55, 0xf2, channel, 0):
            raise RuntimeError(f"USB 发送应答不匹配：标记 {marker:#04x}，命令 {opcode:#04x}，"
                               f"通道 {extension}，序号 {sequence}。")
        if status or sent != 1:
            raise RuntimeError(f"设备未确认本次 CAN 发送：状态 {status:#04x}，成功计数 {sent}。")

    def _read_rx(self):
        buffer = array("B", bytes(RX_CAPACITY))
        try:
            size = self._usb.bulk_read(self._handle, 0x83, 0, buffer, 5)
        except self._usb_error as exc:
            if exc.backend_error_code != -7:
                raise
            return b""
        return bytes(buffer[:size])

    def _discard_stale_rx(self):
        # The USB endpoint can retain data from the last session. Drain it
        # while all channels are stopped, before any frame can anchor the clock.
        discarded = 0
        for _ in range(32):
            chunk = self._read_rx()
            if not chunk:
                if discarded:
                    self._log.info(f"启动首个通道前已清除 {discarded} 字节 USB 接收缓存。")
                return
            discarded += len(chunk)
        raise RuntimeError("启动通道前无法清空 USB 接收缓存。")

    def poll(self, limit):
        if self._channels and len(self._pending) < limit:
            chunk = self._read_rx()
            if chunk:
                self._pending.extend(frame for frame in self._decoder.feed(chunk)
                                     if frame.channel in self._channels)
        return [self._pending.popleft() for _ in range(min(limit, len(self._pending)))]

    def clear(self, channel):
        self._pending = deque(frame for frame in self._pending if frame.channel != channel)

    def is_online(self):
        return self._opened

    def _release(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                if self._claimed:
                    self._usb.release_interface(handle, 0)
            finally:
                self._claimed = False
                self._usb.close_device(handle)

    def close(self):
        try:
            if self._opened:
                errors = []
                try:
                    for channel in self.channels:
                        try:
                            self.close_channel(channel)
                        except (OSError, RuntimeError) as exc:
                            errors.append(exc)
                finally:
                    self._command(0x02)
                if errors:
                    raise errors[0]
        finally:
            self._opened = False
            self._channels.clear()
            self._periodic_channels.clear()
            self._pending.clear()
            try:
                self._release()
            finally:
                self._log.close()
