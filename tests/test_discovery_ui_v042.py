"""GUI acceptance: offline stub search through home and back navigation."""
import os
import time
import unittest
from unittest.mock import patch
from discovery import Repo
from app import App

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','GUI display not available')
class DiscoveryUITests(unittest.TestCase):
    def setUp(self):
        self.app=App()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_home_search_uninstalled_catalog_and_online_unreviewed_repo(self):
        self.app.home_query.set('7-zip')
        self.app._refresh_home_cards()
        self.assertEqual(len(self.app._home_layout_cards),1)
        repo=Repo('opensource/example','Sample app','https://github.com/opensource/example',0,False,'unknown')
        self.app.home_query.set('example')
        with patch('app.search_cached',return_value=([repo],'live')):
            self.app._search_github('home')
            for _ in range(50):
                self.app.update()
                if self.app._discovery_results:
                    break
                time.sleep(0.02)
        self.assertEqual(self.app._current_page,'manage')
        self.assertEqual(self.app._discovery_results,[repo])
        self.assertIn('未经审核',self.app.manage_title.cget('text'))
        self.assertEqual(self.app.all_install_button._state,'disabled')
        self.app._manage_all()
        self.assertFalse(self.app._discovery_active)
        self.assertEqual(self.app.all_install_button._state,'normal')

    def test_non_network_local_filter(self):
        with patch('app.search_repositories',side_effect=AssertionError('should not network')):
            self.app.manage_query.set('SRA')
            self.app._refresh_manage_cards()
            self.assertFalse(self.app._discovery_active)
            self.assertEqual(len(self.app.manage_cards),1)

if __name__=='__main__':unittest.main()
