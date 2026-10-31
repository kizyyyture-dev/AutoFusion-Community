"""Bound log growth without deleting arbitrary files or third-party logs."""
from __future__ import annotations
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024


def append_session_log(folder: Path, line: str) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    current = folder/'session.log'
    prior = folder/'session.log.1'
    if current.is_symlink() or prior.is_symlink():
        raise ValueError('日志文件不允许指向符号链接')
    # Two bounded files at most. Rotation does not touch any other user file.
    incoming = (line+'\n').encode('utf-8')
    if len(incoming) > 128 * 1024:
        incoming = incoming[:128*1024-32] + '[AutoFusion] 日志超长，已截断\n'.encode('utf-8')
    if current.exists() and current.stat().st_size + len(incoming) > MAX_LOG_BYTES:
        current.replace(prior)
    with current.open('ab') as f:
        f.write(incoming)
    return current
