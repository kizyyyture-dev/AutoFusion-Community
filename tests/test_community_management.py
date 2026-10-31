"""A–G: isolated ordinary-user and developer-coexistence acceptance cases."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import core
from community_storage import claim_empty_root, record_install, root_owned, trusted_install
from install_manager import (Asset, default_root, discover_sra_executable,
    download_official, extract_sra_safely, module_executable, uninstall_managed_sra, MARKER)
from lifecycle import inspect_sra
from cache_manager import clean_module_cache
from catalog_adapter import detect_sevenzip

class CommunityAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        env=patch.dict(os.environ,{"LOCALAPPDATA":str(self.base/"profile")})
        env.start();self.addCleanup(env.stop)
        self.root=self.base/"D"/"AutoFusion"/"fusion"

    def archive(self):
        p=self.base/"official.zip"
        with zipfile.ZipFile(p,"w") as z:
            z.writestr("version/SRA.exe",b"mock GUI")
            z.writestr("version/SRA-cli.exe",b"mock CLI")
            z.writestr("version/defaults.json",b"defaults")
        return p

    def install(self):
        claim_empty_root(self.root)
        extract_sra_safely(self.archive(),self.root/"sra")

    def test_A_fresh_download_install_and_management(self):
        archive=self.archive()
        asset=Asset("StarRailAssistant_v2.1.0.zip","https://github.com/example/asset","2.1.0",1,"https://github.com/example")
        def download(asset,target,report):
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(archive,target)
            return hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch("install_manager.latest_asset",return_value=asset),patch("install_manager.download_asset",side_effect=download):
            kind,exe,_,_=download_official("sra",self.root)
        self.assertEqual(kind,"portable");self.assertTrue(exe.is_file())
        self.assertEqual(default_root(candidates=[self.root,self.base/"fallback"]),self.root)
        self.assertEqual(inspect_sra(self.root).state,"managed")
        self.assertEqual(uninstall_managed_sra(self.root)[0],3)
        self.assertEqual(clean_module_cache(self.root,"sra")[0],1)

    def test_B_official_registry_outside_integration_root(self):
        location=self.base/"Vendor"/"7-Zip";location.mkdir(parents=True)
        (location/"7zFM.exe").write_bytes(b"mock")
        class Key:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Registry:
            HKEY_LOCAL_MACHINE=1;HKEY_CURRENT_USER=2
            def OpenKey(self,*args):return Key()
            def QueryInfoKey(self,key):return (1,0,0)
            def EnumKey(self,key,i):return "7-Zip"
            def QueryValueEx(self,key,name):
                values={"DisplayName":"7-Zip 26.05 (x64)","InstallLocation":str(location),
                        "UninstallString":str(location/"Uninstall.exe"),"DisplayVersion":"26.05"}
                return values[name],1
        detected=detect_sevenzip(winreg_module=Registry(),windows=True)
        self.assertEqual(detected.state,"installed")
        self.assertEqual(detected.exe,location/"7zFM.exe")
        self.assertFalse(self.root.exists())

    def test_C_external_selected_portable_is_visible_readonly(self):
        external=self.base/"Portable";external.mkdir()
        cli=external/"SRA-cli.exe";cli.write_bytes(b"external")
        plan=inspect_sra(self.root,selected=cli)
        self.assertEqual(plan.state,"unmanaged")
        self.assertEqual(discover_sra_executable(self.root,cli,external=False),cli)
        with self.assertRaises(ValueError):uninstall_managed_sra(self.root)
        self.assertEqual(cli.read_bytes(),b"external")

    def test_C_known_registry_portable_without_marker(self):
        external=self.base/"RegisteredPortable";external.mkdir()
        cli=external/"SRA-cli.exe";cli.write_bytes(b"external")
        with patch("install_manager._sra_registry_candidates",return_value=[external]):
            self.assertEqual(discover_sra_executable(self.root),cli)

    def test_D_unknown_legacy_marker_is_not_management_authority(self):
        legacy=self.root/"sra";legacy.mkdir(parents=True)
        (legacy/"SRA-cli.exe").write_bytes(b"legacy")
        marker={"module":"sra","edition":"Community","cli":"SRA-cli.exe","files":[
            {"path":"SRA-cli.exe","sha256":hashlib.sha256(b"legacy").hexdigest()}]}
        (legacy/MARKER).write_text(json.dumps(marker),encoding="utf-8")
        fallback=self.base/"D"/"AutoFusion"/"Community"/"fusion"
        self.assertEqual(default_root(candidates=[self.root,fallback]),fallback)
        self.assertEqual(module_executable(self.root,"sra"),legacy/"SRA-cli.exe")
        self.assertEqual(inspect_sra(self.root).state,"unmanaged")
        with self.assertRaises(ValueError):uninstall_managed_sra(self.root)
        self.assertEqual((legacy/"SRA-cli.exe").read_bytes(),b"legacy")
        # External discovery must not block a new installation in another folder.
        claim_empty_root(fallback)
        extract_sra_safely(self.archive(),fallback/"sra")
        self.assertEqual(inspect_sra(fallback).state,"managed")
        self.assertTrue((legacy/MARKER).exists())

    def test_D_copied_receipt_marker_does_not_grant_other_path(self):
        self.install()
        copy=self.base/"copy"
        shutil.copytree(self.root/"sra",copy/"sra")
        self.assertEqual(inspect_sra(copy).state,"unmanaged")
        with self.assertRaises(ValueError):uninstall_managed_sra(copy)

    def test_D_personal_profile_is_not_read(self):
        personal=self.base/"profile"/"AutoFusionPersonal";personal.mkdir(parents=True)
        original=b'{"install_root":"DO_NOT_USE"}'
        (personal/"settings.json").write_bytes(original)
        self.assertEqual(core.load_settings().install_root,"")
        core.save_settings(core.Settings(install_root=str(self.root)))
        self.assertEqual((personal/"settings.json").read_bytes(),original)

    def test_D_foreign_cache_is_not_deleted(self):
        cache=self.root/"_downloads"/"ww";cache.mkdir(parents=True)
        file=cache/"ok-ww-win32-China-setup.exe";file.write_bytes(b"legacy")
        with self.assertRaises(ValueError):clean_module_cache(self.root,"ww")
        with self.assertRaises(ValueError):claim_empty_root(self.root)
        self.assertTrue(file.exists())

    def test_E_missing_drive_candidate_unwritable_falls_back(self):
        first=self.base/"D"/"AutoFusion"/"fusion"
        second=self.base/"profile"/"AutoFusionCommunity"/"fusion"
        def access(p,mode):return not str(p).startswith(str(self.base/"D"))
        # Simulate an unavailable D ancestor independently of the developer PC.
        (self.base/"D").mkdir()
        with patch("install_manager.os.access",side_effect=access):
            self.assertEqual(default_root(candidates=[first,second]),second)

    def test_E_file_conflict_and_all_unusable_prompt_selection(self):
        file=self.base/"file";file.write_text("keep")
        self.assertEqual(default_root(candidates=[file,self.root]),self.root)
        with patch("install_manager.os.access",return_value=False):
            with self.assertRaisesRegex(ValueError,"选择"):default_root(candidates=[self.root])

    def test_F_fresh_process_reads_settings_and_trusted_record(self):
        self.install()
        core.save_settings(core.Settings(install_root=str(self.root)))
        code="from core import load_settings; from pathlib import Path; from lifecycle import inspect_sra; print(inspect_sra(Path(load_settings().install_root)).state)"
        proc=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                            env=os.environ.copy(),capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(proc.stdout.strip(),"managed")

    def test_G_safe_uninstall_preserves_changed_and_user_files(self):
        self.install()
        folder=self.root/"sra"/"version"
        (folder/"SRA.exe").write_bytes(b"user-modified")
        (folder/"user.json").write_bytes(b"personal")
        removed,kept=uninstall_managed_sra(self.root)
        self.assertEqual((removed,kept),(2,1))
        self.assertEqual((folder/"SRA.exe").read_bytes(),b"user-modified")
        self.assertEqual((folder/"user.json").read_bytes(),b"personal")
        self.assertTrue(trusted_install(self.root/"sra"))

    def test_G_manifest_tamper_never_partially_deletes(self):
        self.install()
        marker=self.root/"sra"/MARKER
        meta=json.loads(marker.read_text(encoding="utf-8"))
        meta["files"].append({"path":"../outside","sha256":"0"*64})
        marker.write_text(json.dumps(meta),encoding="utf-8")
        record_install(self.root/"sra")  # Simulate corruption even with a matching local receipt.
        with self.assertRaises(ValueError):uninstall_managed_sra(self.root)
        self.assertTrue((self.root/"sra"/"version"/"SRA.exe").is_file())

    def test_discovery_works_without_writable_install_root_or_valid_saved_path(self):
        import queue
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app import App
        from lifecycle import PortablePlan, WinModuleState
        from catalog_adapter import Detected
        exe=self.base/"ok-ww.exe";exe.write_bytes(b"mock")
        fake=SimpleNamespace(_validated_root=Mock(side_effect=ValueError("choose directory")),
            _log=Mock(),_scan_generation=0,events=queue.Queue(),
            ww_path=SimpleNamespace(get=lambda:str(self.base/"stale.exe")),
            sra_path=SimpleNamespace(get=lambda:""))
        with patch("app.inspect_sra",return_value=PortablePlan("absent",self.root)), \
             patch("app.detect_ww_exe",return_value=exe) as discover, \
             patch("app.inspect_ww",return_value=WinModuleState("installed",exe,(),"found")), \
             patch("app.detect_sevenzip",return_value=Detected("installed",exe)):
            App._schedule_status_scan(fake,quiet=True)
            event,result=fake.events.get(timeout=5)
        self.assertEqual(event,"status_scan_result")
        self.assertEqual(result[3].state,"installed")
        self.assertEqual(result[5].state,"installed")
        discover.assert_called_once()

    def test_header_is_community(self):
        from app import App
        with patch("app.default_root",return_value=self.root):
            app=App()
        try:
            def texts(w):
                result=[]
                try:result.append(str(w.cget("text")))
                except Exception:pass
                for c in w.winfo_children():result.extend(texts(c))
                return result
            labels=texts(app)
            self.assertTrue(any("COMMUNITY" in text for text in labels))
            self.assertFalse(any("PERSONAL" in text for text in labels))
        finally:app.destroy()

if __name__=="__main__":unittest.main()
