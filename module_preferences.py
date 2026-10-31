"""Local-only per-module reminder settings. Never affects manual updates."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from core import data_dir

_ID = re.compile(r'^[a-z0-9_-]{1,60}$')

def preferences_path() -> Path:
    return data_dir() / 'module_preferences.json'

def _load(path=None) -> dict:
    path=Path(path) if path is not None else preferences_path()
    try:
        raw=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or not isinstance(raw.get('muted'),list):return {'muted': []}
        return {'muted':list(dict.fromkeys(x for x in raw['muted'][:200]
                if isinstance(x,str) and _ID.fullmatch(x)))}
    except (OSError,ValueError,TypeError):
        return {'muted': []}

def is_reminder_muted(module: str, path=None) -> bool:
    if not _ID.fullmatch(module):raise ValueError('模块名无效')
    return module in _load(path)['muted']

def set_reminder_muted(module: str, muted: bool, path=None) -> None:
    if not _ID.fullmatch(module) or type(muted) is not bool:raise ValueError('更新提醒参数无效')
    path=Path(path) if path is not None else preferences_path()
    data=_load(path)
    chosen=set(data['muted'])
    if muted:chosen.add(module)
    else:chosen.discard(module)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps({'muted':sorted(chosen)},indent=2),encoding='utf-8')
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)
