"""Conservative self-updater for AutoFusion Community (stdlib only).

The updater never writes inside the user's managed `fusion` directory.
The release feed must be configured by the publisher before online updates can work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from core import data_dir
from version import APP_VERSION

MAX_MANIFEST_SIZE = 128 * 1024
MAX_DOWNLOAD_SIZE = 200 * 1024 * 1024
MAX_FILES = 2500
MAX_UNPACKED_SIZE = 250 * 1024 * 1024
USER_AGENT = 'AutoFusionCommunity-Updater/0.2.4'


@dataclass(frozen=True)
class Release:
    version: str
    archive_url: str
    sha256: str
    notes: str = ''


def _parse_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r'\d+(\.\d+){1,3}', value):
        raise ValueError('版本号格式无效，只支持数字形式，例如 0.2.5')
    return tuple(int(p) for p in value.split('.'))


def is_newer(new: str, current: str = APP_VERSION) -> bool:
    n, c = _parse_version(new), _parse_version(current)
    length = max(len(n), len(c))
    return n + (0,) * (length-len(n)) > c + (0,) * (length-len(c))


def validate_https(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError('更新地址必须是 HTTPS URL')
    parsed = urlsplit(url.strip())
    if (parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or len(url) > 2048):
        raise ValueError('仅允许有效的 HTTPS 更新地址，不允许账号信息或 URL 片段')
    return url.strip()


def feed_config_path() -> Path:
    return data_dir() / 'update_feed.json'


def get_feed_url() -> str:
    paths = [feed_config_path(), Path(__file__).with_name('update_source.json')]
    for path in paths:
        try:
            val = json.loads(path.read_text(encoding='utf-8')).get('manifest_url')
            if val:
                return validate_https(val)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return ''


def set_feed_url(url: str) -> None:
    url = validate_https(url)
    path = feed_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'manifest_url': url}, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def _get_stream(url: str, timeout: int = 30):
    validate_https(url)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json, application/zip'})
    return urllib.request.urlopen(request, timeout=timeout)


def check_update(manifest_url: str, *, opener=None) -> Release | None:
    opener = opener or _get_stream
    with opener(validate_https(manifest_url)) as response:
        content = response.read(MAX_MANIFEST_SIZE+1)
    if len(content) > MAX_MANIFEST_SIZE:
        raise ValueError('更新清单过大')
    try:
        data = json.loads(content.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('在线更新清单格式有误') from exc
    if not isinstance(data, dict):
        raise ValueError('更新清单格式有误')
    release = Release(
        version=data.get('version', ''),
        archive_url=validate_https(data.get('archive_url', '')),
        sha256=data.get('sha256', ''),
        notes=data.get('notes', ''),
    )
    _parse_version(release.version)
    if not isinstance(release.sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', release.sha256):
        raise ValueError('更新清单缺少有效 SHA-256')
    if not isinstance(release.notes, str):
        raise ValueError('更新说明必须是文字')
    if not is_newer(release.version):
        return None
    return release


def _zip_relative_names(z: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    entries = z.infolist()
    if len(entries) > MAX_FILES:
        raise ValueError('安装包中文件数量超出安全上限')
    files = []
    for entry in entries:
        name = entry.filename.replace('\\', '/')
        path = PurePosixPath(name)
        bits = path.parts
        if (name.startswith('/') or not bits or '..' in bits or '.' in bits
                or any(':' in part for part in bits) or '\x00' in name):
            raise ValueError('更新 ZIP 包含不安全文件名')
        if (entry.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('更新 ZIP 包含符号链接')
        if not entry.is_dir():
            files.append((entry,bits))
    if not files:
        raise ValueError('更新 ZIP 中没有文件')
    # Allow the normal ZIP generated for an AutoFusion version: one top-level folder.
    prefixes = {bits[0] for _, bits in files}
    strip = 1 if (len(prefixes) == 1 and all(len(bits)>1 for _,bits in files)) else 0
    normalized = []
    names = set()
    for info, bits in files:
        b = bits[strip:]
        if not b:
            raise ValueError('ZIP 内目录结构异常')
        joined = '/'.join(b)
        if joined.lower() in names:
            raise ValueError('更新 ZIP 有重复文件名')
        names.add(joined.lower())
        normalized.append((info, b))
    return normalized


def _safe_unpack(archive: Path, destination: Path, release_version: str) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        files = _zip_relative_names(z)
        if sum(i.file_size for i,_ in files) > MAX_UNPACKED_SIZE:
            raise ValueError('更新 ZIP 解压后超出安全上限')
        for info, bits in files:
            target = destination.joinpath(*bits)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as stream, target.open('wb') as f:
                shutil.copyfileobj(stream,f,1024*1024)
    expected = ('app.py', 'core.py', 'install_manager.py', 'lifecycle.py', 'cache_manager.py',
                'version.py', 'update_manager.py', 'updater_worker.py',
                'catalog_adapter.py', 'modules_catalog.json', 'discovery.py', 'offline_library.py', 'module_preferences.py', 'discovery_cache.py', 'log_storage.py', '启动AutoFusion.bat')
    for name in expected:
        if not (destination/name).is_file():
            raise ValueError(f'更新包缺少关键程序文件：{name}')
    # Release identity is read as text, never executed during staging.
    version_txt = (destination/'version.py').read_text(encoding='utf-8')
    if not re.search(r'^APP_VERSION\s*=\s*[\"\']'+re.escape(release_version)+r'[\"\']\s*$', version_txt, re.MULTILINE):
        raise ValueError('更新包内版本与发布清单不一致')
    if any((destination/f).exists() for f in ('fusion','_downloads','sra','ok-ww')):
        raise ValueError('更新包不允许携带用户模块目录')


def prepare_release(release: Release, target_dir: Path, on_progress=None, *, opener=None) -> tuple[Path, Path]:
    """Download into OS temp, verify checksum, stage files; return (workdir, plan).

    No files in target_dir are changed by this function.
    """
    opener = opener or _get_stream
    target = target_dir.resolve()
    if not (target/'app.py').is_file() or not (target/'启动AutoFusion.bat').is_file():
        raise ValueError('当前目录不是有效 AutoFusion 程序目录')
    if target.name.lower() == 'fusion' or any((target/x).exists() for x in ('fusion','sra','ok-ww','_downloads')):
        raise ValueError('更新拒绝操作用户模块目录；请将 AutoFusion 程序与 fusion 分开放置')
    # This updater replaces the *program* directory, never user-added files.
    # Refuse rather than silently delete unknown material at the app root.
    known_files = {'app.py','core.py','install_manager.py','lifecycle.py',
                   'cache_manager.py','updater_worker.py','update_manager.py','discovery.py','catalog_adapter.py','modules_catalog.json',
                   'directory_diagnostics.py','offline_library.py','module_preferences.py','discovery_cache.py','log_storage.py','version.py','update_source.json','publish_release.py','README.md','Windows测试指南.txt',
                   '启动AutoFusion.bat','运行诊断.bat','构建Windows发行版.ps1','安全与隐私说明.md','发布前验收清单.md','视频分镜与录屏.md'}
    known_dirs = {'tests','__pycache__'}
    unknown = [item.name for item in target.iterdir()
               if not ((item.is_file() and item.name in known_files)
                        or (item.is_dir() and item.name in known_dirs))]
    if unknown:
        raise ValueError('AutoFusion 程序目录包含未识别文件，避免误删已暂停更新：'+ '、'.join(unknown[:5]))
    workdir = Path(tempfile.mkdtemp(prefix='af-self-update-'))
    archive = workdir/'release.download'
    try:
        size = 0
        sha = hashlib.sha256()
        with opener(release.archive_url) as response, archive.open('wb') as out:
            total_txt = response.headers.get('Content-Length') if hasattr(response,'headers') else None
            total = int(total_txt) if total_txt and total_txt.isdigit() else None
            while True:
                chunk = response.read(1024*512)
                if not chunk:
                    break
                size+=len(chunk)
                if size > MAX_DOWNLOAD_SIZE:
                    raise ValueError('更新包超过允许大小')
                sha.update(chunk)
                out.write(chunk)
                if on_progress:
                    on_progress(size, total)
        if sha.hexdigest().lower() != release.sha256.lower():
            raise ValueError('SHA-256 校验失败，已拒绝更新')
        stage = workdir/'staged'
        _safe_unpack(archive, stage, release.version)
        archive.unlink()  # delete ZIP BEFORE replacing the running app
        # Worker must live outside target folder for safe replacement on Windows.
        worker = workdir/'updater_worker.py'
        shutil.copy2(stage/'updater_worker.py', worker)
        plan = workdir/'plan.json'
        plan.write_text(json.dumps({'target':str(target), 'stage':str(stage), 'workdir':str(workdir),
                        'parent_pid':os.getpid(), 'python':sys.executable, 'version':release.version,
                        'health_file':str(workdir/'health.json'), 'log_file':str(data_dir()/'update-worker.log')}, ensure_ascii=False), encoding='utf-8')
        return workdir, plan
    except Exception:
        shutil.rmtree(workdir,ignore_errors=True)
        raise


def start_update_worker(plan: Path) -> None:
    workdir = plan.parent
    params = json.loads(plan.read_text(encoding='utf-8'))
    log = Path(params['log_file'])
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
    with log.open('ab') as output:
        subprocess.Popen([sys.executable,str(workdir/'updater_worker.py'),str(plan)],
                          cwd=str(workdir), stdout=output, stderr=subprocess.STDOUT,
                          creationflags=flags, close_fds=True)
