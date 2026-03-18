"""
test_multi_source_providers.py - 多平台 Provider 覆盖率突破
============================================================

突破性算法：参数化 × 5 平台 × 3 方法 = 15 个测试从 1 个模板

所有 SourceProvider 子类共享相同的接口 (get_repo_metadata, get_readme, get_file_content)
只需 mock _api_get 和 _raw_get → 覆盖全部 5 个 Provider 的 ~95 行未覆盖代码
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from multi_source import (
    GitHubProvider, GitLabProvider, BitbucketProvider,
    GiteeProvider, CodebergProvider,
    detect_platform, get_provider, fetch_from_any_source,
    get_supported_platforms, RepoMetadata,
)


# ── 各平台的 mock API 响应 ──

_GITHUB_REPO_RESPONSE = {
    "owner": {"login": "test"},
    "name": "repo",
    "full_name": "test/repo",
    "description": "A test repo",
    "stargazers_count": 100,
    "language": "Python",
    "clone_url": "https://github.com/test/repo.git",
    "homepage": "https://test.com",
    "license": {"spdx_id": "MIT"},
    "default_branch": "main",
    "topics": ["ai", "python"],
    "fork": False,
    "archived": False,
    "pushed_at": "2024-01-01T00:00:00Z",
}

_GITLAB_REPO_RESPONSE = {
    "namespace": {"path": "test"},
    "path": "repo",
    "path_with_namespace": "test/repo",
    "description": "GitLab repo",
    "star_count": 50,
    "http_url_to_repo": "https://gitlab.com/test/repo.git",
    "web_url": "https://gitlab.com/test/repo",
    "default_branch": "main",
    "topics": ["ml"],
    "forked_from_project": None,
    "archived": False,
    "last_activity_at": "2024-01-01",
}

_BITBUCKET_REPO_RESPONSE = {
    "full_name": "test/repo",
    "description": "BB repo",
    "language": "JavaScript",
    "website": "https://bb.com",
    "mainbranch": {"name": "master"},
    "parent": None,
}

_GITEE_REPO_RESPONSE = {
    "owner": {"login": "test"},
    "path": "repo",
    "full_name": "test/repo",
    "description": "国内仓库",
    "stargazers_count": 200,
    "language": "Go",
    "html_url": "https://gitee.com/test/repo",
    "homepage": "",
    "license": "Apache-2.0",
    "default_branch": "master",
}

_CODEBERG_REPO_RESPONSE = {
    "owner": {"login": "test"},
    "name": "repo",
    "full_name": "test/repo",
    "description": "Codeberg repo",
    "stars_count": 30,
    "language": "Rust",
    "clone_url": "https://codeberg.org/test/repo.git",
    "website": "https://codeberg.com",
    "default_branch": "main",
    "fork": False,
    "archived": False,
}


# ── 参数化矩阵：5 平台 × 3 方法 ──

_PROVIDERS = [
    ("github", GitHubProvider, _GITHUB_REPO_RESPONSE),
    ("gitlab", GitLabProvider, _GITLAB_REPO_RESPONSE),
    ("bitbucket", BitbucketProvider, _BITBUCKET_REPO_RESPONSE),
    ("gitee", GiteeProvider, _GITEE_REPO_RESPONSE),
    ("codeberg", CodebergProvider, _CODEBERG_REPO_RESPONSE),
]


class TestProviderMetadata:
    """get_repo_metadata: 5 个平台 × 1 个参数化测试"""

    @pytest.mark.parametrize("platform,cls,mock_response", _PROVIDERS,
                             ids=[p[0] for p in _PROVIDERS])
    def test_get_repo_metadata(self, platform, cls, mock_response):
        provider = cls()
        with patch.object(provider, "_api_get", return_value=mock_response):
            meta = provider.get_repo_metadata("test", "repo")
        assert isinstance(meta, RepoMetadata)
        assert meta.platform == platform
        assert meta.repo == "repo"

    @pytest.mark.parametrize("platform,cls,_", _PROVIDERS,
                             ids=[p[0] for p in _PROVIDERS])
    def test_get_readme(self, platform, cls, _):
        provider = cls()
        with patch.object(provider, "_raw_get", return_value="# README"):
            readme = provider.get_readme("test", "repo")
        assert "README" in readme

    @pytest.mark.parametrize("platform,cls,_", _PROVIDERS,
                             ids=[p[0] for p in _PROVIDERS])
    def test_get_readme_not_found(self, platform, cls, _):
        provider = cls()
        with patch.object(provider, "_raw_get", return_value=""):
            readme = provider.get_readme("test", "repo")
        assert readme == ""

    @pytest.mark.parametrize("platform,cls,_", _PROVIDERS,
                             ids=[p[0] for p in _PROVIDERS])
    def test_get_file_content(self, platform, cls, _):
        provider = cls()
        with patch.object(provider, "_raw_get", return_value="import flask"):
            content = provider.get_file_content("test", "repo", "app.py")
        assert content == "import flask"

    @pytest.mark.parametrize("platform,cls,_", _PROVIDERS,
                             ids=[p[0] for p in _PROVIDERS])
    def test_get_clone_url(self, platform, cls, _):
        provider = cls()
        url = provider.get_clone_url("test", "repo")
        assert "test/repo" in url
        assert url.endswith(".git")


class TestProviderAuth:
    """Token 认证路径"""

    def test_github_auth_header(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        provider = GitHubProvider()
        headers = provider._auth_headers()
        assert headers["Authorization"] == "token ghp_test123"

    def test_github_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        provider = GitHubProvider()
        headers = provider._auth_headers()
        assert "Authorization" not in headers

    def test_gitlab_auth_header(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "gl-token")
        provider = GitLabProvider()
        headers = provider._auth_headers()
        assert headers["PRIVATE-TOKEN"] == "gl-token"

    def test_gitee_auth_params(self, monkeypatch):
        monkeypatch.setenv("GITEE_TOKEN", "gitee-token")
        provider = GiteeProvider()
        params = provider._auth_params()
        assert "access_token=gitee-token" in params

    def test_gitee_no_token(self, monkeypatch):
        monkeypatch.delenv("GITEE_TOKEN", raising=False)
        provider = GiteeProvider()
        assert provider._auth_params() == ""


class TestDetectPlatform:
    """URL → 平台自动检测"""

    @pytest.mark.parametrize("url,expected_platform", [
        ("https://github.com/owner/repo", "github"),
        ("https://gitlab.com/owner/repo", "gitlab"),
        ("https://bitbucket.org/owner/repo", "bitbucket"),
        ("https://gitee.com/owner/repo", "gitee"),
        ("https://codeberg.org/owner/repo", "codeberg"),
        ("git@github.com:owner/repo.git", "github"),
        ("git@gitlab.com:owner/repo.git", "gitlab"),
        ("owner/repo", "github"),
    ])
    def test_platform_detection(self, url, expected_platform):
        platform, owner, repo = detect_platform(url)
        assert platform == expected_platform
        if "/" in url and not url.startswith("http") and ":" not in url:
            assert owner == "owner"
        assert "repo" in repo

    def test_bare_project_name(self):
        platform, owner, repo = detect_platform("myproject")
        assert platform == "github"
        assert repo == "myproject"

    def test_trailing_slash(self):
        platform, owner, repo = detect_platform("https://github.com/owner/repo/")
        assert platform == "github"
        assert owner == "owner"


class TestGetProvider:
    """平台 Provider 工厂"""

    @pytest.mark.parametrize("platform", ["github", "gitlab", "bitbucket", "gitee", "codeberg"])
    def test_valid_platform(self, platform):
        provider = get_provider(platform)
        assert provider is not None

    def test_invalid_platform(self):
        with pytest.raises(ValueError, match="不支持的平台"):
            get_provider("sourceforge")


class TestFetchFromAnySource:
    """fetch_from_any_source: 端到端路由测试"""

    def test_github_fetch(self):
        with patch.object(GitHubProvider, "get_repo_metadata",
                         return_value=RepoMetadata(
                             platform="github", owner="test", repo="repo",
                             full_name="test/repo",
                         )):
            meta = fetch_from_any_source("test/repo")
            assert meta.platform == "github"

    def test_gitlab_fetch(self):
        with patch.object(GitLabProvider, "get_repo_metadata",
                         return_value=RepoMetadata(
                             platform="gitlab", owner="test", repo="repo",
                             full_name="test/repo",
                         )):
            meta = fetch_from_any_source("https://gitlab.com/test/repo")
            assert meta.platform == "gitlab"


class TestGetSupportedPlatforms:
    """get_supported_platforms: 平台列表"""

    def test_returns_all_platforms(self):
        platforms = get_supported_platforms()
        assert len(platforms) >= 5
        names = {p["key"] for p in platforms}
        assert "github" in names
        assert "gitlab" in names
        assert "gitee" in names

    def test_platform_structure(self):
        platforms = get_supported_platforms()
        for p in platforms:
            assert "name" in p
            assert "domain" in p
            assert "key" in p
