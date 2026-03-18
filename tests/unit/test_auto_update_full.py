"""auto_update.py 全覆盖测试

覆盖目标：InstallTracker CRUD + check_for_update + check_all_updates +
          format_installed_list + format_update_results
"""
import json
import os
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from auto_update import (
    InstalledProject,
    UpdateInfo,
    InstallTracker,
    check_for_update,
    check_all_updates,
    format_installed_list,
    format_update_results,
)


# ─── InstallTracker ───


class TestInstallTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        return InstallTracker(data_file=tmp_path / "installed.json")

    def test_record_install(self, tracker):
        p = tracker.record_install("owner", "repo", "/tmp/repo", commit="abc123")
        assert p.owner == "owner"
        assert p.installed_commit == "abc123"

    def test_list_installed(self, tracker):
        tracker.record_install("a", "b", "/tmp/b")
        tracker.record_install("c", "d", "/tmp/d")
        lst = tracker.list_installed()
        assert len(lst) == 2

    def test_get_project_found(self, tracker):
        tracker.record_install("owner", "repo", "/tmp/repo")
        p = tracker.get_project("owner", "repo")
        assert p is not None
        assert p.repo == "repo"

    def test_get_project_not_found(self, tracker):
        assert tracker.get_project("x", "y") is None

    def test_remove_project(self, tracker):
        tracker.record_install("owner", "repo", "/tmp/repo")
        assert tracker.remove_project("owner", "repo") is True
        assert tracker.get_project("owner", "repo") is None

    def test_remove_project_not_found(self, tracker):
        assert tracker.remove_project("x", "y") is False

    def test_update_check_time(self, tracker):
        tracker.record_install("owner", "repo", "/tmp/repo")
        tracker.update_check_time("owner", "repo")
        p = tracker.get_project("owner", "repo")
        assert p.last_check != ""

    def test_set_auto_update(self, tracker):
        tracker.record_install("owner", "repo", "/tmp/repo")
        assert tracker.set_auto_update("owner", "repo", True) is True
        p = tracker.get_project("owner", "repo")
        assert p.auto_update is True

    def test_set_auto_update_not_found(self, tracker):
        assert tracker.set_auto_update("x", "y", True) is False

    def test_overwrite_existing(self, tracker):
        tracker.record_install("owner", "repo", "/tmp/v1", commit="aaa")
        tracker.record_install("owner", "repo", "/tmp/v2", commit="bbb")
        lst = tracker.list_installed()
        assert len(lst) == 1
        assert lst[0].installed_commit == "bbb"

    def test_save_chmod_failure(self, tmp_path):
        tracker = InstallTracker(data_file=tmp_path / "installed.json")
        with patch("os.chmod", side_effect=OSError("no chmod")):
            tracker.record_install("o", "r", "/tmp/r")
        assert tracker.list_installed()

    def test_load_corrupt_json(self, tmp_path):
        f = tmp_path / "installed.json"
        f.write_text("{{bad json")
        tracker = InstallTracker(data_file=f)
        assert tracker.list_installed() == []


# ─── check_for_update ───


class TestCheckForUpdate:
    def _project(self, commit="abc1234567", tag="v1.0", branch="main"):
        return InstalledProject(
            owner="owner", repo="repo", install_dir="/tmp/repo",
            installed_at="2024-01-01T00:00:00Z",
            installed_commit=commit, installed_tag=tag,
            installed_branch=branch,
        )

    def test_has_update_new_commit(self):
        def mock_urlopen(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            data = {}
            if "/commits/" in url:
                data = {"sha": "def456789012"}
            elif "/releases/latest" in url:
                data = {"tag_name": "v1.0"}
            elif "/compare/" in url:
                data = {"ahead_by": 5}
            m = MagicMock()
            m.read.return_value = json.dumps(data).encode()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            info = check_for_update(self._project())
        assert info.has_update is True
        assert info.commits_behind == 5

    def test_no_update(self):
        def mock_urlopen(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            data = {}
            if "/commits/" in url:
                data = {"sha": "abc1234567aa"}  # starts with same prefix
            elif "/releases/latest" in url:
                data = {"tag_name": "v1.0"}
            elif "/compare/" in url:
                data = {"ahead_by": 0}
            m = MagicMock()
            m.read.return_value = json.dumps(data).encode()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            info = check_for_update(self._project())
        assert info.error == ""

    def test_api_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            info = check_for_update(self._project())
        assert info.error != ""

    def test_release_404(self):
        import urllib.error
        call_count = [0]

        def mock_urlopen(req, **kw):
            call_count[0] += 1
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if "/commits/" in url:
                m = MagicMock()
                m.read.return_value = json.dumps({"sha": "abc1234567aa"}).encode()
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "/releases/" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/compare/" in url:
                m = MagicMock()
                m.read.return_value = json.dumps({"ahead_by": 0}).encode()
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            info = check_for_update(self._project())
        assert info.error == ""

    def test_new_tag_detected(self):
        def mock_urlopen(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            data = {}
            if "/commits/" in url:
                data = {"sha": "abc1234567aa"}
            elif "/releases/latest" in url:
                data = {"tag_name": "v2.0", "name": "Major Release",
                        "body": "Breaking changes", "published_at": "2024-06-01"}
            elif "/compare/" in url:
                data = {"ahead_by": 0}
            m = MagicMock()
            m.read.return_value = json.dumps(data).encode()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            info = check_for_update(self._project())
        assert info.has_update is True
        assert info.latest_tag == "v2.0"
        assert info.latest_release_name == "Major Release"


# ─── check_all_updates ───


class TestCheckAllUpdates:
    def test_check_all(self, tmp_path):
        tracker = InstallTracker(data_file=tmp_path / "installed.json")
        tracker.record_install("a", "b", "/tmp/b", commit="abc")

        def mock_urlopen(req, **kw):
            m = MagicMock()
            m.read.return_value = json.dumps({"sha": "abc1234567"}).encode()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            results = check_all_updates(tracker)
        assert len(results) == 1


# ─── format_installed_list ───


class TestFormatInstalledList:
    def test_empty(self):
        result = format_installed_list([])
        assert "未记录" in result

    def test_with_projects(self):
        projects = [
            InstalledProject(
                owner="a", repo="b", install_dir="/tmp/b",
                installed_at="2024-01-01T00:00:00Z",
                installed_commit="abc1234", installed_tag="v1.0",
                auto_update=True, last_check="2024-06-01T00:00:00Z",
            ),
            InstalledProject(
                owner="c", repo="d", install_dir="/tmp/d",
                installed_at="2024-02-01T00:00:00Z",
            ),
        ]
        result = format_installed_list(projects)
        assert "a/b" in result
        assert "v1.0" in result
        assert "abc1234" in result
        assert "2 个项目" in result


# ─── format_update_results ───


class TestFormatUpdateResults:
    def test_empty(self):
        result = format_update_results([])
        assert "没有" in result

    def test_mixed_results(self):
        updates = [
            UpdateInfo(owner="a", repo="b", has_update=True,
                       commits_behind=5, current_tag="v1", latest_tag="v2",
                       latest_release_name="New Release",
                       latest_release_body="Line1\nLine2\nLine3\nLine4"),
            UpdateInfo(owner="c", repo="d", has_update=False),
            UpdateInfo(owner="e", repo="f", error="API error"),
        ]
        result = format_update_results(updates)
        assert "a/b" in result
        assert "v2" in result
        assert "5 个 commit" in result
        assert "c/d" in result
        assert "已是最新" in result
        assert "API error" in result


# ─── InstalledProject / UpdateInfo dataclasses ───


class TestDataclasses:
    def test_installed_project_to_from_dict(self):
        p = InstalledProject(owner="a", repo="b", install_dir="/tmp",
                             installed_at="2024-01-01")
        d = p.to_dict()
        p2 = InstalledProject.from_dict(d)
        assert p2.owner == "a"
        assert p2.full_name == "a/b"

    def test_update_info_defaults(self):
        u = UpdateInfo(owner="a", repo="b")
        assert u.has_update is False
        assert u.error == ""
