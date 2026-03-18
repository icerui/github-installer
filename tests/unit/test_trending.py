"""
test_trending.py - 热门项目爬取与缓存测试
==========================================
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from trending import (
    _fmt_stars,
    _item_to_project,
    _merge_with_old,
    _read_cache,
    _write_cache,
    _clean_for_frontend,
    _STATIC_TRENDING,
    _LANG_ICONS,
    _SEARCH_QUERIES,
    get_trending,
    _github_search,
    _fetch_all,
)


# ─────────────────────────────────────────────
#  _fmt_stars
# ─────────────────────────────────────────────

class TestFmtStars:
    def test_small(self):
        assert _fmt_stars(500) == "500"

    def test_thousand(self):
        result = _fmt_stars(1500)
        assert "1.5k" in result

    def test_ten_thousand(self):
        result = _fmt_stars(50000)
        assert "50k" in result

    def test_hundred_thousand(self):
        result = _fmt_stars(100000)
        assert "100k" in result

    def test_zero(self):
        assert _fmt_stars(0) == "0"


# ─────────────────────────────────────────────
#  _item_to_project
# ─────────────────────────────────────────────

class TestItemToProject:
    def test_basic_conversion(self):
        item = {
            "full_name": "user/repo",
            "name": "repo",
            "description": "A test repo",
            "stargazers_count": 50000,
            "language": "Python",
        }
        proj = _item_to_project(item, "AI")
        assert proj["repo"] == "user/repo"
        assert proj["name"] == "repo"
        assert proj["tag"] == "AI"
        assert proj["lang"] == "Python"
        assert proj["icon"] == "🐍"  # Python icon
        assert proj["_stars_num"] == 50000
        assert "50k" in proj["stars"]

    def test_no_language(self):
        item = {
            "full_name": "user/repo",
            "name": "repo",
            "language": None,
            "stargazers_count": 100,
        }
        proj = _item_to_project(item, "工具")
        assert proj["lang"] == ""
        assert proj["icon"] == "🔧"  # Tool tag icon

    def test_long_description_truncated(self):
        item = {
            "full_name": "user/repo",
            "name": "repo",
            "description": "x" * 200,
            "stargazers_count": 0,
            "language": "Go",
        }
        proj = _item_to_project(item, "Web")
        assert len(proj["desc"]) <= 100


# ─────────────────────────────────────────────
#  缓存读写
# ─────────────────────────────────────────────

class TestCache:
    def test_write_and_read(self, tmp_path):
        cache_file = tmp_path / "trending.json"
        projects = [{"repo": "a/b", "name": "b", "stars": "10k+"}]
        with patch("trending._CACHE_DIR", tmp_path), \
             patch("trending._CACHE_FILE", cache_file):
            _write_cache(projects)
            data = _read_cache()
            assert data is not None
            assert data["projects"] == projects

    def test_read_missing(self, tmp_path):
        cache_file = tmp_path / "nonexistent.json"
        with patch("trending._CACHE_FILE", cache_file):
            assert _read_cache() is None

    def test_read_corrupted(self, tmp_path):
        cache_file = tmp_path / "trending.json"
        cache_file.write_text("not json")
        with patch("trending._CACHE_FILE", cache_file):
            assert _read_cache() is None


# ─────────────────────────────────────────────
#  _merge_with_old
# ─────────────────────────────────────────────

class TestMerge:
    def test_new_only(self):
        new = [{"repo": "a/b", "_stars_num": 100}]
        result = _merge_with_old(new, [])
        assert len(result) == 1
        assert result[0]["_stale_count"] == 0

    def test_old_retained(self):
        old = [{"repo": "old/proj", "_stars_num": 50, "_stale_count": 0}]
        new = [{"repo": "new/proj", "_stars_num": 100}]
        result = _merge_with_old(new, old)
        repos = [r["repo"] for r in result]
        assert "old/proj" in repos
        assert "new/proj" in repos

    def test_stale_removed_after_3(self):
        old = [{"repo": "old/proj", "_stars_num": 50, "_stale_count": 3}]
        new = [{"repo": "new/proj", "_stars_num": 100}]
        result = _merge_with_old(new, old)
        repos = [r["repo"] for r in result]
        assert "old/proj" not in repos

    def test_update_existing(self):
        old = [{"repo": "a/b", "_stars_num": 50, "desc": "old desc", "_stale_count": 0}]
        new = [{"repo": "a/b", "_stars_num": 100, "desc": "new desc"}]
        result = _merge_with_old(new, old)
        assert len(result) == 1
        assert result[0]["desc"] == "new desc"
        assert result[0]["_stars_num"] == 100

    def test_top_100_limit(self):
        old = [{"repo": f"old/{i}", "_stars_num": i, "_stale_count": 0} for i in range(50)]
        new = [{"repo": f"new/{i}", "_stars_num": i + 100} for i in range(60)]
        result = _merge_with_old(new, old)
        assert len(result) <= 100

    def test_case_insensitive_merge(self):
        old = [{"repo": "User/Repo", "_stars_num": 50, "_stale_count": 0}]
        new = [{"repo": "user/repo", "_stars_num": 100}]
        result = _merge_with_old(new, old)
        assert len(result) == 1


# ─────────────────────────────────────────────
#  _clean_for_frontend
# ─────────────────────────────────────────────

class TestClean:
    def test_removes_internal_fields(self):
        projects = [
            {"repo": "a/b", "name": "b", "_stars_num": 100, "_stale_count": 0, "_fetched_at": 123},
        ]
        cleaned = _clean_for_frontend(projects)
        assert len(cleaned) == 1
        assert "_stars_num" not in cleaned[0]
        assert "_stale_count" not in cleaned[0]
        assert "_fetched_at" not in cleaned[0]
        assert cleaned[0]["repo"] == "a/b"


# ─────────────────────────────────────────────
#  _github_search (mock)
# ─────────────────────────────────────────────

class TestGithubSearch:
    def test_success(self):
        data = {"items": [{"full_name": "a/b", "name": "b", "stargazers_count": 100}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("trending.urllib.request.urlopen", return_value=mock_resp):
            result = _github_search("topic:ai stars:>5000")
            assert len(result) == 1
            assert result[0]["full_name"] == "a/b"


# ─────────────────────────────────────────────
#  _fetch_all (mock)
# ─────────────────────────────────────────────

class TestFetchAll:
    def test_fetch_all_deduplicates(self):
        items = [
            {"full_name": "a/b", "name": "b", "description": "test",
             "stargazers_count": 100, "language": "Python"},
        ]
        with patch("trending._github_search", return_value=items), \
             patch("trending.time.sleep"):
            result = _fetch_all()
            # Same repo appears in multiple queries but should be deduped
            repos = [r["repo"] for r in result]
            assert repos.count("a/b") == 1

    def test_fetch_all_network_error(self):
        with patch("trending._github_search", side_effect=Exception("network")), \
             patch("trending.time.sleep"):
            result = _fetch_all()
            assert result == []


# ─────────────────────────────────────────────
#  get_trending (integration mock)
# ─────────────────────────────────────────────

class TestGetTrending:
    def setup_method(self):
        import trending
        trending._mem_cache = None
        trending._mem_ts = 0.0

    def test_returns_static_when_no_cache(self, tmp_path):
        cache_file = tmp_path / "trending.json"
        with patch("trending._CACHE_FILE", cache_file), \
             patch("trending._read_cache", return_value=None), \
             patch("trending.Thread") as mock_thread:
            result = get_trending()
            assert len(result) > 0
            # Should be static fallback
            assert result[0]["repo"] == _STATIC_TRENDING[0]["repo"]
            # Should trigger background refresh
            mock_thread.assert_called()

    def test_returns_mem_cache(self):
        import trending
        trending._mem_cache = [
            {"repo": "cached/proj", "name": "proj", "_stars_num": 100}
        ]
        trending._mem_ts = time.time()
        result = get_trending()
        assert result[0]["repo"] == "cached/proj"
        assert "_stars_num" not in result[0]

    def test_disk_cache_fresh(self, tmp_path):
        import trending
        trending._mem_cache = None
        trending._mem_ts = 0.0
        disk_data = {
            "version": 1,
            "updated_at": time.time(),
            "projects": [{"repo": "disk/proj", "name": "proj", "_stars_num": 50}],
        }
        with patch("trending._read_cache", return_value=disk_data):
            result = get_trending()
            assert result[0]["repo"] == "disk/proj"

    def test_disk_cache_expired(self, tmp_path):
        import trending
        trending._mem_cache = None
        trending._mem_ts = 0.0
        disk_data = {
            "version": 1,
            "updated_at": time.time() - 100000,  # old
            "projects": [{"repo": "old/proj", "name": "proj", "_stars_num": 50}],
        }
        with patch("trending._read_cache", return_value=disk_data), \
             patch("trending.Thread") as mock_thread:
            result = get_trending()
            assert result[0]["repo"] == "old/proj"
            mock_thread.assert_called()  # background refresh

    def test_force_refresh(self):
        import trending
        trending._mem_cache = [{"repo": "old/proj", "_stars_num": 10}]
        trending._mem_ts = time.time()
        with patch("trending._read_cache", return_value=None), \
             patch("trending.Thread") as mock_thread:
            result = get_trending(force_refresh=True)
            assert len(result) > 0


# ─────────────────────────────────────────────
#  静态数据完整性
# ─────────────────────────────────────────────

class TestStaticData:
    def test_static_trending_not_empty(self):
        assert len(_STATIC_TRENDING) >= 10

    def test_static_has_required_fields(self):
        for proj in _STATIC_TRENDING:
            assert "repo" in proj
            assert "name" in proj
            assert "desc" in proj
            assert "/" in proj["repo"]

    def test_lang_icons_coverage(self):
        assert "python" in _LANG_ICONS
        assert "javascript" in _LANG_ICONS
        assert "go" in _LANG_ICONS
        assert "rust" in _LANG_ICONS

    def test_search_queries_complete(self):
        tags = {q[1] for q in _SEARCH_QUERIES}
        assert "AI" in tags
        assert "Web" in tags
        assert "工具" in tags
        assert "IoT" in tags
