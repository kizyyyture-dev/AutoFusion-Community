"""UI responsiveness regressions (no network / no user modules modified)."""
import os
import time
import unittest
from unittest.mock import patch
from app import App
from lifecycle import PortablePlan, WinModuleState

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt','Display unavailable')
class ResponsivenessV031(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_nav_does_not_scan_or_hash_modules(self):
        self.app._cached_module_states={'sra':('managed','已部署'),'ww':('installed','已安装')}
        self.app._refresh_home_cards()
        self.app._refresh_manage_cards()
        with patch('app.inspect_sra', side_effect=AssertionError('disk hash on tab switch')), \
             patch('app.inspect_ww', side_effect=AssertionError('registry scan on tab switch')):
            start=time.perf_counter()
            for _ in range(20):
                for page in ('manage','home','logs','settings'):
                    self.app._switch_page(page)
            elapsed=time.perf_counter()-start
        self.assertLess(elapsed, 2.0, f'Page switching too slow: {elapsed}')
        self.assertEqual(sum(b.is_active for b in self.app.nav_buttons.values()),1)

    def test_background_scan_result_updates_status(self):
        app=self.app
        app._scan_generation=1
        root=app._validated_root()
        sra=PortablePlan('absent',root/'sra')
        ww=WinModuleState('absent',None,(),'')
        app._apply_status_scan((1,root,sra,ww,True))
        self.assertEqual(app._module_status('sra')[0],'absent')
        self.assertEqual(app._module_status('ww')[0],'absent')

    def test_ignore_obsolete_scan(self):
        app=self.app
        app._scan_generation=3
        root=app._validated_root()
        sra=PortablePlan('managed',root/'sra')
        ww=WinModuleState('installed',None,(),'')
        before=dict(app._cached_module_states)
        app._apply_status_scan((2,root,sra,ww,True))
        self.assertEqual(app._cached_module_states,before)

    def test_initial_window_large_but_onscreen(self):
        self.app.update_idletasks()
        w,h = self.app.winfo_width(),self.app.winfo_height()
        self.assertGreaterEqual(w, min(960,self.app.winfo_screenwidth()))
        self.assertGreaterEqual(h, min(630,self.app.winfo_screenheight()))
        self.assertLessEqual(w,self.app.winfo_screenwidth())
        self.assertLessEqual(h,self.app.winfo_screenheight())

if __name__=='__main__': unittest.main()
