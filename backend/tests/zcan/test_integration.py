import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from zlg.zlgcan import zlgCan

DBC = Path(__file__).resolve().parents[2] / 'config' / 'Example.dbc'
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'ZCAN_BACKEND':'simulation','ZCAN_DATA_DIR':self.tmp.name})
        self.env.start(); self.d=zlgCan(0,str(DBC)); self.d.chn_open(0); self.d.chn_open(1)
    def tearDown(self):
        self.d.close(); self.env.stop(); self.tmp.cleanup()
    def test_same_name_two_channels_and_stop(self):
        self.d.single_send(0,'TestButtons',{'PressA':1},cycle_time=20)
        self.d.single_send(1,'TestButtons',{'PressB':1},cycle_time=20)
        self.assertEqual(self.d.periodic_payload('TestButtons',0)[0],1)
        self.assertEqual(self.d.periodic_payload('TestButtons',1)[0],2)
        self.d.single_send(0,'TestButtons',state=False)
        self.assertIsNone(self.d.periodic_payload('TestButtons',0))
        self.assertEqual(self.d.periodic_payload('TestButtons',1)[0],2)
    def test_timed_trigger_is_superseded_by_update(self):
        result=[]
        t=threading.Thread(target=lambda:result.append(self.d.send_timed_trigger(0,'TestButtons',{'PressA':1},can_fd=True,fd_brs=False,cycle_time=120,stop_after=True)))
        t.start(); time.sleep(.03)
        self.d.single_send(0,'TestButtons',{'PressA':0,'HeldButton':1},cycle_time=120)
        t.join(2); self.assertEqual(result,[False]); self.assertEqual(self.d.periodic_payload('TestButtons',0)[0],4)
    def test_finite_frames(self):
        self.d.single_send(0,'TestButtons',{'PressA':1},cycle_time=20,send_count=3)
        time.sleep(.15)
        frames=self.d.get_receive_data(0)
        self.assertEqual(len(frames),3)
    def test_timed_trigger_two_phases(self):
        self.assertTrue(self.d.send_timed_trigger(0,'TestButtons',{'PressA':1},can_fd=True,fd_brs=False,cycle_time=20,stop_after=True))
        self.assertEqual([f['data'][0]&1 for f in self.d.get_receive_data(0)],[1,0])
