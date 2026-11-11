import ctypes
import json
import os
from pathlib import Path
from struct import pack
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from can import Message
from can.io import BLFReader

from zlg import zlgcan
from zlg.backend import LoopbackBackend
from zlg.paths import ROOT
from zlg.periodic import Deadline, PeriodicSend
from zlg.tx_timing import TxSample, TxTiming

MS = 1_000_000
DBC = ROOT / "config" / "Example.dbc"


def model_intervals(periods_ms, dll_ms, *, absolute, end_ms=60_000):
    """Deterministic one-device simulation with serialized SDK calls."""
    plans = [Deadline(period * MS, 0) for period in periods_ms]
    starts = [[] for _ in plans]
    skipped = 0
    now = 0
    while now < end_ms * MS:
        index = min(range(len(plans)), key=lambda item: plans[item].next_ns)
        plan = plans[index]
        due = plan.next_ns
        now = max(now, due)
        if now >= end_ms * MS:
            break
        starts[index].append(now)
        now += dll_ms * MS
        if absolute:
            skipped += plan.advance(due, now)
            assert plan.next_ns > now
        else:
            plan.next_ns = now + plan.period_ns
    return [[(b - a) / MS for a, b in zip(group, group[1:])] for group in starts], skipped


class DeadlineTests(unittest.TestCase):
    def test_10_50_500_ms_with_simulated_1_3_8_ms_sdk_times(self):
        for periods in ((10,), (10, 50), (10, 50, 500)):
            for cost in (1, 3, 8):
                with self.subTest(periods=periods, cost=cost):
                    relative, _ = model_intervals(periods, cost, absolute=False)
                    absolute, skipped = model_intervals(periods, cost, absolute=True)
                    self.assertTrue(all(group for group in absolute))
                    self.assertTrue(all(gap >= cost for group in absolute for gap in group))
                    if len(periods) == 1:
                        self.assertEqual(set(relative[0]), {10 + cost})
                        self.assertEqual(set(absolute[0]), {10})
                        self.assertEqual(skipped, 0)

    def test_late_lock_and_slow_send_skip_deadlines_then_rejoin_phase(self):
        plan = Deadline(10 * MS, 10 * MS)
        self.assertEqual(plan.advance(10 * MS, 36 * MS), 2)
        self.assertEqual(plan.next_ns, 40 * MS)
        self.assertEqual(plan.advance(40 * MS, 48 * MS), 0)
        self.assertEqual(plan.next_ns, 50 * MS)

    def test_content_update_keeps_phase_and_period_update_rearms(self):
        now = 0
        first = Message(channel=0, data=b"\x01")
        second = Message(channel=0, data=b"\x02")
        worker = PeriodicSend(first, 10 * MS, Mock(), Mock(), clock=lambda: now)
        worker.advance(0, 2 * MS)
        now = 5 * MS
        worker.update(second, 10 * MS)
        self.assertIsNone(worker.pending(9 * MS))
        self.assertEqual(worker.pending(10 * MS), (second, 10 * MS, 10 * MS))
        now = 11 * MS
        worker.update(first, 50 * MS)
        self.assertIsNone(worker.pending(20 * MS))
        self.assertEqual(worker.pending(61 * MS), (first, 61 * MS, 50 * MS))
        worker.cancel()
        self.assertIsNone(worker.pending(61 * MS))


class PeriodicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {"ZCAN_DATA_DIR": self.directory.name, "ZCAN_TX_TIMING": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.backend = LoopbackBackend()
        factory = patch("zlg.zlgcan.open_backend", return_value=self.backend)
        with factory:
            self.device = zlgcan.zlgCan(0, str(DBC))
        self.addCleanup(self.device.close)
        self.device.chn_open(0)

    def test_multiple_periods_send_and_close_without_leaving_workers(self):
        counts = {}
        all_repeated = Event()
        send = self.backend.send

        def count_send(frame):
            send(frame)
            frame_id = frame.arbitration_id
            counts[frame_id] = counts.get(frame_id, 0) + 1
            if len(counts) == 3 and all(count >= 2 for count in counts.values()):
                all_repeated.set()

        self.backend.send = count_send
        for name, period in (("TestClassicA", 10), ("TestClassicB", 50), ("TestClassicC", 500)):
            self.device.single_send(0, name, can_fd=False, cycle_time=period)
        workers = [task["func"] for task in self.device.thread_messages.values()]
        self.assertTrue(all_repeated.wait(2))
        self.device.close()
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertFalse(self.device.thread_messages)

    def test_channel_reset_cancels_worker_waiting_for_io(self):
        entered, proceed, cancelled = Event(), Event(), Event()
        dispatch = self.device._send_periodic

        def gated(worker, *, first=False):
            if not first:
                entered.set()
                proceed.wait(2)
            dispatch(worker, first=first)

        self.device._send_periodic = gated
        self.device.single_send(0, "TestClassicA", can_fd=False, cycle_time=10)
        worker = self.device.thread_messages[(0, "TestClassicA")]["func"]
        cancel = worker.cancel

        def notify_cancel():
            cancel()
            cancelled.set()

        worker.cancel = notify_cancel
        reset = Thread(target=self.device.reset, args=(0,), daemon=True)
        try:
            self.assertTrue(entered.wait(2))
            with self.device._io_lock:
                proceed.set()
                reset.start()
                self.assertTrue(cancelled.wait(2))
        finally:
            proceed.set()
            if reset.ident is not None:
                reset.join(2)
        self.assertFalse(reset.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.device.channels, ())
        self.assertEqual(self.device._tx_timing.report()["retained"], 1)

    def test_periodic_tx_progresses_while_blf_writer_is_blocked(self):
        writing, release, sent_during_write = Event(), Event(), Event()
        send = self.backend.send

        def mark_send(frame):
            send(frame)
            if writing.is_set() and not release.is_set():
                sent_during_write.set()

        def block_write(_frame):
            writing.set()
            release.wait(2)

        def make_writer(stream):
            writer.stop.side_effect = stream.close
            return writer

        self.backend.send = mark_send
        writer = Mock()
        writer.on_message_received.side_effect = block_write
        with patch("zlg.zlgcan.BLFWriter", side_effect=make_writer):
            self.device.blf_start("blocked")
        try:
            self.device.single_send(0, "TestClassicA", can_fd=False, cycle_time=10)
            self.assertTrue(writing.wait(2))
            self.assertTrue(sent_during_write.wait(1))
        finally:
            release.set()
        self.device.close()
        writer.stop.assert_called_once()
        self.assertFalse(self.device.thread_messages)

    def test_blf_drains_tail_on_stop_and_reset_and_preserves_prior_file(self):
        # Keep the frames in the backend queue until the lifecycle operation.
        with patch.object(self.device, "_sync_receiver"):
            for frame_count, reset_first in ((1200, False), (1500, True)):
                self.device.blf_start("tail")
                for index in range(frame_count):
                    self.backend.send(Message(channel=0, arbitration_id=6, is_extended_id=False,
                                              data=index.to_bytes(2, "little")))
                if reset_first:
                    self.device.reset(0)
                self.device.blf_stop()

        log_directory = Path(self.directory.name) / "can_log"
        for name, frame_count in (("tail.blf", 1200), ("tail_01.blf", 1500)):
            with BLFReader(log_directory / name) as reader:
                frames = list(reader)
            self.assertEqual([int.from_bytes(frame.data, "little") for frame in frames], list(range(frame_count)))
            self.assertTrue(all(frame.channel == 0 and not frame.is_rx for frame in frames))

    def test_failure_stops_periodic_task_and_exports_stats(self):
        original = self.backend.send
        failed = Event()
        calls = []
        def fail_after_first(frame):
            calls.append(frame)
            if len(calls) == 1:
                return original(frame)
            failed.set()
            raise RuntimeError("simulated DLL failure")

        self.backend.send = fail_after_first
        self.device.single_send(0, "TestClassicA", can_fd=False, cycle_time=1)
        worker = self.device.thread_messages[(0, "TestClassicA")]["func"]
        self.assertTrue(failed.wait(2))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.device.close()
        output, = (Path(self.directory.name) / "tx_timing").glob("*.json")
        report = json.loads(output.read_text())
        self.assertEqual(report["summary"][0]["attempts"], 2)
        self.assertEqual(report["summary"][0]["failures"], 1)
        self.assertFalse(report["bus_timestamps"])
        self.assertEqual(len(calls), 2)


class WindowsLayoutAndTimingTests(unittest.TestCase):
    def test_device_error_records_are_reported_outside_can_frames(self):
        with patch("zlg.zlgcan.ZCAN") as factory:
            factory.return_value.OpenDevice.return_value = 1
            backend = zlgcan.WindowsCanBackend(0)
        backend._error_log = Mock()
        batch = (zlgcan.ZCANDataObj * 2)()
        for item, kind, subtype, state in [(batch[0], 1, 2, 1), (batch[1], 2, 3, 4)]:
            item.dataType = 2
            item.chnl = 1
            payload = pack("<Q6B2x", 123, kind, subtype, state, 6, 8, 0)
            ctypes.memmove(ctypes.addressof(item) + 8, payload, len(payload))
        self.assertEqual(backend.decode_poll((batch, 2)), [])
        messages = " ".join(call.args[0] for call in backend._error_log.warning.call_args_list)
        self.assertIn("ACK 错误", messages)
        self.assertIn("Bus-Off / 驱动发送缓冲区溢出", messages)

    def test_ctypes_match_sdk_record_layout(self):
        cases = [
            (zlgcan.ZCAN_CAN_FRAME, 16, {"can_dlc": 4, "data": 8}),
            (zlgcan.ZCAN_CANFD_FRAME, 72, {"len": 4, "brs": 5, "data": 8}),
            (zlgcan.ZCAN_Transmit_Data, 20, {"transmit_type": 16}),
            (zlgcan.ZCAN_TransmitFD_Data, 76, {"transmit_type": 72}),
            (zlgcan.ZCAN_CHANNEL_STATUS, 12, {"Reserved": 8}),
            (zlgcan.ZCAN_AUTO_TRANSMIT_OBJ, 28, {"interval": 4, "obj": 8}),
            (zlgcan.ZCANFD_AUTO_TRANSMIT_OBJ, 84, {"interval": 4, "obj": 8}),
            (zlgcan.ZCANDataObj, 100, {"chnl": 1, "zcanfddata": 8}),
            (zlgcan.ZCANFDData, 88, {"flag": 8, "frame": 16}),
        ]
        for structure, size, offsets in cases:
            with self.subTest(structure=structure.__name__):
                self.assertEqual(ctypes.sizeof(structure), size)
                for field, offset in offsets.items():
                    self.assertEqual(getattr(structure, field).offset, offset)
        self.assertEqual(zlgcan.ZCAN_CHANNEL_STATUS.Reserved.size, 4)

    def test_dll_boundary_records_native_return_count(self):
        sdk = zlgcan.ZCAN.__new__(zlgcan.ZCAN)
        sdk._ZCAN__dll = Mock()
        sdk._ZCAN__dll.ZCAN_TransmitData.return_value = 0
        sample = TxSample(1, 0, 0x123, False, False, 10 * MS, 0, 0, 3 * MS)
        with patch("zlg.zlgcan.time.perf_counter_ns", side_effect=[4 * MS, 7 * MS]):
            self.assertEqual(sdk.TransmitData(1, (zlgcan.ZCANDataObj * 1)(), 1, sample), 0)
        self.assertEqual(sample.returned_frames, 0)
        self.assertEqual(sample.lock_wait_ns, 3 * MS)
        self.assertEqual(sample.api_duration_ns, 3 * MS)

    def test_report_groups_gaps_and_bounds_samples(self):
        stats = TxTiming("test DLL", capacity=3)
        for start in (0, 10, 26, 47):
            sample = TxSample(1, 0, 0x123, False, True, 10 * MS, start * MS, start * MS,
                              (start + 2) * MS, api_start_ns=(start + 2) * MS,
                              api_return_ns=(start + 3) * MS, returned_frames=1)
            stats.record(sample)
        report = stats.report()
        self.assertEqual(report["evicted"], 1)
        summary, = report["summary"]
        self.assertEqual(summary["p95_ns"], 21 * MS)
        self.assertEqual(summary["over_1_5x"], 2)
        self.assertEqual(summary["over_2x"], 1)
        self.assertEqual(summary["max_lock_wait_ns"], 2 * MS)
