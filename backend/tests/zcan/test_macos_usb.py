from collections import deque
import os
from pathlib import Path
from struct import pack, unpack_from
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, call, patch

from can import Message
from zlg.backend import LoopbackBackend
from zlg.macos_usb import CHANNEL_CONFIG, MacUsbCanBackend, _RxDecoder, _periodic_payload, _tx_packet
from zlg.paths import ROOT
from zlg.ucfd_identity import RESPONSE, crc16, encode_packet, exchange_command
from zlg.zlgcan import zlgCan


class MacUsbWireTests(unittest.TestCase):
    @staticmethod
    def periodic_backend():
        device = MacUsbCanBackend.__new__(MacUsbCanBackend)
        device._opened = True
        device._channels = set()
        device._periodic_channels = set()
        device._pending = deque()
        device._decoder = _RxDecoder()
        device._claimed = False
        device._handle = None
        device._command = Mock()
        device._read_rx = Mock(return_value=b"")
        device._log = Mock()
        device.continuous_rx = False
        device.send = Mock(side_effect=AssertionError("Hardware timing must not send from the host."))
        return device

    def test_hardware_periodic_classic_and_fd_payloads(self):
        classic = Message(channel=0, arbitration_id=0x230, is_extended_id=False, data=b"\xa5")
        fd = Message(channel=1, arbitration_id=0x12345, is_extended_id=True,
                     is_fd=True, bitrate_switch=True, data=bytes(range(12)), check=True)
        self.assertEqual(
            _periodic_payload(classic, 0, 10, True),
            bytes.fromhex("64 00 00 00 00 00 00 01 00 00 00 00 30 02 00 00 00 40 00 00 00 00 00 01 a5")
            + bytes(63),
        )
        self.assertEqual(
            _periodic_payload(fd, 99, 20, False),
            bytes.fromhex("c8 00 00 00 00 00 63 00 00 00 00 00 45 23 01 00 10 0a 00 00 00 00 01 0c")
            + bytes(range(12)) + bytes(52),
        )



    def test_hardware_slots_update_trigger_and_stop(self):
        backend = self.periodic_backend()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ZCAN_DATA_DIR": directory}), \
                patch("zlg.zlgcan.open_backend", return_value=backend):
            device = zlgCan(0, str(ROOT / "config" / "Example.dbc"), hardware_periodic=True)
            try:
                device.chn_open(0)
                backend._command.reset_mock()
                device.single_send(0, "TestButtons", {"HeldButton": 1}, cycle_time=50)
                device.single_send(0, "TestButtons", {"PressB": 1}, cycle_time=50)
                with patch("zlg.zlgcan.time") as clock:
                    self.assertTrue(device.send_timed_trigger(0, "TestButtons", {"PressA": 1}, can_fd=True, fd_brs=True, cycle_time=50, stop_after=True))
                self.assertEqual(clock.sleep.call_args_list, [call(.05), call(.05)])
                commands = [entry.args for entry in backend._command.call_args_list]
                slots = [(unpack_from("<IHBB", p), p[24]) for op, _, p in commands if op == 0x16]
                self.assertEqual(slots, [((500,0,0,1),4),((500,0,0,1),6),((500,0,0,1),7),((500,0,0,1),6),((500,0,0,0),6)])
                self.assertIn(call(0x17, 0, b"\x55"), backend._command.call_args_list)
            finally:
                device.close()

    def test_uncertain_hardware_reply_stops_channel_and_drops_tasks(self):
        backend = self.periodic_backend()
        dbc = ROOT / "config" / "Example.dbc"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ZCAN_DATA_DIR": directory}), \
                patch("zlg.zlgcan.open_backend", return_value=backend):
            device = zlgCan(0, str(dbc), hardware_periodic=True)
            self.addCleanup(device.close)
            device.chn_open(0)
            device.single_send(0, "TestClassicA", can_fd=False, cycle_time=10)
            def timeout_on_slot(opcode, _channel=0, _payload=b""):
                if opcode == 0x16:
                    raise OSError("lost slot reply")
            backend._command.side_effect = timeout_on_slot
            with self.assertRaisesRegex(RuntimeError, "已停止通道 0"):
                device.single_send(0, "TestClassicB", can_fd=False, cycle_time=50)
            self.assertEqual(backend._command.call_args_list[-2:], [call(0x17, 0, b"\xaa"), call(0x13, 0)])
            self.assertEqual(device.channels, ())
            self.assertFalse(device.thread_messages)
            device.close()


    def test_hardware_channel_requires_reset_if_stopping_cannot_be_confirmed(self):
        backend = self.periodic_backend()
        dbc = ROOT / "config" / "Example.dbc"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ZCAN_DATA_DIR": directory}), \
                patch("zlg.zlgcan.open_backend", return_value=backend):
            device = zlgCan(0, str(dbc), hardware_periodic=True)
            self.addCleanup(device.close)
            device.chn_open(0)
            device.single_send(0, "TestClassicA", can_fd=False, cycle_time=10)

            def timeout_on_slot_and_stop(opcode, _channel=0, _payload=b""):
                if opcode in (0x16, 0x13):
                    raise OSError("lost reply")
            backend._command.side_effect = timeout_on_slot_and_stop
            with self.assertRaisesRegex(RuntimeError, "无法确认停发"):
                device.single_send(0, "TestClassicA", state=False)
            self.assertIsNone(device.periodic_payload("TestClassicA"))
            self.assertTrue(device.thread_messages)
            backend._command.reset_mock(side_effect=True)
            with self.assertRaisesRegex(RuntimeError, "定时状态未知"):
                device.single_send(0, "TestClassicB", can_fd=False, cycle_time=50)
            backend._command.assert_not_called()
            device.reset(0)
            self.assertFalse(device.thread_messages)
            device.chn_open(0)
            device.single_send(0, "TestClassicB", can_fd=False, cycle_time=50)
            self.assertIsNotNone(device.periodic_payload("TestClassicB"))
            device.close()

    def test_rejected_usb_command_drains_reply_before_next_command(self):
        backend = Mock()
        backend.bulk_write.side_effect = lambda _handle, _ep, _interface, data, _timeout: len(data)
        replies = deque()
        for opcode, status in [(0x12, 1), (0x13, 0)]:
            header = RESPONSE.pack(0x55, opcode, 0, 0, status, 0)
            replies.extend((header, crc16(header)))

        def read(_handle, _ep, _interface, buffer, _timeout):
            payload = replies.popleft()
            for index, byte in enumerate(payload):
                buffer[index] = byte
            return len(payload)

        backend.bulk_read.side_effect = read
        with self.assertRaisesRegex(RuntimeError, "status=1"):
            exchange_command(backend, object(), 0x12)
        self.assertEqual(exchange_command(backend, object(), 0x13), b"")
        self.assertFalse(replies)

    def test_idle_open_channel_drains_rx_without_starting_a_blf_recording(self):
        backend = LoopbackBackend()
        backend.continuous_rx = True
        received = Event()
        frame = Message(channel=0, arbitration_id=0x123, is_extended_id=False, data=b"\x01")
        pending = deque([frame])

        def poll(_limit):
            if backend.channels and pending:
                received.set()
                return [pending.popleft()]
            return []

        backend.poll = poll
        dbc = ROOT / "config" / "Example.dbc"
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"ZCAN_DATA_DIR": directory}), \
                patch("zlg.zlgcan.open_backend", return_value=backend):
            device = zlgCan(0, str(dbc))
            try:
                self.assertIsNone(device._receive_thread)
                device.chn_open(0)
                self.assertTrue(received.wait(1))
                self.assertIsNone(device.custom_log)
                self.assertEqual(device.get_receive_data()[0]["arbitration_id"], 0x123)
                reader = device._receive_thread
                device.blf_start("rx")
                device.blf_stop()
                self.assertTrue(reader.is_alive())
                self.assertTrue((Path(directory) / "can_log" / "rx.blf").is_file())
                device.reset(0)
                self.assertFalse(reader.is_alive())
            finally:
                device.close()

    def test_crc_and_single_shot_fd_packet(self):
        self.assertEqual(crc16(b"123456789"), bytes.fromhex("37 4b"))
        frame = Message(channel=1, arbitration_id=0x12345, is_extended_id=True,
                        is_fd=True, bitrate_switch=True, data=bytes(range(12)), check=True)
        packet = _tx_packet(frame)
        self.assertEqual(packet[:12], bytes.fromhex("55 f2 01 00 54 00 00 00 01 00 00 00"))
        self.assertEqual(packet[12:28], bytes.fromhex("00 00 00 00 45 23 01 80 11 4a 00 00 00 00 01 0c"))
        self.assertEqual(packet[28:40], bytes(range(12)))

    def test_rx_split_and_combined_packets_keep_channel_and_frame_type(self):
        can20 = bytes.fromhex("01 00 00 00 0a 00 00 00 23 01 00 00 00 00 10 00 00 00 00 01")
        can20 += b"\xaa" + bytes(7)
        fd = bytes.fromhex("01 00 00 00 14 00 00 00 45 23 01 00 10 2a 10 00 00 00 01 0c")
        fd += bytes(range(12)) + bytes(52)
        first = encode_packet(0xf1, 0, 0, can20)
        second = encode_packet(0xf2, 0, 0, fd)
        decoder = _RxDecoder()
        self.assertEqual(decoder.feed(first[:9]), [])
        classic, flexible = decoder.feed(first[9:] + second)
        self.assertEqual((classic.channel, classic.arbitration_id, classic.is_fd, classic.data),
                         (0, 0x123, False, b"\xaa"))
        self.assertEqual((flexible.channel, flexible.arbitration_id, flexible.data),
                         (1, 0x12345, bytes(range(12))))
        self.assertTrue(flexible.is_extended_id and flexible.is_fd and flexible.bitrate_switch)
        self.assertTrue(classic.is_rx)
        self.assertFalse(flexible.is_rx)
        damaged = second[:-1] + bytes([second[-1] ^ 1])
        with self.assertRaisesRegex(RuntimeError, "校验"):
            decoder.feed(damaged)

    def test_first_channel_discards_stale_rx_before_calibrating_timestamps(self):
        device = self.periodic_backend()

        def packet(*ticks):
            records = [pack("<IIIHBB", tick, 0x1c5, 1 << 20, 0, 0, 1) + bytes(8) for tick in ticks]
            return encode_packet(0xf1, 0, 0, pack("<HH", len(records), 0) + b"".join(records))

        stale = packet(86_297_000)
        fresh = packet(100_000_000, 100_020_000)
        reads = deque([stale[:10], stale[10:], b""])
        device._read_rx.side_effect = reads.popleft

        def start_channel(opcode, channel=0, payload=b""):
            if opcode == 0x12 and channel == 0:
                self.assertFalse(reads)
                reads.append(fresh)

        device._command.side_effect = start_channel
        device.open_channel(0)
        with patch("zlg.macos_usb.time.time", return_value=1_794_290_000.020):
            first, second = device.poll(1024)
        self.assertAlmostEqual(first.timestamp, 1_794_290_000.000, places=6)
        self.assertAlmostEqual(second.timestamp - first.timestamp, 0.020, places=6)

        # Adding another channel must not drain channel 0's shared USB stream.
        reads.append(packet(100_040_000))
        device.open_channel(1)
        third, = device.poll(1024)
        self.assertAlmostEqual(third.timestamp - second.timestamp, 0.020, places=6)

    def test_open_channel_accepts_periodic_tx_without_a_session_frame_limit(self):
        device = MacUsbCanBackend.__new__(MacUsbCanBackend)
        device._opened = True
        device._channels = set()
        device._periodic_channels = set()
        device._command = Mock()
        device._read_rx = Mock(return_value=b"")
        device._log = Mock()
        device._usb = Mock()
        device._handle = object()
        device._last_tx = float("-inf")
        frame = Message(channel=1, arbitration_id=0x123, is_extended_id=False,
                        data=b"\x01", check=True)
        with self.assertRaisesRegex(RuntimeError, "未打开"):
            device.send(frame)

        device.open_channel(1)
        self.assertEqual(device.channels, (1,))
        clock, mode = unpack_from("<II", CHANNEL_CONFIG)
        self.assertEqual((clock, mode), (80_000_000, 0))
        for offset, expected in [(8, 500_000), (14, 2_000_000)]:
            tseg1, tseg2, _, _, prescaler = unpack_from("<BBBBH", CHANNEL_CONFIG, offset)
            self.assertEqual(clock // ((prescaler + 1) * (tseg1 + tseg2 + 3)), expected)

        def ack(_handle, _ep, _interface, buffer, _timeout):
            header = bytes.fromhex("55 f2 01 00 00 01")
            for index, byte in enumerate(header + crc16(header)):
                buffer[index] = byte
            return len(buffer)

        device._usb.bulk_write.side_effect = lambda _handle, _ep, _interface, data, _timeout: len(data)
        device._usb.bulk_read.side_effect = ack
        with patch("zlg.macos_usb.time.sleep"):
            for _ in range(21):
                device.send(frame)
        self.assertEqual(device._usb.bulk_write.call_count, 21)
