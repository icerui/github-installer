"""
test_cmd_coverage.py - CLI 命令处理器覆盖率突破
=================================================

突破性算法：按"可测试模式"分类 → 参数化矩阵 → O(K) 代码覆盖 O(N) 函数

覆盖 main.py 中所有 cmd_* 函数 (~365 行未覆盖代码)

模式 A: 零依赖纯函数 (cmd_platforms, cmd_flags, cmd_events)
模式 B: 轻量 mock 委托 (cmd_doctor, cmd_resume-list, cmd_kb)
模式 C: 子命令分发器 (cmd_skills, cmd_config, cmd_updates, cmd_autopilot)
模式 D: 网络/I/O 委托 (cmd_audit, cmd_license, cmd_chain, cmd_uninstall)
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest


# ── 惰性导入 main 模块 ──
@pytest.fixture(scope="module")
def main_mod():
    import main
    return main


# ── Mock 工厂 ──

def _mock_project_info(**overrides):
    """模拟 fetch_project 返回的 ProjectInfo"""
    info = MagicMock()
    info.owner = overrides.get("owner", "test")
    info.repo = overrides.get("repo", "project")
    info.full_name = f"{info.owner}/{info.repo}"
    info.dependency_files = overrides.get("dep_files", {"requirements.txt": "flask==2.0"})
    info.readme = "# Test"
    info.language = "Python"
    info.project_types = ["python_package"]
    info.description = "Test project"
    return info


def _mock_doctor_report():
    report = MagicMock()
    report.checks = [MagicMock(name="git", status="ok")]
    return report


# ========================================================
#  模式 A: 零依赖纯函数 — 直接调用即可
# ========================================================

class TestPureCLICommands:
    """这些命令调用的模块只做纯计算/读内存，无外部 I/O"""

    def test_cmd_platforms(self, main_mod):
        result = main_mod.cmd_platforms()
        assert result["status"] == "ok"
        assert isinstance(result["platforms"], list)
        assert len(result["platforms"]) >= 5  # GitHub, GitLab, Bitbucket, Gitee, Codeberg

    @pytest.mark.parametrize("action", ["show", "list"])
    def test_cmd_flags(self, main_mod, action):
        args = SimpleNamespace(flags_action=action, group=None)
        result = main_mod.cmd_flags(args)
        assert result["status"] == "ok"
        assert "flags" in result

    def test_cmd_flags_by_group(self, main_mod):
        args = SimpleNamespace(flags_action="list", group="security")
        result = main_mod.cmd_flags(args)
        assert result["status"] == "ok"
        assert isinstance(result["flags"], list)

    def test_cmd_flags_unknown(self, main_mod):
        args = SimpleNamespace(flags_action="nonexistent")
        result = main_mod.cmd_flags(args)
        assert result["status"] == "error"

    def test_cmd_events_empty(self, main_mod):
        args = SimpleNamespace(type=None, limit=50)
        result = main_mod.cmd_events(args)
        assert result["status"] == "ok"
        assert "events" in result
        assert "total" in result


# ========================================================
#  模式 B: 轻量 mock 委托 — mock 1~2 个函数
# ========================================================

class TestLightMockCommands:

    def test_cmd_doctor(self, main_mod):
        mock_report = _mock_doctor_report()
        with patch("doctor.run_doctor", return_value=mock_report), \
             patch("doctor.format_doctor_report", return_value="OK"), \
             patch("doctor.doctor_to_dict", return_value={"status": "ok", "checks": []}):
            result = main_mod.cmd_doctor()
            assert result["status"] == "ok"

    def test_cmd_doctor_json(self, main_mod):
        mock_report = _mock_doctor_report()
        with patch("doctor.run_doctor", return_value=mock_report), \
             patch("doctor.format_doctor_report", return_value="OK"), \
             patch("doctor.doctor_to_dict", return_value={"status": "ok", "checks": []}):
            result = main_mod.cmd_doctor(json_output=True)
            assert result["status"] == "ok"

    def test_cmd_resume_list_empty(self, main_mod):
        mock_mgr = MagicMock()
        mock_mgr.get_resumable.return_value = []
        with patch("checkpoint.CheckpointManager", return_value=mock_mgr):
            result = main_mod.cmd_resume()
            assert result["status"] == "ok"
            assert result["resumable"] == []

    def test_cmd_resume_list_with_items(self, main_mod):
        mock_cp = MagicMock()
        mock_cp.project = "owner/repo"
        mock_mgr = MagicMock()
        mock_mgr.get_resumable.return_value = [mock_cp]
        with patch("checkpoint.CheckpointManager", return_value=mock_mgr), \
             patch("checkpoint.format_checkpoint_list", return_value="checkpoints"):
            result = main_mod.cmd_resume()
            assert result["status"] == "ok"
            assert len(result["resumable"]) == 1

    def test_cmd_resume_not_found(self, main_mod):
        mock_mgr = MagicMock()
        mock_mgr.get_checkpoint.return_value = None
        with patch("checkpoint.CheckpointManager", return_value=mock_mgr), \
             patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")):
            result = main_mod.cmd_resume("owner/repo")
            assert result["status"] == "error"


# ========================================================
#  模式 C: 子命令分发器 — 参数化 action 矩阵
# ========================================================

class TestCmdSkills:
    """cmd_skills 的 5 个子命令用 1 个 mock SkillManager 覆盖"""

    @pytest.fixture(autouse=True)
    def _setup_skills_mock(self):
        self.mock_mgr = MagicMock()
        # list_skills 返回带 meta 属性的 mock 列表
        mock_skill = MagicMock()
        mock_skill.meta.name = "auto-venv"
        mock_skill.meta.version = "1.0"
        mock_skill.meta.description = "Auto venv"
        mock_skill.enabled = True
        self.mock_mgr.list_skills.return_value = [mock_skill]
        self.mock_mgr.create_skill.return_value = Path("/tmp/test-skill")
        self.mock_mgr.remove_skill.return_value = True
        self.mock_mgr.export_skill.return_value = {"name": "test"}

    @pytest.mark.parametrize("action,extra,expected_status", [
        ("list", {}, "ok"),
        ("init", {}, "ok"),
        ("create", {"name": "test", "desc": "Test skill"}, "ok"),
        ("remove", {"name": "auto-venv"}, "ok"),
        ("export", {"name": "auto-venv"}, "ok"),
    ])
    def test_skills_action(self, main_mod, action, extra, expected_status):
        args = SimpleNamespace(skills_action=action, **extra)
        with patch("skills.SkillManager", return_value=self.mock_mgr), \
             patch("skills.ensure_builtin_skills"), \
             patch("skills.format_skills_list", return_value="skills list"):
            result = main_mod.cmd_skills(args)
            assert result["status"] == expected_status

    def test_skills_unknown_action(self, main_mod):
        args = SimpleNamespace(skills_action="unknown")
        with patch("skills.SkillManager", return_value=self.mock_mgr), \
             patch("skills.ensure_builtin_skills"), \
             patch("skills.format_skills_list", return_value=""):
            result = main_mod.cmd_skills(args)
            assert result["status"] == "error"


class TestCmdConfig:
    """cmd_config 的 3 个子命令"""

    @pytest.fixture(autouse=True)
    def _setup_config_mock(self):
        self.mock_result = MagicMock()
        self.mock_result.config = {"install_mode": "safe", "telemetry": True}
        self.mock_result.valid = True
        self.mock_result.errors = []
        self.mock_result.warnings = []

    @pytest.mark.parametrize("action", ["show", "validate", "path"])
    def test_config_action(self, main_mod, action):
        args = SimpleNamespace(config_action=action)
        with patch("config_schema.load_and_validate", return_value=self.mock_result), \
             patch("config_schema.format_validation_result", return_value="ok"), \
             patch("onboard.CONFIG_FILE", Path("/tmp/config.json")):
            result = main_mod.cmd_config(args)
            assert result["status"] == "ok"

    def test_config_unknown(self, main_mod):
        args = SimpleNamespace(config_action="unknown")
        with patch("config_schema.load_and_validate", return_value=self.mock_result), \
             patch("config_schema.format_validation_result", return_value=""), \
             patch("onboard.CONFIG_FILE", Path("/tmp/config.json")):
            result = main_mod.cmd_config(args)
            assert result["status"] == "error"


class TestCmdUpdates:
    """cmd_updates 的 3 个子命令 (list/check/remove)"""

    @pytest.fixture(autouse=True)
    def _setup_tracker_mock(self):
        self.mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.to_dict.return_value = {"owner": "a", "repo": "b"}
        self.mock_tracker.list_installed.return_value = [mock_proj]
        self.mock_tracker.remove_project.return_value = True

    @pytest.mark.parametrize("action,extra,expected_status", [
        ("list", {}, "ok"),
        ("check", {}, "ok"),
        ("remove", {"name": "owner/repo"}, "ok"),
    ])
    def test_updates_action(self, main_mod, action, extra, expected_status):
        args = SimpleNamespace(updates_action=action, **extra)
        with patch("auto_update.InstallTracker", return_value=self.mock_tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.format_installed_list", return_value="list"), \
             patch("auto_update.format_update_results", return_value="results"), \
             patch("auto_update.updates_to_dict", return_value={"updates": []}):
            result = main_mod.cmd_updates(args)
            assert result["status"] == expected_status

    def test_updates_remove_bad_format(self, main_mod):
        args = SimpleNamespace(updates_action="remove", name="badformat")
        with patch("auto_update.InstallTracker", return_value=self.mock_tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.format_installed_list", return_value=""), \
             patch("auto_update.format_update_results", return_value=""), \
             patch("auto_update.updates_to_dict", return_value={}):
            result = main_mod.cmd_updates(args)
            assert result["status"] == "error"

    def test_updates_unknown(self, main_mod):
        args = SimpleNamespace(updates_action="unknown")
        with patch("auto_update.InstallTracker", return_value=self.mock_tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.format_installed_list", return_value=""), \
             patch("auto_update.format_update_results", return_value=""), \
             patch("auto_update.updates_to_dict", return_value={}):
            result = main_mod.cmd_updates(args)
            assert result["status"] == "error"


class TestCmdKb:
    """cmd_kb 的 3 个子命令 (stats/search/rate)"""

    @pytest.fixture(autouse=True)
    def _setup_kb_mock(self):
        self.mock_kb = MagicMock()
        self.mock_kb.get_stats.return_value = {"total": 0, "success_count": 0, "fail_count": 0}
        self.mock_kb.search.return_value = []
        self.mock_kb.get_success_rate.return_value = {"rate": 0.0, "success": 0, "total": 0}

    @pytest.mark.parametrize("action,extra", [
        ("stats", {}),
        ("search", {"query": "pytorch"}),
        ("rate", {"project": "test/repo"}),
    ])
    def test_kb_action(self, main_mod, action, extra):
        args = SimpleNamespace(kb_action=action, **extra)
        with patch("knowledge_base.KnowledgeBase", return_value=self.mock_kb), \
             patch("knowledge_base.format_kb_stats", return_value="stats"), \
             patch("knowledge_base.format_search_results", return_value="results"):
            result = main_mod.cmd_kb(args)
            assert result["status"] == "ok"

    def test_kb_search_empty_query(self, main_mod):
        args = SimpleNamespace(kb_action="search", query="")
        with patch("knowledge_base.KnowledgeBase", return_value=self.mock_kb), \
             patch("knowledge_base.format_kb_stats", return_value=""), \
             patch("knowledge_base.format_search_results", return_value=""):
            result = main_mod.cmd_kb(args)
            assert result["status"] == "error"

    def test_kb_unknown(self, main_mod):
        args = SimpleNamespace(kb_action="unknown")
        with patch("knowledge_base.KnowledgeBase", return_value=self.mock_kb), \
             patch("knowledge_base.format_kb_stats", return_value=""), \
             patch("knowledge_base.format_search_results", return_value=""):
            result = main_mod.cmd_kb(args)
            assert result["status"] == "error"


class TestCmdAutopilot:
    """cmd_autopilot 的子命令 (run/resume/dry_run)"""

    def test_autopilot_dry_run(self, main_mod):
        args = SimpleNamespace(
            autopilot_action="run", projects="a/b c/d",
            dry_run=True, dir=None, llm=None,
        )
        with patch("autopilot.parse_project_list", return_value=["a/b", "c/d"]):
            result = main_mod.cmd_autopilot(args)
            assert result["status"] == "ok"
            assert result["dry_run"] is True
            assert result["total"] == 2

    def test_autopilot_empty_projects(self, main_mod):
        args = SimpleNamespace(
            autopilot_action="run", projects="",
            dry_run=False, dir=None, llm=None,
        )
        result = main_mod.cmd_autopilot(args)
        assert result["status"] == "error"

    def test_autopilot_resume(self, main_mod):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"total": 2, "completed": 2}
        args = SimpleNamespace(
            autopilot_action="resume", llm=None, dir=None,
        )
        with patch("autopilot.resume_autopilot", return_value=mock_result), \
             patch("autopilot.parse_project_list", return_value=[]), \
             patch("autopilot.run_autopilot", return_value=mock_result), \
             patch("autopilot.format_batch_result", return_value="report"):
            result = main_mod.cmd_autopilot(args)
            assert result["status"] == "ok"

    def test_autopilot_resume_empty(self, main_mod):
        args = SimpleNamespace(
            autopilot_action="resume", llm=None, dir=None,
        )
        with patch("autopilot.resume_autopilot", return_value=None), \
             patch("autopilot.parse_project_list", return_value=[]), \
             patch("autopilot.run_autopilot"), \
             patch("autopilot.format_batch_result", return_value=""):
            result = main_mod.cmd_autopilot(args)
            assert result["status"] == "error"

    def test_autopilot_no_valid_projects(self, main_mod):
        args = SimpleNamespace(
            autopilot_action="run", projects="garbage",
            dry_run=False, dir=None, llm=None,
        )
        with patch("autopilot.parse_project_list", return_value=[]):
            result = main_mod.cmd_autopilot(args)
            assert result["status"] == "error"


class TestCmdRegistry:
    """cmd_registry 的子命令"""

    @pytest.mark.parametrize("action", ["list", "detect"])
    def test_registry_action(self, main_mod, action):
        mock_reg = MagicMock()
        mock_inst = MagicMock()
        mock_inst.info.name = "pip"
        mock_inst.info.version = "24.0"
        mock_inst.info.ecosystems = ["python"]
        mock_reg.list_all.return_value = [mock_inst]
        mock_reg.list_available.return_value = [mock_inst]
        mock_reg.format_registry.return_value = "registry"
        mock_reg.to_dict.return_value = {"pip": {}}

        args = SimpleNamespace(registry_action=action)
        with patch("installer_registry.InstallerRegistry", return_value=mock_reg):
            result = main_mod.cmd_registry(args)
            assert result["status"] == "ok"

    def test_registry_unknown(self, main_mod):
        mock_reg = MagicMock()
        args = SimpleNamespace(registry_action="unknown")
        with patch("installer_registry.InstallerRegistry", return_value=mock_reg):
            result = main_mod.cmd_registry(args)
            assert result["status"] == "error"


# ========================================================
#  模式 D: 网络/IO 委托 — mock 外部调用
# ========================================================

class TestCmdAudit:
    """cmd_audit: mock fetch_project + audit_project"""

    def test_audit_success(self, main_mod):
        mock_info = _mock_project_info()
        with patch.object(main_mod, "fetch_project", return_value=mock_info), \
             patch("dependency_audit.audit_project", return_value=[]), \
             patch("dependency_audit.format_audit_results", return_value="ok"), \
             patch("dependency_audit.audit_to_dict", return_value={"results": []}):
            result = main_mod.cmd_audit("test/project")
            assert result["status"] == "ok"

    def test_audit_no_deps(self, main_mod):
        mock_info = _mock_project_info(dep_files={})
        with patch.object(main_mod, "fetch_project", return_value=mock_info):
            result = main_mod.cmd_audit("test/project")
            assert result["status"] == "ok"
            assert result["results"] == []

    def test_audit_error(self, main_mod):
        with patch.object(main_mod, "fetch_project", side_effect=Exception("net error")):
            result = main_mod.cmd_audit("test/project")
            assert result["status"] == "error"


class TestCmdLicense:
    """cmd_license: mock network calls"""

    def test_license_found(self, main_mod):
        mock_result = MagicMock()
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("license_check.fetch_license_from_github", return_value=("MIT", "MIT License text")), \
             patch("license_check.analyze_license", return_value=mock_result), \
             patch("license_check.format_license_result", return_value="MIT"), \
             patch("license_check.license_to_dict", return_value={"spdx": "MIT", "risk": "low"}):
            result = main_mod.cmd_license("owner/repo")
            assert result["status"] == "ok"

    def test_license_not_found(self, main_mod):
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("license_check.fetch_license_from_github", return_value=(None, None)):
            result = main_mod.cmd_license("owner/repo")
            assert result["status"] == "ok"
            assert "risk" in result

    def test_license_error(self, main_mod):
        with patch("fetcher.parse_repo_identifier", side_effect=Exception("parse error")):
            result = main_mod.cmd_license("bad")
            assert result["status"] == "error"


class TestCmdChain:
    """cmd_chain: mock cmd_plan + dep_chain"""

    def test_chain_success(self, main_mod):
        mock_plan = {
            "status": "ok",
            "plan": {"steps": [{"command": "pip install -r requirements.txt",
                                "description": "Install deps"}]},
            "project": "test/repo",
        }
        mock_chain = MagicMock()
        mock_chain.to_dict.return_value = {"nodes": []}
        mock_chain.has_cycle.return_value = False
        mock_chain.nodes = []

        with patch.object(main_mod, "cmd_plan", return_value=mock_plan), \
             patch("dep_chain.build_chain_from_plan", return_value=mock_chain), \
             patch("dep_chain.format_dep_chain", return_value="chain"):
            result = main_mod.cmd_chain("test/repo")
            assert result["status"] == "ok"
            assert "chain" in result

    def test_chain_plan_fails(self, main_mod):
        mock_plan = {"status": "error", "message": "not found"}
        with patch.object(main_mod, "cmd_plan", return_value=mock_plan):
            result = main_mod.cmd_chain("nonexistent/repo")
            assert result["status"] == "error"


class TestCmdUninstall:
    """cmd_uninstall: mock all dependencies"""

    @pytest.fixture(autouse=True)
    def _setup_uninstall_mocks(self):
        self.mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.install_dir = "/tmp/test"
        self.mock_tracker.get_project.return_value = mock_proj
        self.mock_tracker.remove_project.return_value = True

        self.mock_plan = MagicMock()
        self.mock_plan.error = None

    def test_uninstall_dry_run(self, main_mod):
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("auto_update.InstallTracker", return_value=self.mock_tracker), \
             patch("uninstaller.plan_uninstall", return_value=self.mock_plan), \
             patch("uninstaller.format_uninstall_plan", return_value="plan"), \
             patch("uninstaller.uninstall_to_dict", return_value={"actions": []}):
            result = main_mod.cmd_uninstall("owner/repo", confirm=False)
            assert result["status"] == "ok"
            assert result.get("action") == "dry_run"

    def test_uninstall_confirmed(self, main_mod):
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("auto_update.InstallTracker", return_value=self.mock_tracker), \
             patch("uninstaller.plan_uninstall", return_value=self.mock_plan), \
             patch("uninstaller.execute_uninstall", return_value={"success": True, "freed_mb": 100, "errors": []}), \
             patch("uninstaller.format_uninstall_plan", return_value="plan"), \
             patch("uninstaller.uninstall_to_dict", return_value={"actions": []}):
            result = main_mod.cmd_uninstall("owner/repo", confirm=True)
            assert result["status"] == "ok"

    def test_uninstall_not_found(self, main_mod):
        mock_tracker = MagicMock()
        mock_tracker.get_project.return_value = None
        with patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("auto_update.InstallTracker", return_value=mock_tracker):
            result = main_mod.cmd_uninstall("owner/repo")
            assert result["status"] == "error"

    def test_uninstall_parse_error(self, main_mod):
        with patch("fetcher.parse_repo_identifier", side_effect=Exception("bad id")):
            result = main_mod.cmd_uninstall("bad")
            assert result["status"] == "error"
