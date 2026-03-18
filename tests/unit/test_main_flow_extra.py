"""
main.py 安装流 + cmd_install 覆盖
目标：覆盖 cmd_plan LLM 分支, cmd_install 全流程, main() CLI 入口
"""
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))

import main as main_mod
from fetcher import RepoInfo


def _fake_info(**overrides):
    defaults = dict(
        owner="test", repo="proj", full_name="test/proj",
        description="test", stars=100, language="Python",
        license="MIT", default_branch="main",
        readme="# Test\n```bash\npip install torch\n```",
        project_type=["python", "pytorch"],
        dependency_files={"requirements.txt": "torch"},
        clone_url="https://github.com/test/proj.git",
        homepage="",
    )
    defaults.update(overrides)
    return RepoInfo(**defaults)


_MOCK_ENV = {
    "os": {"type": "macos", "arch": "arm64", "chip": "M3", "is_apple_silicon": True},
    "gpu": {"type": "apple_mps"},
    "package_managers": {"pip": {}, "brew": {}},
    "runtimes": {"python3": {"available": True}, "git": {"available": True}},
}


# ─── cmd_plan LLM path ───────────────────────

class TestCmdPlanLLM:
    """Cover lines 146-165: LLM enhancement branch"""

    def test_plan_with_llm_enhancement(self):
        info = _fake_info(project_type=["python"])
        mock_llm = MagicMock()
        mock_llm.name = "test-llm-7b"
        llm_response = json.dumps({
            "steps": [
                {"command": "pip install torch", "description": "install", "_warning": ""},
                {"command": "python main.py", "description": "run", "_warning": ""},
            ]
        })
        mock_llm.complete.return_value = llm_response

        with patch.object(main_mod, "fetch_project", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                with patch.object(main_mod, "create_provider", return_value=mock_llm):
                    result = main_mod.cmd_plan("test/proj")

        assert result["status"] == "ok"

    def test_plan_llm_failure_fallback(self):
        info = _fake_info(project_type=["python"])
        mock_llm = MagicMock()
        mock_llm.name = "broken-llm"
        mock_llm.complete.side_effect = RuntimeError("API error")

        with patch.object(main_mod, "fetch_project", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                with patch.object(main_mod, "create_provider", return_value=mock_llm):
                    result = main_mod.cmd_plan("test/proj")

        # Should fall back to SmartPlanner
        assert result["status"] == "ok"

    def test_plan_small_model_detection(self):
        """LLM with '1.5b' in name → small model prompt"""
        info = _fake_info(project_type=["python"])
        mock_llm = MagicMock()
        mock_llm.name = "qwen-1.5b-instruct"
        mock_llm.complete.return_value = json.dumps({
            "steps": [{"command": "pip install torch", "description": "d", "_warning": ""}]
        })

        with patch.object(main_mod, "fetch_project", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                with patch.object(main_mod, "create_provider", return_value=mock_llm):
                    result = main_mod.cmd_plan("test/proj")

        assert result["status"] == "ok"

    def test_plan_use_local_mode(self):
        info = _fake_info()
        with patch.object(main_mod, "fetch_project_local", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                result = main_mod.cmd_plan("test/proj", llm_force="none", use_local=True)

        assert result["status"] == "ok"


# ─── cmd_install full flow ────────────────────

class TestCmdInstall:
    """Cover lines 215-410: full install flow with audit, license, skills, execution"""

    def test_install_dry_run(self):
        info = _fake_info()
        with patch.object(main_mod, "fetch_project", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                result = main_mod.cmd_install("test/proj", llm_force="none", dry_run=True)

        assert result["status"] == "ok"
        assert result["dry_run"] is True

    def test_install_plan_fails(self):
        with patch.object(main_mod, "fetch_project", side_effect=Exception("not found")):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                result = main_mod.cmd_install("bad/repo", llm_force="none")

        assert result["status"] == "error"

    def test_install_with_audit_warnings(self):
        """Dependency audit finds vulnerabilities"""
        info = _fake_info(dependency_files={"requirements.txt": "evil-pkg\n"})

        mock_vuln = MagicMock()
        mock_vuln.risk = "critical"
        mock_vuln.package = "evil-pkg"
        mock_vuln.description = "malicious"

        mock_audit_result = MagicMock()
        mock_audit_result.vulnerabilities = [mock_vuln]

        with patch.object(main_mod, "fetch_project", return_value=info):
            with patch.object(main_mod, "EnvironmentDetector") as MockDet:
                MockDet.return_value.detect.return_value = _MOCK_ENV
                with patch("main.audit_project", return_value=[mock_audit_result], create=True):
                    with patch("main.RISK_CRITICAL", "critical", create=True):
                        with patch("main.RISK_HIGH", "high", create=True):
                            result = main_mod.cmd_install(
                                "test/proj", llm_force="none", dry_run=True
                            )

        assert result["status"] == "ok"


# ─── cmd_resume with identifier ──────────────

class TestCmdResumeWithId:
    """Cover lines 825-845: resume with actual project identifier"""

    def test_resume_no_checkpoint(self):
        with patch("checkpoint.CheckpointManager") as MockCM:
            mgr = MockCM.return_value
            mgr.get_checkpoint.return_value = None
            result = main_mod.cmd_resume(identifier="test/proj")

        assert result["status"] == "error"
        assert "未找到" in result["message"]

    def test_resume_already_complete(self):
        with patch("checkpoint.CheckpointManager") as MockCM:
            mgr = MockCM.return_value
            mgr.get_checkpoint.return_value = MagicMock()
            mgr.get_resume_step.return_value = None
            result = main_mod.cmd_resume(identifier="test/proj")

        assert result["status"] == "ok"
        assert "已完成" in result["message"]


# ─── main() CLI entry ────────────────────────

class TestMainCLI:
    """Cover lines 1314-1370: main() dispatcher"""

    def test_main_no_args(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["gitinstall"])
        with pytest.raises(SystemExit):
            main_mod.main()

    def test_main_detect(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["gitinstall", "detect"])
        exit_code = None
        orig_exit = sys.exit
        def mock_exit(code=0):
            nonlocal exit_code
            exit_code = code
            raise SystemExit(code)
        monkeypatch.setattr(sys, "exit", mock_exit)
        try:
            main_mod.main()
        except SystemExit:
            pass
        # detect should succeed
        assert exit_code is None or exit_code == 0

    def test_main_doctor(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["gitinstall", "doctor"])
        with patch.object(main_mod, "cmd_doctor", return_value={"status": "ok"}):
            try:
                main_mod.main()
            except SystemExit:
                pass

    def test_cli_main_alias(self):
        """cli_main is an alias for main"""
        assert main_mod.cli_main is main_mod.main
