import json
import tempfile
import unittest
from pathlib import Path
from core import Settings, build_command, selected_modules, save_settings, load_settings, Runner


class TestCore(unittest.TestCase):
    def test_ww_command(self):
        s = Settings(ww_exe=r"D:\Tools\ok-ww.exe", ww_task=2)
        cmd, wd = build_command("ww", s, check_exe=False)
        self.assertEqual(cmd[-3:], ["-t", "2", "-e"])

    def test_sra_command(self):
        s = Settings(sra_exe=r"D:\SRA\SRA-cli.exe", sra_config="Default")
        cmd, wd = build_command("sra", s, check_exe=False)
        self.assertEqual(cmd[-5:], ["-e", "run", "Default", "+", "exit"])

    def test_bad_sra_config(self):
        with self.assertRaises(ValueError):
            build_command("sra", Settings(sra_exe="x.exe", sra_config="Default & del *"), check_exe=False)

    def test_live_mode_requires_exe_path(self):
        s = Settings(dry_run=False, ww_exe="", sra_exe="")
        with self.assertRaisesRegex(ValueError, "程序路径"):
            Runner(lambda *_: None).start(s)

    def test_dry_run_validates_task_configuration(self):
        s = Settings(ww_exe="", sra_exe="", ww_task=0, dry_run=True)
        with self.assertRaisesRegex(ValueError, "任务序号"):
            Runner(lambda *_: None).start(s)

    def test_snapshot_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            s = Settings(ww_exe="xxx", sra_config="自定义", enable_sra=False)
            save_settings(s, target)
            self.assertEqual(load_settings(target), s)

    def test_dry_run_two_tasks(self):
        import time
        events=[]
        runner=Runner(lambda l,t: events.append((l,t)))
        s=Settings(ww_exe="", sra_exe="", dry_run=True)
        runner.start(s)
        while runner.running:
            time.sleep(.05)
        self.assertEqual([x[0] for x in events].count("success"), 2)
        self.assertTrue(any(x[0]=="complete" for x in events))


if __name__ == "__main__":
    unittest.main()
