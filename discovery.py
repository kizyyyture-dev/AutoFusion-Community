"""Read-only GitHub project discovery and opt-in, download-only Release assets.

This module NEVER executes downloaded assets or registers them as managed modules.
Unreviewed search results are not an AutoFusion safety endorsement.
No API tokens, paid API, server, crawler or persistent background polling.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_HOST = 'api.github.com'
MAX_API_BYTES = 512 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
ALLOWED_EXTENSIONS = ('.exe', '.msi', '.msix', '.msixbundle', '.zip', '.7z')
_IDENT = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
_SHA256 = re.compile(r'^sha256:([0-9a-fA-F]{64})$')


@dataclass(frozen=True)
class Repo:
    full_name: str
    description: str
    url: str
    stars: int
    archived: bool
    license_id: str


@dataclass(frozen=True)
class Asset:
    repo: str
    name: str
    size: int
    url: str
    release_url: str
    sha256: str = ''  # GitHub's self-reported asset digest (not independent trust proof)


def _repo_name(name: str) -> tuple[str, str]:
    parts = name.split('/')
    if len(parts) != 2 or not all(_IDENT.fullmatch(p) for p in parts):
        raise ValueError('GitHub 仓库名称无效')
    return tuple(parts)


def _github_url(url: str, repo: str | None = None, asset: bool = False) -> str:
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname != 'github.com' or parts.username or
            parts.password or parts.port or parts.fragment):
        raise ValueError('只接受 GitHub HTTPS 官方页面链接')
    if repo:
        owner, project = _repo_name(repo)
        prefix = f'/{owner}/{project}/'
        path = urllib.parse.unquote(parts.path)
        if not path.lower().startswith(prefix.lower()):
            raise ValueError('下载链接不属于所选仓库')
        if asset and '/releases/download/' not in path.lower():
            raise ValueError('只允许 GitHub Release 的正式附件')
    return url


def _get_json(url: str, opener=None) -> dict:
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname != API_HOST or p.username or p.password or p.port:
        raise ValueError('仅允许 GitHub 官方 API')
    request = urllib.request.Request(url, headers={'User-Agent':'AutoFusionCommunity-Discovery/0.4.2',
                             'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
    try:
        response = (opener or urllib.request.urlopen)(request, timeout=12)
        with response as r:
            final = urllib.parse.urlsplit(r.geturl())
            if final.scheme != 'https' or final.hostname != API_HOST:
                raise ValueError('GitHub API 重定向到未知站点，已拒绝')
            content = r.read(MAX_API_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise ValueError('GitHub API 访问次数受限，请稍后再试，或直接访问 GitHub 网站') from e
        if e.code == 404:
            raise ValueError('没有找到公开的 GitHub 仓库或正式发布包') from e
        raise ValueError(f'GitHub 返回 HTTP {e.code}，请检查网络') from e
    except urllib.error.URLError as e:
        raise ValueError('无法连接 GitHub API，请检查网络或通过浏览器访问官网') from e
    if len(content) > MAX_API_BYTES:
        raise ValueError('GitHub 返回数据过大')
    try:
        data = json.loads(content.decode('utf-8'))
    except (UnicodeError, ValueError) as e:
        raise ValueError('GitHub 返回的数据不是有效 JSON') from e
    if not isinstance(data, dict):
        raise ValueError('GitHub 返回的数据结构不正确')
    return data


def _to_repo(item: dict) -> Repo | None:
    if not isinstance(item, dict) or item.get('private') is True:
        return None
    full = item.get('full_name', '')
    try:
        owner, name = _repo_name(full)
        html_url = _github_url(item.get('html_url', ''))
        if urllib.parse.urlsplit(html_url).path.strip('/').casefold() != full.casefold():
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    license_item = item.get('license')
    license_id = license_item.get('spdx_id', '') if isinstance(license_item, dict) else ''
    return Repo(full, (item.get('description') or '')[:180], html_url,
                int(item.get('stargazers_count') or 0), bool(item.get('archived')),
                str(license_id or '未知')[:35])


def search_repositories(query: str, opener=None) -> list[Repo]:
    query = query.strip()
    if len(query) < 2 or len(query) > 100 or any(ord(c) < 32 for c in query):
        raise ValueError('请输入 2–100 个字符的项目名称或 GitHub 仓库地址')
    match = re.fullmatch(r'https://github\.com/([^/]+)/([^/#?]+)/?', query, re.I)
    short = re.fullmatch(r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)', query)
    if match or short:
        owner, name = _repo_name('/'.join((match or short).groups()))
        item = _get_json(f'https://api.github.com/repos/{owner}/{name}', opener)
        repo = _to_repo(item)
        return [repo] if repo else []
    url = 'https://api.github.com/search/repositories?' + urllib.parse.urlencode({'q':query, 'per_page':8})
    result = _get_json(url, opener)
    entries = result.get('items')
    if not isinstance(entries, list):
        raise ValueError('GitHub 搜索结果格式无效')
    return [repo for item in entries[:8] if (repo := _to_repo(item)) is not None]


def latest_release_assets(repo: Repo, opener=None) -> tuple[str, list[Asset]]:
    owner, project = _repo_name(repo.full_name)
    data = _get_json(f'https://api.github.com/repos/{owner}/{project}/releases/latest', opener)
    release_url = _github_url(data.get('html_url', ''), repo.full_name)
    if '/releases/tag/' not in urllib.parse.urlsplit(release_url).path.lower():
        raise ValueError('GitHub 返回的发布页链接无效')
    assets = data.get('assets', [])
    if not isinstance(assets, list):
        raise ValueError('GitHub Release 附件格式错误')
    available = []
    for item in assets[:60]:
        if not isinstance(item, dict) or item.get('state') != 'uploaded':
            continue
        name, url = item.get('name', ''), item.get('browser_download_url', '')
        size = item.get('size', 0)
        if not (isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_.()\-+ ]{1,180}', name)
                and name.lower().endswith(ALLOWED_EXTENSIONS)
                and isinstance(size,int) and 0 < size <= MAX_ASSET_BYTES):
            continue
        try:
            _github_url(url, repo.full_name, asset=True)
        except (ValueError, TypeError):
            continue
        digest = item.get('digest')
        m = _SHA256.fullmatch(digest) if isinstance(digest,str) else None
        available.append(Asset(repo.full_name, name, size, url, release_url,
                               m.group(1).lower() if m else ''))
        if len(available) >= 20:
            break
    return release_url, available


def _trusted_asset_host(hostname: str) -> bool:
    host = (hostname or '').lower().rstrip('.')
    return host == 'github.com' or host.endswith('.githubusercontent.com')


class GitHubReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme != 'https' or not _trusted_asset_host(p.hostname or ''):
            raise ValueError('下载重定向到非 GitHub 官方附件域名，已阻止')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_release_asset(asset: Asset, destination: Path, progress=None, opener=None) -> tuple[str, bool]:
    """Only SAVE file after user confirmation. Never run, unpack, install or register it.

    Returns computed SHA-256 and whether GitHub's asset digest matched. No digest
    is reported as unverified; it is never interpreted as independent security proof.
    """
    _repo_name(asset.repo)
    _github_url(asset.url, asset.repo, asset=True)
    if not (asset.name.lower().endswith(ALLOWED_EXTENSIONS) and 0 < asset.size <= MAX_ASSET_BYTES):
        raise ValueError('附件格式或大小不符合下载策略')
    dest = Path(destination)
    if not dest.is_absolute() or not dest.parent.is_dir() or dest.is_dir() or dest.exists():
        raise ValueError('目标路径无效或文件已存在；不会覆盖原文件')
    if dest.suffix.casefold() != Path(asset.name).suffix.casefold():
        raise ValueError('保存文件需要保留附件原始格式')
    req = urllib.request.Request(asset.url, headers={'User-Agent':'AutoFusionCommunity-Discovery/0.4.2'})
    fetch = opener or urllib.request.build_opener(GitHubReleaseRedirect()).open
    temp_path = None
    try:
        response = fetch(req, timeout=35)
        with response as resp:
            final = urllib.parse.urlsplit(resp.geturl())
            if final.scheme != 'https' or not _trusted_asset_host(final.hostname or ''):
                raise ValueError('附件下载链接指向未经允许的站点')
            with tempfile.NamedTemporaryFile(mode='wb', dir=str(dest.parent), prefix='.af-', suffix='.part', delete=False) as tmp:
                temp_path = Path(tmp.name)
                sha = hashlib.sha256()
                count = 0
                while True:
                    block = resp.read(256*1024)
                    if not block:
                        break
                    count += len(block)
                    if count > MAX_ASSET_BYTES or count > asset.size:
                        raise ValueError('附件大小超过 GitHub 公布的限制')
                    sha.update(block)
                    tmp.write(block)
                    if progress:
                        progress(count, asset.size)
                if count != asset.size:
                    raise ValueError('下载大小与 GitHub Release 信息不符')
        digest = sha.hexdigest()
        if asset.sha256 and digest != asset.sha256:
            raise ValueError('SHA-256 与 GitHub 附件信息不匹配，拒绝保存')
        # Never silently overwrite a user file. Use an atomic link on the same filesystem.
        os.link(temp_path, dest)
        temp_path.unlink()
        temp_path = None
        return digest, bool(asset.sha256)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
