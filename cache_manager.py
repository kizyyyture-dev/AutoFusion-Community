"""Conservative, per-module download cache management.

Never scans or deletes arbitrary directories: only known official archive filenames
inside a regular AutoFusion _downloads/<module> subdirectory. Supports caches
created by v0.2.2, before this module existed. No registry edits.
"""
from __future__ import annotations

import re
import zipfile
from community_storage import root_owned, linked
from dataclasses import dataclass
from pathlib import Path

SRA_PATTERN = re.compile(r'StarRailAssistant_v(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?\.zip', re.I)
WW_NAME = 'ok-ww-win32-china-setup.exe'


@dataclass(frozen=True)
class CachePlan:
    module: str
    directory: Path
    files: tuple[Path, ...]
    total_bytes: int
    unrecognized_files: int = 0
    unsafe: bool = False
    reason: str = ''


def cache_plan(root: Path, module: str) -> CachePlan:
    if module not in ('sra', 'ww'):
        raise ValueError('未知模块')
    root = Path(root)
    cache_root = root / '_downloads'
    directory = cache_root / module
    # Never traverse an alias to another location, even if the child directory is regular.
    for candidate in (root, cache_root, directory):
        if candidate.is_symlink():
            return CachePlan(module, directory, (), 0, unsafe=True,
                             reason='缓存路径存在符号链接，拒绝自动操作。')
    if not directory.exists():
        return CachePlan(module, directory, (), 0)
    if not directory.is_dir():
        return CachePlan(module, directory, (), 0, unsafe=True,
                         reason='缓存路径不是文件夹，拒绝自动操作。')
    files, count_other, total = [], 0, 0
    try:
        for child in directory.iterdir():
            known = (bool(SRA_PATTERN.fullmatch(child.name)) if module == 'sra'
                     else child.name.casefold() == WW_NAME)
            if child.is_symlink():
                # An unrecognized symlink is also left untouched.
                if known:
                    return CachePlan(module, directory, (), 0, unsafe=True,
                                     reason='官方安装包路径是符号链接，拒绝自动清理。')
                count_other += 1
            elif not child.is_file() or not known:
                count_other += 1
            else:
                size = child.stat().st_size
                if size > 2 * 1024**3:
                    return CachePlan(module, directory, (), 0, unsafe=True,
                                     reason='缓存文件过大，拒绝自动操作。')
                total += size
                files.append(child)
    except OSError as exc:
        return CachePlan(module, directory, (), 0, unsafe=True, reason=str(exc))
    return CachePlan(module, directory, tuple(sorted(files)), total, count_other)


def clean_module_cache(root: Path, module: str) -> tuple[int, int]:
    """Clean official-release-named files only; preserve unknown/user files.

    Existing v0.2.2 caches have no separate download receipt, so provenance
    is based on the dedicated AutoFusion cache path + exact upstream name.
    """
    plan = cache_plan(root, module)
    if plan.unsafe:
        raise ValueError('安装缓存不安全，未删除任何文件：' + plan.reason)
    if plan.files and not root_owned(root):
        raise ValueError("该目录没有 Community 创建的缓存归属记录，不清理来源未确认的安装缓存")
    removed, bytes_deleted = 0, 0
    for path in plan.files:
        # Re-check just before removal. Never follow a newly swapped link.
        if not path.exists() or path.is_symlink() or not path.is_file():
            continue
        if path.parent != plan.directory:
            continue
        size = path.stat().st_size
        path.unlink()
        removed += 1
        bytes_deleted += size
    try:
        plan.directory.rmdir()  # only empty directory; never remove unknown files
    except OSError:
        pass
    return removed, bytes_deleted


def offline_package(root: Path, module: str) -> Path | None:
    """Select a single known cached official release; refuse ambiguous caches.

    This does not establish a cryptographic publisher signature; archive extraction
    is still subject to extract_sra_safely's ZIP validations.
    """
    plan = cache_plan(root, module)
    if plan.unsafe:
        raise ValueError(plan.reason)
    if not plan.files:
        return None
    if module == 'ww':
        return plan.files[0] if len(plan.files) == 1 else None
    candidates = []
    for file in plan.files:
        match = SRA_PATTERN.fullmatch(file.name)
        assert match
        if zipfile.is_zipfile(file):
            numbers = tuple(int(x or 0) for x in match.groups())
            candidates.append((numbers, file))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]
