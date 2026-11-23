import os
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.can_service import CanService, WakeProfile, WakeStart, WakeUpdate, browse_files, save_profile, start_wake, stop_wake, update_wake, ROOT

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ZCAN_BACKEND':'simulation','ZCAN_DATA_DIR':self.tmp.name})
        self.env.start(); self.s=CanService(); self.path=ROOT/'config'/'Example.dbc'
    def tearDown(self):
        self.s.close(); self.env.stop(); self.tmp.cleanup()
    def test_browser_defaults_to_project_and_filters_files(self):
        self.assertEqual(browse_files()['path'],str(ROOT.parent))
        folder=Path(self.tmp.name); (folder/'nested').mkdir(); (folder/'one.dbc').touch(); (folder/'other.txt').touch()
        self.assertEqual([x['name'] for x in browse_files(str(folder))['entries']],['nested','one.dbc'])
    def test_catalog_contains_signal_ranges_and_choices_without_device(self):
        info=self.s.load_dbc(str(self.path)); self.assertIsNone(self.s.device)
        message=next(x for x in info['messages'] if x['name']=='TestButtons')
        signal=next(x for x in message['signals'] if x['name']=='PressA')
        self.assertEqual(message['id'],0x287); self.assertEqual(signal['maximum'],1)
    def test_profile_persists_by_dbc_content_and_rejects_invalid_values(self):
        self.s.load_dbc(str(self.path)); profile={'TestButtons':{'PressA':1}}
        self.s.save_wake_profile(profile); other=CanService(); other.load_dbc(str(self.path))
        self.assertEqual(other.wake_profile(),profile)
        with self.assertRaises(ValueError): self.s.save_wake_profile({'TestButtons':{'PressA':4}})
        self.assertEqual(self.s.wake_profile(),profile)
    def test_wake_takeover_only_cancels_owned_tasks(self):
        self.s.load_dbc(str(self.path)); self.s.save_wake_profile({'TestButtons':{'PressA':1}})
        self.s.open('simulation','',[0]); self.s.start_wake(0)
        self.assertTrue(self.s.status()['wake_active'])
        self.s.send_msg_by_name(0,'TestButtons',{'PressA':0},cycle_time=50)
        self.s.require().stop_wake()
        self.assertTrue(self.s.status()['tasks'][0]['active'])

    def test_empty_wake_profile_cannot_overwrite_saved_configuration(self):
        self.s.load_dbc(str(self.path))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(can=self.s)))
        initial = {'TestButtons': {'PressA': 1}}
        updated = {'TestButtons': {'PressA': 0, 'PressB': 1}}
        save_profile(WakeProfile(profile=initial), request)
        save_profile(WakeProfile(profile=updated), request)
        save_profile(WakeProfile(profile=updated), request)
        with self.assertRaises(HTTPException) as error:
            save_profile(WakeProfile(profile={}), request)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.s.wake_profile(), updated)
        self.assertEqual(json.loads(self.s.profile_path().with_suffix('.bak.json').read_text()), initial)

    def test_wake_restart_uses_submitted_draft_without_overwriting_saved_profile(self):
        # Consume simulated TX echoes directly so the service receiver cannot race the test.
        with patch.object(self.s, '_receive'):
            self.s.open('simulation', str(self.path), [0])
        device = self.s.require()
        message = device.db.get_message_by_name('TestSpeed')
        saved = {'TestSpeed': {'DisplaySpeed': 20}, 'TestButtons': {'PressA': {'raw': 1}}}
        self.s.save_wake_profile(saved)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(can=self.s)))

        def expect_transmitted_speed(expected):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                payloads = [bytes(f['data']) for f in device.get_receive_data()
                            if f['arbitration_id'] == 453 and not f['is_rx']]
                if payloads:
                    self.assertTrue(all(message.decode(payload, decode_choices=False)['DisplaySpeed'] == expected
                                        for payload in payloads))
                    return
                time.sleep(.01)
            self.fail('No simulated TestSpeed transmission')

        self.assertTrue(start_wake(WakeStart(channel=0), request)['wake_active'])
        expect_transmitted_speed(20)
        self.assertFalse(stop_wake(request)['wake_active'])
        device.get_receive_data()  # Discard any echoes queued before the stop.
        draft = {**saved, 'TestSpeed': {'DisplaySpeed': 10}, 'TestClassicA': {'Value': 7}}
        status = start_wake(WakeStart(channel=0, profile=draft), request)
        self.assertTrue(status['wake_active'])
        expect_transmitted_speed(10)
        self.assertEqual(next(t for t in status['tasks'] if t['name'] == 'TestButtons')['signals']['PressA'], 1)
        self.assertTrue(next(t for t in status['tasks'] if t['name'] == 'TestClassicA')['wake'])
        self.assertEqual(self.s.wake_profile(), saved)

    def test_live_wake_update_validates_before_adding_new_tasks_and_cannot_restart_stopped_wake(self):
        self.s.open('simulation', str(self.path), [0])
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(can=self.s)))
        initial = {'TestButtons': {'PressA': 1}}
        start_wake(WakeStart(profile=initial), request)
        with self.assertRaises(HTTPException):
            update_wake(WakeUpdate(profile={**initial, 'TestSpeed': {'DisplaySpeed': 350}}), request)
        self.assertEqual([task['name'] for task in self.s.status()['tasks']], ['TestButtons'])
        status = update_wake(WakeUpdate(profile={**initial, 'TestSpeed': {'DisplaySpeed': 10}}), request)
        self.assertEqual({task['name'] for task in status['tasks'] if task['wake']}, {'TestButtons', 'TestSpeed'})
        stop_wake(request)
        with self.assertRaises(HTTPException):
            update_wake(WakeUpdate(profile=initial), request)
        self.assertEqual(self.s.status()['tasks'], [])

    def test_invalid_or_empty_draft_does_not_start_the_saved_profile_or_partial_tasks(self):
        self.s.open('simulation', str(self.path), [0])
        saved = {'TestButtons': {'PressA': 1}}
        self.s.save_wake_profile(saved)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(can=self.s)))
        for draft in ({}, {'TestButtons': {'PressA': 0}, 'TestSpeed': {'DisplaySpeed': 350}}):
            with self.subTest(draft=draft):
                with self.assertRaises(HTTPException) as error:
                    start_wake(WakeStart(profile=draft), request)
                self.assertEqual(error.exception.status_code, 400)
                status = self.s.status()
                self.assertFalse(status['wake_active'])
                self.assertEqual(status['tasks'], [])
                self.assertEqual(self.s.wake_profile(), saved)

    def test_catalog_cannot_change_while_connected(self):
        self.s.open('simulation',str(self.path),[0])
        with self.assertRaises(ValueError): self.s.load_dbc(str(self.path))
    def test_modified_dbc_rejects_wake_start(self):
        path=Path(self.tmp.name)/'copy.dbc'; path.write_bytes(self.path.read_bytes())
        self.s.load_dbc(str(path)); self.s.save_wake_profile({'TestButtons':{'PressA':1}})
        self.s.open('simulation','',[0]); path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError): self.s.start_wake(0)

    def test_import_original_qsettings_ini_and_wrong_dbc(self):
        import io
        from fastapi import UploadFile
        from app.can_service import import_wake_profile
        self.s.load_dbc(str(self.path))
        profile={'TestButtons':{'PressA':1}}
        text='[dbc]\n'+self.s.dbc_key+'='+json.dumps(json.dumps(profile))+'\n'
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(can=self.s)))
        file=UploadFile(filename='wake.ini',file=io.BytesIO(text.encode()))
        self.assertEqual(import_wake_profile(request,file),profile)
        file=UploadFile(filename='wake.ini',file=io.BytesIO(b'[dbc]\nother="{}"\n'))
        with self.assertRaises(HTTPException) as err: import_wake_profile(request,file)
        self.assertEqual(err.exception.status_code,400)
