"""Official-release installer for AutoFusion Community.

SRA: install vendor's portable ZIP into an exclusively owned module directory.
OK-WW: download the *vendor's installer* and give control to its installer UI.
No code from repositories is executed automatically during download/extraction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

REPOSITORIES = {"ww": "ok-oldking/ok-wuthering-waves", "sra": "Shasnow/StarRailAssistant"}
MAX_DOWNLOAD = 2 * 1024**3   # 2 GiB compressed
MAX_UNPACK = 5 * 1024**3     # 5 GiB uncompressed
MAX_MEMBERS = 40000
CHUNK = 1024 * 1024
MARKER = ".autofusion-managed.json"
Progress = Callable[[str], None]


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    version: str
    size: int
    release_url: str


def default_root() -> Path:
    from core import data_dir
    return data_dir() / "Modules"


def _allowed_host(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    return (p.scheme == 'https' and p.username is None and p.password is None and
            p.port in (None, 443) and
            (host in {'api.github.com', 'github.com'} or host.endswith('.githubusercontent.com')))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_host(newurl):
            raise ValueError('GitHub 返回了非预期的文件下载地址，已停止。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(SafeRedirect())


def choose_asset(module: str, release: dict) -> Asset:
    if module not in REPOSITORIES:
        raise ValueError('未知模块')
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise ValueError('GitHub 发布信息缺少 assets')
    matches = []
    for item in assets:
        name = str(item.get('name') or '')
        if module == 'sra':
            is_match = bool(re.fullmatch(r'StarRailAssistant_v\d+\.\d+\.\d+(?:\.\d+)?\.zip', name, re.I))
        else:
            is_match = name.lower() == 'ok-ww-win32-china-setup.exe'
        if is_match:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError('无法唯一识别官方完整发布包；请打开项目主页检查发布文件，不会下载源码包或精简包。')
    chosen = matches[0]
    url = str(chosen.get('browser_download_url') or '')
    if not _allowed_host(url) or urllib.parse.urlsplit(url).hostname != 'github.com':
        raise ValueError('发布包地址不属于官方 GitHub Release')
    size = chosen.get('size', 0)
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_DOWNLOAD:
        raise ValueError('发布包大小异常或超过 2 GiB 安全限制')
    return Asset(chosen['name'], url, str(release.get('tag_name') or 'unknown'), size,
                 str(release.get('html_url') or 'https://github.com/'+REPOSITORIES[module]+'/releases'))


def latest_asset(module: str, opener=None) -> Asset:
    if module not in REPOSITORIES:
        raise ValueError('未知模块')
    url = f'https://api.github.com/repos/{REPOSITORIES[module]}/releases/latest'
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                               'User-Agent': 'AutoFusionCommunity/0.2 (official-release-client)',
                                               'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with (opener or _opener()).open(req, timeout=25) as resp:
            data = resp.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('GitHub 发布信息过大')
        release = json.loads(data)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'查询发布信息失败：HTTP {exc.code}（可能是 GitHub 限流或网络问题）。') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f'无法访问 GitHub 发布信息：{exc}。如果当前网络无法连接 GitHub，本版暂不支持国内镜像自动下载。') from exc
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('返回的版本不是稳定正式发布版')
    return choose_asset(module, release)


def download_asset(asset: Asset, target: Path, report: Progress = lambda _: None, opener=None) -> str:
    if not _allowed_host(asset.url):
        raise ValueError('不允许从非官方地址下载')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Always stream to a .part file; a truncated download is never advertised as completed.
    part = target.with_name(target.name + '.part')
    h = hashlib.sha256()
    total = 0
    previous = -1
    req = urllib.request.Request(asset.url, headers={'User-Agent': 'AutoFusionCommunity/0.2'})
    try:
        with (opener or _opener()).open(req, timeout=50) as resp, part.open('wb') as file:
            if not _allowed_host(resp.geturl()):
                raise ValueError('下载发生不安全的重定向')
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD or (asset.size and total > asset.size):
                    raise ValueError('下载大小异常，已停止')
                h.update(chunk)
                file.write(chunk)
                if asset.size:
                    percent = min(100, total * 100 // asset.size)
                    if percent != previous and (percent >= previous + 2 or percent == 100):
                        report(f'下载中 {percent}%（{total // 1024**2} MiB）')
                        previous = percent
                elif total // (20 * 1024**2) != previous:
                    previous = total // (20 * 1024**2)
                    report(f'已下载 {total // 1024**2} MiB')
        if asset.size and total != asset.size:
            raise ValueError(f'文件长度与 GitHub 公布的大小不一致（收到 {total}，应为 {asset.size}）')
        if total == 0:
            raise ValueError('下载结果为空')
        part.replace(target)
        report(f'下载完成；SHA-256: {h.hexdigest()}（可用于自行核对）')
        return h.hexdigest()
    except Exception:
        part.unlink(missing_ok=True)
        raise


def _relative_zip_path(info: zipfile.ZipInfo) -> Path:
    name = info.filename.replace('\\', '/')
    pure = PurePosixPath(name)
    if (not name or name.startswith('/') or name.startswith('//') or
            ':' in name or '..' in pure.parts or '\x00' in name):
        raise ValueError(f'ZIP 包含不安全文件路径：{name[:100]}')
    for part in pure.parts:
        base = part.split('.')[0].casefold()
        if (part.rstrip(' .') != part or base in {'con', 'prn', 'aux', 'nul'} or
                re.fullmatch(r'(com|lpt)[1-9]', base) or part.casefold() == MARKER.casefold()):
            raise ValueError(f'ZIP 包含 Windows 不安全文件名：{name[:100]}')
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
        raise ValueError('ZIP 含符号链接，不自动安装')
    return Path(*pure.parts)


def extract_sra_safely(archive: Path, destination: Path, report: Progress = lambda _: None) -> list[dict]:
    """Install to a fresh folder; return installed files + hashes for safe uninstall."""
    if destination.exists():
        raise FileExistsError('目标模块目录已存在，不会覆盖原有安装或文件：' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / ('.autofusion-staging-' + uuid.uuid4().hex)
    inventory = []
    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise ValueError('ZIP 文件数量异常')
            if sum(x.file_size for x in members) > MAX_UNPACK:
                raise ValueError('解压体积超过 5 GiB 限制')
            names = set()
            entries = []
            for info in members:
                rel = _relative_zip_path(info)
                key = str(rel).replace('\\', '/').casefold()
                if key in names:
                    raise ValueError('ZIP 中存在重复路径，拒绝覆盖')
                names.add(key)
                entries.append((info, rel))
            stage.mkdir()
            for index, (info, rel) in enumerate(entries):
                output = stage / rel
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                hasher = hashlib.sha256()
                actual_size = 0
                with zf.open(info) as src, output.open('xb') as dst:
                    while True:
                        block = src.read(CHUNK)
                        if not block:
                            break
                        actual_size += len(block)
                        if actual_size > info.file_size or actual_size > MAX_UNPACK:
                            raise ValueError('ZIP 条目大小异常')
                        hasher.update(block)
                        dst.write(block)
                inventory.append({'path': rel.as_posix(), 'sha256': hasher.hexdigest(), 'size': actual_size})
                if index and index % 120 == 0:
                    report(f'解压中 {index}/{len(entries)} 个文件')
        cli_candidates = [p for p in stage.rglob('*') if p.is_file() and p.name.lower() == 'sra-cli.exe']
        desktop_candidates = [p for p in stage.rglob('*') if p.is_file() and p.name.lower() == 'sra.exe']
        if len(cli_candidates) != 1 or len(desktop_candidates) != 1:
            raise ValueError('完整 ZIP 内没有唯一的 SRA-cli.exe 与 SRA.exe；请检查上游发布结构')
        meta = {'module': 'sra', 'created': int(time.time()), 'files': inventory,
                'cli': cli_candidates[0].relative_to(stage).as_posix(),
                'gui': desktop_candidates[0].relative_to(stage).as_posix()}
        (stage / MARKER).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(destination)
        report(f'SRA 已安装到：{destination}')
        return inventory
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def module_executable(root: Path, module: str) -> Path | None:
    if module != 'sra':
        return None
    folder = root / 'sra'
    try:
        marker = json.loads((folder / MARKER).read_text(encoding='utf-8'))
        if marker.get('module') != 'sra':
            return None
        rel = Path(marker['cli'])
        path = folder / rel
        if (not rel.is_absolute() and '..' not in rel.parts and path.is_file() and
                not path.is_symlink() and path.resolve().is_relative_to(folder.resolve())):
            return path
    except (OSError, TypeError, ValueError, KeyError):
        pass
    return None


def sra_desktop_executable(root: Path) -> Path | None:
    folder = root / 'sra'
    try:
        marker = json.loads((folder / MARKER).read_text(encoding='utf-8'))
        if marker.get('module') != 'sra':
            return None
        rel = Path(marker['gui'])
        p = folder / rel
        if not rel.is_absolute() and '..' not in rel.parts and p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(folder.resolve()):
            return p
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def uninstall_managed_sra(root: Path) -> tuple[int, int]:
    """Remove only unchanged originally installed files, keeping user data and edited files.
    Returns (removed_file_count, files_left_count). Never blindly rmtree a module.
    """
    folder = root / 'sra'
    if folder.is_symlink():
        raise ValueError('模块目录是链接，拒绝卸载')
    markerfile = folder / MARKER
    try:
        meta = json.loads(markerfile.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError('未找到 AutoFusion 安装清单，不能安全自动卸载') from exc
    if meta.get('module') != 'sra' or not isinstance(meta.get('files'), list):
        raise ValueError('模块清单无效')
    removed, kept = 0, 0
    base = folder.resolve()
    for entry in meta['files']:
        rel = Path(str(entry.get('path', '')))
        if not str(rel) or str(rel) == '.' or rel.is_absolute() or '..' in rel.parts:
            raise ValueError('安装清单包含不安全路径')
        path = folder / rel
        if not path.exists():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(base) or not path.is_file():
            kept += 1
            continue
        hasher = hashlib.sha256()
        with path.open('rb') as f:
            for buf in iter(lambda: f.read(CHUNK), b''):
                hasher.update(buf)
        if hasher.hexdigest() == entry.get('sha256'):
            path.unlink()
            removed += 1
        else:
            kept += 1
    # Keep marker if any recorded file remains, so user can retry later.
    if kept == 0:
        markerfile.unlink(missing_ok=True)
    for parent, dirs, files in os.walk(folder, topdown=False, followlinks=False):
        candidate = Path(parent)
        if candidate.is_symlink():
            continue
        try:
            candidate.rmdir()  # only remove empty directories
        except OSError:
            pass
    return removed, kept


def _walk_registry_candidates() -> list[Path]:
    if sys.platform != 'win32':
        return []
    import winreg
    found = []
    root = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                parent = winreg.OpenKey(hive, root, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with parent:
                i = 0
                while True:
                    try:
                        subname = winreg.EnumKey(parent, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(parent, subname) as sub:
                            def read(name):
                                try: return str(winreg.QueryValueEx(sub, name)[0])
                                except OSError: return ''
                            if 'ok-ww' not in (read('DisplayName') + ' ' + subname).lower():
                                continue
                            location = read('InstallLocation').strip().strip('"')
                            if location: found.append(Path(location) / 'ok-ww.exe')
                            icon = read('DisplayIcon').split(',')[0].strip().strip('"')
                            if icon: found.append(Path(icon))
                    except OSError:
                        pass
    return found


def detect_ww_exe(root: Path | None = None) -> Path | None:
    candidates = _walk_registry_candidates()
    env = os.environ
    for base in (root, Path(env.get('LOCALAPPDATA', str(Path.home()))) / 'Programs',
                 Path(env.get('PROGRAMFILES', 'C:/Program Files')),
                 Path(env.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'))):
        if base:
            candidates.extend((Path(base) / 'ok-ww' / 'ok-ww.exe',
                               Path(base) / 'OK-WW' / 'ok-ww.exe'))
    for item in candidates:
        if item.name.lower() == 'ok-ww.exe' and item.is_file():
            return item
    return None


def download_official(module: str, root: Path, report: Progress = lambda _: None) -> tuple[str, Path, Asset, str]:
    """Download and install SRA; download WW setup and return it for explicit launch."""
    if module not in REPOSITORIES:
        raise ValueError('未知模块')
    report('正在检查 GitHub 官方稳定版发布信息…')
    asset = latest_asset(module)
    report(f'已选择 {asset.version}：{asset.name}（约 {asset.size // 1024**2} MiB）')
    root.mkdir(parents=True, exist_ok=True)
    if module == 'sra' and (root / 'sra').exists():
        raise FileExistsError('SRA 目标目录已经存在，拒绝覆盖；请先检查当前安装。')
    cache = root / '_downloads' / module
    download_target = cache / asset.name
    digest = download_asset(asset, download_target, report)
    if module == 'ww':
        return ('setup', download_target, asset, digest)
    extract_sra_safely(download_target, root / 'sra', report)
    exe = module_executable(root, 'sra')
    if not exe:
        raise RuntimeError('SRA 安装完成但未能识别命令行程序')
    return ('portable', exe, asset, digest)
