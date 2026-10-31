"""Offline regressions confined to this checkout and its temporary profile."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

root = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
temporary_root = root / ".test-tmp"
temporary_root.mkdir(exist_ok=True)
if not temporary_root.resolve().is_relative_to(root):
    raise SystemExit("Temporary directory must stay inside the checkout")
with tempfile.TemporaryDirectory(prefix="profile-", dir=temporary_root) as profile:
    for key in ("LOCALAPPDATA", "HOME", "USERPROFILE", "TEMP", "TMP"):
        os.environ[key] = profile
    tempfile.tempdir = profile
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
