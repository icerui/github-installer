"""
Tests for multi_source.py - 多源代码托管平台支持
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from multi_source import (
    detect_platform, get_provider, get_supported_platforms,
    GitHubProvider, GitLabProvider, BitbucketProvider,
    GiteeProvider, CodebergProvider,
    RepoMetadata,
)


class TestDetectPlatform:
    """测试平台自动识别"""

    def test_github_https(self):
        p, o, r = detect_platform("https://github.com/pytorch/pytorch")
        assert p == "github"
        assert o == "pytorch"
        assert r == "pytorch"

    def test_github_with_git_suffix(self):
        p, o, r = detect_platform("https://github.com/user/repo.git")
        assert p == "github"
        assert o == "user"
        assert r == "repo"

    def test_github_ssh(self):
        p, o, r = detect_platform("git@github.com:user/repo.git")
        assert p == "github"
        assert o == "user"
        assert r == "repo"

    def test_gitlab_https(self):
        p, o, r = detect_platform("https://gitlab.com/inkscape/inkscape")
        assert p == "gitlab"
        assert o == "inkscape"
        assert r == "inkscape"

    def test_gitlab_with_path(self):
        p, o, r = detect_platform("https://gitlab.com/gnome/gnome-shell/")
        assert p == "gitlab"
        assert o == "gnome"
        assert r == "gnome-shell"

    def test_bitbucket(self):
        p, o, r = detect_platform("https://bitbucket.org/atlassian/python-bitbucket")
        assert p == "bitbucket"
        assert o == "atlassian"
        assert r == "python-bitbucket"

    def test_gitee(self):
        p, o, r = detect_platform("https://gitee.com/openharmony/docs")
        assert p == "gitee"
        assert o == "openharmony"
        assert r == "docs"

    def test_codeberg(self):
        p, o, r = detect_platform("https://codeberg.org/forgejo/forgejo")
        assert p == "codeberg"
        assert o == "forgejo"
        assert r == "forgejo"

    def test_owner_repo_defaults_github(self):
        p, o, r = detect_platform("pytorch/pytorch")
        assert p == "github"
        assert o == "pytorch"
        assert r == "pytorch"

    def test_bare_name_defaults_github(self):
        p, o, r = detect_platform("ComfyUI")
        assert p == "github"
        assert o == ""
        assert r == "ComfyUI"

    def test_trailing_slash(self):
        p, o, r = detect_platform("https://github.com/user/repo/")
        assert p == "github"
        assert o == "user"
        assert r == "repo"

    def test_gitee_with_git(self):
        p, o, r = detect_platform("https://gitee.com/user/repo.git")
        assert p == "gitee"
        assert r == "repo"


class TestGetProvider:
    def test_github(self):
        p = get_provider("github")
        assert isinstance(p, GitHubProvider)

    def test_gitlab(self):
        p = get_provider("gitlab")
        assert isinstance(p, GitLabProvider)

    def test_bitbucket(self):
        p = get_provider("bitbucket")
        assert isinstance(p, BitbucketProvider)

    def test_gitee(self):
        p = get_provider("gitee")
        assert isinstance(p, GiteeProvider)

    def test_codeberg(self):
        p = get_provider("codeberg")
        assert isinstance(p, CodebergProvider)

    def test_invalid_platform(self):
        try:
            get_provider("unknown")
            assert False, "应该抛出 ValueError"
        except ValueError:
            pass


class TestGitHubProvider:
    def test_clone_url(self):
        p = GitHubProvider()
        url = p.get_clone_url("pytorch", "pytorch")
        assert url == "https://github.com/pytorch/pytorch.git"

    def test_auth_headers_no_token(self):
        with patch.dict("os.environ", {}, clear=True):
            p = GitHubProvider()
            p._token = ""
            h = p._auth_headers()
            assert "Authorization" not in h

    def test_auth_headers_with_token(self):
        p = GitHubProvider()
        p._token = "ghp_test123"
        h = p._auth_headers()
        assert h["Authorization"] == "token ghp_test123"


class TestGitLabProvider:
    def test_project_id_encoding(self):
        p = GitLabProvider()
        pid = p._project_id("inkscape", "inkscape")
        assert pid == "inkscape%2Finkscape"

    def test_clone_url(self):
        p = GitLabProvider()
        url = p.get_clone_url("inkscape", "inkscape")
        assert "gitlab.com" in url


class TestBitbucketProvider:
    def test_clone_url(self):
        p = BitbucketProvider()
        url = p.get_clone_url("user", "repo")
        assert "bitbucket.org" in url


class TestGiteeProvider:
    def test_clone_url(self):
        p = GiteeProvider()
        url = p.get_clone_url("user", "repo")
        assert "gitee.com" in url

    def test_auth_params_no_token(self):
        p = GiteeProvider()
        p._token = ""
        assert p._auth_params() == ""

    def test_auth_params_with_token(self):
        p = GiteeProvider()
        p._token = "test_token"
        assert "access_token=test_token" in p._auth_params()


class TestCodebergProvider:
    def test_clone_url(self):
        p = CodebergProvider()
        url = p.get_clone_url("forgejo", "forgejo")
        assert "codeberg.org" in url


class TestRepoMetadata:
    def test_creation(self):
        meta = RepoMetadata(
            platform="github",
            owner="pytorch",
            repo="pytorch",
            full_name="pytorch/pytorch",
            description="Tensors",
            stars=80000,
            language="Python",
        )
        assert meta.platform == "github"
        assert meta.stars == 80000
        assert meta.is_fork is False
        assert meta.is_archived is False

    def test_defaults(self):
        meta = RepoMetadata(
            platform="gitlab",
            owner="a",
            repo="b",
            full_name="a/b",
        )
        assert meta.stars == 0
        assert meta.default_branch == "main"
        assert meta.topics == []


class TestGetSupportedPlatforms:
    def test_returns_all(self):
        platforms = get_supported_platforms()
        assert len(platforms) == 5
        names = [p["key"] for p in platforms]
        assert "github" in names
        assert "gitlab" in names
        assert "bitbucket" in names
        assert "gitee" in names
        assert "codeberg" in names
