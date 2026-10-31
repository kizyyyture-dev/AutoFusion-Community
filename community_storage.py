"""Local Community install receipts; discovery never grants deletion authority."""
import hashlib
import json
import os
from pathlib import Path
from core import data_dir
MARKER = ".autofusion-managed.json"

def linked(path):
    p=Path(path).absolute()
    for part in (p,*p.parents):
        if part.is_symlink() or (hasattr(part,"is_junction") and part.is_junction()):
            return True
    return False

def _key(path):
    return os.path.normcase(str(Path(path).resolve()))

def _read():
    p=data_dir()/"managed-installations.json"
    if linked(p): return {"roots":[],"installs":{}}
    try:
        data=json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data.get("roots"),list) or not isinstance(data.get("installs"),dict):
            raise ValueError("Invalid receipts")
        return data
    except (OSError,ValueError,TypeError,AttributeError):
        return {"roots":[],"installs":{}}

def _write(data):
    p=data_dir()/"managed-installations.json"
    if linked(p): raise ValueError("安装记录目录含链接，拒绝写入")
    p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix(".tmp")
    if linked(temp): raise ValueError("安装记录临时路径异常")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(p)

def root_owned(path):
    return not linked(path) and _key(path) in _read()["roots"]

def claim_empty_root(path):
    p=Path(path)
    if linked(p): raise ValueError("整合目录含链接，请选择普通目录")
    if root_owned(p): return
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise ValueError("目录已有来源未确认的文件，请在设置中选择空目录；现有软件仍可只读识别")
    p.mkdir(parents=True,exist_ok=True)
    data=_read();data["roots"].append(_key(p));_write(data)

def record_install(folder):
    p=Path(folder); marker=p/MARKER
    if linked(marker): raise ValueError("安装清单路径异常")
    data=_read()
    data["installs"][_key(p)]=hashlib.sha256(marker.read_bytes()).hexdigest()
    _write(data)

def trusted_install(folder):
    p=Path(folder);marker=p/MARKER
    if linked(marker): return False
    try: return _read()["installs"].get(_key(p))==hashlib.sha256(marker.read_bytes()).hexdigest()
    except OSError: return False

def forget_install(folder):
    data=_read();data["installs"].pop(_key(folder),None);_write(data)
