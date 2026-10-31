"""v0.4 catalog tests: deterministic, offline, never change Windows installation."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from catalog_adapter import (load_catalog, check_sevenzip_metadata, download_sevenzip,
                             detect_sevenzip, Detected, uninstall_path_from_record)

class Response:
    def __init__(self, data, url='https://netix.dl.sourceforge.net/project/sevenzip/7-Zip/26.05/7z2605-x64.exe'):
        self.stream=io.BytesIO(data); self.url=url
    def read(self,n=-1): return self.stream.read(n)
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self,*args): self.stream.close()

class CatalogTests(unittest.TestCase):
    def test_catalog_contains_verified_legacy_and_non_github(self):
        catalog=load_catalog()
        self.assertEqual(set(catalog),{'ww','sra','sevenzip'})
        self.assertIn('sourceforge.net',catalog['sevenzip']['download_url'])
        self.assertEqual(catalog['ww']['adapter'],'legacy_ww')

    def test_manifest_cannot_change_reviewed_source(self):
        item=load_catalog()['sevenzip'].copy()
        item['download_url']='https://example.org/evil.exe'
        with self.assertRaises(ValueError): check_sevenzip_metadata(item)

    def test_non_windows_doesnt_scan(self):
        self.assertEqual(detect_sevenzip(windows=False).state,'absent')

    def test_download_checks_hash_and_cleans_partial(self):
        item=load_catalog()['sevenzip']
        with tempfile.TemporaryDirectory() as tmp,patch('catalog_adapter.sevenzip_arch_supported',return_value=True):
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                download_sevenzip(Path(tmp), item,opener=lambda *_args, **_k: Response(b'MZ'+b'hello'*100000))
            self.assertEqual(list((Path(tmp)/'_downloads'/'sevenzip').iterdir()),[])

    def test_reject_host_redirect_not_sourceforge(self):
        item=load_catalog()['sevenzip']
        with tempfile.TemporaryDirectory() as tmp,patch('catalog_adapter.sevenzip_arch_supported',return_value=True):
            with self.assertRaisesRegex(ValueError,'重定向'):
                download_sevenzip(Path(tmp),item,opener=lambda *_args, **_k: Response(b'MZ',url='https://untrusted.example/x.exe'))

    def test_download_offline_mock_accepts_known_hash(self):
        data=b'MZ'+(b'7zip fake test binary' * 12000)
        item=load_catalog()['sevenzip'].copy()
        item['sha256']=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp,\
             patch('catalog_adapter.sevenzip_arch_supported',return_value=True),\
             patch('catalog_adapter.check_sevenzip_metadata'):
            file,h=download_sevenzip(Path(tmp),item,opener=lambda *_a,**_kw:Response(data))
            self.assertEqual(file.read_bytes(),data)
            self.assertEqual(h,item['sha256'])
            file2,_=download_sevenzip(Path(tmp),item,opener=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('Unexpected re-download')))
            self.assertEqual(file,file2)

    def test_uninstall_never_uses_random_command(self):
        rec=Detected('installed',None,'cmd /c del C:\\Users\\file', '26.05','C:\\Program Files\\7-Zip')
        self.assertIsNone(uninstall_path_from_record(rec))

if __name__=='__main__': unittest.main()

class RegistryProbeTests(unittest.TestCase):
    class Registry:
        HKEY_LOCAL_MACHINE='HKLM'; HKEY_CURRENT_USER='HKCU'
        def __init__(self, info): self.info=info
        class Key:
            def __init__(self, path, info): self.path=path; self.info=info
            def __enter__(self):return self
            def __exit__(self,*a):pass
        def OpenKey(self,parent,name):
            if isinstance(parent,self.Key):
                if name=='sample': return self.Key('entry',self.info)
                raise OSError('missing')
            return self.Key('root',self.info)
        def QueryInfoKey(self,root):return (1,0,0)
        def EnumKey(self,root,i):return 'sample'
        def QueryValueEx(self,node,name):
            if node.path!='entry' or name not in self.info: raise OSError('missing')
            return self.info[name],1

    def test_registered_7zip_install_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'7zFM.exe';exe.write_bytes(b'MZ')
            reg=self.Registry({'DisplayName':'7-Zip 26.05 (x64)', 'InstallLocation':folder,
                               'DisplayVersion':'26.05', 'UninstallString':str(Path(folder)/'Uninstall.exe')})
            result=detect_sevenzip(winreg_module=reg,windows=True)
            self.assertEqual(result.state,'installed')
            self.assertEqual(result.exe,exe)
            self.assertEqual(result.version,'26.05')

    def test_stale_registration_does_not_count_as_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            reg=self.Registry({'DisplayName':'7-Zip 26.05 (x64)', 'InstallLocation':folder,
                               'DisplayVersion':'26.05'})
            with patch.dict('os.environ',{'ProgramFiles':folder,'ProgramW6432':folder}):
                self.assertEqual(detect_sevenzip(winreg_module=reg,windows=True).state,'stale')

    def test_unrelated_product_not_classified_as_7zip(self):
        with tempfile.TemporaryDirectory() as folder:
            reg=self.Registry({'DisplayName':'7-Zip Plugin Cleaner', 'InstallLocation':folder})
            with patch.dict('os.environ',{'ProgramFiles':folder,'ProgramW6432':folder}):
                self.assertEqual(detect_sevenzip(winreg_module=reg,windows=True).state,'absent')
