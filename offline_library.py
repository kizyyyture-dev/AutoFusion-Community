"""AutoFusion offline installer vault (stdlib only; never executes saved files).

All library objects are private user-provided copies, not trusted installations.
The library does not publish or redistribute software and does not infer license rights.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
ALLOWED = ('.exe', '.msi', '.zip', '.7z', '.msix', '.msixbundle', '.appx', '.appxbundle')
MAX_FILE = 2 * 1024**3  # cap individual files at 2 GiB, with limited disk use
MAX_ITEMS = 500
MAX_BUNDLE = 4 * 1024**3
_ID = re.compile(r'^[0-9a-f]{32}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')


def default_library_root(install_root: str | Path) -> Path:
    """Separate from managed modules and program; follow chosen drive."""
    return Path(install_root).expanduser().absolute().parent / 'OfflinePackages'


def _within(base: Path, target: Path) -> bool:
    try:
        return os.path.commonpath((str(base.resolve()), str(target.resolve()))) == str(base.resolve())
    except (OSError, ValueError):
        return False


def _validate_root(root: Path, managed_root: Path | None = None) -> Path:
    root = Path(root).expanduser().absolute()
    if root == root.parent or root.is_symlink():
        raise ValueError('离线库不能位于磁盘根目录或符号链接位置')
    program = Path(__file__).resolve().parent
    if root == program or _within(program, root) or _within(root, program):
        raise ValueError('离线库必须独立于 AutoFusion 程序目录')
    if managed_root is not None:
        managed = Path(managed_root).expanduser().absolute()
        if root == managed or _within(managed, root) or _within(root, managed):
            raise ValueError('离线库必须独立于 fusion 模块目录')
    if (root / 'files').is_symlink():
        raise ValueError('离线库 files 目录不允许是符号链接')
    return root


def _file_name(original: str) -> str:
    name = Path(original).name
    if '/' in original or '\\' in original:
        raise ValueError('不得在名称中包含文件路径')
    if (not name or len(name) > 180 or name in ('.', '..') or
            name[-1:] in (' ', '.') or any(c in name for c in '<>:"/\\|?*\x00') or
            any(ord(c) < 32 for c in name) or
            Path(name).suffix.casefold() not in ALLOWED):
        raise ValueError('只接受文件名正常的 Windows 安装包或便携压缩包')
    return name


def _safe_record(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError('离线库索引损坏：记录不是对象')
    uid = entry.get('id')
    name = _file_name(str(entry.get('name', '')))
    sha = entry.get('sha256')
    size = entry.get('size')
    if not (isinstance(uid, str) and _ID.fullmatch(uid) and isinstance(sha, str)
            and _SHA.fullmatch(sha) and type(size) is int and 0 < size <= MAX_FILE):
        raise ValueError('离线库索引存在不安全的记录')
    suffix = Path(name).suffix.lower()
    # Derive storage path from validated id and suffix; never use untrusted path.
    return {'id': uid, 'name': name, 'size': size, 'sha256': sha,
            'added': str(entry.get('added', ''))[:40],
            'note': str(entry.get('note', ''))[:300], 'suffix': suffix}


class OfflineLibrary:
    def __init__(self, root: Path, *, managed_root: Path | None = None):
        self.root = _validate_root(root, managed_root)
        self.files = self.root / 'files'
        self.index = self.root / 'index.json'

    def _load(self) -> list[dict]:
        if self.index.is_symlink():
            raise ValueError('离线库索引不允许是符号链接')
        try:
            raw = json.loads(self.index.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        if not isinstance(raw, dict) or raw.get('schema') != SCHEMA or not isinstance(raw.get('entries'), list):
            raise ValueError('离线库索引格式不正确，请检查 index.json')
        entries = raw['entries']
        if len(entries) > MAX_ITEMS:
            raise ValueError('离线库记录数量超过上限')
        clean = [_safe_record(item) for item in entries]
        if len({e['id'] for e in clean}) != len(clean):
            raise ValueError('离线库存在重复的文件 ID')
        return clean

    def list_items(self) -> list[dict]:
        return self._load()

    def _save(self, items: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.index.is_symlink():
            raise ValueError('拒绝写入符号链接目录')
        payload = {'schema': SCHEMA, 'entries': [
            {key: e[key] for key in ('id', 'name', 'size', 'sha256', 'added', 'note')} for e in items]}
        fd, tmp = tempfile.mkstemp(prefix='.af-index-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.index)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def path_for(self, entry: dict) -> Path:
        safe = _safe_record(entry)
        target = self.files / (safe['id'] + safe['suffix'])
        if target.is_symlink() or not _within(self.files, target):
            raise ValueError('离线文件路径不安全')
        return target

    def _from_stream(self, stream, name: str, size: int, note: str = '', expected: str = '') -> dict:
        name = _file_name(name)
        if not (0 < size <= MAX_FILE):
            raise ValueError('安装包大小不支持（单文件上限 2 GiB）')
        items = self._load()
        if len(items) >= MAX_ITEMS:
            raise ValueError('离线包数量达到上限')
        self.files.mkdir(parents=True, exist_ok=True)
        if self.files.is_symlink():
            raise ValueError('文件保存目录不安全')
        ident = uuid.uuid4().hex
        dest = self.files / (ident + Path(name).suffix.lower())
        temp = self.files / ('.af-' + ident + '.tmp')
        sha = hashlib.sha256()
        total = 0
        try:
            with temp.open('xb') as out:
                while True:
                    chunk = stream.read(min(1024 * 1024, size - total + 1))
                    if not chunk: break
                    total += len(chunk)
                    if total > size: raise ValueError('文件在导入过程中发生变化或超出预期')
                    sha.update(chunk)
                    out.write(chunk)
                out.flush(); os.fsync(out.fileno())
            if total != size:
                raise ValueError('读取到的文件大小与源文件不符')
            digest = sha.hexdigest()
            if expected and digest != expected:
                raise ValueError('离线包 SHA-256 校验失败')
            for existing in items:
                if existing['sha256'] == digest:
                    raise ValueError('此文件的内容已经在离线库中，无需重复导入')
            entry = {'id': ident, 'name': name, 'size': total, 'sha256': digest,
                     'added': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     'note': str(note)[:300], 'suffix': Path(name).suffix.lower()}
            os.replace(temp, dest)
            try: self._save(items + [entry])
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            return entry
        finally:
            temp.unlink(missing_ok=True)

    def import_file(self, path: Path) -> dict:
        source = Path(path).expanduser().absolute()
        if (source.is_symlink() or not source.is_file() or _within(self.root, source)):
            raise ValueError('源文件无效、是符号链接或已经位于离线库内')
        _file_name(source.name)
        size = source.stat().st_size
        with source.open('rb') as file:
            return self._from_stream(file, source.name, size)

    def verify(self, ident: str) -> tuple[bool, str]:
        entry = self._get(ident)
        path = self.path_for(entry)
        if not path.is_file() or path.stat().st_size != entry['size']:
            return False, '文件缺失或大小已变化'
        digest = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1024*1024), b''):
                digest.update(chunk)
        matched = digest.hexdigest() == entry['sha256']
        return matched, 'SHA-256 与入库时一致' if matched else '文件内容已变化；不可当作可信备份'

    def _get(self, ident: str) -> dict:
        for e in self._load():
            if e['id'] == ident:
                return e
        raise ValueError('离线包记录不存在')

    def remove(self, ident: str) -> str:
        """Only delete one managed copied file, never a source file outside vault."""
        items = self._load()
        entry = self._get(ident)
        target = self.path_for(entry)
        tomb = self.files / ('.af-delete-' + ident)
        moved = False
        try:
            if target.is_file():
                os.replace(target, tomb)
                moved = True
            self._save([e for e in items if e['id'] != ident])
        except Exception:
            if moved: os.replace(tomb, target)
            raise
        if moved: tomb.unlink()
        return entry['name']

    def export_bundle(self, ids: list[str], target: Path) -> Path:
        """Export only explicitly selected verified copies. User owns sharing rights."""
        if not ids or len(ids) != len(set(ids)) or len(ids) > MAX_ITEMS:
            raise ValueError('请选择至少一个有效安装包')
        target = Path(target).expanduser().absolute()
        if target.suffix.casefold() != '.zip' or target.exists() or not target.parent.is_dir():
            raise ValueError('导出位置必须是尚不存在的 ZIP 文件')
        if _within(self.root, target):
            raise ValueError('不能把备份 ZIP 放在离线库目录内')
        entries = [self._get(x) for x in ids]
        if sum(e['size'] for e in entries) > MAX_BUNDLE:
            raise ValueError('离线备份导出总大小不能超过 4 GiB')
        for entry in entries:
            valid, detail = self.verify(entry['id'])
            if not valid: raise ValueError(f'{entry["name"]}：{detail}')
        temp = target.parent / ('.af-export-' + uuid.uuid4().hex + '.tmp')
        try:
            with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
                z.writestr('manifest.json', json.dumps({'schema': 1, 'type': 'AutoFusionOfflineBundle',
                       'warning': 'File hashes verify consistency only, not safety or redistribution rights.',
                       'entries': [{k:e[k] for k in ('id','name','size','sha256')} for e in entries]},
                       ensure_ascii=False, indent=2))
                for e in entries:
                    z.write(self.path_for(e), 'packages/' + e['id'] + e['suffix'])
            os.replace(temp, target)
            return target
        finally:
            temp.unlink(missing_ok=True)

    def import_bundle(self, path: Path) -> list[dict]:
        """Import self-describing portable library backups; no archive extraction."""
        source = Path(path).expanduser().absolute()
        if not source.is_file() or source.is_symlink() or _within(self.root, source):
            raise ValueError('备份文件位置不安全')
        with zipfile.ZipFile(source) as z:
            if len(z.infolist()) > MAX_ITEMS+1:
                raise ValueError('备份条目过多')
            info = z.getinfo('manifest.json')
            if info.file_size > 256*1024:
                raise ValueError('备份清单过大')
            data = json.loads(z.read(info).decode('utf-8'))
            if not isinstance(data, dict) or data.get('type') != 'AutoFusionOfflineBundle' or data.get('schema') != 1:
                raise ValueError('不是有效的 AutoFusion 离线备份')
            rows = data.get('entries')
            if not isinstance(rows, list) or not rows or len(rows) > MAX_ITEMS:
                raise ValueError('备份清单无有效文件')
            if len(self._load()) + len(rows) > MAX_ITEMS:
                raise ValueError('离线库剩余容量不足')
            if sum(int(e.get('size', 0)) for e in rows if isinstance(e, dict)) > MAX_BUNDLE:
                raise ValueError('离线备份总解压大小超过 4 GiB 的安全上限')
            # Validate entire manifest and archive before modifying the library.
            names = {i.filename for i in z.infolist()}
            if len(names) != len(z.infolist()):
                raise ValueError('ZIP 含重复路径')
            expected = {'manifest.json'}
            clean = []
            for row in rows:
                entry = _safe_record({**row, 'added':'', 'note':''})
                storage = 'packages/' + entry['id'] + entry['suffix']
                expected.add(storage)
                file_info = z.getinfo(storage)
                if file_info.is_dir() or file_info.file_size != entry['size'] or file_info.file_size > MAX_FILE:
                    raise ValueError('备份文件大小与清单不一致')
                mode = file_info.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise ValueError('拒绝导入带符号链接的备份')
                clean.append((entry, storage))
            if names != expected:
                raise ValueError('备份 ZIP 包含未记录的额外内容')
            imported = []
            try:
                for entry, storage in clean:
                    with z.open(storage) as f:
                        imported.append(self._from_stream(f, entry['name'], entry['size'],
                                         note='从 AutoFusion 离线备份导入', expected=entry['sha256']))
            except Exception:
                for e in reversed(imported):
                    self.remove(e['id'])
                raise
            return imported
