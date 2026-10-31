"""V0.4.1 regression tests: no network / Windows uninstall mutations."""
import os
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from catalog_adapter import Detected, compare_release_versions, sevenzip_update_message

class Versions(unittest.TestCase):
    def test_numeric_comparison(self):
        self.assertEqual(compare_release_versions('24.08','26.05'),-1)
        self.assertEqual(compare_release_versions('26.05','26.05'),0)
        self.assertEqual(compare_release_versions('26.05.0','26.05'),0)
        self.assertEqual(compare_release_versions('26.06','26.05'),1)
        self.assertIsNone(compare_release_versions('unknown','26.05'))
        self.assertIsNone(compare_release_versions('24.08','unverified'))
    def test_offer_update_for_detected_old_version(self):
        r=Detected('installed',Path('C:/Program Files/7-Zip/7zFM.exe'),'','24.08','C:/Program Files/7-Zip')
        self.assertEqual(sevenzip_update_message(r,'26.05')[0],'update')
        self.assertIn('24.08',sevenzip_update_message(r,'26.05')[1])
    def test_no_overwrite_if_newer_or_stale(self):
        self.assertEqual(sevenzip_update_message(Detected('installed',version='26.06'),'26.05')[0],'newer')
        self.assertEqual(sevenzip_update_message(Detected('stale',version='24.08'),'26.05')[0],'repair')
    def test_unknown_does_not_invent_upgrade(self):
        self.assertEqual(sevenzip_update_message(Detected('installed'),'26.05')[0],'unknown')

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','no GUI display')
class DetailUI(unittest.TestCase):
    def test_path_buttons_and_back_button(self):
        from app import App
        app=App()
        try:
            app._open_module_detail('sevenzip'); app.update_idletasks()
            self.assertTrue(app.manage_back.primary)
            self.assertTrue(callable(app._open_sevenzip_location))
            self.assertTrue(callable(app._copy_sevenzip_path))
            self.assertTrue(hasattr(app,'sevenzip_path'))
        finally: app.destroy()

if __name__=='__main__': unittest.main()
