"""Conservative, read-only install-state inspection and uninstall planning.

Read-only functions never modify modules, registry, or Windows installation data.
Actual deletion is delegated to the existing manifest-checked portable uninstaller;
installer-type modules use their own official uninstaller.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from install_manager import CHUNK, MARKER


@dataclass(frozen=True)
class PortablePlan:
    state: str
    location: Path
    original_files: int = 0
    removable: int = 0
    changed: int = 0
    missing: int = 0
    user_files: int = 0
    unsafe: int = 0
    bytes_reclaimable: int = 0
    examples: tuple[str, ...] = ()
    message: str = ''


@dataclass(frozen=True)
class WindowsInstall:
    name: str
    version: str
    location: str
    uninstall_command: str
    key_id: str


@dataclass(frozen=True)
class WinModuleState:
    state: str
    executable: Optional[Path]
    records: tuple[WindowsInstall, ...]
    message: str


def _valid_manifest_path(folder: Path, entry: dict) -> Path:
    if not isinstance(entry, dict):
        raise ValueError('清单文件条目格式错误')
    name = entry.get('path')
    digest = entry.get('sha256')
    if (not isinstance(name, str) or not name or name.startswith(('/', '\\'))
            or ':' in name or '\\' in name or '\x00' in name
            or not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest)):
        raise ValueError('安装清单包含不安全文件条目')
    rel = PurePosixPath(name)
    if any(part in ('', '..', '.') for part in rel.parts) or rel.as_posix() == MARKER:
        raise ValueError('安装清单包含不安全相对路径')
    return folder.joinpath(*rel.parts)


def _inside_without_links(base: Path, target: Path) -> bool:
    if not target.resolve().is_relative_to(base.resolve()):
        return False
    current = target
    while current != base:
        if current.is_symlink():
            return False
        if current.parent == current:
            return False
        current = current.parent
    return not base.is_symlink()


def inspect_sra(root: Path) -> PortablePlan:
    """Read-only plan for an already installed V0.2.2 SRA ZIP.

    Does not follow symlinks or classify untracked user files as deletable.
    """
    folder = root / 'sra'
    if not folder.exists() and not folder.is_symlink():
        return PortablePlan('absent', folder, message='未发现由 AutoFusion 管理的 SRA 目录。')
    if not folder.is_dir() or folder.is_symlink():
        return PortablePlan('unsafe', folder, message='SRA 目录不是普通文件夹，拒绝自动卸载。')
    marker = folder / MARKER
    if marker.is_symlink() or not marker.is_file():
        return PortablePlan('unmanaged', folder, message='找不到可信的 AutoFusion 安装清单，不会自动删除。')
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        entries = data.get('files')
        if data.get('module') != 'sra' or not isinstance(entries, list) or not entries or len(entries) > 40000:
            raise ValueError('无效的 SRA 文件清单')
        planned = []
        seen = set()
        for entry in entries:
            target = _valid_manifest_path(folder, entry)
            relative = target.relative_to(folder).as_posix().casefold()
            if relative in seen:
                raise ValueError('清单中包含重复路径')
            seen.add(relative)
            planned.append((target, entry['sha256'].lower()))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return PortablePlan('unmanaged', folder, message=f'安装清单损坏或不安全，拒绝删除：{exc}')

    removable = changed = missing = unsafe = bytes_ok = 0
    examples = []
    for target, digest in planned:
        if not target.exists() and not target.is_symlink():
            missing += 1
        elif not _inside_without_links(folder, target) or not target.is_file():
            unsafe += 1
        else:
            try:
                h = hashlib.sha256()
                with target.open('rb') as file:
                    for buf in iter(lambda: file.read(CHUNK), b''):
                        h.update(buf)
                if h.hexdigest() == digest:
                    removable += 1
                    bytes_ok += target.stat().st_size
                else:
                    changed += 1
            except OSError:
                unsafe += 1
        if len(examples) < 5 and target.name.lower().endswith('.exe'):
            examples.append(target.relative_to(folder).as_posix())
    user_count = 0
    try:
        for directory, dirs, files in os.walk(folder, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for filename in files:
                p = Path(directory) / filename
                if p == marker:
                    continue
                if p.relative_to(folder).as_posix().casefold() not in seen:
                    user_count += 1
    except OSError:
        unsafe += 1
    return PortablePlan('managed', folder, len(planned), removable, changed, missing,
                        user_count, unsafe, bytes_ok, tuple(examples),
                        '已完成只读扫描。仅未修改的原始安装文件可以由 Core 自动清理。')


def _looks_like_ww(display_name: str) -> bool:
    name = display_name.casefold().strip()
    return bool(re.search(r'(?<![a-z0-9])ok[ -]?ww(?=$|[\s:（(\[])', name) or
                'ok-wuthering-waves' in name)


def find_ww_registry_entries() -> tuple[WindowsInstall, ...]:
    """Read only relevant Windows Add/Remove Programs entries. No registry edits."""
    if sys.platform != 'win32':
        return ()
    import winreg

    subroot = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    seen = set()
    matches = []
    for hive, hive_name in ((winreg.HKEY_CURRENT_USER, 'HKCU'), (winreg.HKEY_LOCAL_MACHINE, 'HKLM')):
        for view, view_name in ((winreg.KEY_WOW64_64KEY, '64'), (winreg.KEY_WOW64_32KEY, '32')):
            try:
                handle = winreg.OpenKey(hive, subroot, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with handle:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(handle, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        subkey = winreg.OpenKey(handle, name, 0, winreg.KEY_READ | view)
                        with subkey:
                            def read(field):
                                try:
                                    value, _ = winreg.QueryValueEx(subkey, field)
                                    return value.strip() if isinstance(value, str) else ''
                                except OSError:
                                    return ''
                            display_name = read('DisplayName')
                            if not _looks_like_ww(display_name):
                                continue
                            candidate = WindowsInstall(display_name, read('DisplayVersion'),
                                                       read('InstallLocation').strip('" '),
                                                       read('UninstallString'),
                                                       f'{hive_name}/{view_name}/{name}')
                            # Same key visible in both registry views on 32-bit systems.
                            fingerprint = (hive_name, name, candidate.uninstall_command)
                            if fingerprint not in seen:
                                seen.add(fingerprint)
                                matches.append(candidate)
                    except OSError:
                        continue
    return tuple(matches)


def inspect_ww(executable: Optional[Path] = None, *, records: Optional[tuple[WindowsInstall, ...]] = None) -> WinModuleState:
    records = find_ww_registry_entries() if records is None else tuple(records)
    if executable and executable.name.casefold() != 'ok-ww.exe':
        executable = None
    file_found = bool(executable and executable.is_file() and not executable.is_symlink())
    if not file_found:
        # Fall back to registered installed locations (still read-only).
        for item in records:
            if item.location:
                p = Path(item.location) / 'ok-ww.exe'
                if p.name.casefold() == 'ok-ww.exe' and p.is_file() and not p.is_symlink():
                    executable = p
                    file_found = True
                    break
    if file_found and records:
        return WinModuleState('installed', executable, records, '检测到 OK-WW 程序和 Windows 安装登记。')
    if records and not file_found:
        return WinModuleState('stale', None, records, '发现 OK-WW 安装登记，但程序文件不存在；可能曾被手动删除。')
    if file_found:
        return WinModuleState('unregistered', executable, records, '发现 OK-WW 可执行文件，但没有找到相应 Windows 卸载登记。')
    return WinModuleState('absent', None, records, '暂未检测到 OK-WW 程序或安装登记。')


def plan_text_sra(plan: PortablePlan) -> str:
    if plan.state != 'managed':
        return f'【SRA 状态】{plan.state}\n位置：{plan.location}\n{plan.message}\n没有执行任何删除。'
    return (f'【SRA 便携版安全卸载 · 只读预览】\n目录：{plan.location}\n'
            f'清单记录：{plan.original_files} 个文件\n'
            f'可删除的未修改原版文件：{plan.removable} 个（约 {plan.bytes_reclaimable / 1024**2:.1f} MiB）\n'
            f'已修改文件：{plan.changed} 个（保留）\n'
            f'原版文件已缺失：{plan.missing} 个\n'
            f'新增 / 用户文件：{plan.user_files} 个（保留）\n'
            f'不安全或不可访问项：{plan.unsafe} 个（保留）\n\n'
            '执行卸载仅删除清单记录且哈希未变化的原始文件，绝不直接清空整个目录。\n'
            '下载缓存、用户配置及共享组件不在本次清理范围内。')


def plan_text_ww(state: WinModuleState) -> str:
    lines = [f'【OK-WW 官方安装状态 · 只读预览】', state.message]
    if state.executable:
        lines.append('已检测程序：' + str(state.executable))
    if state.records:
        for i, item in enumerate(state.records, 1):
            lines.append(f'\n登记 {i}：{item.name} {item.version}'.rstrip())
            lines.append(f'安装位置：{item.location or "未登记"}')
            lines.append(f'官方卸载命令：{item.uninstall_command or "未登记"}')
    else:
        lines.append('未找到可信的官方卸载命令。可通过 Windows「已安装的应用」查找。')
    lines.append('\nAutoFusion 不会直接删除 OK-WW 目录或注册表；将由官方卸载器处理。')
    return '\n'.join(lines)
