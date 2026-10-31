"""Small per-user cache of GitHub *search results only*, never download authority.

Stale records merely provide a navigation fallback; Release metadata and assets
must still be checked through the normal GitHub endpoints.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from core import data_dir
from discovery import Repo, search_repositories

TTL_SECONDS = 15 * 60
STALE_SECONDS = 24 * 60 * 60
MAX_FILE = 128 * 1024


def cache_file() -> Path:
    return data_dir() / 'github_search_cache.json'


def _key(query: str) -> str:
    text = ' '.join(query.split()).strip().casefold()
    if not 2 <= len(text) <= 100:
        raise ValueError('查询词应为 2–100 个字符')
    return text


def _decode_repo(raw: dict) -> Repo | None:
    if not isinstance(raw, dict):
        return None
    try:
        name = raw['full_name']
        url = raw['url']
        if not isinstance(name, str) or not re.fullmatch(r'[\w.-]{1,100}/[\w.-]{1,100}', name, re.ASCII):
            return None
        expected = 'https://github.com/' + name
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'github.com'
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/').casefold() != ('/'+name).casefold()):
            return None
        return Repo(name, str(raw.get('description',''))[:180], expected,
                    max(0, int(raw.get('stars',0))), bool(raw.get('archived',False)),
                    str(raw.get('license_id','未知'))[:35])
    except (KeyError, ValueError, TypeError):
        return None


def _read(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FILE:
            return {}
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or raw.get('schema') != 1:
            return {}
        return raw.get('queries', {}) if isinstance(raw.get('queries'),dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _cached(rows: dict, key: str, now: float) -> tuple[list[Repo], float] | None:
    item = rows.get(key)
    if not isinstance(item, dict) or not isinstance(item.get('results'), list):
        return None
    stamp = item.get('timestamp', -1)
    if type(stamp) not in (int,float) or stamp < 0 or now < stamp or now - stamp > STALE_SECONDS:
        return None
    decoded = [_decode_repo(x) for x in item['results'][:8]]
    if any(x is None for x in decoded):
        return None
    return decoded, now - stamp


def search_cached(query: str, *, searcher=search_repositories,
                  path: Path | None = None, now: float | None = None) -> tuple[list[Repo], str]:
    """Return (result, 'live'|'cached'|'stale'), never cache Release file links."""
    key = _key(query)
    now = time.time() if now is None else now
    path = Path(path) if path else cache_file()
    rows = _read(path)
    old = _cached(rows, key, now)
    if old is not None and old[1] <= TTL_SECONDS:
        return old[0], 'cached'
    try:
        result = searcher(query)
        if not isinstance(result, list) or any(not isinstance(x, Repo) for x in result):
            raise ValueError('GitHub 搜索响应格式异常')
    except (OSError, ValueError) as err:
        if old is not None:
            return old[0], 'stale'
        raise
    sanitized = [_decode_repo(vars(x)) for x in result[:8]]
    verified = [x for x in sanitized if x is not None]
    # Cache size remains bounded and does not contain executable download URLs.
    rows = {k:v for k,v in rows.items() if isinstance(k,str) and k != key and
            isinstance(v,dict) and type(v.get('timestamp')) in (int,float) and
            0 <= now - v['timestamp'] <= STALE_SECONDS}
    rows[key] = {'timestamp':now,'results':[vars(x) for x in verified]}
    if len(rows) > 25:
        rows = dict(sorted(rows.items(),key=lambda kv:kv[1]['timestamp'],reverse=True)[:25])
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps({'schema':1,'queries':rows},ensure_ascii=False),encoding='utf-8')
            if temp.stat().st_size <= MAX_FILE and not path.is_symlink():
                os.replace(temp,path)
        finally:
            temp.unlink(missing_ok=True)
    except OSError:
        pass  # A cache failure cannot stop a valid live search.
    return verified, 'live'
