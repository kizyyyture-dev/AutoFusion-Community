"""Offline-only tests for per-module cached installers and uninstall integration."""
from __future__ import annotations
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app import App
from cache_manager import cache_plan, clean_module_cache, offline_package
from install_manager import extract_sra_safely
from core import Settings, load_settings, save_settings


class CacheManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'fusion'
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def make_sra_zip(self, name='StarRailAssistant_v2.24.1.zip'):
        out = self.root / '_downloads' / 'sra' / name
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('SRA.exe', b'gui')
            z.writestr('SRA-cli.exe', b'cli')
        return out

    def test_old_release_cache_recognized_and_cleaned(self):
        z = self.make_sra_zip()
        other = self.root / '_downloads' / 'sra' / 'personal.zip'
        other.write_bytes(b'personal')
        ww_cache = self.root / '_downloads' / 'ww'
        ww_cache.mkdir(parents=True)
        installer = ww_cache / 'ok-ww-win32-China-setup.exe'
        installer.write_bytes(b'MZother-project')
        plan = cache_plan(self.root, 'sra')
        self.assertEqual(plan.total_bytes, z.stat().st_size)
        self.assertEqual(plan.unrecognized_files, 1)
        removed, size = clean_module_cache(self.root, 'sra')
        self.assertEqual(removed, 1)
        self.assertEqual(size, plan.total_bytes)
        self.assertFalse(z.exists())
        self.assertTrue(other.exists())
        self.assertTrue(installer.exists())

    def test_ww_clear_keeps_sra_zip(self):
        sra = self.make_sra_zip()
        ww = self.root / '_downloads' / 'ww' / 'ok-ww-win32-China-setup.exe'
        ww.parent.mkdir(parents=True)
        ww.write_bytes(b'MZinstaller')
        self.assertEqual(clean_module_cache(self.root, 'ww')[0], 1)
        self.assertTrue(sra.exists())
        self.assertFalse(ww.exists())

    def test_no_archive_returns_none(self):
        self.assertIsNone(offline_package(self.root, 'sra'))

    def test_offline_reinstall_from_legacy_zip(self):
        z = self.make_sra_zip()
        self.assertEqual(offline_package(self.root, 'sra'), z)
        extract_sra_safely(z, self.root / 'sra')
        self.assertTrue((self.root / 'sra' / 'SRA.exe').is_file())
        self.assertTrue(z.is_file())

    def test_symlink_in_cache_path_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.root / '_downloads').symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'当前 Windows/文件系统禁止创建测试用符号链接：{exc}')
            plan = cache_plan(self.root, 'sra')
            self.assertTrue(plan.unsafe)
            with self.assertRaises(ValueError):
                clean_module_cache(self.root, 'sra')

    def test_symlink_archive_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            archive = Path(outside) / 'StarRailAssistant_v2.24.1.zip'
            archive.write_bytes(b'external data')
            location = self.root / '_downloads' / 'sra'
            location.mkdir(parents=True)
            try:
                (location / archive.name).symlink_to(archive)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'当前 Windows/文件系统禁止创建测试用符号链接：{exc}')
            with self.assertRaises(ValueError):
                clean_module_cache(self.root, 'sra')
            self.assertTrue(archive.exists())

    def test_unrecognized_files_remain_and_no_delete_whole_dir(self):
        z = self.make_sra_zip()
        f = z.parent / 'user-notes.txt'
        f.write_text('keep', encoding='utf-8')
        clean_module_cache(self.root, 'sra')
        self.assertTrue(f.exists())
        self.assertTrue(f.parent.is_dir())

    def test_option_persists_on_older_settings_json(self):
        file = self.root / 'settings.json'
        file.write_text('{"install_root":"D:\\u005c\\u005cAutoFusion\\u005c\\u005cfusion"}', encoding='utf-8')
        settings = load_settings(file)
        self.assertFalse(settings.keep_install_packages)
        settings.keep_install_packages = True
        save_settings(settings, file)
        self.assertTrue(load_settings(file).keep_install_packages)

    def test_cancel_uninstall_keeps_package_and_program(self):
        z = self.make_sra_zip()
        extract_sra_safely(z, self.root / 'sra')
        app = self._fake_app(keep=False)
        with patch('app.messagebox.askyesno', return_value=False), patch('app.messagebox.showinfo'):
            App._uninstall_sra(app)
        self.assertTrue(z.exists())
        self.assertTrue((self.root / 'sra' / 'SRA.exe').exists())

    def test_uninstall_default_clears_cache_but_not_user_file(self):
        z = self.make_sra_zip()
        extract_sra_safely(z, self.root / 'sra')
        user = self.root / 'sra' / 'my-settings.json'
        user.write_text('keep me', encoding='utf-8')
        app = self._fake_app(keep=False)
        with patch('app.messagebox.askyesno', return_value=True), patch('app.messagebox.showinfo'), \
             patch('app.save_settings'):
            App._uninstall_sra(app)
        self.assertFalse(z.exists())
        self.assertFalse((self.root / 'sra' / 'SRA.exe').exists())
        self.assertTrue(user.exists())

    def test_uninstall_keep_option_preserves_archive(self):
        z = self.make_sra_zip()
        extract_sra_safely(z, self.root / 'sra')
        app = self._fake_app(keep=True)
        with patch('app.messagebox.askyesno', return_value=True), patch('app.messagebox.showinfo'), \
             patch('app.save_settings'):
            App._uninstall_sra(app)
        self.assertTrue(z.exists())
        self.assertFalse((self.root / 'sra' / 'SRA.exe').exists())
        self.assertEqual(offline_package(self.root, 'sra'), z)

    def _fake_app(self, keep):
        return SimpleNamespace(
            _busy_uninstall=lambda: False,
            _validated_root=lambda: self.root,
            _running_sra_processes=lambda: [],
            _log=Mock(),
            _read_settings=lambda: Settings(),
            sra_path=SimpleNamespace(set=Mock()),
            _manage_scan=Mock(),
            keep_install_packages=SimpleNamespace(get=lambda: keep),
        )

if __name__ == '__main__':
    unittest.main()
