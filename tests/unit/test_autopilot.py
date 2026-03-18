"""
test_autopilot.py - 批量自动安装测试
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from autopilot import (
    BatchItem, BatchResult,
    parse_project_list, format_batch_result,
    _save_state, _load_state, AUTOPILOT_DIR,
)


class TestBatchItem:
    """测试 BatchItem"""

    def test_creation(self):
        item = BatchItem(identifier="owner/repo")
        assert item.identifier == "owner/repo"
        assert item.status == "pending"
        assert item.error == ""


class TestBatchResult:
    """测试 BatchResult"""

    def test_success_rate(self):
        result = BatchResult(total=4, completed=3, failed=1)
        assert result.success_rate == 0.75

    def test_success_rate_zero_division(self):
        result = BatchResult(total=0, completed=0, failed=0)
        assert result.success_rate == 0.0

    def test_to_dict(self):
        result = BatchResult(
            total=2, completed=1, failed=1,
            items=[
                BatchItem(identifier="a/b", status="completed", duration_sec=10.0),
                BatchItem(identifier="c/d", status="failed", error="boom"),
            ],
        )
        d = result.to_dict()
        assert d["total"] == 2
        assert d["completed"] == 1
        assert len(d["items"]) == 2
        assert d["items"][0]["identifier"] == "a/b"
        assert d["items"][1]["error"] == "boom"


class TestParseProjectList:
    """测试 parse_project_list()"""

    def test_single_project(self):
        projects = parse_project_list("owner/repo")
        assert projects == ["owner/repo"]

    def test_space_separated(self):
        projects = parse_project_list("a/b c/d e/f")
        assert projects == ["a/b", "c/d", "e/f"]

    def test_comma_separated(self):
        projects = parse_project_list("a/b,c/d,e/f")
        assert projects == ["a/b", "c/d", "e/f"]

    def test_from_text_file(self, tmp_path):
        f = tmp_path / "projects.txt"
        f.write_text("# Comments\na/b\nc/d\n  e/f  # inline comment\n\n")
        projects = parse_project_list(str(f))
        assert projects == ["a/b", "c/d", "e/f"]

    def test_from_json_file(self, tmp_path):
        f = tmp_path / "projects.json"
        f.write_text(json.dumps(["a/b", "c/d"]))
        projects = parse_project_list(str(f))
        assert projects == ["a/b", "c/d"]

    def test_from_json_objects(self, tmp_path):
        f = tmp_path / "projects.json"
        f.write_text(json.dumps([
            {"project": "a/b"},
            {"repo": "c/d"},
        ]))
        projects = parse_project_list(str(f))
        assert projects == ["a/b", "c/d"]

    def test_empty_input(self):
        projects = parse_project_list("")
        assert projects == []

    def test_file_with_only_comments(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("# just a comment\n# another one\n")
        projects = parse_project_list(str(f))
        assert projects == []


class TestPersistence:
    """测试状态持久化"""

    def test_save_and_load(self, tmp_path, monkeypatch):
        import autopilot
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(autopilot, "AUTOPILOT_DIR", tmp_path)
        monkeypatch.setattr(autopilot, "AUTOPILOT_STATE", state_file)

        result = BatchResult(
            total=2, completed=1, failed=0, skipped=1,
            items=[
                BatchItem(identifier="a/b", status="completed"),
                BatchItem(identifier="c/d", status="skipped"),
            ],
        )
        _save_state(result)
        loaded = _load_state()
        assert loaded is not None
        assert loaded.total == 2
        assert loaded.completed == 1
        assert len(loaded.items) == 2

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        import autopilot
        monkeypatch.setattr(autopilot, "AUTOPILOT_STATE",
                          tmp_path / "nonexistent.json")
        assert _load_state() is None


class TestFormatBatchResult:
    """测试格式化函数"""

    def test_format_completed(self):
        result = BatchResult(
            total=2, completed=2, failed=0,
            total_duration_sec=25.5,
            items=[
                BatchItem(identifier="a/b", status="completed", duration_sec=10.0),
                BatchItem(identifier="c/d", status="completed", duration_sec=15.5),
            ],
        )
        text = format_batch_result(result)
        assert "自动驾驶" in text
        assert "a/b" in text
        assert "✅" in text

    def test_format_with_failures(self):
        result = BatchResult(
            total=2, completed=1, failed=1,
            total_duration_sec=20.0,
            items=[
                BatchItem(identifier="a/b", status="completed", duration_sec=10.0),
                BatchItem(identifier="c/d", status="failed", error="网络超时",
                         duration_sec=10.0),
            ],
        )
        text = format_batch_result(result)
        assert "❌" in text
        assert "网络超时" in text

    def test_format_with_skipped(self):
        result = BatchResult(
            total=3, completed=1, failed=0, skipped=2,
            items=[
                BatchItem(identifier="a/b", status="completed", duration_sec=5.0),
                BatchItem(identifier="c/d", status="skipped"),
                BatchItem(identifier="e/f", status="skipped"),
            ],
        )
        text = format_batch_result(result)
        assert "跳过" in text
