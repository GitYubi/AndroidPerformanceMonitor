import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from app.can_service import CanService

DBC = Path(__file__).resolve().parents[2] / 'config' / 'Example.dbc'


class LifecycleTest(TestCase):
    def test_disconnect_stops_tasks_closes_and_allows_new_session(self):
        with patch.dict('os.environ', {'ZCAN_BACKEND': 'simulation'}):
            service = CanService()
            try:
                service.open('simulation', str(DBC), [0])
                old = service.device
                old.single_send(0, 'TestButtons', {'PressA': 1}, cycle_time=20)
                with patch.object(old._backend, 'is_online', return_value=False):
                    deadline = time.monotonic() + 3
                    while service.device is not None and time.monotonic() < deadline:
                        time.sleep(.02)
                self.assertIsNone(service.device)
                self.assertFalse(old._backend.is_online())
                self.assertIn('断开', service.error)
                self.assertFalse(any(task['func'].is_alive() for task in old.thread_messages.values()))
                service.open('simulation', str(DBC), [0])
                self.assertIsNot(service.device, old)
            finally:
                service.close()
