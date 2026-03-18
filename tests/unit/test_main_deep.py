"""main.py 深层分支覆盖测试

覆盖 cmd_plan (LLM 分支), cmd_install (审计/许可/skills/执行/回退/遥测),
cmd_resume, cmd_events, cmd_autopilot, cmd_skills, cmd_config, cmd_updates,
cmd_flags, cmd_registry, cmd_kb, cmd_chain, cmd_uninstall, main() CLI
"""
import json
import os
import sys

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import main as main_mod


# ─── 通用 mock fixtures ─────────────────────

def _mock_env():
    return {
        "os": {"type": "macos", "version": "14.0", "arch": "arm64",
               "is_apple_silicon": True, "chip": "M3"},
        "hardware": {"cpu_count": 8, "ram_gb": 32},
        "gpu": {"type": "mps", "name": "M3"},
        "runtimes": {"python": {"version": "3.13"}},
        "package_managers": {"pip": {"available": True}},
        "disk": {"free_gb": 100},
    }


def _mock_project_info():
    info = MagicMock()
    info.owner = "testowner"
    info.repo = "testrepo"
    info.project_name = "testowner/testrepo"
    info.project_types = ["python"]
    info.dependency_files = {"requirements.txt": "flask==3.0"}
    info.readme = "# Test Readme"
    info.files = ["requirements.txt", "README.md"]
    info.to_dict.return_value = {"owner": "testowner", "repo": "testrepo"}
    return info


def _mock_plan_result():
    return {
        "status": "ok",
        "plan": {
            "steps": [
                {"command": "pip install -r requirements.txt", "description": "安装依赖"},
            ],
            "launch_command": "python app.py",
        },
        "project": "testowner/testrepo",
        "llm_used": "heuristic",
        "strategy": "heuristic",
        "confidence": "high",
        "_owner": "testowner",
        "_repo": "testrepo",
        "_project_types": ["python"],
        "_dependency_files": {"requirements.txt": "flask==3.0"},
        "_env": _mock_env(),
    }


def _mock_preflight():
    """preflight_check 返回 PreflightResult 对象"""
    pf = MagicMock()
    pf.all_ready = True
    pf.missing_tools = []
    pf.install_commands = []
    return pf


def _mock_exec_result(success=True):
    """executor.execute_plan 返回的结果对象"""
    r = MagicMock()
    r.success = success
    r.project = "testowner/testrepo"
    r.install_dir = "/tmp/repo"
    r.launch_command = "python app.py"
    r.error_summary = "" if success else "build failed"
    step = MagicMock()
    step.success = success
    r.steps = [step]
    return r


# ═══════════════════════════════════════════
#  cmd_detect
# ═══════════════════════════════════════════

class TestCmdDetect:
    def test_basic(self):
        mock_det = MagicMock()
        mock_det.detect.return_value = _mock_env()
        with patch.object(main_mod, "EnvironmentDetector", return_value=mock_det), \
             patch.object(main_mod, "format_env_summary", return_value="summary"):
            result = main_mod.cmd_detect()
        assert result["status"] == "ok"


# ═══════════════════════════════════════════
#  cmd_fetch
# ═══════════════════════════════════════════

class TestCmdFetch:
    def test_success(self):
        info = _mock_project_info()
        with patch.object(main_mod, "fetch_project", return_value=info), \
             patch.object(main_mod, "format_project_summary", return_value="summary"):
            result = main_mod.cmd_fetch("testowner/testrepo")
        assert result["status"] == "ok"

    def test_not_found(self):
        with patch.object(main_mod, "fetch_project", side_effect=FileNotFoundError("not found")):
            result = main_mod.cmd_fetch("bad/repo")
        assert result["status"] == "error"

    def test_generic_error(self):
        with patch.object(main_mod, "fetch_project", side_effect=RuntimeError("oops")):
            result = main_mod.cmd_fetch("bad/repo")
        assert result["status"] == "error"


# ═══════════════════════════════════════════
#  cmd_plan
# ═══════════════════════════════════════════

class TestCmdPlan:
    def _setup_mocks(self, confidence="high", llm_force=None):
        mock_det = MagicMock()
        mock_det.detect.return_value = _mock_env()
        info = _mock_project_info()

        mock_planner = MagicMock()
        mock_planner.generate_plan.return_value = {
            "plan": {"steps": [{"command": "pip install -r requirements.txt",
                                "description": "install deps"}]},
            "strategy": "template_python",
            "confidence": confidence,
            "project_name": "testowner/testrepo",
        }

        mock_llm = MagicMock()
        mock_llm.model_name = "gpt-4o"

        return mock_det, info, mock_planner, mock_llm

    def test_plan_high_confidence(self):
        det, info, planner, llm = self._setup_mocks("high")
        with patch.object(main_mod, "EnvironmentDetector", return_value=det), \
             patch.object(main_mod, "fetch_project", return_value=info), \
             patch.object(main_mod, "SmartPlanner", return_value=planner), \
             patch.object(main_mod, "create_provider", return_value=llm), \
             patch.object(main_mod, "check_command_safety", return_value=(True, "")), \
             patch.object(main_mod, "get_gpu_info", return_value={}), \
             patch.object(main_mod, "check_hardware_compatibility", return_value={}):
            result = main_mod.cmd_plan("testowner/testrepo")
        assert result["status"] == "ok"

    def test_plan_low_confidence_triggers_llm(self):
        det, info, planner, llm = self._setup_mocks("low")
        llm_response = '{"steps": [{"command": "make", "description": "build"}]}'
        llm.generate.return_value = llm_response

        with patch.object(main_mod, "EnvironmentDetector", return_value=det), \
             patch.object(main_mod, "fetch_project", return_value=info), \
             patch.object(main_mod, "SmartPlanner", return_value=planner), \
             patch.object(main_mod, "create_provider", return_value=llm), \
             patch.object(main_mod, "check_command_safety", return_value=(True, "")), \
             patch.object(main_mod, "get_gpu_info", return_value={}), \
             patch.object(main_mod, "check_hardware_compatibility", return_value={}):
            result = main_mod.cmd_plan("testowner/testrepo")
        assert result["status"] == "ok"

    def test_plan_with_heuristic_provider(self):
        det, info, planner, _ = self._setup_mocks("low")
        heuristic_llm = MagicMock(spec=main_mod.HeuristicProvider)
        heuristic_llm.model_name = "heuristic"

        with patch.object(main_mod, "EnvironmentDetector", return_value=det), \
             patch.object(main_mod, "fetch_project", return_value=info), \
             patch.object(main_mod, "SmartPlanner", return_value=planner), \
             patch.object(main_mod, "create_provider", return_value=heuristic_llm), \
             patch.object(main_mod, "check_command_safety", return_value=(True, "")), \
             patch.object(main_mod, "get_gpu_info", return_value={}), \
             patch.object(main_mod, "check_hardware_compatibility", return_value={}):
            result = main_mod.cmd_plan("testowner/testrepo", llm_force="none")
        assert result["status"] == "ok"

    def test_plan_local_mode(self):
        det, info, planner, llm = self._setup_mocks("high")
        with patch.object(main_mod, "EnvironmentDetector", return_value=det), \
             patch.object(main_mod, "fetch_project_local", return_value=info), \
             patch.object(main_mod, "SmartPlanner", return_value=planner), \
             patch.object(main_mod, "create_provider", return_value=llm), \
             patch.object(main_mod, "check_command_safety", return_value=(True, "")), \
             patch.object(main_mod, "get_gpu_info", return_value={}), \
             patch.object(main_mod, "check_hardware_compatibility", return_value={}):
            result = main_mod.cmd_plan(".", use_local=True)
        assert result["status"] == "ok"


# ═══════════════════════════════════════════
#  cmd_install
# ═══════════════════════════════════════════

class TestCmdInstall:
    def test_install_dry_run(self):
        plan_result = _mock_plan_result()
        with patch.object(main_mod, "cmd_plan", return_value=plan_result), \
             patch("resilience.preflight_check", return_value=_mock_preflight()), \
             patch("resilience.generate_fallback_plans", return_value=[]), \
             patch("dependency_audit.audit_project", return_value=[]), \
             patch("license_check.fetch_license_from_github", return_value=("MIT", "")), \
             patch("license_check.analyze_license", return_value=MagicMock(risk="safe", issues=[])), \
             patch("skills.SkillManager") as mock_sm:
            mock_sm.return_value.find_matching_skills.return_value = []
            result = main_mod.cmd_install("testowner/testrepo", dry_run=True)
        assert result["status"] == "ok"

    def test_install_full_execution(self, tmp_path):
        plan_result = _mock_plan_result()
        mock_executor = MagicMock()
        mock_executor.execute_plan.return_value = _mock_exec_result(success=True)
        with patch.object(main_mod, "cmd_plan", return_value=plan_result), \
             patch("resilience.preflight_check", return_value=_mock_preflight()), \
             patch("resilience.generate_fallback_plans", return_value=[]), \
             patch("dependency_audit.audit_project", return_value=[]), \
             patch("license_check.fetch_license_from_github", return_value=("MIT", "")), \
             patch("license_check.analyze_license", return_value=MagicMock(risk="safe", issues=[])), \
             patch("skills.SkillManager") as mock_sm, \
             patch.object(main_mod, "InstallExecutor", return_value=mock_executor), \
             patch("db.record_install_telemetry"), \
             patch("auto_update.InstallTracker") as mock_tracker:
            mock_sm.return_value.find_matching_skills.return_value = []
            mock_tracker.return_value.record_install = MagicMock()
            result = main_mod.cmd_install("testowner/testrepo", install_dir=str(tmp_path))
        assert result["status"] == "ok"

    def test_install_with_fallback(self, tmp_path):
        plan_result = _mock_plan_result()
        mock_executor = MagicMock()
        # Main plan fails, fallback succeeds
        mock_executor.execute_plan.side_effect = [
            _mock_exec_result(success=False),
            _mock_exec_result(success=True),
        ]
        fb = MagicMock()
        fb.strategy = "docker"
        fb.tier = 2
        fb.confidence = "medium"
        fb.steps = [{"command": "docker compose up", "description": "fallback"}]
        with patch.object(main_mod, "cmd_plan", return_value=plan_result), \
             patch("resilience.preflight_check", return_value=_mock_preflight()), \
             patch("resilience.generate_fallback_plans", return_value=[fb]), \
             patch("dependency_audit.audit_project", return_value=[]), \
             patch("license_check.fetch_license_from_github", return_value=("MIT", "")), \
             patch("license_check.analyze_license", return_value=MagicMock(risk="safe", issues=[])), \
             patch("skills.SkillManager") as mock_sm, \
             patch.object(main_mod, "InstallExecutor", return_value=mock_executor), \
             patch("db.record_install_telemetry"), \
             patch("auto_update.InstallTracker") as mock_tracker:
            mock_sm.return_value.find_matching_skills.return_value = []
            mock_tracker.return_value.record_install = MagicMock()
            result = main_mod.cmd_install("testowner/testrepo", install_dir=str(tmp_path))

    def test_install_plan_fails(self):
        with patch.object(main_mod, "cmd_plan",
                          return_value={"status": "error", "message": "not found"}):
            result = main_mod.cmd_install("bad/repo")
        assert result["status"] == "error"


# ═══════════════════════════════════════════
#  cmd_doctor
# ═══════════════════════════════════════════

class TestCmdDoctor:
    def test_cmd_doctor(self):
        report = MagicMock()
        with patch("doctor.run_doctor", return_value=report), \
             patch("doctor.format_doctor_report", return_value="ok"), \
             patch("doctor.doctor_to_dict", return_value={"status": "ok"}):
            result = main_mod.cmd_doctor()
        assert result["status"] == "ok"

    def test_cmd_doctor_json(self):
        report = MagicMock()
        with patch("doctor.run_doctor", return_value=report), \
             patch("doctor.format_doctor_report", return_value="ok"), \
             patch("doctor.doctor_to_dict", return_value={"status": "ok", "checks": []}):
            result = main_mod.cmd_doctor(json_output=True)


# ═══════════════════════════════════════════
#  cmd_skills
# ═══════════════════════════════════════════

class TestCmdSkills:
    def test_skills_list(self):
        args = MagicMock()
        args.skills_action = "list"
        sm = MagicMock()
        sm.list_skills.return_value = []
        with patch("skills.SkillManager", return_value=sm), \
             patch("skills.format_skills_list", return_value="no skills"), \
             patch("skills.ensure_builtin_skills"):
            result = main_mod.cmd_skills(args)

    def test_skills_create(self):
        args = MagicMock()
        args.skills_action = "create"
        args.name = "test-skill"
        args.desc = "A test skill"
        sm = MagicMock()
        sm.create_skill.return_value = {"status": "ok"}
        with patch("skills.SkillManager", return_value=sm), \
             patch("skills.ensure_builtin_skills"):
            result = main_mod.cmd_skills(args)


# ═══════════════════════════════════════════
#  cmd_config
# ═══════════════════════════════════════════

class TestCmdConfig:
    def test_config_show(self):
        args = MagicMock()
        args.config_action = "show"
        val_result = MagicMock()
        val_result.config = {"api_key": "sk-xxx12345"}
        with patch("config_schema.load_and_validate", return_value=val_result), \
             patch("onboard.CONFIG_FILE", "/tmp/config.toml"):
            result = main_mod.cmd_config(args)
        assert result["status"] == "ok"

    def test_config_validate(self):
        args = MagicMock()
        args.config_action = "validate"
        val_result = MagicMock()
        val_result.valid = True
        val_result.errors = []
        val_result.warnings = []
        with patch("config_schema.load_and_validate", return_value=val_result), \
             patch("config_schema.format_validation_result", return_value="valid"), \
             patch("onboard.CONFIG_FILE", "/tmp/config.toml"):
            result = main_mod.cmd_config(args)
        assert result["status"] == "ok"

    def test_config_path(self):
        args = MagicMock()
        args.config_action = "path"
        with patch("onboard.CONFIG_FILE", "/tmp/config.toml"):
            result = main_mod.cmd_config(args)
        assert result["status"] == "ok"


# ═══════════════════════════════════════════
#  cmd_audit / cmd_license
# ═══════════════════════════════════════════

class TestCmdAudit:
    def test_audit(self):
        info = _mock_project_info()
        audit_result = MagicMock()
        with patch.object(main_mod, "fetch_project", return_value=info), \
             patch("dependency_audit.audit_project", return_value=audit_result), \
             patch("dependency_audit.format_audit_results", return_value="ok"), \
             patch("dependency_audit.audit_to_dict", return_value={"status": "ok"}):
            result = main_mod.cmd_audit("testowner/testrepo")


class TestCmdLicense:
    def test_license(self):
        compat = MagicMock()
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("license_check.fetch_license_from_github", return_value=("MIT", "text")), \
             patch("license_check.analyze_license", return_value=compat), \
             patch("license_check.format_license_result", return_value="MIT ok"), \
             patch("license_check.license_to_dict", return_value={"spdx": "MIT"}):
            result = main_mod.cmd_license("owner/repo")


# ═══════════════════════════════════════════
#  cmd_updates
# ═══════════════════════════════════════════

class TestCmdUpdates:
    def test_updates_list(self):
        args = MagicMock()
        args.updates_action = "list"
        tracker = MagicMock()
        tracker.list_installed.return_value = []
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("auto_update.format_installed_list", return_value="none"):
            result = main_mod.cmd_updates(args)

    def test_updates_check(self):
        args = MagicMock()
        args.updates_action = "check"
        tracker = MagicMock()
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.format_update_results", return_value="ok"), \
             patch("auto_update.updates_to_dict", return_value={"updates": []}):
            result = main_mod.cmd_updates(args)

    def test_updates_remove(self):
        args = MagicMock()
        args.updates_action = "remove"
        args.project = "owner/repo"
        tracker = MagicMock()
        tracker.remove_project.return_value = True
        with patch("auto_update.InstallTracker", return_value=tracker):
            result = main_mod.cmd_updates(args)


# ═══════════════════════════════════════════
#  cmd_resume
# ═══════════════════════════════════════════

class TestCmdResume:
    def test_resume_list(self):
        mgr = MagicMock()
        mgr.get_resumable.return_value = []
        with patch("checkpoint.CheckpointManager", return_value=mgr), \
             patch("checkpoint.format_checkpoint_list", return_value="none"):
            result = main_mod.cmd_resume()
        assert result["status"] == "ok"

    def test_resume_specific(self):
        cp = MagicMock()
        cp.steps = [MagicMock(status="completed"), MagicMock(status="failed", command="make")]
        cp.plan = {"steps": [{"command": "install"}, {"command": "make"}]}
        cp.install_dir = "/tmp/repo"
        mgr = MagicMock()
        mgr.get_checkpoint.return_value = cp
        mgr.get_resume_step.return_value = 1
        with patch("checkpoint.CheckpointManager", return_value=mgr), \
             patch("checkpoint.format_resume_plan", return_value="resume from step 2"), \
             patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch.object(main_mod, "cmd_install", return_value={"status": "ok"}):
            result = main_mod.cmd_resume("owner/repo")


# ═══════════════════════════════════════════
#  cmd_events
# ═══════════════════════════════════════════

class TestCmdEvents:
    def test_events(self):
        args = MagicMock()
        args.type = None
        args.limit = 20
        bus = MagicMock()
        bus.get_history.return_value = []
        with patch("event_bus.get_event_bus", return_value=bus):
            result = main_mod.cmd_events(args)

    def test_events_with_type_filter(self):
        args = MagicMock()
        args.type = "install"
        args.limit = 10
        bus = MagicMock()
        bus.get_history.return_value = [MagicMock(event_type="install")]
        with patch("event_bus.get_event_bus", return_value=bus):
            result = main_mod.cmd_events(args)


# ═══════════════════════════════════════════
#  cmd_autopilot
# ═══════════════════════════════════════════

class TestCmdAutopilot:
    def test_autopilot_run(self):
        args = MagicMock()
        args.autopilot_action = "run"
        args.source = "project1,project2"
        args.dry_run = False
        args.max_concurrent = 1
        batch = MagicMock()
        with patch("autopilot.parse_project_list", return_value=["p1", "p2"]), \
             patch("autopilot.run_autopilot", return_value=batch), \
             patch("autopilot.format_batch_result", return_value="done"):
            result = main_mod.cmd_autopilot(args)

    def test_autopilot_resume(self):
        args = MagicMock()
        args.autopilot_action = "resume"
        batch = MagicMock()
        with patch("autopilot.resume_autopilot", return_value=batch), \
             patch("autopilot.format_batch_result", return_value="resumed"):
            result = main_mod.cmd_autopilot(args)


# ═══════════════════════════════════════════
#  cmd_uninstall
# ═══════════════════════════════════════════

class TestCmdUninstall:
    def test_uninstall_dry_run(self):
        tracker = MagicMock()
        tracker.get_install_info.return_value = {"install_dir": "/tmp/repo"}
        plan = MagicMock()
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("fetcher.parse_repo_identifier", return_value=("o", "r")), \
             patch("uninstaller.plan_uninstall", return_value=plan), \
             patch("uninstaller.format_uninstall_plan", return_value="plan"), \
             patch("uninstaller.uninstall_to_dict", return_value={"plan": {}}):
            result = main_mod.cmd_uninstall("o/r", confirm=False)

    def test_uninstall_confirm(self):
        tracker = MagicMock()
        tracker.get_install_info.return_value = {"install_dir": "/tmp/repo"}
        plan = MagicMock()
        exec_result = MagicMock()
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("fetcher.parse_repo_identifier", return_value=("o", "r")), \
             patch("uninstaller.plan_uninstall", return_value=plan), \
             patch("uninstaller.execute_uninstall", return_value=exec_result), \
             patch("uninstaller.format_uninstall_plan", return_value="plan"), \
             patch("uninstaller.uninstall_to_dict", return_value={"result": {}}):
            result = main_mod.cmd_uninstall("o/r", confirm=True)


# ═══════════════════════════════════════════
#  cmd_flags / cmd_registry / cmd_chain / cmd_kb / cmd_platforms
# ═══════════════════════════════════════════

class TestCmdMisc:
    def test_flags_list(self):
        args = MagicMock()
        args.flags_action = "list"
        args.group = None
        with patch("feature_flags.get_all_status", return_value={}), \
             patch("feature_flags.format_flags_table", return_value="flags"), \
             patch("feature_flags.list_flags", return_value=[]):
            result = main_mod.cmd_flags(args)

    def test_registry_list(self):
        args = MagicMock()
        args.registry_action = "list"
        reg = MagicMock()
        reg.list_all.return_value = []
        reg.format_registry.return_value = "registry"
        with patch("installer_registry.InstallerRegistry", return_value=reg):
            result = main_mod.cmd_registry(args)

    def test_chain(self):
        plan = {"status": "ok", "plan": {"steps": []}}
        chain = MagicMock()
        with patch.object(main_mod, "cmd_plan", return_value=plan), \
             patch("dep_chain.build_chain_from_plan", return_value=chain), \
             patch("dep_chain.format_dep_chain", return_value="chain"):
            result = main_mod.cmd_chain("owner/repo")

    def test_kb_stats(self):
        args = MagicMock()
        args.kb_action = "stats"
        kb = MagicMock()
        kb.get_stats.return_value = {"total": 0}
        with patch("knowledge_base.KnowledgeBase", return_value=kb), \
             patch("knowledge_base.format_kb_stats", return_value="stats"):
            result = main_mod.cmd_kb(args)

    def test_kb_search(self):
        args = MagicMock()
        args.kb_action = "search"
        args.query = "flask"
        args.limit = 5
        kb = MagicMock()
        kb.search.return_value = []
        with patch("knowledge_base.KnowledgeBase", return_value=kb), \
             patch("knowledge_base.format_search_results", return_value="results"):
            result = main_mod.cmd_kb(args)

    def test_platforms(self):
        with patch("multi_source.get_supported_platforms",
                    return_value=[{"name": "github", "env_token": "GITHUB_TOKEN",
                                   "base_url": "https://github.com", "domain": "github.com"}]):
            result = main_mod.cmd_platforms()


# ═══════════════════════════════════════════
#  main() CLI dispatcher
# ═══════════════════════════════════════════

class TestMainCli:
    def test_main_detect(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["gitinstall", "detect"])
        mock_det = MagicMock()
        mock_det.detect.return_value = _mock_env()
        with patch.object(main_mod, "EnvironmentDetector", return_value=mock_det), \
             patch.object(main_mod, "format_env_summary", return_value="ok"), \
             patch("builtins.print"):
            try:
                main_mod.main()
            except SystemExit:
                pass

    def test_main_web(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["gitinstall", "web", "--port", "9999"])
        with patch("web.start_server"):
            try:
                main_mod.main()
            except SystemExit:
                pass

    def test_main_onboard(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["gitinstall", "onboard"])
        with patch("onboard.run_onboard"):
            try:
                main_mod.main()
            except SystemExit:
                pass
