"""autopilot.py 全覆盖测试

覆盖目标：run_autopilot(), resume_autopilot(), _save_state(), _load_state(),
          format_batch_result(), parse_project_list()
"""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from autopilot import (
    BatchItem,
    BatchResult,
    parse_project_list,
    run_autopilot,
    resume_autopilot,
    _save_state,
    _load_state,
    format_batch_result,
    AUTOPILOT_DIR,
    AUTOPILOT_STATE,
)
from unittest.mock import patch, MagicMock


# ─── parse_project_list ───


class TestParseProjectList:
    def test_single_repo(self):
        assert parse_project_list("owner/repo") == ["owner/repo"]

    def test_comma_separated(self):
        result = parse_project_list("a/b, c/d, e/f")
        assert result == ["a/b", "c/d", "e/f"]

    def test_space_separated(self):
        result = parse_project_list("a/b c/d")
        assert result == ["a/b", "c/d"]

    def test_from_text_file(self, tmp_path):
        f = tmp_path / "projects.txt"
        f.write_text("a/b\n# comment\nc/d\ne/f # inline comment\n")
        result = parse_project_list(str(f))
        assert result == ["a/b", "c/d", "e/f"]

    def test_from_json_file(self, tmp_path):
        f = tmp_path / "projects.json"
        f.write_text(json.dumps(["a/b", "c/d"]))
        result = parse_project_list(str(f))
        assert result == ["a/b", "c/d"]

    def test_from_json_file_dicts(self, tmp_path):
        f = tmp_path / "projects.json"
        f.write_text(json.dumps([{"project": "a/b"}, {"repo": "c/d"}]))
        result = parse_project_list(str(f))
        assert result == ["a/b", "c/d"]


# ─── _save_state / _load_state ───


class TestStatePersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)

        result = BatchResult(total=2, completed=1, failed=1)
        result.items = [
            BatchItem(identifier="a/b", status="completed", duration_sec=1.5),
            BatchItem(identifier="c/d", status="failed", error="boom"),
        ]
        _save_state(result)
        assert state_file.exists()

        loaded = _load_state()
        assert loaded is not None
        assert loaded.total == 2
        assert loaded.completed == 1
        assert loaded.failed == 1
        assert len(loaded.items) == 2
        assert loaded.items[0].identifier == "a/b"
        assert loaded.items[1].error == "boom"

    def test_load_no_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", tmp_path / "nope.json")
        assert _load_state() is None

    def test_load_corrupt(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        f.write_text("not json{{{")
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", f)
        assert _load_state() is None

    def test_save_chmod_failure(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)

        def bad_chmod(*a, **kw):
            raise OSError("no chmod")

        monkeypatch.setattr(os, "chmod", bad_chmod)
        _save_state(BatchResult(total=1))
        assert state_file.exists()


# ─── run_autopilot ───


class TestRunAutopilot:
    @pytest.fixture(autouse=True)
    def _patch_state(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)

    def test_all_success(self):
        with patch("main.cmd_install", return_value={"success": True, "install_dir": "/tmp/x", "plan_strategy": "pip"}):
            result = run_autopilot(["a/b", "c/d"])
        assert result.total == 2
        assert result.completed == 2
        assert result.failed == 0
        assert result.items[0].status == "completed"
        assert result.items[0].install_dir == "/tmp/x"

    def test_some_failures(self):
        def side_effect(proj, **kw):
            if "bad" in proj:
                return {"success": False, "error_summary": "nope"}
            return {"success": True}

        with patch("main.cmd_install", side_effect=side_effect):
            result = run_autopilot(["good/ok", "bad/fail", "good/ok2"])
        assert result.completed == 2
        assert result.failed == 1
        assert result.items[1].status == "failed"
        assert "nope" in result.items[1].error

    def test_exception_handling(self):
        with patch("main.cmd_install", side_effect=RuntimeError("crash")):
            result = run_autopilot(["a/b"])
        assert result.failed == 1
        assert result.items[0].status == "failed"
        assert "crash" in result.items[0].error

    def test_keyboard_interrupt(self):
        call_count = 0

        def side_effect(proj, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise KeyboardInterrupt()
            return {"success": True}

        with patch("main.cmd_install", side_effect=side_effect):
            result = run_autopilot(["a/b", "c/d", "e/f"])
        assert result.completed == 1
        assert result.skipped >= 1
        assert result.items[1].status == "skipped"

    def test_progress_callback(self):
        progress = []

        def cb(current, total, item):
            progress.append((current, total, item.identifier))

        with patch("main.cmd_install", return_value={"success": True}):
            run_autopilot(["a/b", "c/d"], on_progress=cb)
        assert len(progress) == 2
        assert progress[0] == (1, 2, "a/b")

    def test_dry_run_param(self):
        with patch("main.cmd_install", return_value={"success": True}) as mock:
            run_autopilot(["a/b"], dry_run=True, llm_force="gpt-4")
        mock.assert_called_once_with("a/b", install_dir=None, llm_force="gpt-4", dry_run=True)


# ─── resume_autopilot ───


class TestResumeAutopilot:
    def test_resume_no_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", tmp_path / "none.json")
        assert resume_autopilot() is None

    def test_resume_all_done(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)
        result = BatchResult(total=1, completed=1)
        result.items = [BatchItem(identifier="a/b", status="completed")]
        _save_state(result)
        r = resume_autopilot()
        assert r is not None
        assert r.completed == 1

    def test_resume_pending(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)

        result = BatchResult(total=3, completed=1)
        result.items = [
            BatchItem(identifier="a/b", status="completed"),
            BatchItem(identifier="c/d", status="pending"),
            BatchItem(identifier="e/f", status="pending"),
        ]
        _save_state(result)

        with patch("main.cmd_install", return_value={"success": True}):
            r = resume_autopilot()
        assert r is not None
        assert r.completed >= 2

    def test_resume_skipped(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "autopilot"
        state_file = state_dir / "state.json"
        monkeypatch.setattr("autopilot.AUTOPILOT_DIR", state_dir)
        monkeypatch.setattr("autopilot.AUTOPILOT_STATE", state_file)

        result = BatchResult(total=2, completed=1, skipped=1)
        result.items = [
            BatchItem(identifier="a/b", status="completed"),
            BatchItem(identifier="c/d", status="skipped"),
        ]
        _save_state(result)

        with patch("main.cmd_install", return_value={"success": True}):
            r = resume_autopilot()
        assert r is not None


# ─── format_batch_result ───


class TestFormatBatchResult:
    def test_all_statuses(self):
        result = BatchResult(total=4, completed=1, failed=1, skipped=1,
                             total_duration_sec=10.0)
        result.items = [
            BatchItem(identifier="a/b", status="completed", duration_sec=2.0),
            BatchItem(identifier="c/d", status="failed", duration_sec=3.0, error="boom"),
            BatchItem(identifier="e/f", status="skipped"),
            BatchItem(identifier="g/h", status="pending"),
        ]
        output = format_batch_result(result)
        assert "a/b" in output
        assert "c/d" in output
        assert "boom" in output
        assert "跳过" in output
        assert "pending" in output
        assert "成功率" in output

    def test_empty_result(self):
        result = BatchResult()
        output = format_batch_result(result)
        assert "0 个项目" in output

    def test_success_rate_display(self):
        result = BatchResult(total=2, completed=2, total_duration_sec=5.0)
        result.items = [
            BatchItem(identifier="a/b", status="completed", duration_sec=2.0),
            BatchItem(identifier="c/d", status="completed", duration_sec=3.0),
        ]
        output = format_batch_result(result)
        assert "100.0%" in output


# ─── BatchResult dataclass ───


class TestBatchResult:
    def test_success_rate_zero_div(self):
        r = BatchResult()
        assert r.success_rate == 0.0

    def test_to_dict(self):
        r = BatchResult(total=1, completed=1)
        r.items = [BatchItem(identifier="a/b", status="completed")]
        d = r.to_dict()
        assert d["total"] == 1
        assert d["items"][0]["identifier"] == "a/b"
