"""Read-only, bounded Windows folder hints for creative applications.

Only predefined vendor locations / Windows uninstall *registration* are inspected.
Never deletes, changes, follows links for size scans, or searches arbitrary drives.
Finding a folder does not mean the program is installed, and absence is not proof
that data does not exist elsewhere. No subprocesses or network activity.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class FolderHint:
    app: str
    role: str
    path: Path
    source: str
    shared: bool = False
    note: str = ''
    exists: bool = False


APPS = {'ae': 'After Effects', 'ps': 'Photoshop', 'cad': 'AutoCAD'}
DOCS = {
    'ae': 'https://helpx.adobe.com/after-effects/desktop/get-started/preferences-and-settings/preferences.html',
    'ps': 'https://helpx.adobe.com/photoshop/desktop/troubleshoot/tool-feature-issues/photoshop-plug-in-folder-locations.html',
    'cad': 'https://help.autodesk.com/cloudhelp/2026/CHS/AutoCAD-Customization/files/GUID-5E50A846-C80B-4FFD-8DD3-C20B22098008.htm',
}
MAX_MATCHES = 25


def _candidate_roots(env: Mapping[str, str]) -> dict[str, Path | None]:
    """No guess about current OS drive when a Windows env var is absent."""
    return {key: Path(env[key]) for key in ('ProgramFiles', 'ProgramFiles(x86)',
            'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'ProgramData') if env.get(key)}


def _direct_children(parent: Path, prefix: str) -> list[Path]:
    """One bounded directory enumeration, not a recursive filesystem search."""
    if not parent.is_dir():
        return []
    try:
        names = sorted((p for p in parent.iterdir() if p.name.casefold().startswith(prefix.casefold())
                        and p.is_dir() and not p.is_symlink()), key=lambda p:p.name.casefold())
    except (PermissionError, OSError):
        return []
    return names[:MAX_MATCHES]


def _registry_locations(app: str) -> list[Path]:
    """Only official Windows uninstall records. No registry writes; no full-disk search."""
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    prefix = {'ae':'Adobe After Effects', 'ps':'Adobe Photoshop', 'cad':'AutoCAD'}[app]
    uninstall = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    result = []
    seen = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for flag in (getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0)):
            try:
                root = winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | flag)
            except OSError:
                continue
            with root:
                i = 0
                while i < 1500 and len(result) < MAX_MATCHES:
                    try:
                        subname = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, subname) as sub:
                            name = str(winreg.QueryValueEx(sub, 'DisplayName')[0])
                            if not name.casefold().startswith(prefix.casefold()):
                                continue
                            loc = winreg.QueryValueEx(sub, 'InstallLocation')[0]
                            if not isinstance(loc, str) or not loc.strip():
                                continue
                            # Strip only wrapping double-quotes; never execute registry content.
                            loc = os.path.expandvars(loc.strip().strip('"'))
                            p = Path(loc)
                            if not p.is_absolute() or not p.is_dir():
                                continue
                            key = os.path.normcase(str(p))
                            if key not in seen:
                                result.append(p); seen.add(key)
                    except (OSError, ValueError, TypeError):
                        pass
    return result


def scan_known_locations(app: str, env: Mapping[str, str] | None = None, *,
                         include_registry: bool = True) -> list[FolderHint]:
    """Read-only folder enumeration; result categories express uncertainty.

    Does not open any app, estimate recursive disk usage, or make unsafe cleanup
    suggestions. Missing paths are skipped except the shared-folder hints.
    """
    if app not in APPS:
        raise ValueError('目前目录诊断仅支持 After Effects / Photoshop / AutoCAD')
    environment = os.environ if env is None else env
    roots = _candidate_roots(environment)
    found: list[FolderHint] = []
    dedup: set[str] = set()

    def add(role: str, path: Path, source: str, *, shared=False, note='', show_missing=False):
        if not path.is_absolute():
            return
        key = os.path.normcase(str(path)).casefold()
        if key in dedup:
            return
        try:
            exists = path.is_dir()
        except (PermissionError, OSError):
            exists = False
        if not exists and not show_missing:
            return
        dedup.add(key)
        found.append(FolderHint(APPS[app], role, path, source, shared, note, exists))

    if app == 'cad':
        # Autodesk documents these plugin and support locations; do not enumerate
        # subfolders recursively or treat shared content as disposable.
        for pf in ('ProgramFiles', 'ProgramFiles(x86)'):
            root = roots.get(pf)
            if root:
                for folder in _direct_children(root/'Autodesk', 'AutoCAD'):
                    add('程序目录（候选）',folder,'Autodesk 常见安装位置',
                        note='AutoCAD 版本可能不同，目录存在不代表已经安装')
                add('共享插件（ApplicationPlugins）',root/'Autodesk'/'ApplicationPlugins',
                    'Autodesk 官方插件搜索目录',shared=True,note='其他 Autodesk 软件也可能使用该位置，不可直接清理')
        for parent_key in ('APPDATA','LOCALAPPDATA'):
            root = roots.get(parent_key)
            if root:
                for folder in _direct_children(root/'Autodesk','AutoCAD'):
                    add('用户配置/支持目录',folder,'Autodesk 用户数据候选',
                        note='可能包含 AutoLISP、插件和个人配置；不能作为卸载残留删除')
        roaming=roots.get('APPDATA')
        if roaming:
            add('共享插件（用户级）',roaming/'Autodesk'/'ApplicationPlugins',
                'Autodesk 官方用户插件目录',shared=True, note='可由其他 Autodesk 产品加载；不删')
        programdata=roots.get('ProgramData')
        if programdata:
            add('共享插件（系统级）',programdata/'Autodesk'/'ApplicationPlugins',
                'Autodesk 系统级插件候选',shared=True, note='系统共享目录，不能直接删除')
        if include_registry and env is None:
            for loc in _registry_locations('cad'):
                add('Windows 安装登记目录',loc,'Windows 卸载登记',note='不自动操作原版卸载程序')
        return found

    prefix = {'ae':'Adobe After Effects', 'ps':'Adobe Photoshop'}[app]
    for pf in ('ProgramFiles', 'ProgramFiles(x86)'):
        root = roots.get(pf)
        if not root:
            continue
        vendor = root / 'Adobe'
        for folder in _direct_children(vendor, prefix):
            add('程序目录（候选）', folder, 'Adobe 常见安装位置',
                note='仅凭目录存在不能确认软件当前仍已安装')
            if app == 'ae':
                add('版本专属插件', folder / 'Support Files' / 'Plug-ins', 'Adobe 默认插件目录',
                    note='可能包括原厂插件及第三方插件，勿直接删除')
            else:
                add('版本专属插件', folder / 'Plug-ins', 'Adobe 官方插件目录',
                    note='可能包括用户插件，勿直接删除')
        if app == 'ae':
            add('共享插件（MediaCore）',root/'Adobe'/'Common'/'Plug-ins'/'7.0'/'MediaCore',
                'Adobe 共享插件目录',shared=True, note='可能同时被 Premiere Pro 等程序使用；不可直接清理')
        else:
            add('共享插件（Creative Cloud）',root/'Common Files'/'Adobe'/'Plug-Ins'/'CC',
                'Adobe 官方共享插件目录',shared=True,note='可能被多个 Adobe 程序使用；不可直接清理')

    if include_registry and env is None:
        for loc in _registry_locations(app):
            add('Windows 安装登记目录', loc, 'Windows 卸载登记',
                note='只能说明有安装记录及目录，具体安装状态仍以原版管理器为准')

    roaming = roots.get('APPDATA')
    local = roots.get('LOCALAPPDATA')
    home = roots.get('USERPROFILE')
    if app == 'ae':
        if roaming:
            add('偏好设置',roaming/'Adobe'/'After Effects', 'Adobe 官方偏好设置位置',
                note='可能含快捷键、工作区等用户设置；勿删')
        if local:
            add('可能的本地数据',local/'Adobe'/'After Effects', '常见位置（非完整清单）',
                note='AE 磁盘缓存可能由用户另行指定路径；请在 AE「首选项→磁盘」确认')
        if home:
            add('文档与相关设置',home/'Documents'/'Adobe'/'After Effects',
                'Adobe 相关文档位置',note='可能含用户工程或预设，绝不可当作卸载残留清理')
    else:
        if roaming:
            for folder in _direct_children(roaming/'Adobe','Adobe Photoshop'):
                add('偏好设置', folder, 'Adobe 官方设置目录',
                    note='可能包含动作、笔刷、预设等重要用户内容')
        if local:
            for folder in _direct_children(local/'Adobe','Adobe Photoshop'):
                add('可能的本地数据', folder, '候选数据目录',
                    note='并非完整缓存清单；PS 暂存盘位置请在原版首选项查看')
    return found
