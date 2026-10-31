"""UI regression tests; no network, no installer or third-party executable run."""
import os
import tkinter as tk
import unittest
from unittest.mock import patch

from app import App


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'GUI display unavailable')
class UiV3Tests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_four_pages_open(self):
        self.assertEqual(set(self.app.nav_buttons), {'home', 'manage', 'settings', 'logs'})
        for page in self.app.nav_buttons:
            self.app._switch_page(page)
            self.app.update_idletasks()
            self.assertTrue(self.app.nav_buttons[page].is_active)
            self.assertEqual(sum(b.is_active for b in self.app.nav_buttons.values()), 1)

    def test_empty_home_without_pinned_game_cards(self):
        with patch.object(self.app, '_module_status', return_value=('absent', '未安装')):
            self.app.home_query.set('')
            self.app._refresh_home_cards()
            self.assertEqual(len(self.app._home_layout_cards), 0)
            self.assertIn('0 个项目', self.app.home_count.cget('text'))

    def test_installed_one_card_and_no_edit_to_core(self):
        def status(module):
            return ('installed','已安装') if module == 'ww' else ('absent','未安装')
        with patch.object(self.app, '_module_status', side_effect=status):
            self.app.home_query.set('')
            self.app._refresh_home_cards()
            self.assertEqual(len(self.app._home_layout_cards), 1)
            self.app.home_query.set('星穹')
            self.app._refresh_home_cards()
            self.assertEqual(len(self.app._home_layout_cards), 1)  # search catalog includes available

    def test_module_catalog_and_details(self):
        self.app._switch_page('manage')
        self.assertEqual(len(self.app.manage_cards), 3)
        self.app._open_module_detail('sra')
        self.assertEqual(self.app._manage_details_key, 'sra')
        self.assertEqual(len(self.app.install_buttons), 1)
        self.app._manage_all()
        self.assertEqual(len(self.app.manage_cards), 3)

    def test_filter_logs(self):
        self.app._log('success', 'TEST_OK')
        self.app._log('error', 'TEST_FAIL')
        self.app.log_filter.set('错误')
        self.app._rerender_logs()
        visible = self.app.log.get('1.0', 'end')
        self.assertIn('TEST_FAIL', visible)
        self.assertNotIn('TEST_OK', visible)

    def test_installer_callbacks_are_still_available(self):
        for method in ('_install', '_open_original', '_uninstall_sra', '_uninstall_ww',
                       '_offline_install_sra', '_check_self_update', '_preview_sra'):
            self.assertTrue(callable(getattr(self.app, method)))


if __name__ == '__main__':
    unittest.main()
