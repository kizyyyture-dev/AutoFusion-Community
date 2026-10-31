import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from discovery import Repo
from discovery_cache import search_cached
from module_preferences import is_reminder_muted,set_reminder_muted
from directory_diagnostics import scan_known_locations,APPS
from log_storage import append_session_log, MAX_LOG_BYTES

class CacheAndPreferences(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def test_cache_memoizes_query_no_repeat_api(self):
        item=Repo('example/demo','demo','https://github.com/example/demo',0,False,'MIT')
        calls=[]
        def fetch(q):calls.append(q);return [item]
        path=self.root/'cache.json'
        self.assertEqual(search_cached('demo',searcher=fetch,path=path,now=1000)[1],'live')
        self.assertEqual(search_cached('demo',searcher=lambda q:1/0,path=path,now=1200)[1],'cached')
        self.assertEqual(len(calls),1)
        # Expired but still within fallback window, return historical navigation only.
        self.assertEqual(search_cached('demo',searcher=lambda q:(_ for _ in ()).throw(ValueError('limit')),path=path,now=2000)[1],'stale')
    def test_cache_rejects_bad_url(self):
        item=Repo('example/demo','demo','https://untrusted.example/a',0,False,'MIT')
        rows,source=search_cached('demo',searcher=lambda _:[item],path=self.root/'cache',now=1000)
        self.assertEqual(rows,[])
    def test_module_muting_persists(self):
        path=self.root/'settings.json'
        self.assertFalse(is_reminder_muted('sevenzip',path))
        set_reminder_muted('sevenzip',True,path)
        self.assertTrue(is_reminder_muted('sevenzip',path))
        self.assertFalse(is_reminder_muted('ww',path))
        set_reminder_muted('sevenzip',False,path)
        self.assertFalse(is_reminder_muted('sevenzip',path))
    def test_autocad_readonly_known_plugin_paths(self):
        pf=self.root/'Program Files';(pf/'Autodesk'/'AutoCAD 2026').mkdir(parents=True)
        roaming=self.root/'AppData';(roaming/'Autodesk'/'ApplicationPlugins').mkdir(parents=True)
        with patch('subprocess.Popen',side_effect=AssertionError('must be read-only')):
            rows=scan_known_locations('cad',{'ProgramFiles':str(pf),'APPDATA':str(roaming)},include_registry=False)
        self.assertTrue(any('AutoCAD 2026' in str(x.path) for x in rows))
        self.assertTrue(any(x.shared and x.exists for x in rows))
        self.assertTrue(all(x.exists for x in rows))
    def test_log_rotates_into_only_one_backup(self):
        d=self.root/'logs'; d.mkdir()
        (d/'session.log').write_bytes(b'x'*(MAX_LOG_BYTES-1))
        append_session_log(d,'new log')
        self.assertTrue((d/'session.log.1').exists())
        self.assertEqual((d/'session.log').read_text(),'new log\n')
        self.assertEqual(len(list(d.iterdir())),2)

if __name__=='__main__':unittest.main()
