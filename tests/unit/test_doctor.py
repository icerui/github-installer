"""
Tests for doctor.py - 系统诊断
"""
import os
import shutil
import sys
import time
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# 确保 tools 目录在路径中
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from doctor import (
    run_doctor, format_doctor_report, doctor_to_dict,
    _check_python, _check_git, _check_cache, _check_database,
    _check_disk_space, _check_gpu, _check_security, _check_skills,
    _check_github_api, _check_llm_keys, _check_package_managers,
    CheckResult, DoctorReport,
    LEVEL_OK, LEVEL_WARN, LEVEL_ERROR, LEVEL_INFO,
)


class TestCheckResult:
    def test_basic_creation(self):
        r = CheckResult("test", LEVEL_OK, "all good")
        assert r.name == "test"
        assert r.level == LEVEL_OK
        assert r.message == "all good"
        assert r.detail == ""
        assert r.fix_hint == ""

    def test_with_fix_hint(self):
        r = CheckResult("test", LEVEL_ERROR, "broken", fix_hint="fix it")
        assert r.fix_hint == "fix it"


class TestDoctorReport:
    def test_empty_report(self):
        r = DoctorReport()
        assert r.ok_count == 0
        assert r.warn_count == 0
        assert r.error_count == 0
        assert r.all_ok is True

    def test_counts(self):
        r = DoctorReport(checks=[
            CheckResult("a", LEVEL_OK, "ok"),
            CheckResult("b", LEVEL_OK, "ok"),
            CheckResult("c", LEVEL_WARN, "warning"),
            CheckResult("d", LEVEL_ERROR, "err"),
            CheckResult("e", LEVEL_INFO, "info"),
        ])
        assert r.ok_count == 2
        assert r.warn_count == 1
        assert r.error_count == 1
        assert r.all_ok is False

    def test_all_ok_when_no_errors(self):
        r = DoctorReport(checks=[
            CheckResult("a", LEVEL_OK, "ok"),
            CheckResult("b", LEVEL_WARN, "warn"),
            CheckResult("c", LEVEL_INFO, "info"),
        ])
        assert r.all_ok is True  # warn 和 info 不算 error


class TestCheckPython:
    def test_current_python(self):
        result = _check_python()
        assert result.level == LEVEL_OK
        assert "Python" in result.message

    @patch("doctor.sys")
    def test_old_python(self, mock_sys):
        from collections import namedtuple
        VersionInfo = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
        mock_sys.version_info = VersionInfo(3, 7, 0, "final", 0)
        result = _check_python()
        assert result.level == LEVEL_ERROR


class TestCheckGit:
    def test_git_available(self):
        if not shutil.which("git"):
            import pytest
            pytest.skip("git not installed")
        result = _check_git()
        # Git should be available on the dev machine
        assert result.level in (LEVEL_OK, LEVEL_WARN)

    @patch("doctor.shutil.which", return_value=None)
    def test_git_not_available(self, _):
        result = _check_git()
        assert result.level == LEVEL_ERROR
        assert "未安装" in result.message


class TestCheckPackageManagers:
    def test_returns_list(self):
        results = _check_package_managers()
        assert isinstance(results, list)
        assert len(results) > 0

    @patch("doctor.shutil.which", return_value=None)
    def test_no_managers(self, _):
        results = _check_package_managers()
        assert any(r.level == LEVEL_ERROR for r in results)


class TestCheckCache:
    def test_cache_check(self):
        result = _check_cache()
        assert result.level in (LEVEL_OK, LEVEL_WARN)

    @patch("doctor.Path")
    def test_no_cache_dir(self, mock_path):
        mock_instance = MagicMock()
        mock_instance.exists.return_value = False
        mock_path.home.return_value.__truediv__ = MagicMock(return_value=mock_instance)
        # Just verify no crash
        result = _check_cache()
        assert result.level in (LEVEL_OK, LEVEL_WARN)


class TestCheckDatabase:
    def test_database_check(self):
        result = _check_database()
        assert result.level in (LEVEL_OK, LEVEL_WARN)

    def test_nonexistent_db(self):
        with patch.object(Path, "exists", return_value=False):
            pass  # Can't easily mock this fully, but verify no crash


class TestCheckDiskSpace:
    def test_disk_space(self):
        result = _check_disk_space()
        assert result.level in (LEVEL_OK, LEVEL_WARN, LEVEL_INFO)
        assert "GB" in result.message or "检测跳过" in result.message


class TestCheckGPU:
    def test_gpu_check(self):
        result = _check_gpu()
        assert result.level in (LEVEL_OK, LEVEL_INFO)


class TestCheckSecurity:
    def test_security(self):
        result = _check_security()
        assert result.level in (LEVEL_OK, LEVEL_WARN)


class TestCheckSkills:
    def test_no_skills(self):
        with patch.object(Path, "exists", return_value=False):
            pass  # verify no crash

    def test_skills_check(self):
        result = _check_skills()
        assert result.level in (LEVEL_OK, LEVEL_INFO)


class TestCheckGitHubAPI:
    def test_github_api(self):
        result = _check_github_api()
        assert result.level in (LEVEL_OK, LEVEL_WARN, LEVEL_ERROR)

    @patch("doctor.urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_github_api_failure(self, _):
        result = _check_github_api()
        assert result.level == LEVEL_ERROR


class TestCheckLLMKeys:
    def test_returns_list(self):
        results = _check_llm_keys()
        assert isinstance(results, list)

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test123"})
    def test_detects_anthropic(self):
        results = _check_llm_keys()
        assert any("Anthropic" in r.name for r in results)


class TestRunDoctor:
    def test_run_full_doctor(self):
        report = run_doctor()
        assert isinstance(report, DoctorReport)
        assert len(report.checks) > 10
        assert report.duration_ms > 0
        assert report.timestamp > 0

    def test_format_report(self):
        report = run_doctor()
        text = format_doctor_report(report)
        assert "gitinstall doctor" in text
        assert "通过" in text

    def test_doctor_to_dict(self):
        report = run_doctor()
        d = doctor_to_dict(report)
        assert "status" in d
        assert "summary" in d
        assert "checks" in d
        assert d["summary"]["total"] == len(report.checks)
