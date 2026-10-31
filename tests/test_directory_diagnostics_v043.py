"""V0.4.3 read-only diagnostic path tests (no Adobe applications required)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from directory_diagnostics import scan_known_locations


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env={'ProgramFiles':str(self.root/'Program Files'),
                  'ProgramFiles(x86)':str(self.root/'Program Files (x86)'),
                  'APPDATA':str(self.root/'Roaming'),
                  'LOCALAPPDATA':str(self.root/'Local'),
                  'USERPROFILE':str(self.root/'Profile')}

    def mkdir(self,path):
        path.mkdir(parents=True,exist_ok=True)
        return path

    def test_ae_known_locations_and_no_false_installed_claim(self):
        program=self.mkdir(Path(self.env['ProgramFiles'])/'Adobe'/'Adobe After Effects 2026')
        self.mkdir(program/'Support Files'/'Plug-ins')
        self.mkdir(Path(self.env['APPDATA'])/'Adobe'/'After Effects')
        out=scan_known_locations('ae',self.env)
        self.assertTrue(any(x.role=='版本专属插件' and x.exists for x in out))
        self.assertTrue(any(x.role=='偏好设置' and x.exists for x in out))
        self.assertFalse(any('已安装' in x.role for x in out))
        self.assertFalse(any(x.role=='共享插件（MediaCore）' for x in out))

    def test_ps_shared_paths_distinguished_and_no_writes(self):
        shared=self.mkdir(Path(self.env['ProgramFiles'])/'Common Files'/'Adobe'/'Plug-Ins'/'CC')
        self.mkdir(Path(self.env['APPDATA'])/'Adobe'/'Adobe Photoshop 2025')
        before={str(x) for x in self.root.rglob('*')}
        out=scan_known_locations('ps',self.env)
        self.assertTrue(any(x.path==shared and x.shared for x in out))
        self.assertTrue(any(x.role=='偏好设置' and not x.shared for x in out))
        self.assertEqual(before,{str(x) for x in self.root.rglob('*')})

    def test_no_scan_elsewhere_and_nonexistent_paths_not_generated(self):
        hidden=self.mkdir(self.root/'SomeOtherDrive'/'Adobe Photoshop 2030')
        with patch('directory_diagnostics._registry_locations', side_effect=AssertionError('no registry with injected env')):
            out=scan_known_locations('ps',self.env)
        self.assertEqual(out,[])
        self.assertTrue(hidden.is_dir())

    def test_invalid_module_rejected(self):
        with self.assertRaises(ValueError): scan_known_locations('unknown',self.env)

    def test_direct_enumeration_bounded_and_precise(self):
        root=Path(self.env['ProgramFiles'])/'Adobe'
        self.mkdir(root/'Adobe Photoshop 2025')
        self.mkdir(root/'Adobe PhotoShop 2026')
        self.mkdir(root/'SomeOtherProgram')
        self.mkdir(root/'Other'/'Adobe Photoshop 2030')
        out=scan_known_locations('ps',self.env)
        installs=[x for x in out if x.role=='程序目录（候选）']
        self.assertEqual(len(installs),2)
        self.assertFalse(any('2030' in str(x.path) for x in out))

    def test_ae_cache_is_only_a_hint_not_assumed_fixed_location(self):
        self.mkdir(Path(self.env['LOCALAPPDATA'])/'Adobe'/'After Effects')
        out=scan_known_locations('ae',self.env)
        hint=next(x for x in out if x.role=='可能的本地数据')
        self.assertIn('用户另行指定',hint.note)

if __name__=='__main__': unittest.main()
