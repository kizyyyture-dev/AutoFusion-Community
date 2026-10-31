"""Offline discovery and download security tests. Never contact the internet."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from discovery import (Repo, Asset, search_repositories, latest_release_assets,
                       download_release_asset, GitHubReleaseRedirect)


class MockResponse:
    def __init__(self, body, url='https://api.github.com/search/repositories'):
        self.stream = io.BytesIO(body)
        self.url = url
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()
    def read(self, size=-1): return self.stream.read(size)
    def geturl(self): return self.url


def data_opener(payload):
    def opener(request, timeout=12):
        return MockResponse(json.dumps(payload).encode(), request.full_url)
    return opener


REPO = {'full_name':'someone/safe-looking-project','description':'test','html_url':'https://github.com/someone/safe-looking-project',
        'stargazers_count':5,'archived':False,'license':{'spdx_id':'MIT'},'private':False}

class DiscoveryTests(unittest.TestCase):
    def test_uninstalled_project_can_appear_without_catalog_registration(self):
        items=search_repositories('safe-looking', opener=data_opener({'items':[REPO]}))
        self.assertEqual([item.full_name for item in items], ['someone/safe-looking-project'])
        self.assertEqual(items[0].license_id,'MIT')

    def test_github_url_exact_repo(self):
        items=search_repositories('https://github.com/someone/safe-looking-project', opener=data_opener(REPO))
        self.assertEqual(len(items),1)

    def test_search_no_network_for_invalid_input(self):
        for q in ('', 'x', 'x'*101, 'abc\nxyz'):
            with self.assertRaises(ValueError):
                search_repositories(q, opener=lambda *_: self.fail('network should not occur'))

    def test_reject_fake_website_and_private_repo(self):
        fake = dict(REPO, html_url='https://bad.example/someone/safe-looking-project')
        private = dict(REPO, private=True)
        items=search_repositories('query', opener=data_opener({'items':[fake,private]}))
        self.assertEqual(items,[])

    def test_assets_only_uploads_and_known_extensions(self):
        release={'html_url':'https://github.com/someone/safe-looking-project/releases/tag/v1', 'assets':[
            {'name':'Project-win-x64.zip','size':4,'state':'uploaded','browser_download_url':'https://github.com/someone/safe-looking-project/releases/download/v1/Project-win-x64.zip', 'digest':'sha256:'+('a'*64)},
            {'name':'rm-all.ps1','size':10,'state':'uploaded','browser_download_url':'https://github.com/someone/safe-looking-project/releases/download/v1/rm-all.ps1'},
            {'name':'installer.exe','size':10,'state':'uploaded','browser_download_url':'https://github.com/someone/other/releases/download/v1/installer.exe'},
        ]}
        url, assets=latest_release_assets(Repo('someone/safe-looking-project','','https://github.com/someone/safe-looking-project',0,False,'MIT'),opener=data_opener(release))
        self.assertEqual(len(assets),1)
        self.assertEqual(assets[0].sha256,'a'*64)

    def test_redirect_different_host_blocked(self):
        handler=GitHubReleaseRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request('https://github.com/o/r/releases/download/v/file.zip'),None,302,'found',{},'https://evil.example/file.zip')

    def test_download_to_user_chosen_file_only(self):
        payload=b'download from github test'
        asset=Asset('someone/safe-looking-project','a.zip',len(payload),
              'https://github.com/someone/safe-looking-project/releases/download/v1/a.zip',
              'https://github.com/someone/safe-looking-project/releases/tag/v1',hashlib.sha256(payload).hexdigest())
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'a.zip'
            sha, verified=download_release_asset(asset,file,
                opener=lambda req, timeout=35: MockResponse(payload,'https://release-assets.githubusercontent.com/example'))
            self.assertTrue(verified)
            self.assertEqual(sha,asset.sha256)
            self.assertEqual(file.read_bytes(),payload)
            with self.assertRaises(ValueError):
                download_release_asset(asset,file,opener=lambda req,timeout=35: self.fail('never fetch existing'))

    def test_missing_or_mismatched_hash_never_claims_trust(self):
        payload=b'abcd'
        url='https://github.com/someone/safe-looking-project/releases/download/v1/a.zip'
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            item=Asset('someone/safe-looking-project','a.zip',4,url,'https://github.com/someone/safe-looking-project/releases/tag/v1')
            sha, verified=download_release_asset(item,folder/'no_digest.zip',opener=lambda req,timeout=35: MockResponse(payload,url))
            self.assertFalse(verified)
            bad=Asset(item.repo,item.name,4,url,item.release_url,'a'*64)
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                download_release_asset(bad,folder/'bad.zip',opener=lambda req,timeout=35: MockResponse(payload,url))
            self.assertFalse((folder/'bad.zip').exists())
            self.assertEqual(len(list(folder.glob('.af-*.part'))),0)

    def test_bad_redirect_and_size_mismatch(self):
        asset=Asset('someone/safe-looking-project','a.zip',8,
            'https://github.com/someone/safe-looking-project/releases/download/v1/a.zip',
            'https://github.com/someone/safe-looking-project/releases/tag/v1')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'foo.zip'
            with self.assertRaisesRegex(ValueError,'未经允许'):
                download_release_asset(asset,path,opener=lambda req,timeout=35: MockResponse(b'1234','https://evil.com/a.zip'))
            with self.assertRaisesRegex(ValueError,'大小'):
                download_release_asset(asset,path,opener=lambda req,timeout=35: MockResponse(b'1234',asset.url))
            self.assertFalse(path.exists())

    def test_private_search_api_rate_limit_error(self):
        def limited(req,timeout=12):
            raise HTTPError(req.full_url,403,'rate limited',{},None)
        with self.assertRaisesRegex(ValueError,'次数受限'):
            search_repositories('project',opener=limited)

if __name__=='__main__':unittest.main()
