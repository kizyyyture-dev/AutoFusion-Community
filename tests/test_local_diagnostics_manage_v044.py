"""Module Management shows a separate read-only software diagnostic area."""
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import App
from directory_diagnostics import FolderHint

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'No Tk display')
class LocalDiagnosisManageTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.update_idletasks()

    def tearDown(self):
        diag = getattr(self.app, '_diag_window', None)
        if diag and diag.winfo_exists(): diag.destroy()
        self.app.destroy()

    def test_adobe_cards_are_not_installer_modules(self):
        app = self.app
        app._switch_page('manage')
        self.assertEqual(len(app.manage_cards), 3)
        self.assertEqual(len(app._local_diag_cards), 3)
        self.assertNotIn('ae', dict(app._all_modules()))
        self.assertNotIn('ps', dict(app._all_modules()))
        self.assertEqual(len(app.install_buttons), 0)

    def test_search_adobe_and_open_directly_selected_app(self):
        app = self.app
        app.manage_query.set('Photoshop')
        app._refresh_manage_cards()
        self.assertEqual(len(app.manage_cards), 0)
        self.assertEqual(len(app._local_diag_cards), 1)
        with patch('app.scan_known_locations', return_value=[]):
            app._open_directory_diagnostics('ps')
            self.assertEqual(app._diag_selected.get(), 'Photoshop')
            app._open_directory_diagnostics('ae')
            self.assertEqual(app._diag_selected.get(), 'After Effects')

    def test_scan_only_on_manage_visit_and_never_modifies_files(self):
        app = self.app
        hint = FolderHint('After Effects', 'Windows 安装登记目录', Path('/testing/AE'), 'Windows 安装登记', exists=True)
        with patch('app.scan_known_locations', side_effect=lambda key: [hint] if key == 'ae' else []) as scan, \
                patch('app.subprocess.Popen', side_effect=AssertionError('No external launch')):
            app._ensure_local_diag_scan()
            for _ in range(80):
                app.update()
                time.sleep(0.012)
                if app._local_diag_results['ae'] is not None:
                    break
            self.assertEqual(app._local_diag_results['ae'][0], 1)
            self.assertEqual(app._local_diag_results['ps'][0], 0)
            self.assertGreaterEqual(scan.call_count, 2)
            count = scan.call_count
            app._switch_page('home')
            app._switch_page('manage')
            app._switch_page('settings')
            app._switch_page('manage')
            app.update_idletasks()
            self.assertEqual(scan.call_count, count)

if __name__ == '__main__': unittest.main()
