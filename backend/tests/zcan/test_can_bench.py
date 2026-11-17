import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import can_bench

class BenchTest(unittest.TestCase):
    def options(self, directory, safe):
        return argparse.Namespace(confirm_safe=safe, duration=.25, channel=0, scenario='update',
            base_id=0x100, listener_interface=None, listener_channel=None, backend='simulation',
            device_id=None, fd=False, brs=False, output=Path(directory))

    def test_refuses_to_open_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(can_bench, 'open_backend') as opened:
            with self.assertRaises(ValueError): can_bench.run(self.options(directory, False))
            opened.assert_not_called()

    def test_simulated_payload_update(self):
        with tempfile.TemporaryDirectory() as directory:
            result = can_bench.run(self.options(directory, True))
            data = (Path(directory) / 'host_calls.csv').read_text()
            self.assertIn('0000000000000000', data)
            self.assertIn('0100000000000000', data)
            self.assertGreater(result['host_call_count'], 0)
            self.assertIsNone(result['lost_frames'])
            self.assertIsNone(result['ids']['0x100']['absolute_deviation_ms']['p99'])
