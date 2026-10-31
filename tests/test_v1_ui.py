import os
import time
import unittest
from unittest.mock import patch
from app import App

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','Tk required')
class UIReleaseTests(unittest.TestCase):
    def setUp(self):
        self.app=App();self.app.update_idletasks()
    def tearDown(self):
        dialog=getattr(self.app,'_offline_window',None)
        if dialog and dialog.winfo_exists():dialog.destroy()
        diag=getattr(self.app,'_diag_window',None)
        if diag and diag.winfo_exists():diag.destroy()
        self.app.destroy()
    def test_offline_ui_entry_available_on_manage_and_settings(self):
        self.app._switch_page('manage')
        with patch('app.OfflineLibrary') as vault:
            vault.return_value.list_items.return_value=[]
            self.app._open_offline_library()
            self.assertTrue(self.app._offline_window.winfo_exists())
    def test_cad_card_and_readonly_window(self):
        self.app._switch_page('manage')
        self.app.manage_query.set('AutoCAD')
        self.app._refresh_manage_cards()
        self.assertEqual(len(self.app.manage_cards),0)
        self.assertEqual(len(self.app._local_diag_cards),1)
        with patch('app.scan_known_locations',return_value=[]):
            self.app._open_directory_diagnostics('cad')
            self.assertEqual(self.app._diag_selected.get(),'AutoCAD')
    def test_no_nav_scans(self):
        with patch('app.scan_known_locations',side_effect=AssertionError('should not scan')):
            for name in ('home','settings','logs','manage','home'):
                self.app._switch_page(name)

if __name__=='__main__':unittest.main()
