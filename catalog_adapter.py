"""Strict read-only module catalog and official installer integration.

No arbitrary JSON code execution, no recursive disk scanning and no silent uninstall.
7-Zip is an intentionally PINNED Windows x64 pilot to verify a non-GitHub
SourceForge release; update its hash + version after verifying a new release.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CATALOG_FILE = Path(__file__).with_name('modules_catalog.json')
REGISTERED_ADAPTERS = {'ww':'legacy_ww', 'sra':'legacy_sra', 'sevenzip':'official_sevenzip_x64'}
MAX_CATALOG_BYTES = 64 * 1024
MAX_7ZIP_BYTES = 30 * 1024 * 1024


def load_catalog(path: Path = CATALOG_FILE) -> dict:
    if path.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError('模块目录过大')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or raw.get('schema') != 1 or not isinstance(raw.get('modules'), list):
        raise ValueError('模块目录结构不受支持')
    found = {}
    for item in raw['modules']:
        if not isinstance(item, dict) or item.get('id') not in REGISTERED_ADAPTERS:
            raise ValueError('模块目录包含未经适配的项目')
        key = item['id']
        if key in found or item.get('adapter') != REGISTERED_ADAPTERS[key]:
            raise ValueError('模块目录有重复或不匹配的适配器')
        for prop in ('title','subtitle','type','kind','description','website'):
            val = item.get(prop)
            if not isinstance(val, str) or not val.strip() or len(val) > 400:
                raise ValueError('模块描述字段无效：'+prop)
        if urlsplit(item['website']).scheme != 'https':
            raise ValueError('项目主页必须为 HTTPS')
        if key == 'sevenzip':
            check_sevenzip_metadata(item)
        found[key] = item
    if not {'ww','sra','sevenzip'}.issubset(found):
        raise ValueError('模块目录缺少已注册适配器')
    return found


def check_sevenzip_metadata(item: dict) -> None:
    # Keep source and checksum fixed to verified pilot asset. Editing JSON cannot
    # change installable code origin without a reviewed application update.
    if item.get('adapter') != 'official_sevenzip_x64':
        raise ValueError('7-Zip 适配器无效')
    if item.get('version') != '26.05' or item.get('asset') != '7z2605-x64.exe':
        raise ValueError('7-Zip 测试版本未审核')
    if item.get('download_url') != ('https://sourceforge.net/projects/sevenzip/files/'
                                   '7-Zip/26.05/7z2605-x64.exe/download'):
        raise ValueError('7-Zip 测试来源未审核')
    if item.get('sha256') != 'fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0':
        raise ValueError('7-Zip 校验信息未审核')


@dataclass(frozen=True)
class Detected:
    state: str  # absent / installed / stale / unregistered
    exe: Path | None = None
    uninstall: str = ''
    version: str = ''
    location: str = ''


def detect_sevenzip(*, winreg_module=None, windows=None) -> Detected:
    """Look up precise Windows uninstall records, never enumerate the whole disk."""
    if windows is None:
        windows = sys.platform == 'win32'
    if not windows:
        return Detected('absent')
    if winreg_module is None:
        import winreg as winreg_module
    wr = winreg_module
    records = []
    keys = (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
    for hive in (wr.HKEY_LOCAL_MACHINE, wr.HKEY_CURRENT_USER):
        for key in keys:
            try:
                with wr.OpenKey(hive, key) as root:
                    count = wr.QueryInfoKey(root)[0]
                    for i in range(min(count, 5000)):
                        try:
                            sub = wr.EnumKey(root, i)
                            with wr.OpenKey(root, sub) as node:
                                def value(field):
                                    try: return str(wr.QueryValueEx(node, field)[0]).strip()
                                    except OSError: return ''
                                name = value('DisplayName')
                                # Match the product EXACTLY. Avoid 7-Zip-related add-ons.
                                if not re.fullmatch(r'7-Zip(?:\s+\d+(?:\.\d+)*(?:\s*\(x64\))?)?', name, re.I):
                                    continue
                                records.append((value('InstallLocation'), value('UninstallString'), value('DisplayVersion')))
                        except OSError:
                            continue
            except OSError:
                continue
    for location, uninstall, version in records:
        if location:
            exe = Path(os.path.expandvars(location.strip(' "'))) / '7zFM.exe'
            if exe.is_file():
                return Detected('installed', exe, uninstall, version, location)
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramW6432')):
        if base:
            exe = Path(base)/'7-Zip'/'7zFM.exe'
            if exe.is_file():
                return Detected('installed' if records else 'unregistered', exe,
                                records[0][1] if records else '',
                                records[0][2] if records else '',
                                str(exe.parent))
    if records:
        return Detected('stale', None, records[0][1], records[0][2], records[0][0])
    return Detected('absent')


def compare_release_versions(installed: str, available: str) -> int | None:
    """Compare numeric release versions: -1 older, 0 same, 1 newer, None unknown.

    Never guess for nonstandard registry strings, since Windows DisplayVersion
    may be vendor-specific.
    """
    def parse(value):
        value = value.strip()
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}', value):
            return None
        return tuple(int(part) for part in value.split('.'))
    a, b = parse(installed), parse(available)
    if a is None or b is None:
        return None
    length = max(len(a), len(b))
    a, b = a + (0,) * (length - len(a)), b + (0,) * (length - len(b))
    return (a > b) - (a < b)


def sevenzip_update_message(record: Detected, available: str) -> tuple[str, str]:
    """Return a status code and readable message, no network or file changes."""
    if record.state == 'absent':
        return ('absent', f'未安装 · 可下载已审核版本 {available}')
    if record.state == 'stale':
        return ('repair', '安装登记异常 · 请先修复')
    if record.state not in ('installed', 'unregistered'):
        return ('unknown', '安装状态待确认')
    relation = compare_release_versions(record.version, available)
    if relation == -1:
        return ('update', f'有可用更新 · {record.version} → {available}')
    if relation == 0:
        return ('current', f'已安装 · 与收录版本 {available} 一致')
    if relation == 1:
        return ('newer', f'已安装 {record.version} · 高于收录版本 {available}')
    return ('unknown', f'已安装 · 版本{record.version or "未知"}，无法自动比较')


def sevenzip_arch_supported() -> bool:
    return sys.platform == 'win32' and platform.machine().lower() in ('amd64','x86_64')


def sevenzip_package_target(root: Path, item: dict) -> Path:
    check_sevenzip_metadata(item)
    # Owned cache is intentionally isolated from OK-WW and SRA.
    return root / '_downloads' / 'sevenzip' / item['asset']


def download_sevenzip(root: Path, item: dict, progress=None, *, opener=None) -> tuple[Path,str]:
    """Download vetted official release, enforce size and SHA-256 before promotion.

    The default downloader uses HTTPS and follows the known SourceForge mirror redirects.
    No EXE is ever executed from this method.
    """
    if not sevenzip_arch_supported():
        raise ValueError('此测试适配器仅支持 Windows x64；请使用 7-Zip 官网选择正确架构')
    check_sevenzip_metadata(item)
    target = sevenzip_package_target(root, item)
    if target.exists():
        with target.open('rb') as cached:
            h=hashlib.file_digest(cached, 'sha256').hexdigest()
        if h == item['sha256']:
            return target,h
        raise ValueError('发现同名但哈希不匹配的缓存，未自动覆盖；请手动检查此文件')
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix(target.suffix+'.part')
    if temp.exists():
        raise ValueError('存在未完成的 7-Zip 下载临时文件，请检查或手动移除后重试')
    request=urllib.request.Request(item['download_url'],headers={'User-Agent':'AutoFusionCommunity/0.4.1'})
    open_request=opener or urllib.request.urlopen
    count=0
    digest=hashlib.sha256()
    try:
        with open_request(request,timeout=35) as reply:
            host=urlsplit(reply.geturl()).hostname or ''
            valid=(host=='sourceforge.net' or host.endswith('.sourceforge.net') or
                   host.endswith('.sf.net'))
            if not valid:
                raise ValueError('下载重定向到了未经允许的站点；请改用官方页面')
            with temp.open('xb') as output:
                while True:
                    block=reply.read(1024*256)
                    if not block: break
                    count += len(block)
                    if count>MAX_7ZIP_BYTES:
                        raise ValueError('文件超过 7-Zip 安装包的安全大小')
                    output.write(block)
                    digest.update(block)
                    if progress: progress(count)
        h=digest.hexdigest()
        if count<100000 or h != item['sha256']:
            raise ValueError('安装包 SHA-256 不匹配；已删除不可信下载数据')
        temp.replace(target)
        return target,h
    except Exception:
        try: temp.unlink(missing_ok=True)
        except OSError: pass
        raise


def uninstall_path_from_record(record: Detected) -> Path | None:
    """Only allow opening an uninstall EXE directly INSIDE the matched 7-Zip install location."""
    if not record.uninstall or not record.location:
        return None
    cmd = record.uninstall.strip()
    # Windows quoted path or simple nonquoted path; reject arguments and shell strings.
    if cmd.startswith('"'):
        ix=cmd.find('"',1)
        raw=cmd[1:ix] if ix>1 and not cmd[ix+1:].strip() else ''
    else:
        raw=cmd if not any(c in cmd for c in '&|<>%') else ''
    try:
        exe=Path(os.path.expandvars(raw))
        expected=Path(os.path.expandvars(record.location.strip(' "'))).resolve()
        if exe.is_file() and exe.suffix.lower()=='.exe' and exe.name.lower()=='uninstall.exe' and exe.parent.resolve()==expected:
            return exe
    except (OSError,ValueError):
        pass
    return None
