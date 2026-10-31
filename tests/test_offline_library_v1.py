"""No internet, third-party execution or modifications outside temporary vault."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from offline_library import OfflineLibrary, default_library_root, _safe_record, _file_name


class OfflineLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.program=self.base/'program'
        self.program.mkdir()
        program_patch=patch('offline_library.__file__',str(self.program/'offline_library.py'))
        program_patch.start()
        self.addCleanup(program_patch.stop)
        self.managed=self.base/'fusion'
        self.managed.mkdir()
        self.lib=OfflineLibrary(self.base/'OfflinePackages',managed_root=self.managed)
        self.source=self.base/'Installer.zip'
        self.source.write_bytes(b'file content not executable\0'*300)

    def test_separate_from_fusion_and_app(self):
        with self.assertRaises(ValueError): OfflineLibrary(self.program/'OfflinePackages')
        self.assertEqual(default_library_root(self.managed),self.base/'OfflinePackages')
        with self.assertRaises(ValueError): OfflineLibrary(self.managed/'files',managed_root=self.managed)

    def test_import_check_export_import_and_source_preserved(self):
        entry=self.lib.import_file(self.source)
        self.assertEqual(len(self.lib.list_items()),1)
        self.assertEqual(entry['sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())
        valid,_=self.lib.verify(entry['id'])
        self.assertTrue(valid)
        self.assertEqual(self.lib.path_for(entry).read_bytes(),self.source.read_bytes())
        bundle=self.base/'backup.zip'
        self.lib.export_bundle([entry['id']],bundle)
        with zipfile.ZipFile(bundle) as z:
            self.assertEqual(len(z.namelist()),2)
            self.assertIn('manifest.json',z.namelist())
        self.lib.remove(entry['id'])
        self.assertTrue(self.source.is_file())
        self.assertFalse(self.lib.path_for(entry).exists())
        restored=self.lib.import_bundle(bundle)
        self.assertEqual(len(restored),1)
        self.assertTrue(self.lib.verify(restored[0]['id'])[0])

    def test_mutated_copy_detected_without_auto_deleting(self):
        e=self.lib.import_file(self.source)
        self.lib.path_for(e).write_bytes(b'MALICIOUS CONTENT')
        good,msg=self.lib.verify(e['id'])
        self.assertFalse(good)
        self.assertEqual(len(self.lib.list_items()),1)
        with self.assertRaises(ValueError):self.lib.export_bundle([e['id']],self.base/'backup.zip')

    def test_dedup_and_no_source_deletion(self):
        self.lib.import_file(self.source)
        with self.assertRaisesRegex(ValueError,'重复导入'):
            self.lib.import_file(self.source)
        self.assertTrue(self.source.is_file())

    def test_invalid_names_and_index_path_escape(self):
        for name in ('evil/../foo.zip','evil\\file.zip','bad.txt','file.exe.','x?.zip'):
            with self.subTest(name=name), self.assertRaises(ValueError): _file_name(name)
        with self.assertRaises(ValueError):_safe_record({'id':'../../evil', 'name':'x.exe','size':1,'sha256':'0'*64})
        (self.lib.root).mkdir()
        self.lib.index.write_text(json.dumps({'schema':1,'entries':[{'id':'x'*32,'name':'x.exe','size':1,'sha256':'0'*64}]}))
        with self.assertRaises(ValueError):self.lib.list_items()
        self.assertTrue(self.source.is_file())

    def test_corrupt_zip_extra_content_rejected(self):
        e=self.lib.import_file(self.source)
        bundle=self.base/'backup.zip'
        self.lib.export_bundle([e['id']],bundle)
        self.lib.remove(e['id'])
        evil=self.base/'evil.zip'
        with zipfile.ZipFile(bundle) as old, zipfile.ZipFile(evil,'w') as new:
            for f in old.infolist():new.writestr(f,old.read(f))
            new.writestr('../outside.exe','danger')
        with self.assertRaises(ValueError):self.lib.import_bundle(evil)
        self.assertFalse((self.base/'outside.exe').exists())
        self.assertEqual(self.lib.list_items(),[])

    def test_modified_backup_fails_hash_and_leaves_empty_library(self):
        entry=self.lib.import_file(self.source)
        bundle=self.base/'backup.zip'
        self.lib.export_bundle([entry['id']],bundle)
        self.lib.remove(entry['id'])
        new=self.base/'modified.zip'
        with zipfile.ZipFile(bundle) as old,zipfile.ZipFile(new,'w') as z:
            for name in old.namelist():
                z.writestr(name,b'bad payload' if name.startswith('packages/') else old.read(name))
        with self.assertRaises(ValueError):self.lib.import_bundle(new)
        self.assertEqual(self.lib.list_items(),[])

    def test_reject_import_source_symlink(self):
        sym=self.base/'linked.exe'
        try:sym.symlink_to(self.source)
        except (OSError,NotImplementedError):self.skipTest('filesystem does not permit symlinks')
        with self.assertRaises(ValueError):self.lib.import_file(sym)

    def test_no_execution_and_no_network(self):
        with patch('subprocess.Popen',side_effect=AssertionError('execution forbidden')):
            with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
                self.lib.import_file(self.source)
                self.lib.list_items()


if __name__=='__main__':unittest.main()
