import os
import unittest
from pathlib import Path
from unittest.mock import patch
import core
from install_manager import default_root
class CommunityIsolation(unittest.TestCase):
    def test_profile_and_modules_are_independent(self):
        with patch.dict(os.environ, {"LOCALAPPDATA":str(Path.cwd()/"temporary-profile")}):
            self.assertEqual(core.data_dir().name,"AutoFusionCommunity")
            self.assertEqual(default_root(candidates=[core.data_dir()/"fusion"]),core.data_dir()/"fusion")
    def test_payment_resources_not_distributed(self):
        root=Path(__file__).resolve().parents[1]
        self.assertFalse((root/"donation_assets.py").exists())
        self.assertNotIn("from donation_assets", (root/"app.py").read_text(encoding="utf-8"))
    def test_four_pages_and_manual_updates(self):
        from app import App
        from tkinter import messagebox
        app=App()
        try:
            self.assertEqual(set(app.nav_buttons),{"home","manage","settings","logs"})
            self.assertFalse(hasattr(app,"support_page"))
            for page in app.nav_buttons:
                app._switch_page(page); app.update_idletasks()
            with patch.object(messagebox,"showinfo") as info:
                app._check_self_update()
                self.assertTrue(info.called)
        finally:
            app.destroy()
