"""Regression tests: one yes/no confirmation, no typed phrase and no double prompt."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import App
from install_manager import extract_sra_safely
from lifecycle import WindowsInstall, WinModuleState
import zipfile


class ConfirmationUiTests(unittest.TestCase):
    def sra_fixture(self, root):
        archive = root / 'sra.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('SRA.exe', b'gui')
            zf.writestr('SRA-cli.exe', b'cli')
        extract_sra_safely(archive, root / 'sra')
        personal = root / 'sra' / 'personal-settings.json'
        personal.write_text('{"mySetting": true}', encoding='utf-8')
        return personal

    def sra_app(self, root):
        state = SimpleNamespace(
            _busy_uninstall=lambda: False,
            _validated_root=lambda: root,
            _running_sra_processes=lambda: [],
            _log=Mock(),
            _read_settings=lambda: object(),
            sra_path=SimpleNamespace(set=Mock()),
            _manage_scan=Mock(),
        )
        return state

    def test_decline_sra_does_not_delete_anything(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            personal = self.sra_fixture(root)
            app = self.sra_app(root)
            with patch('app.messagebox.askyesno', return_value=False) as confirm, \
                 patch('app.simpledialog.askstring', side_effect=AssertionError('Unexpected typed confirmation')):
                App._uninstall_sra(app)
            confirm.assert_called_once()
            self.assertTrue((root / 'sra' / 'SRA.exe').exists())
            self.assertTrue(personal.exists())

    def test_accept_sra_deletes_only_managed_files_after_single_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            personal = self.sra_fixture(root)
            app = self.sra_app(root)
            with patch('app.messagebox.askyesno', return_value=True) as confirm, \
                 patch('app.messagebox.showinfo') as info, \
                 patch('app.save_settings') as save, \
                 patch('app.simpledialog.askstring', side_effect=AssertionError('Unexpected typed confirmation')):
                App._uninstall_sra(app)
            confirm.assert_called_once()
            info.assert_called_once()
            save.assert_called_once()
            self.assertFalse((root / 'sra' / 'SRA.exe').exists())
            self.assertFalse((root / 'sra' / 'SRA-cli.exe').exists())
            self.assertEqual(personal.read_text(encoding='utf-8'), '{"mySetting": true}')
            app._manage_scan.assert_called_once_with(quiet=True)

    def ww_fixture(self, root):
        entry = WindowsInstall('OK-WW', '1.2.3', str(root / 'ok-ww'),
                               '"C:\\Example\\uninstall.exe"', 'HKCU/64/OK-WW')
        state = WinModuleState('installed', root / 'ok-ww' / 'ok-ww.exe', (entry,), '发现原版程序')
        app = SimpleNamespace(
            _busy_uninstall=lambda: False,
            _validated_root=lambda: root,
            ww_path=SimpleNamespace(get=lambda: ''),
            _log=Mock(),
        )
        return app, state

    def test_decline_ww_does_not_launch_installer(self):
        with tempfile.TemporaryDirectory() as td:
            app, state = self.ww_fixture(Path(td))
            with patch('app.sys.platform', 'win32'), \
                 patch('app.detect_ww_exe', return_value=None), \
                 patch('app.inspect_ww', return_value=state), \
                 patch('app.messagebox.askyesno', return_value=False) as confirm, \
                 patch('app.subprocess.Popen') as popen, \
                 patch('app.simpledialog.askstring', side_effect=AssertionError('Unexpected typed confirmation')):
                App._uninstall_ww(app)
            confirm.assert_called_once()
            popen.assert_not_called()

    def test_accept_ww_launches_only_official_uninstaller(self):
        with tempfile.TemporaryDirectory() as td:
            app, state = self.ww_fixture(Path(td))
            with patch('app.sys.platform', 'win32'), \
                 patch('app.detect_ww_exe', return_value=None), \
                 patch('app.inspect_ww', return_value=state), \
                 patch('app.messagebox.askyesno', return_value=True) as confirm, \
                 patch('app.messagebox.showinfo'), \
                 patch('app.subprocess.Popen') as popen, \
                 patch('app.simpledialog.askstring', side_effect=AssertionError('Unexpected typed confirmation')):
                App._uninstall_ww(app)
            confirm.assert_called_once()
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], state.records[0].uninstall_command)


if __name__ == '__main__':
    unittest.main()
