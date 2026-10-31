import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from install_manager import MARKER, extract_sra_safely, uninstall_managed_sra
from lifecycle import (WindowsInstall, inspect_sra, inspect_ww, plan_text_sra,
                       plan_text_ww, _looks_like_ww)


def sample_install(root):
    zipfile_path = root / 'sra-release.zip'
    with zipfile.ZipFile(zipfile_path, 'w') as z:
        z.writestr('SRA.exe', b'bin GUI')
        z.writestr('SRA-cli.exe', b'bin CLI')
        z.writestr('defaults/config.json', b'{"key":true}')
    extract_sra_safely(zipfile_path, root / 'sra')


class PortableLifecycleTests(unittest.TestCase):
    def test_preview_is_read_only_and_counts_managed_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample_install(root)
            before = {(p.relative_to(root).as_posix(), p.read_bytes())
                      for p in (root / 'sra').rglob('*') if p.is_file()}
            plan = inspect_sra(root)
            self.assertEqual(plan.state, 'managed')
            self.assertEqual((plan.removable, plan.changed, plan.user_files), (3, 0, 0))
            self.assertEqual(plan.bytes_reclaimable, sum(len(data) for _, data in before if _.endswith(('.exe', 'config.json'))))
            self.assertIn('只读预览', plan_text_sra(plan))
            after = {(p.relative_to(root).as_posix(), p.read_bytes())
                     for p in (root / 'sra').rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_modified_original_and_extra_user_config_never_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample_install(root)
            (root / 'sra' / 'SRA-cli.exe').write_bytes(b'modified')
            custom = root / 'sra' / 'personal-profile.json'
            custom.write_text('private')
            plan = inspect_sra(root)
            self.assertEqual((plan.removable, plan.changed, plan.user_files), (2, 1, 1))
            removed, kept = uninstall_managed_sra(root)
            self.assertEqual((removed, kept), (2, 1))
            self.assertEqual(custom.read_text(), 'private')
            self.assertEqual((root / 'sra' / 'SRA-cli.exe').read_bytes(), b'modified')
            self.assertTrue((root / 'sra' / MARKER).exists())

    def test_unmanaged_or_deleted_marker_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample_install(root)
            (root / 'sra' / MARKER).unlink()
            self.assertEqual(inspect_sra(root).state, 'unmanaged')
            with self.assertRaises(ValueError):
                uninstall_managed_sra(root)
            self.assertTrue((root / 'sra' / 'SRA.exe').exists())

    def test_path_traversal_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample_install(root)
            outside = root / 'secret.txt'
            outside.write_text('must survive')
            marker = root / 'sra' / MARKER
            data = json.loads(marker.read_text())
            data['files'].append({'path': '../secret.txt', 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()})
            marker.write_text(json.dumps(data))
            self.assertEqual(inspect_sra(root).state, 'unmanaged')
            self.assertEqual(outside.read_text(), 'must survive')

    def test_symlink_directory_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / 'real'
            real.mkdir()
            try:
                (root / 'sra').symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest('symlinks unavailable')
            self.assertEqual(inspect_sra(root).state, 'unsafe')

    def test_empty_install(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(inspect_sra(Path(td)).state, 'absent')


class WindowsLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.entry = WindowsInstall('OK-WW 1.2.3', '1.2.3', '',
                                    '"C:\\Example\\uninst.exe"', 'HKCU/64/OK-WW')

    def test_registered_but_missing_files_report_stale(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / 'ok-ww.exe'
            state = inspect_ww(exe, records=(self.entry,))
            self.assertEqual(state.state, 'stale')
            self.assertIn('官方卸载', plan_text_ww(state))
            self.assertEqual(len(state.records), 1)

    def test_registered_and_present(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / 'ok-ww.exe'
            exe.write_bytes(b'fake')
            self.assertEqual(inspect_ww(exe, records=(self.entry,)).state, 'installed')

    def test_executable_without_uninstall_record(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / 'ok-ww.exe'
            exe.write_bytes(b'fake')
            self.assertEqual(inspect_ww(exe, records=()).state, 'unregistered')

    def test_registry_name_filter(self):
        self.assertTrue(_looks_like_ww('OK-WW 1.2.3'))
        self.assertTrue(_looks_like_ww('ok-wuthering-waves'))
        self.assertFalse(_looks_like_ww('Other ok-ww-helper'))
        self.assertFalse(_looks_like_ww('Something else'))


if __name__ == '__main__':
    unittest.main()
