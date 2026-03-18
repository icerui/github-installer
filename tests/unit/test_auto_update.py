"""tests/unit/test_auto_update.py - 自动更新追踪测试"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from auto_update import (
    InstalledProject,
    UpdateInfo,
    InstallTracker,
    format_installed_list,
    format_update_results,
    updates_to_dict,
)


# ─────────────────────────────────────────────
#  InstalledProject 测试
# ─────────────────────────────────────────────

class TestInstalledProject:
    def test_full_name(self):
        p = InstalledProject(owner="pytorch", repo="pytorch", install_dir="/tmp/pytorch",
                             installed_at="2025-01-01T00:00:00Z")
        assert p.full_name == "pytorch/pytorch"

    def test_to_dict_roundtrip(self):
        p = InstalledProject(
            owner="torvalds", repo="linux", install_dir="/tmp/linux",
            installed_at="2025-01-01T00:00:00Z",
            installed_commit="abc1234", installed_tag="v6.7",
        )
        d = p.to_dict()
        p2 = InstalledProject.from_dict(d)
        assert p2.owner == "torvalds"
        assert p2.repo == "linux"
        assert p2.installed_tag == "v6.7"

    def test_from_dict_defaults(self):
        p = InstalledProject.from_dict({})
        assert p.owner == ""
        assert p.installed_branch == "main"
        assert p.auto_update is False


# ─────────────────────────────────────────────
#  InstallTracker 测试
# ─────────────────────────────────────────────

class TestInstallTracker:
    def _make_tracker(self, tmp_path):
        return InstallTracker(data_file=Path(tmp_path) / "installed.json")

    def test_empty_list(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        assert tracker.list_installed() == []

    def test_record_and_list(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("pytorch", "pytorch", "/tmp/pytorch", commit="abc123")
        projects = tracker.list_installed()
        assert len(projects) == 1
        assert projects[0].owner == "pytorch"
        assert projects[0].installed_commit == "abc123"

    def test_record_overwrites_duplicate(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("user", "repo", "/tmp/v1", tag="v1.0")
        tracker.record_install("user", "repo", "/tmp/v2", tag="v2.0")
        projects = tracker.list_installed()
        assert len(projects) == 1
        assert projects[0].installed_tag == "v2.0"

    def test_get_project(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("owner", "repo", "/tmp/test")
        p = tracker.get_project("owner", "repo")
        assert p is not None
        assert p.full_name == "owner/repo"

    def test_get_project_not_found(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        assert tracker.get_project("no", "exist") is None

    def test_remove_project(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("a", "b", "/tmp/ab")
        tracker.record_install("c", "d", "/tmp/cd")
        assert tracker.remove_project("a", "b") is True
        assert len(tracker.list_installed()) == 1
        assert tracker.list_installed()[0].owner == "c"

    def test_remove_nonexistent(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        assert tracker.remove_project("no", "exist") is False

    def test_set_auto_update(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("a", "b", "/tmp/ab")
        assert tracker.set_auto_update("a", "b", True) is True
        p = tracker.get_project("a", "b")
        assert p.auto_update is True

    def test_set_auto_update_not_found(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        assert tracker.set_auto_update("no", "exist", True) is False

    def test_update_check_time(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("a", "b", "/tmp/ab")
        tracker.update_check_time("a", "b")
        p = tracker.get_project("a", "b")
        assert p.last_check != ""

    def test_case_insensitive_lookup(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_install("PyTorch", "PyTorch", "/tmp/pt")
        assert tracker.get_project("pytorch", "pytorch") is not None

    def test_corrupted_file_handled(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        # Write corrupted data
        data_file = Path(tmp_path) / "installed.json"
        data_file.parent.mkdir(parents=True, exist_ok=True)
        data_file.write_text("not json!!!")
        assert tracker.list_installed() == []

    def test_multiple_projects(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for i in range(5):
            tracker.record_install(f"owner{i}", f"repo{i}", f"/tmp/r{i}")
        assert len(tracker.list_installed()) == 5


# ─────────────────────────────────────────────
#  UpdateInfo 测试
# ─────────────────────────────────────────────

class TestUpdateInfo:
    def test_defaults(self):
        info = UpdateInfo(owner="a", repo="b")
        assert info.has_update is False
        assert info.error == ""


# ─────────────────────────────────────────────
#  格式化测试
# ─────────────────────────────────────────────

class TestFormat:
    def test_empty_list(self):
        text = format_installed_list([])
        assert "未记录" in text

    def test_with_projects(self):
        projects = [
            InstalledProject(
                owner="pytorch", repo="pytorch", install_dir="/tmp/pt",
                installed_at="2025-01-01T00:00:00Z", installed_tag="v2.0",
            ),
        ]
        text = format_installed_list(projects)
        assert "pytorch/pytorch" in text
        assert "v2.0" in text

    def test_update_results_empty(self):
        text = format_update_results([])
        assert "没有" in text

    def test_update_with_available(self):
        updates = [
            UpdateInfo(owner="a", repo="b", has_update=True,
                       current_tag="v1.0", latest_tag="v2.0",
                       commits_behind=15),
        ]
        text = format_update_results(updates)
        assert "可更新" in text
        assert "15" in text

    def test_update_all_up_to_date(self):
        updates = [
            UpdateInfo(owner="a", repo="b", has_update=False),
        ]
        text = format_update_results(updates)
        assert "最新" in text

    def test_updates_to_dict(self):
        updates = [
            UpdateInfo(owner="a", repo="b", has_update=True, commits_behind=5),
            UpdateInfo(owner="c", repo="d", has_update=False),
        ]
        d = updates_to_dict(updates)
        assert d["total"] == 2
        assert d["available"] == 1
        assert len(d["updates"]) == 2
