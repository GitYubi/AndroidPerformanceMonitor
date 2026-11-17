import unittest
from unittest.mock import patch
from types import SimpleNamespace
from can import Message
from can_adapters import hirain


class FakeBus:
    def __init__(self, channel, **kwargs):
        self.port_handle = 1
        self.kwargs = kwargs
        self.sent = []
        self.frames = [Message(arbitration_id=0x123, data=[1], is_extended_id=False)]
        self.closed = False
    def send(self, message): self.sent.append(message)
    def recv(self, timeout): return self.frames.pop(0) if self.frames else None
    def shutdown(self): self.closed = True


def sdk():
    bs = SimpleNamespace(TestBaseVCI_ChlMode=SimpleNamespace(CANFD=3, CAN=0),
        TestBaseVCI_SAMPLEPOINT=SimpleNamespace(SAMPLE_80=80, SAMPLE_75=75))
    records = [dict(bus_capabilities=3, hw_name='SERIAL', channel_index=i+5, hw_channel_no=i) for i in range(2)]
    return SimpleNamespace(BasicStructure=bs, HRCANBus=FakeBus, get_driver_config=lambda: records)


class HirainTest(unittest.TestCase):
    def test_discovery_and_io(self):
        with patch.object(hirain, '_sdk', return_value=sdk()), patch.object(hirain, '_driver_channels', side_effect=lambda sdk: sdk.get_driver_config()):
            result = hirain.discover()
            self.assertEqual(result[0]['channels'][1]['sdk_channel_index'], 6)
            self.assertIsNone(result[0]['model'])
            backend = hirain.HirainCanBackend('SERIAL')
            backend.open_channel(1)
            bus = backend._buses[1]
            self.assertEqual(bus.kwargs['hwserial'], 'SERIAL')
            backend.send(Message(channel=1, arbitration_id=0x123, data=[1], is_extended_id=False))
            self.assertEqual(backend.poll(2)[0].channel, 1)
            backend.close()
            self.assertTrue(bus.closed)
    def test_no_device_is_not_a_connected_device(self):
        stub = sdk(); stub.get_driver_config = lambda: []
        with patch.object(hirain, '_sdk', return_value=stub), patch.object(hirain, '_driver_channels', side_effect=lambda sdk: sdk.get_driver_config()):
            self.assertEqual(hirain.discover(), [])
            with self.assertRaises(RuntimeError): hirain.HirainCanBackend()

    def test_duplicate_device_identity_is_rejected(self):
        stub = sdk()
        records = stub.get_driver_config()
        stub.get_driver_config = lambda: records + [records[0].copy()]
        with patch.object(hirain, '_sdk', return_value=stub), patch.object(hirain, '_driver_channels', side_effect=lambda sdk: sdk.get_driver_config()):
            with self.assertRaisesRegex(RuntimeError, '重复'):
                hirain.discover()

    def test_four_channel_device_uses_device_local_channel(self):
        stub = sdk()
        stub.get_driver_config = lambda: [dict(bus_capabilities=3, hw_name='PRO', channel_index=i+5, hw_channel_no=i) for i in range(4)]
        with patch.object(hirain, '_sdk', return_value=stub), patch.object(hirain, '_driver_channels', side_effect=lambda sdk: sdk.get_driver_config()):
            backend = hirain.HirainCanBackend('PRO')
            backend.open_channel(3)
            self.assertEqual(backend.channels, (3,))
            self.assertEqual(backend.poll(1)[0].channel, 3)
            backend.close()
