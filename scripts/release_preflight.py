"""Read-only review of the exact source tree intended for GitHub publication."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--require-license", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
root_files = {
    ".gitignore", ".gitattributes", "启动AutoFusion.bat", "README.md",
    "ARCHITECTURE.md", "CONTRIBUTING.md", "SECURITY.md", "REVIEW_REPORT.md",
    "LICENSE_POLICY.md", "LICENSE_CANDIDATE.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "requirements.txt", "requirements-build.txt",
    "modules_catalog.json", "update_source.json", "run_build_tests.py",
    "app.py", "core.py", "community_storage.py", "cache_manager.py", "catalog_adapter.py",
    "directory_diagnostics.py", "discovery.py", "discovery_cache.py",
    "install_manager.py", "lifecycle.py", "log_storage.py",
    "module_preferences.py", "offline_library.py", "update_manager.py",
    "updater_worker.py", "version.py",
}
scripts = {"scripts/build_windows.py", "scripts/release_preflight.py"}
patterns = [
    re.compile(r"github_pat_[A-Za-z0-9_]{15,}|gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"alipay|wechat|_EMBEDDED|收款码|支持开发者"),
]
excluded = {".git", "__pycache__", ".test-tmp", "build", "dist", ".venv", ".release-review"}
failures = []
manifest = {}
for path in sorted(root.rglob("*")):
    rel = path.relative_to(root)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        failures.append(f"Linked path: {rel}")
        continue
    if any(part in excluded for part in rel.parts):
        continue
    if not path.is_file():
        continue
    name = rel.as_posix()
    allowed = (
        (len(rel.parts) == 1 and name in root_files)
        or name in scripts
        or (len(rel.parts) == 2 and rel.parts[0] == "tests" and path.suffix == ".py")
        or (len(rel.parts) == 2 and rel.parts[0] == "licenses"
            and path.suffix in {".txt", ".terms"})
    )
    if not allowed:
        failures.append(f"Unexpected publication file: {name}")
        continue
    if path.stat().st_size > 1024 * 1024:
        failures.append(f"Unexpected large source file: {name}")
        continue
    text = path.read_text(encoding="utf-8-sig")
    if any(pattern.search(text) for pattern in (patterns[:2] if name == "scripts/release_preflight.py" else patterns)):
        failures.append(f"Review sensitive text: {name}")
    manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
if args.require_license and not (root / "LICENSE").exists():
    failures.append("Final LICENSE is missing; owner confirmation required")
print(json.dumps({"files": manifest, "failures": failures,
                  "license_status": "confirmed" if (root / "LICENSE").exists()
                  else "PENDING: confirm license before publishing"},
                 ensure_ascii=False, indent=2))
sys.exit(1 if failures else 0)
