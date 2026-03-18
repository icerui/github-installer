"""
validate_top100.py 全面测试 — 纯函数 + mock I/O 覆盖 282 行
"""
import json
import sys
import os
import io
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))

from validate_top100 import (
    _make_env, _validate_steps, ProjectResult,
    load_history, save_history, detect_new_projects,
    validate_project, generate_report, print_report,
    show_last_report, save_report, run_validation, cmd_validate,
    PLATFORMS, _DANGEROUS_PATTERNS,
)


# ─── _make_env ───────────────────────────────

class TestMakeEnv:
    @pytest.mark.parametrize("os_type,arch,gpu", [
        ("macos", "arm64", "apple_mps"),
        ("linux", "x86_64", "nvidia"),
        ("windows", "x86_64", "none"),
    ])
    def test_make_env_os_types(self, os_type, arch, gpu):
        env = _make_env(os_type, arch, gpu)
        assert env["os"]["type"] == os_type
        assert env["os"]["arch"] == arch
        assert env["gpu"]["type"] == gpu
        assert env["hardware"]["cpu_count"] == 8
        assert env["runtimes"]["python"]["available"] is True
        assert env["package_managers"]["pip"]["available"] is True

    def test_make_env_chip(self):
        env = _make_env("macos", "arm64", "apple_mps", "Apple M3 Ultra")
        assert env["os"]["chip"] == "Apple M3 Ultra"

    def test_make_env_brew_only_macos(self):
        mac = _make_env("macos")
        linux = _make_env("linux")
        assert mac["package_managers"]["brew"]["available"] is True
        assert linux["package_managers"]["brew"]["available"] is False

    def test_make_env_apt_only_linux(self):
        linux = _make_env("linux")
        win = _make_env("windows")
        assert linux["package_managers"]["apt"]["available"] is True
        assert win["package_managers"]["apt"]["available"] is False

    def test_platforms_dict(self):
        assert len(PLATFORMS) == 3
        assert "macOS-ARM" in PLATFORMS
        assert "Linux-CUDA" in PLATFORMS
        assert "Windows-CPU" in PLATFORMS


# ─── _validate_steps ─────────────────────────

class TestValidateSteps:
    @pytest.mark.parametrize("danger", _DANGEROUS_PATTERNS)
    def test_dangerous_patterns(self, danger):
        steps = [{"command": f"echo {danger}"}]
        issues = _validate_steps(steps, "linux")
        assert any(danger in i for i in issues)

    def test_safe_steps_no_issues(self):
        steps = [
            {"command": "pip install torch"},
            {"command": "git clone https://github.com/foo/bar.git"},
        ]
        assert _validate_steps(steps, "linux") == []

    def test_windows_no_sudo(self):
        steps = [{"command": "sudo apt install python3"}]
        issues = _validate_steps(steps, "windows")
        assert any("sudo" in i for i in issues)

    def test_windows_no_apt(self):
        steps = [{"command": "apt install git"}]
        issues = _validate_steps(steps, "windows")
        assert any("apt" in i for i in issues)

    def test_windows_no_brew(self):
        steps = [{"command": "brew install python"}]
        issues = _validate_steps(steps, "windows")
        assert any("brew" in i for i in issues)

    def test_macos_no_apt(self):
        steps = [{"command": "apt-get install git"}]
        issues = _validate_steps(steps, "macos")
        assert any("apt" in i for i in issues)

    def test_macos_no_winget(self):
        steps = [{"command": "winget install git"}]
        issues = _validate_steps(steps, "macos")
        assert any("winget" in i for i in issues)

    def test_linux_no_winget(self):
        steps = [{"command": "choco install git"}]
        issues = _validate_steps(steps, "linux")
        assert any("choco" in i or "winget" in i for i in issues)

    def test_empty_command_skipped(self):
        steps = [{"command": ""}]
        assert _validate_steps(steps, "linux") == []


# ─── ProjectResult ───────────────────────────

class TestProjectResult:
    def test_all_pass_true(self):
        r = ProjectResult(repo="a/b", name="b", stars=1, language="Python", tag="AI")
        r.platforms = {
            "macOS-ARM": {"pass": True},
            "Linux-CUDA": {"pass": True},
        }
        assert r.all_pass is True
        assert r.pass_count == 2

    def test_all_pass_false(self):
        r = ProjectResult(repo="a/b", name="b", stars=1, language="Python", tag="AI")
        r.platforms = {
            "macOS-ARM": {"pass": True},
            "Linux-CUDA": {"pass": False},
        }
        assert r.all_pass is False
        assert r.pass_count == 1

    def test_empty_platforms(self):
        r = ProjectResult(repo="a/b", name="b", stars=1, language="Python", tag="AI")
        assert r.all_pass is True
        assert r.pass_count == 0

    def test_fetch_failure(self):
        r = ProjectResult(repo="a/b", name="b", stars=0, language="", tag="",
                          fetch_ok=False, fetch_error="not found")
        assert r.fetch_ok is False
        assert r.all_pass is False  # no platforms = vacuously true but fetch failed

    def test_is_new_flag(self):
        r = ProjectResult(repo="a/b", name="b", stars=1, language="", tag="", is_new=True)
        assert r.is_new is True


# ─── load_history / save_history ─────────────

class TestHistory:
    def test_load_history_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("validate_top100._HISTORY_FILE", tmp_path / "none.json")
        h = load_history()
        assert h == {"repos": [], "last_run": None}

    def test_load_history_corrupt(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        monkeypatch.setattr("validate_top100._HISTORY_FILE", f)
        h = load_history()
        assert h == {"repos": [], "last_run": None}

    def test_save_and_load(self, tmp_path, monkeypatch):
        f = tmp_path / "history.json"
        monkeypatch.setattr("validate_top100._HISTORY_FILE", f)
        save_history(["a/b", "c/d"])
        h = load_history()
        assert h["repos"] == ["a/b", "c/d"]
        assert h["last_run"] is not None


# ─── detect_new_projects ─────────────────────

class TestDetectNewProjects:
    def test_all_new(self):
        current = [{"repo": "a/b"}, {"repo": "c/d"}]
        history = {"repos": []}
        new = detect_new_projects(current, history)
        assert new == {"a/b", "c/d"}

    def test_none_new(self):
        current = [{"repo": "a/b"}]
        history = {"repos": ["a/b"]}
        new = detect_new_projects(current, history)
        assert new == set()

    def test_case_insensitive(self):
        current = [{"repo": "A/B"}]
        history = {"repos": ["a/b"]}
        new = detect_new_projects(current, history)
        assert new == set()

    def test_mixed(self):
        current = [{"repo": "a/b"}, {"repo": "c/d"}, {"repo": "e/f"}]
        history = {"repos": ["a/b", "c/d"]}
        new = detect_new_projects(current, history)
        assert new == {"e/f"}


# ─── validate_project ────────────────────────

class TestValidateProject:
    def _make_info(self):
        from fetcher import RepoInfo
        return RepoInfo(
            owner="test", repo="proj", full_name="test/proj",
            description="t", stars=100, language="Python",
            license="MIT", default_branch="main", readme="# Test",
            project_type=["python"], dependency_files={"requirements.txt": "torch"},
            clone_url="https://github.com/test/proj.git",
            homepage="",
        )

    def test_validate_with_info(self):
        from planner import SmartPlanner
        info = self._make_info()
        planner = SmartPlanner()
        result = validate_project("test/proj", planner, project_info=info)
        assert result.name == "proj"
        assert result.fetch_ok is True
        assert len(result.platforms) == 3

    def test_validate_fetch_failure(self):
        from planner import SmartPlanner
        planner = SmartPlanner()
        with patch("validate_top100.fetch_project", side_effect=Exception("boom")):
            result = validate_project("bad/repo", planner)
        assert result.fetch_ok is False
        assert "boom" in result.fetch_error

    def test_validate_rate_limit(self):
        from planner import SmartPlanner
        planner = SmartPlanner()
        with patch("validate_top100.fetch_project", side_effect=PermissionError("rate")):
            result = validate_project("rate/limited", planner)
        assert not result.fetch_ok
        assert "RATELIMIT" in result.fetch_error

    def test_validate_planner_exception(self):
        """Planner exception for specific platform → that platform fails"""
        info = self._make_info()
        planner = MagicMock()
        planner.generate_plan.side_effect = RuntimeError("plan fail")
        result = validate_project("test/proj", planner, project_info=info)
        for plat_data in result.platforms.values():
            assert plat_data["pass"] is False
            assert plat_data["confidence"] == "error"


# ─── run_validation ──────────────────────────

class TestRunValidation:
    def test_empty_targets(self, capsys):
        results = run_validation([], set(), quick_mode=False)
        assert results == []

    def test_quick_mode_filters(self, capsys):
        projects = [{"repo": "a/b"}, {"repo": "c/d"}]
        new_repos = {"c/d"}
        r = ProjectResult(repo="c/d", name="d", stars=1, language="Python", tag="AI")
        r.platforms = {"Linux-CUDA": {"pass": True, "confidence": "high",
                                       "strategy": "known", "steps": 3,
                                       "issues": [], "has_launch": True}}
        with patch("validate_top100.validate_project", return_value=r) as mock_vp:
            results = run_validation(projects, new_repos, quick_mode=True)
        assert len(results) == 1
        mock_vp.assert_called_once()

    def test_category_filter(self, capsys):
        projects = [
            {"repo": "a/b", "tag": "AI"},
            {"repo": "c/d", "tag": "Web"},
        ]
        r = ProjectResult(repo="a/b", name="b", stars=1, language="Python", tag="AI")
        r.platforms = {"macOS-ARM": {"pass": True, "confidence": "high",
                                      "strategy": "known", "steps": 3,
                                      "issues": [], "has_launch": True}}
        with patch("validate_top100.validate_project", return_value=r) as mock_vp:
            results = run_validation(projects, set(), category_filter="AI")
        assert len(results) == 1

    def test_rate_limit_handling(self, capsys):
        projects = [{"repo": "a/b"}, {"repo": "c/d"}]
        rate_limited = ProjectResult(
            repo="a/b", name="", stars=0, language="", tag="",
            fetch_ok=False, fetch_error="RATELIMIT: GitHub API 频率超限"
        )
        ok_result = ProjectResult(
            repo="c/d", name="d", stars=1, language="Python", tag=""
        )
        ok_result.platforms = {"Linux-CUDA": {"pass": True, "confidence": "high",
                                               "strategy": "known", "steps": 3,
                                               "issues": [], "has_launch": True}}
        with patch("validate_top100.validate_project", side_effect=[rate_limited, ok_result]):
            with patch("validate_top100.time.sleep"):
                results = run_validation(projects, set())
        assert len(results) == 2


# ─── generate_report ─────────────────────────

class TestGenerateReport:
    def _make_results(self):
        r1 = ProjectResult(repo="a/b", name="b", stars=100, language="Python", tag="AI", is_new=True)
        r1.platforms = {
            "macOS-ARM": {"pass": True, "confidence": "high", "strategy": "known",
                          "steps": 3, "issues": [], "has_launch": True},
            "Linux-CUDA": {"pass": True, "confidence": "high", "strategy": "known",
                           "steps": 3, "issues": [], "has_launch": True},
        }
        r2 = ProjectResult(repo="c/d", name="d", stars=50, language="Rust", tag="Tool")
        r2.platforms = {
            "macOS-ARM": {"pass": True, "confidence": "medium", "strategy": "template",
                          "steps": 2, "issues": [], "has_launch": False},
            "Linux-CUDA": {"pass": False, "confidence": "low", "strategy": "template",
                           "steps": 1, "issues": ["Step 0: 危险命令 'mkfs'"], "has_launch": False},
        }
        return [r1, r2]

    def test_report_structure(self):
        results = self._make_results()
        report = generate_report(results, {"a/b"})
        s = report["summary"]
        assert s["total_projects"] == 2
        assert s["total_tests"] == 4
        assert s["passed_tests"] == 3
        assert s["pass_rate"] == 75.0
        assert s["all_pass_projects"] == 1
        assert s["new_projects"] == 1
        assert s["new_projects_pass"] == 1

    def test_confidence_breakdown(self):
        results = self._make_results()
        report = generate_report(results, set())
        cb = report["confidence_breakdown"]
        assert cb["high"] == 2
        assert cb["medium"] == 1
        assert cb["low"] == 1

    def test_failed_projects(self):
        results = self._make_results()
        report = generate_report(results, set())
        assert len(report["failed_projects"]) == 1
        assert report["failed_projects"][0]["repo"] == "c/d"

    def test_empty_results(self):
        report = generate_report([], set())
        assert report["summary"]["total_projects"] == 0
        assert report["summary"]["pass_rate"] == 0

    def test_fetch_failure_in_report(self):
        r = ProjectResult(repo="x/y", name="", stars=0, language="", tag="",
                          fetch_ok=False, fetch_error="timeout")
        report = generate_report([r], set())
        assert report["summary"]["fetch_failures"] == 1


# ─── print_report / show_last_report ─────────

class TestReportOutput:
    def test_print_report(self, capsys):
        results = TestGenerateReport()._make_results()
        report = generate_report(results, {"a/b"})
        print_report(report)
        out = capsys.readouterr().out
        assert "75.0%" in out
        assert "兼容性验证报告" in out

    def test_show_last_report_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("validate_top100._REPORT_FILE", tmp_path / "none.json")
        show_last_report()
        out = capsys.readouterr().out
        assert "没有找到" in out

    def test_show_last_report_exists(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "report.json"
        results = TestGenerateReport()._make_results()
        report = generate_report(results, set())
        f.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr("validate_top100._REPORT_FILE", f)
        show_last_report()
        out = capsys.readouterr().out
        assert "75.0%" in out

    def test_save_report(self, tmp_path, monkeypatch):
        f = tmp_path / "report.json"
        monkeypatch.setattr("validate_top100._REPORT_FILE", f)
        report = {"summary": {"total": 1}}
        save_report(report)
        assert f.exists()
        data = json.loads(f.read_text("utf-8"))
        assert data["summary"]["total"] == 1


# ─── cmd_validate ────────────────────────────

class TestCmdValidate:
    def test_report_only(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("validate_top100._REPORT_FILE", tmp_path / "none.json")
        result = cmd_validate(report_only=True)
        assert result["status"] == "ok"
        assert result["action"] == "report_shown"

    def test_crawl_empty(self, monkeypatch):
        monkeypatch.setattr("validate_top100.crawl_top100", lambda: [])
        result = cmd_validate()
        assert result["status"] == "error"

    def test_full_flow(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("validate_top100._REPORT_FILE", tmp_path / "report.json")
        monkeypatch.setattr("validate_top100._HISTORY_FILE", tmp_path / "history.json")
        monkeypatch.setattr("validate_top100.crawl_top100", lambda: [
            {"repo": "a/b", "tag": "AI", "stars": "1k", "_stars_num": 1000},
        ])
        r = ProjectResult(repo="a/b", name="b", stars=100, language="Python", tag="AI")
        r.platforms = {
            "macOS-ARM": {"pass": True, "confidence": "high", "strategy": "known",
                          "steps": 3, "issues": [], "has_launch": True},
        }
        monkeypatch.setattr("validate_top100.validate_project", lambda *a, **kw: r)
        monkeypatch.setattr("validate_top100.time.sleep", lambda x: None)
        result = cmd_validate()
        assert result["status"] == "ok"
        assert "summary" in result

    def test_no_results(self, monkeypatch, capsys):
        monkeypatch.setattr("validate_top100.crawl_top100", lambda: [
            {"repo": "a/b", "tag": "AI"},
        ])
        monkeypatch.setattr("validate_top100.run_validation", lambda *a, **kw: [])
        result = cmd_validate()
        assert result["status"] == "ok"
        assert "没有需要验证" in result.get("message", "")


# ─── crawl_top100 ────────────────────────────

class TestCrawl:
    def test_crawl_delegates(self, capsys):
        with patch("trending._fetch_all", return_value=[{"repo": "a/b"}]):
            from validate_top100 import crawl_top100
            result = crawl_top100()
        assert len(result) == 1
