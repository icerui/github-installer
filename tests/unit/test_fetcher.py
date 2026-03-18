"""
test_fetcher.py - fetcher.py 项目信息抓取测试
===============================================
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from fetcher import (
    parse_repo_identifier,
    detect_project_types,
    format_project_summary,
    RepoInfo,
    GitHubFetcher,
    _cache_path,
    _cache_read,
    _cache_write,
    _KNOWN_DEP_FILES,
    _detect_language_from_files,
    _find_readme,
    _extract_local_dep_files,
    extract_dependency_files,
    fetch_project,
    fetch_project_local,
)


# ─────────────────────────────────────────────
#  URL/名称解析
# ─────────────────────────────────────────────

class TestParseRepoIdentifier:
    def test_full_url(self):
        owner, repo = parse_repo_identifier("https://github.com/user/repo")
        assert owner == "user"
        assert repo == "repo"

    def test_url_with_git(self):
        owner, repo = parse_repo_identifier("https://github.com/user/repo.git")
        assert owner == "user"
        assert repo == "repo"

    def test_url_with_tree(self):
        owner, repo = parse_repo_identifier("https://github.com/user/repo/tree/main")
        assert owner == "user"
        assert repo == "repo"

    def test_owner_repo_format(self):
        owner, repo = parse_repo_identifier("comfyanonymous/ComfyUI")
        assert owner == "comfyanonymous"
        assert repo == "ComfyUI"

    def test_github_dot_com(self):
        owner, repo = parse_repo_identifier("github.com/user/repo")
        assert owner == "user"
        assert repo == "repo"

    def test_name_only(self):
        owner, repo = parse_repo_identifier("ComfyUI")
        assert owner == ""
        assert repo == "ComfyUI"

    def test_whitespace_stripped(self):
        owner, repo = parse_repo_identifier("  user/repo  ")
        assert owner == "user"
        assert repo == "repo"

    def test_invalid_owner_rejected(self):
        with pytest.raises(ValueError, match="无效的仓库所有者"):
            parse_repo_identifier("../evil/repo")

    def test_invalid_repo_rejected(self):
        with pytest.raises(ValueError, match="无效的仓库名"):
            parse_repo_identifier("user/..")

    def test_ssh_style_url(self):
        owner, repo = parse_repo_identifier("git@github.com:user/repo.git")
        assert owner == "user"
        assert repo == "repo"


# ─────────────────────────────────────────────
#  项目类型识别
# ─────────────────────────────────────────────

class TestDetectProjectTypes:
    def test_python_from_language(self):
        types = detect_project_types({"language": "Python"}, "", {})
        assert "python" in types

    def test_node_from_language(self):
        types = detect_project_types({"language": "JavaScript"}, "", {})
        assert "node" in types

    def test_typescript_from_language(self):
        types = detect_project_types({"language": "TypeScript"}, "", {})
        assert "node" in types

    def test_rust_from_language(self):
        types = detect_project_types({"language": "Rust"}, "", {})
        assert "rust" in types

    def test_from_requirements_txt(self):
        types = detect_project_types({}, "", {"requirements.txt": "flask"})
        assert "python" in types

    def test_from_package_json(self):
        types = detect_project_types({}, "", {"package.json": "{}"})
        assert "node" in types

    def test_from_cargo_toml(self):
        types = detect_project_types({}, "", {"Cargo.toml": ""})
        assert "rust" in types

    def test_from_dockerfile(self):
        types = detect_project_types({}, "", {"Dockerfile": "FROM python"})
        assert "docker" in types

    def test_pytorch_from_readme(self):
        types = detect_project_types({}, "pip install torch", {})
        assert "pytorch" in types

    def test_docker_from_readme(self):
        types = detect_project_types({}, "run `docker-compose up`", {})
        assert "docker" in types

    def test_conda_from_readme(self):
        types = detect_project_types({}, "conda install numpy", {})
        assert "conda" in types

    def test_conda_word_boundary(self):
        # "secondary" 不应匹配 conda
        types = detect_project_types({}, "secondary option", {})
        assert "conda" not in types

    def test_multiple_types(self):
        types = detect_project_types(
            {"language": "Python"},
            "pip install torch",
            {"requirements.txt": "torch", "Dockerfile": "FROM python"},
        )
        assert "python" in types
        assert "pytorch" in types
        assert "docker" in types

    def test_cabal_file(self):
        types = detect_project_types({}, "", {"myproject.cabal": ""})
        assert "haskell" in types

    def test_stack_yaml(self):
        types = detect_project_types({}, "", {"stack.yaml": ""})
        assert "haskell" in types

    def test_go_mod(self):
        types = detect_project_types({}, "", {"go.mod": ""})
        assert "go" in types

    def test_haskell_language(self):
        types = detect_project_types({"language": "Haskell"}, "", {})
        assert "haskell" in types

    def test_unknown_language(self):
        types = detect_project_types({"language": "UnknownLang"}, "", {})
        assert len(types) == 0

    def test_no_language(self):
        types = detect_project_types({}, "", {})
        assert isinstance(types, list)


# ─────────────────────────────────────────────
#  缓存
# ─────────────────────────────────────────────

class TestCache:
    def test_cache_path_deterministic(self):
        p1 = _cache_path("http://example.com/foo")
        p2 = _cache_path("http://example.com/foo")
        assert p1 == p2

    def test_cache_path_different_urls(self):
        p1 = _cache_path("http://example.com/foo")
        p2 = _cache_path("http://example.com/bar")
        assert p1 != p2

    def test_cache_roundtrip(self, tmp_path):
        with patch("fetcher._CACHE_DIR", tmp_path), \
             patch("fetcher._NO_CACHE", False):
            url = "http://test.example.com/data"
            data = {"key": "value"}
            _cache_write(url, data)
            result = _cache_read(url)
            assert result == data

    def test_cache_no_cache_mode(self, tmp_path):
        with patch("fetcher._CACHE_DIR", tmp_path), \
             patch("fetcher._NO_CACHE", True):
            _cache_write("http://test.example.com/x", {"a": 1})
            result = _cache_read("http://test.example.com/x")
            assert result is None

    def test_cache_miss(self, tmp_path):
        with patch("fetcher._CACHE_DIR", tmp_path), \
             patch("fetcher._NO_CACHE", False):
            result = _cache_read("http://nonexistent.example.com/data")
            assert result is None


# ─────────────────────────────────────────────
#  RepoInfo + format
# ─────────────────────────────────────────────

class TestRepoInfo:
    def test_dataclass_creation(self):
        info = RepoInfo(
            owner="user", repo="repo", full_name="user/repo",
            description="test", stars=100, language="Python",
            license="MIT", default_branch="main", readme="# Hello",
            project_type=["python"], dependency_files={},
            clone_url="https://github.com/user/repo.git",
            homepage="https://github.com/user/repo",
        )
        assert info.full_name == "user/repo"

    def test_format_summary(self):
        info = RepoInfo(
            owner="pytorch", repo="pytorch", full_name="pytorch/pytorch",
            description="PyTorch deep learning framework", stars=85000,
            language="Python", license="BSD-3-Clause", default_branch="main",
            readme="", project_type=["python", "pytorch"],
            dependency_files={}, clone_url="https://github.com/pytorch/pytorch.git",
            homepage="https://pytorch.org",
        )
        text = format_project_summary(info)
        assert "pytorch/pytorch" in text
        assert "85,000" in text
        assert "Python" in text


# ─────────────────────────────────────────────
#  本地分析辅助函数
# ─────────────────────────────────────────────

class TestLocalAnalysis:
    def test_detect_language(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "util.py").write_text("def x(): pass")
        (tmp_path / "app.js").write_text("console.log()")
        result = _detect_language_from_files(tmp_path)
        assert result == "Python"

    def test_detect_language_no_files(self, tmp_path):
        result = _detect_language_from_files(tmp_path)
        assert result == "Unknown"

    def test_detect_language_skips_tests(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("test")
        (tmp_path / "tests" / "test_b.py").write_text("test")
        (tmp_path / "main.rs").write_text("fn main()")
        result = _detect_language_from_files(tmp_path)
        assert result == "Rust"

    def test_find_readme(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello")
        result = _find_readme(tmp_path)
        assert "Hello" in result

    def test_find_readme_missing(self, tmp_path):
        result = _find_readme(tmp_path)
        assert result == ""

    def test_find_readme_lowercase(self, tmp_path):
        (tmp_path / "readme.md").write_text("# hi")
        result = _find_readme(tmp_path)
        assert "hi" in result

    def test_extract_dep_files(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\nrequests")
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "README.md").write_text("# hello")  # not a dep file
        result = _extract_local_dep_files(tmp_path)
        assert "requirements.txt" in result
        assert "package.json" in result
        assert "README.md" not in result

    def test_extract_dep_files_skips_venv(self, tmp_path):
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "requirements.txt").write_text("internal")
        (tmp_path / "requirements.txt").write_text("real")
        result = _extract_local_dep_files(tmp_path)
        assert len([k for k in result if k.endswith("requirements.txt")]) == 1

    def test_extract_cabal_file(self, tmp_path):
        (tmp_path / "myproject.cabal").write_text("name: myproject")
        result = _extract_local_dep_files(tmp_path)
        assert "myproject.cabal" in result


# ─────────────────────────────────────────────
#  KNOWN_DEP_FILES 完整性
# ─────────────────────────────────────────────

class TestKnownDepFiles:
    def test_contains_major_files(self):
        expected = [
            "requirements.txt", "setup.py", "pyproject.toml",
            "package.json", "Cargo.toml", "go.mod",
            "Dockerfile", "docker-compose.yml",
        ]
        for f in expected:
            assert f in _KNOWN_DEP_FILES, f"Missing: {f}"


# ─────────────────────────────────────────────
#  GitHubFetcher (mocked network)
# ─────────────────────────────────────────────

class TestGitHubFetcher:
    def _mock_urlopen(self, body, status=200, content_type="application/json"):
        """Create a mock urlopen context manager."""
        import io
        mock_resp = MagicMock()
        mock_resp.read.return_value = body.encode("utf-8") if isinstance(body, str) else body
        mock_resp.headers = {"Content-Type": content_type}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_get_json(self, tmp_path):
        f = GitHubFetcher()
        data = {"name": "test_repo", "stargazers_count": 100}
        mock = self._mock_urlopen(json.dumps(data))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            result = f._get("https://api.github.com/repos/user/repo")
            assert result["name"] == "test_repo"

    def test_get_404(self):
        import urllib.error
        f = GitHubFetcher()
        err = urllib.error.HTTPError("url", 404, "Not Found", {}, MagicMock(read=lambda: b""))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", side_effect=err):
            with pytest.raises(FileNotFoundError):
                f._get("https://api.github.com/repos/user/nonexistent")

    def test_get_403_rate_limit(self):
        import urllib.error
        f = GitHubFetcher()
        headers = MagicMock()
        headers.get.return_value = None
        err = urllib.error.HTTPError("url", 403, "Forbidden", headers, MagicMock(read=lambda: b""))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", side_effect=err):
            with pytest.raises(PermissionError, match="GitHub API 频率超限"):
                f._get("https://api.github.com/repos/user/repo", _retries=0)

    def test_get_raw_ssrf_blocked(self):
        f = GitHubFetcher()
        result = f._get_raw("http://evil.example.com/malicious")
        assert result is None

    def test_get_304_etag_reuse(self):
        """ETag 条件请求: 304 Not Modified → 复用缓存数据（不消耗配额）"""
        import urllib.error
        f = GitHubFetcher()
        url = "https://api.github.com/repos/user/repo"
        old_data = {"name": "repo", "stargazers_count": 100}

        # 模拟: 缓存过期但有 ETag → 发送 If-None-Match → 收到 304
        headers = MagicMock()
        headers.get.return_value = None
        err304 = urllib.error.HTTPError(url, 304, "Not Modified", headers, MagicMock(read=lambda: b""))
        with patch("fetcher._cache_read", return_value=None), \
             patch("fetcher._cache_read_etag", return_value=('"abc123"', old_data)), \
             patch("fetcher._cache_write") as mock_write, \
             patch("fetcher.urllib.request.urlopen", side_effect=err304):
            result = f._get(url)
        assert result == old_data
        mock_write.assert_called_once()  # 刷新了 TTL

    def test_get_403_fallback_to_stale_cache(self):
        """被限速时降级使用过期缓存"""
        import urllib.error
        f = GitHubFetcher()
        url = "https://api.github.com/repos/user/repo"
        old_data = {"name": "repo"}
        headers = MagicMock()
        headers.get.return_value = None
        err403 = urllib.error.HTTPError(url, 403, "Forbidden", headers, MagicMock(read=lambda: b""))
        with patch("fetcher._cache_read", return_value=None), \
             patch("fetcher._cache_read_etag", return_value=(None, old_data)), \
             patch("fetcher.urllib.request.urlopen", side_effect=err403):
            result = f._get(url, _retries=0)
        assert result == old_data  # 降级返回旧数据而非报错

    def test_get_raw_success(self):
        f = GitHubFetcher()
        mock = self._mock_urlopen("flask\nrequests", content_type="text/plain")
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            result = f._get_raw("https://raw.githubusercontent.com/user/repo/main/requirements.txt")
            assert "flask" in result

    def test_search_repo(self):
        f = GitHubFetcher()
        data = {"items": [{"owner": {"login": "user"}, "name": "repo"}]}
        mock = self._mock_urlopen(json.dumps(data))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            owner, repo = f.search_repo("repo")
            assert owner == "user"
            assert repo == "repo"

    def test_search_repo_not_found(self):
        f = GitHubFetcher()
        data = {"items": []}
        mock = self._mock_urlopen(json.dumps(data))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            with pytest.raises(FileNotFoundError):
                f.search_repo("nonexistent12345")

    def test_fetch_readme_api(self):
        import base64
        f = GitHubFetcher()
        content = base64.b64encode(b"# Hello World").decode()
        data = {"encoding": "base64", "content": content}
        mock = self._mock_urlopen(json.dumps(data))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            result = f.fetch_readme("user", "repo")
            assert "Hello World" in result

    def test_fetch_repo_info(self):
        f = GitHubFetcher()
        data = {"name": "repo", "stargazers_count": 50, "default_branch": "main"}
        mock = self._mock_urlopen(json.dumps(data))
        with patch("fetcher._NO_CACHE", True), \
             patch("fetcher.urllib.request.urlopen", return_value=mock):
            result = f.fetch_repo_info("user", "repo")
            assert result["name"] == "repo"


# ─────────────────────────────────────────────
#  extract_dependency_files
# ─────────────────────────────────────────────

class TestExtractDependencyFiles:
    def test_extract_from_contents_api(self):
        f = GitHubFetcher()
        contents = [
            {"name": "requirements.txt", "type": "file"},
            {"name": "README.md", "type": "file"},
            {"name": "src", "type": "dir"},
        ]
        def mock_get(url, timeout=15, _retries=2):
            if "contents" in url:
                return contents
            return None

        with patch.object(f, "_get", side_effect=mock_get), \
             patch.object(f, "_get_raw", return_value="flask\nrequests"):
            result = extract_dependency_files(f, "user", "repo", "main")
            assert "requirements.txt" in result


# ─────────────────────────────────────────────
#  fetch_project (full integration mock)
# ─────────────────────────────────────────────

class TestFetchProject:
    def test_fetch_project_full(self):
        repo_data = {
            "name": "repo", "owner": {"login": "user"},
            "description": "A tool", "stargazers_count": 500,
            "language": "Python", "license": {"spdx_id": "MIT"},
            "default_branch": "main",
            "clone_url": "https://github.com/user/repo.git",
            "homepage": "https://repo.dev",
        }
        contents = [
            {"name": "requirements.txt", "type": "file"},
            {"name": "README.md", "type": "file"},
        ]
        import base64
        readme_b64 = base64.b64encode(b"# Repo\npip install repo").decode()

        call_count = {"n": 0}
        def mock_get(url, timeout=15, _retries=2):
            call_count["n"] += 1
            if "/repos/user/repo/readme" in url:
                return {"encoding": "base64", "content": readme_b64}
            if "/repos/user/repo/contents" in url:
                return contents
            if "/repos/user/repo" in url:
                return repo_data
            return None

        f = GitHubFetcher()
        with patch("fetcher.GitHubFetcher", return_value=f), \
             patch.object(f, "_get", side_effect=mock_get), \
             patch.object(f, "_get_raw", return_value="flask\nrequests"):
            result = fetch_project("user/repo")
            assert result.full_name == "user/repo"
            assert result.language == "Python"
            assert "python" in result.project_type


# ─────────────────────────────────────────────
#  fetch_project_local
# ─────────────────────────────────────────────

class TestFetchProjectLocal:
    def test_name_only_rejected(self):
        with pytest.raises(ValueError, match="本地模式需要完整"):
            fetch_project_local("ComfyUI")

    def test_clone_success(self, tmp_path):
        clone_target = tmp_path / "repo"
        clone_target.mkdir()
        (clone_target / "README.md").write_text("# Test Repo")
        (clone_target / "requirements.txt").write_text("flask")
        (clone_target / "main.py").write_text("print('hello')")

        def fake_subprocess_run(cmd, **kwargs):
            # Simulate git clone by copying files
            import shutil
            dest = Path(cmd[-1])
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(clone_target, dest)
            mock_result = MagicMock()
            mock_result.returncode = 0
            return mock_result

        with patch("fetcher.subprocess.run", side_effect=fake_subprocess_run):
            result = fetch_project_local("user/repo")
            assert result.full_name == "user/repo"
            assert "python" in result.project_type
            assert "Test Repo" in result.readme

    def test_clone_not_found(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "repository not found"
        with patch("fetcher.subprocess.run", return_value=mock_result):
            with pytest.raises(FileNotFoundError):
                fetch_project_local("user/nonexistent")

    def test_clone_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "other error"
        with patch("fetcher.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="git clone 失败"):
                fetch_project_local("user/repo")
