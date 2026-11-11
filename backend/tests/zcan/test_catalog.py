import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.can_service import CanService, browse_files, ROOT

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
    def test_catalog_cannot_change_while_connected(self):
        self.s.open('simulation',str(self.path),[0])
        with self.assertRaises(ValueError): self.s.load_dbc(str(self.path))
    def test_modified_dbc_rejects_wake_start(self):
        path=Path(self.tmp.name)/'copy.dbc'; path.write_bytes(self.path.read_bytes())
        self.s.load_dbc(str(path)); self.s.save_wake_profile({'TestButtons':{'PressA':1}})
        self.s.open('simulation','',[0]); path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError): self.s.start_wake(0)

    def test_import_original_qsettings_ini_and_wrong_dbc(self):
        import io, json
        from types import SimpleNamespace
        from fastapi import UploadFile, HTTPException
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
