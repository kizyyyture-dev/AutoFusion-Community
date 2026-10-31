import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from install_manager import (Asset, choose_asset, _relative_zip_path, _allowed_host,
                             extract_sra_safely, module_executable, sra_desktop_executable,
                             uninstall_managed_sra, download_official)


def make_zip(path, extras=None):
    entries = {'StarRailAssistant_v2.23.2/SRA.exe': b'gui bin',
               'StarRailAssistant_v2.23.2/SRA-cli.exe': b'cli bin',
               'StarRailAssistant_v2.23.2/data/example.dat': b'other data'}
    entries.update(extras or {})
    with zipfile.ZipFile(path, 'w') as z:
        for name, value in entries.items():
            z.writestr(name, value)


class AssetTests(unittest.TestCase):
    def test_asset_selection_sra(self):
        release = {'tag_name': 'v2.23.2', 'assets': [
            {'name': 'StarRailAssistant_Core_v2.23.2.zip', 'browser_download_url': 'https://github.com/x/no', 'size': 100},
            {'name': 'StarRailAssistant_v2.23.2.zip', 'browser_download_url': 'https://github.com/Shasnow/StarRailAssistant/releases/download/v2.23.2/StarRailAssistant_v2.23.2.zip', 'size': 301}]}
        a = choose_asset('sra', release)
        self.assertEqual(a.name, 'StarRailAssistant_v2.23.2.zip')
        self.assertEqual(a.size, 301)

    def test_asset_selection_ww(self):
        release = {'tag_name': 'v3.6.7', 'assets': [
            {'name': 'ok-ww-win32.zip', 'browser_download_url': 'https://github.com/a/a', 'size': 20},
            {'name': 'ok-ww-win32-China-setup.exe', 'browser_download_url': 'https://github.com/ok-oldking/ok-wuthering-waves/releases/download/v3.6.7/ok-ww-win32-China-setup.exe', 'size': 500}]}
        self.assertEqual(choose_asset('ww', release).name, 'ok-ww-win32-China-setup.exe')

    def test_unofficial_domain_rejected(self):
        self.assertFalse(_allowed_host('http://github.com/path'))
        self.assertFalse(_allowed_host('https://github.com.evil.example/a'))
        self.assertFalse(_allowed_host('https://evil.example/a'))
        self.assertTrue(_allowed_host('https://release-assets.githubusercontent.com/test'))
        with self.assertRaises(ValueError):
            choose_asset('ww', {'tag_name': 'v3', 'assets': [{'name': 'ok-ww-win32-China-setup.exe', 'browser_download_url': 'https://bad.example/x.exe', 'size': 55}]})

    def test_zip_path_rejection(self):
        for text in ('../x.exe', '/x.exe', 'C:/Windows/x.exe', 'dir/../../x.exe', 'CON.txt', 'directory/file. ', '.autofusion-managed.json'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    _relative_zip_path(zipfile.ZipInfo(text))


class InstallTests(unittest.TestCase):
    def test_portable_install_locate_and_safe_uninstall(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'release.zip'
            make_zip(archive)
            extracted = root / 'fusion' / 'sra'
            items = extract_sra_safely(archive, extracted)
            self.assertEqual(len(items), 3)
            cli = module_executable(root / 'fusion', 'sra')
            self.assertTrue(cli)
            self.assertEqual(cli.name, 'SRA-cli.exe')
            self.assertEqual(sra_desktop_executable(root / 'fusion').name, 'SRA.exe')
            # User modified one shipped file and created a personal file; both must survive.
            cli.write_bytes(b'USER MODIFICATION')
            user = extracted / 'settings-private.json'
            user.write_text('{"username": "local"}')
            removed, kept = uninstall_managed_sra(root / 'fusion')
            self.assertEqual((removed, kept), (2, 1))
            self.assertEqual(cli.read_bytes(), b'USER MODIFICATION')
            self.assertTrue(user.exists())
            self.assertTrue((extracted / '.autofusion-managed.json').exists())

    def test_nonmanaged_directory_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'sra'
            folder.mkdir()
            (folder / 'some-other-project.txt').write_text('do not delete')
            with self.assertRaises(ValueError):
                uninstall_managed_sra(Path(temp))
            self.assertTrue((folder / 'some-other-project.txt').exists())

    def test_installer_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_zip(root / 'a.zip')
            (root / 'sra').mkdir()
            with self.assertRaises(FileExistsError):
                extract_sra_safely(root / 'a.zip', root / 'sra')

    def test_traversal_archive_does_not_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'evil.zip'
            make_zip(archive, {'../evil.exe': b'evil'})
            with self.assertRaises(ValueError):
                extract_sra_safely(archive, root / 'sra')
            self.assertFalse((root / 'evil.exe').exists())
            self.assertFalse((root / 'sra').exists())


if __name__ == '__main__':
    unittest.main()

class DownloadIntegrationTests(unittest.TestCase):
    def test_sra_install_from_official_asset_mock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = root / 'original.zip'
            make_zip(sample)
            asset = Asset('StarRailAssistant_v2.23.2.zip',
                          'https://github.com/Shasnow/StarRailAssistant/releases/download/v2.23.2/StarRailAssistant_v2.23.2.zip',
                          'v2.23.2', sample.stat().st_size, 'https://github.com/Shasnow/StarRailAssistant/releases', )
            def fake_download(asset, dest, report):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(sample.read_bytes())
                return 'local-test-digest'
            with patch('install_manager.latest_asset', return_value=asset), patch('install_manager.download_asset', side_effect=fake_download):
                kind, path, version, digest = download_official('sra', root / 'fusion')
            self.assertEqual(kind, 'portable')
            self.assertEqual(path.name.lower(), 'sra-cli.exe')
            self.assertEqual(version.version, 'v2.23.2')
            self.assertEqual(digest, 'local-test-digest')

    def test_ww_downloader_does_not_launch_vendor_installer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            asset = Asset('ok-ww-win32-China-setup.exe',
                          'https://github.com/ok-oldking/ok-wuthering-waves/releases/download/v3.6.7/ok-ww-win32-China-setup.exe',
                          'v3.6.7', 9, 'https://github.com/ok-oldking/ok-wuthering-waves/releases')
            def fake_download(asset, dest, report):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b'EXE TEST!')
                return 'local-test-digest'
            with patch('install_manager.latest_asset', return_value=asset), patch('install_manager.download_asset', side_effect=fake_download):
                kind, path, version, digest = download_official('ww', root / 'fusion')
            self.assertEqual(kind, 'setup')
            self.assertEqual(path.read_bytes(), b'EXE TEST!')
