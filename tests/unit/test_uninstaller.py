"""tests/unit/test_uninstaller.py - 安全卸载测试"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import uninstaller
from uninstaller import (
    CleanupItem,
    UninstallPlan,
    _is_safe_path,
    _dir_size,
    _find_venvs,
    _find_docker_artifacts,
    _find_cache,
    _find_build_artifacts,
    plan_uninstall,
    execute_uninstall,
    format_uninstall_plan,
    uninstall_to_dict,
)


# ─────────────────────────────────────────────
#  安全路径检查测试
# ─────────────────────────────────────────────

class TestSafePath:
    def test_home_subdir_safe(self):
        assert _is_safe_path(Path.home() / "projects" / "test") is True

    def test_home_itself_not_safe(self):
        assert _is_safe_path(Path.home()) is False

    def test_root_not_safe(self):
        assert _is_safe_path(Path("/")) is False

    def test_system_dirs_not_safe(self):
        for d in ["/usr", "/bin", "/etc", "/var", "/System", "/Library"]:
            assert _is_safe_path(Path(d)) is False, f"{d} should not be safe"

    def test_tmp_safe(self):
        assert _is_safe_path(Path("/tmp/test-project")) is True


# ─────────────────────────────────────────────
#  文件探测测试
# ─────────────────────────────────────────────

class TestFindVenvs:
    def test_find_venv(self, tmp_path):
        venv_dir = tmp_path / "venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "activate").touch()
        result = _find_venvs(tmp_path)
        assert len(result) == 1
        assert result[0].name == "venv"

    def test_find_dotenv(self, tmp_path):
        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").touch()
        result = _find_venvs(tmp_path)
        assert len(result) == 1

    def test_no_venv(self, tmp_path):
        result = _find_venvs(tmp_path)
        assert result == []

    def test_false_positive_avoided(self, tmp_path):
        # A directory named "venv" but without venv indicators
        (tmp_path / "venv").mkdir()
        result = _find_venvs(tmp_path)
        assert result == []


class TestFindDockerArtifacts:
    def test_docker_compose(self, tmp_path):
        (tmp_path / "docker-compose.yml").touch()
        result = _find_docker_artifacts(tmp_path)
        assert len(result) == 1
        assert "docker-compose" in result[0]

    def test_no_docker(self, tmp_path):
        result = _find_docker_artifacts(tmp_path)
        assert result == []


class TestFindBuildArtifacts:
    def test_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        result = _find_build_artifacts(tmp_path)
        assert any(p.name == "__pycache__" for p in result)

    def test_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        result = _find_build_artifacts(tmp_path)
        assert any(p.name == "node_modules" for p in result)

    def test_egg_info(self, tmp_path):
        (tmp_path / "mypackage.egg-info").mkdir()
        result = _find_build_artifacts(tmp_path)
        assert any("egg-info" in p.name for p in result)


class TestDirSize:
    def test_empty_dir(self, tmp_path):
        assert _dir_size(tmp_path) == 0

    def test_with_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello" * 100)
        (tmp_path / "b.txt").write_text("world" * 200)
        size = _dir_size(tmp_path)
        assert size > 0


# ─────────────────────────────────────────────
#  卸载计划测试
# ─────────────────────────────────────────────

class TestPlanUninstall:
    def _patch_safe(self, tmp_path):
        """Ensure tmp_path is in SAFE_BASES for macOS /private/var/... paths."""
        return patch.object(uninstaller, "SAFE_BASES",
                            [Path.home(), Path("/tmp"), tmp_path])

    def test_nonexistent_dir(self):
        plan = plan_uninstall("owner", "repo", "/tmp/nonexistent_xyz_12345")
        assert plan.error

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix paths")
    def test_unsafe_path(self):
        plan = plan_uninstall("owner", "repo", "/usr")
        assert plan.error
        assert "安全" in plan.error

    def test_basic_plan(self, tmp_path):
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("print('hello')")
        (project_dir / "__pycache__").mkdir()
        (project_dir / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00" * 100)

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("owner", "repo", str(project_dir))
        assert not plan.error
        assert len(plan.items) > 0
        types = [item.item_type for item in plan.items]
        assert "directory" in types

    def test_clean_only(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "__pycache__").mkdir()
        (project_dir / "node_modules").mkdir()

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("owner", "repo", str(project_dir), clean_only=True)
        types = [item.item_type for item in plan.items]
        assert "directory" not in types

    def test_with_git_warning(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("owner", "repo", str(project_dir))
        assert any(".git" in w for w in plan.warnings)

    def test_with_user_data_warning(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".env").write_text("SECRET=xxx")

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("owner", "repo", str(project_dir))
        assert any("用户数据" in w for w in plan.warnings)


# ─────────────────────────────────────────────
#  卸载执行测试
# ─────────────────────────────────────────────

class TestExecuteUninstall:
    def _patch_safe(self, tmp_path):
        return patch.object(uninstaller, "SAFE_BASES",
                            [Path.home(), Path("/tmp"), tmp_path])

    def test_execute_removes_files(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("hello")
        (project_dir / "__pycache__").mkdir()

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("own", "rep", str(project_dir))
            result = execute_uninstall(plan)
        assert result["success"] is True
        assert not project_dir.exists()

    def test_execute_with_error_plan(self):
        plan = UninstallPlan(owner="a", repo="b", install_dir="/nonexistent",
                             error="测试错误")
        result = execute_uninstall(plan)
        assert result["success"] is False

    def test_execute_clean_only(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("hello")
        cache = project_dir / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-313.pyc").write_bytes(b"\x00" * 50)

        with self._patch_safe(tmp_path):
            plan = plan_uninstall("own", "rep", str(project_dir), clean_only=True)
            result = execute_uninstall(plan)
        # Main dir should still exist
        assert project_dir.exists()
        assert (project_dir / "main.py").exists()
        # Cache should be gone
        assert not cache.exists()


# ─────────────────────────────────────────────
#  UninstallPlan 属性测试
# ─────────────────────────────────────────────

class TestUninstallPlan:
    def test_total_size_mb(self):
        plan = UninstallPlan(owner="a", repo="b", install_dir="/tmp",
                             total_size=10 * 1024 * 1024)
        assert abs(plan.total_size_mb - 10.0) < 0.01


# ─────────────────────────────────────────────
#  格式化 & 序列化测试
# ─────────────────────────────────────────────

class TestFormat:
    def test_format_error_plan(self):
        plan = UninstallPlan(owner="a", repo="b", install_dir="/tmp",
                             error="目录不存在")
        text = format_uninstall_plan(plan)
        assert "目录不存在" in text

    def test_format_empty_plan(self):
        plan = UninstallPlan(owner="a", repo="b", install_dir="/tmp")
        text = format_uninstall_plan(plan)
        assert "无需清理" in text

    def test_format_with_items(self):
        plan = UninstallPlan(
            owner="a", repo="b", install_dir="/tmp",
            items=[
                CleanupItem(path="/tmp/project", item_type="directory",
                            size_bytes=1024*1024*50, description="项目主目录"),
                CleanupItem(path="/tmp/project/venv", item_type="venv",
                            size_bytes=1024*1024*200, description="Python 虚拟环境"),
            ],
            total_size=1024*1024*250,
        )
        text = format_uninstall_plan(plan)
        assert "项目主目录" in text
        assert "虚拟环境" in text
        assert "250" in text

    def test_to_dict(self):
        plan = UninstallPlan(
            owner="a", repo="b", install_dir="/tmp/test",
            items=[CleanupItem(path="/tmp/test", item_type="directory",
                               size_bytes=1024, description="Test")],
            total_size=1024,
        )
        d = uninstall_to_dict(plan)
        assert d["owner"] == "a"
        assert d["repo"] == "b"
        assert len(d["items"]) == 1
        assert d["total_size_mb"] == 0.0
