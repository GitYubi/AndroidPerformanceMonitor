import os
import tempfile
import unittest
from unittest.mock import patch

from app.can_service import CanService


class SignalSeriesTests(unittest.TestCase):
    def setUp(self):
        data_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'ZCAN_BACKEND': 'simulation', 'ZCAN_DATA_DIR': data_dir}))
        self.service = CanService()
        self.addCleanup(self.service.close)
        self.service.open('simulation', '', [0, 1])

    def receive(self, raw, **overrides):
        service = self.service
        with service.lock:
            service.seq += 1
            frame = {
                'sequence': service.seq, 'timestamp': 1700000000 + service.seq,
                'channel': 0, 'arbitration_id': 453, 'is_extended_id': False,
                'is_rx': True, 'is_error_frame': False, 'is_remote_frame': False,
                'data': list(raw.to_bytes(2, 'little') + bytes(6)),
            }
            service.frames.append({**frame, **overrides})

    def series(self, after=0, session=''):
        return self.service.signal_series('TestSpeed', 'DisplaySpeed', 0, after, session)

    def test_scaled_samples_ignore_tx_and_other_channels_or_formats(self):
        self.receive(125)
        self.receive(500, is_rx=False)
        self.receive(600, channel=1)
        self.receive(700, is_extended_id=True)
        self.receive(800, is_error_frame=True)
        self.receive(900, is_remote_frame=True)
        self.receive(1000, data=[0])
        result = self.series()
        self.assertEqual(result['points'], [{'timestamp': 1700000001, 'value': 12.5}])
        self.assertEqual(result['latest'], 7)
        self.receive(250)
        delta = self.series(result['latest'], result['session'])
        self.assertEqual(delta['points'], [{'timestamp': 1700000008, 'value': 25.0}])

    def test_reopen_resets_cursor_and_signal_history(self):
        self.receive(100)
        before = self.series()
        self.service.close()
        self.service.open('simulation', '', [0])
        self.receive(300)
        after = self.series(before['latest'], before['session'])
        self.assertNotEqual(after['session'], before['session'])
        self.assertEqual(after['points'], [{'timestamp': 1700000001, 'value': 30.0}])
