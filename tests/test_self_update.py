"""Offline unit/integration tests for self-update: no internet, no user files."""
import hashlib
import io
import json
import os
import sys
import subprocess
from contextlib import contextmanager
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from update_manager import (Release, check_update, is_newer, validate_https,
                            prepare_release, _safe_unpack, _zip_relative_names)
from updater_worker import apply_update


REQUIRED = ['app.py','core.py','install_manager.py','lifecycle.py','cache_manager.py',
            'version.py','update_manager.py','updater_worker.py',
            'catalog_adapter.py','modules_catalog.json','discovery.py','offline_library.py','module_preferences.py','discovery_cache.py','log_storage.py','启动AutoFusion.bat']


def make_zip(version='0.2.5', versioned_folder=True, bad=None, app_code=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w', zipfile.ZIP_DEFLATED) as z:
        for path in REQUIRED:
            content='print("ok")\n'
            if path=='version.py': content=f'APP_VERSION = "{version}"\n'
            if path=='app.py' and app_code is not None: content=app_code
            name=('AutoFusion_Personal_'+version+'/' if versioned_folder else '')+path
            z.writestr(name,content)
        if bad:
            z.writestr(bad,'bad')
    return stream.getvalue()


class MockHttp:
    def __init__(self,body):
        self.obj=io.BytesIO(body)
        self.headers={'Content-Length': str(len(body))}
    def __enter__(self):return self
    def __exit__(self,*a): self.obj.close()
    def read(self,n=-1):return self.obj.read(n)


class ManagerTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(is_newer('0.2.5','0.2.4'))
        self.assertFalse(is_newer('0.2.4.0','0.2.4'))
        self.assertFalse(is_newer('0.2.3.9','0.2.4'))
        with self.assertRaises(ValueError): is_newer('latest','0.2.4')

    def test_https_only(self):
        with self.assertRaises(ValueError): validate_https('http://example.com/manifest.json')
        with self.assertRaises(ValueError): validate_https('file:///tmp/a.json')
        with self.assertRaises(ValueError): validate_https('https://a:b@site.com/x')
        self.assertEqual(validate_https('https://example.com/latest.json'),'https://example.com/latest.json')

    def test_manifest_newer_and_same(self):
        data={'version':'1.0.1','archive_url':'https://example.com/release.zip','sha256':'a'*64,'notes':'修复'}
        latest=check_update('https://example.com/latest.json',opener=lambda _:MockHttp(json.dumps(data).encode()))
        self.assertEqual(latest.version,'1.0.1')
        data['version']='0.2.4'
        self.assertIsNone(check_update('https://example.com/latest.json',opener=lambda _:MockHttp(json.dumps(data).encode())))

    def test_reject_manifest_without_sha(self):
        data={'version':'1.0.1','archive_url':'https://example.com/release.zip','sha256':''}
        with self.assertRaises(ValueError): check_update('https://example.com/latest.json',opener=lambda _:MockHttp(json.dumps(data).encode()))

    def test_staging_and_no_zip_left(self):
        archive=make_zip()
        release=Release('0.2.5','https://example.com/r.zip',hashlib.sha256(archive).hexdigest())
        with tempfile.TemporaryDirectory() as home:
            target=Path(home)/'AutoFusionPersonal';target.mkdir()
            (target/'app.py').write_text('old')
            (target/'启动AutoFusion.bat').write_text('old')
            temp, plan=prepare_release(release,target,opener=lambda _:MockHttp(archive))
            try:
                self.assertTrue((temp/'staged'/'app.py').exists())
                self.assertFalse((temp/'release.download').exists())
                self.assertTrue((target/'app.py').read_text()=='old')
                self.assertEqual(json.loads(plan.read_text())['version'],'0.2.5')
            finally:
                import shutil
                shutil.rmtree(temp)

    def test_sha_failure_preserves_old(self):
        archive=make_zip()
        release=Release('0.2.5','https://example.com/r.zip','0'*64)
        with tempfile.TemporaryDirectory() as home:
            target=Path(home)/'AutoFusionPersonal';target.mkdir()
            (target/'app.py').write_text('old');(target/'启动AutoFusion.bat').write_text('old')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                prepare_release(release,target,opener=lambda _:MockHttp(archive))
            self.assertEqual((target/'app.py').read_text(),'old')

    def test_archive_escape_refused(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)
            blob=make_zip(bad='AutoFusion_Personal_0.2.5/../../unsafe.txt')
            archive=root/'zip'; archive.write_bytes(blob)
            with self.assertRaises(ValueError): _safe_unpack(archive,root/'out','0.2.5')
            self.assertFalse((root/'unsafe.txt').exists())

    def test_wrong_version_refused(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home); archive=root/'zip';archive.write_bytes(make_zip('0.2.6'))
            with self.assertRaisesRegex(ValueError,'版本'):
                _safe_unpack(archive,root/'out','0.2.5')

    def test_refuse_user_fusion_dir(self):
        archive=make_zip()
        rel=Release('0.2.5','https://example.com/r.zip',hashlib.sha256(archive).hexdigest())
        with tempfile.TemporaryDirectory() as home:
            target=Path(home)/'AutoFusionPersonal';target.mkdir()
            (target/'app.py').write_text('old');(target/'启动AutoFusion.bat').write_text('old')
            (target/'fusion').mkdir()
            with self.assertRaises(ValueError):prepare_release(rel,target,opener=lambda _:MockHttp(archive))


@contextmanager
def tracked_update_processes():
    """Ensure every child started by update tests has exited before Windows temp cleanup.

    On Windows, even an exited Python process can briefly retain its cwd/exe;
    explicitly waiting is necessary before TemporaryDirectory removes its tree.
    This mocks only process creation for tracking, not the updater assertions.
    """
    real_popen = subprocess.Popen
    children = []

    def tracked_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        children.append(proc)
        return proc

    try:
        with patch('updater_worker.subprocess.Popen', side_effect=tracked_popen):
            yield
    finally:
        for proc in children:
            try:
                proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


class WorkerTests(unittest.TestCase):
    def test_worker_success_replaces_app_and_cleans_old(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base);target=root/'AutoFusionPersonal';target.mkdir()
            (target/'app.py').write_text('old');(target/'启动AutoFusion.bat').write_text('old')
            work=root/'af-self-update-test';work.mkdir()
            stage=work/'staged';stage.mkdir()
            code=("import json,sys,time\nfrom pathlib import Path\n"
                  "p=Path(sys.argv[sys.argv.index('--af-update-health')+1])\n"
                  "p.write_text(json.dumps({'version':'0.2.5','ready':True}))\n"
                  "time.sleep(2)\n")
            (stage/'app.py').write_text(code)
            (stage/'version.py').write_text('APP_VERSION="0.2.5"')
            plan=dict(target=str(target),stage=str(stage),workdir=str(work),
                      parent_pid=0,python=sys.executable,version='0.2.5',
                      health_file=str(work/'health.json'))
            with tracked_update_processes():
                self.assertTrue(apply_update(plan,wait_for_parent=False,health_timeout=3))
            self.assertEqual((target/'app.py').read_text(),code)
            self.assertFalse(any(root.glob('*.af-backup-*')))

    def test_worker_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base);target=root/'AutoFusionPersonal';target.mkdir()
            # Rollback starts the prior app; give it valid Python, not a stray identifier.
            (target/'app.py').write_text('print("old restored")\n');(target/'启动AutoFusion.bat').write_text('old')
            work=root/'af-self-update-test';work.mkdir()
            stage=work/'staged';stage.mkdir()
            (stage/'app.py').write_text('raise SystemExit(3)')
            plan=dict(target=str(target),stage=str(stage),workdir=str(work),
                      parent_pid=0,python=sys.executable,version='0.2.5',
                      health_file=str(work/'health.json'))
            with tracked_update_processes():
                with self.assertRaises(RuntimeError):
                    apply_update(plan,wait_for_parent=False,health_timeout=3)
            self.assertEqual((target/'app.py').read_text(),'print("old restored")\n')
            self.assertFalse(any(root.glob('*.af-backup-*')))

if __name__=='__main__':unittest.main()
