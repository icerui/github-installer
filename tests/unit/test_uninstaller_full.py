"""uninstaller.py 全覆盖测试

覆盖目标：_find_venvs, _find_docker_artifacts, _find_cache, _find_build_artifacts,
          plan_uninstall, execute_uninstall, _remove_dir_except_configs,
          format_uninstall_plan, _size_str, uninstall_to_dict
"""
import os
import platform
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

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
    _remove_dir_except_configs,
    format_uninstall_plan,
    _size_str,
    uninstall_to_dict,
)
from pathlib import Path


# ─── _size_str ───


class TestSizeStr:
    def test_bytes(self):
        assert "500 B" in _size_str(500)

    def test_kilobytes(self):
        assert "KB" in _size_str(2048)

    def test_megabytes(self):
        assert "MB" in _size_str(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in _size_str(2 * 1024 * 1024 * 1024)


# ─── _find_venvs ───


class TestFindVenvs:
    def test_find_venv(self, tmp_path):
        venv_dir = tmp_path / "venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").touch()
        result = _find_venvs(tmp_path)
        assert len(result) == 1

    def test_find_dotenv(self, tmp_path):
        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").touch()
        result = _find_venvs(tmp_path)
        assert len(result) == 1

    def test_find_conda(self, tmp_path):
        conda_dir = tmp_path / ".conda" / "conda-meta"
        conda_dir.mkdir(parents=True)
        result = _find_venvs(tmp_path)
        assert len(result) == 1

    def test_no_venvs(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert _find_venvs(tmp_path) == []

    def test_venv_with_activate(self, tmp_path):
        venv_dir = tmp_path / "env" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "activate").touch()
        result = _find_venvs(tmp_path)
        assert len(result) == 1


# ─── _find_docker_artifacts ───


class TestFindDockerArtifacts:
    def test_compose_file(self, tmp_path):
        (tmp_path / "docker-compose.yml").touch()
        result = _find_docker_artifacts(tmp_path)
        assert len(result) == 1
        assert "docker-compose:" in result[0]

    def test_compose_yaml(self, tmp_path):
        (tmp_path / "compose.yaml").touch()
        result = _find_docker_artifacts(tmp_path)
        assert len(result) == 1

    def test_no_docker(self, tmp_path):
        assert _find_docker_artifacts(tmp_path) == []


# ─── _find_cache ───


class TestFindCache:
    def test_find_cache(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".cache" / "gitinstall"
        cache_dir.mkdir(parents=True)
        (cache_dir / "myrepo_cache.json").touch()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _find_cache("owner", "myrepo")
        assert len(result) == 1

    def test_no_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _find_cache("owner", "repo")
        assert result == []


# ─── _find_build_artifacts ───


class TestFindBuildArtifacts:
    def test_find_build_dirs(self, tmp_path):
        for d in ["build", "dist", "node_modules", "__pycache__", "target"]:
            (tmp_path / d).mkdir()
        result = _find_build_artifacts(tmp_path)
        assert len(result) == 5

    def test_egg_info_glob(self, tmp_path):
        (tmp_path / "mypackage.egg-info").mkdir()
        result = _find_build_artifacts(tmp_path)
        assert len(result) == 1


# ─── plan_uninstall ───


class TestPlanUninstall:
    def test_basic_plan(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("print('hello')")
        (project_dir / ".git").mkdir()

        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = plan_uninstall("owner", "repo", str(project_dir))
        assert plan.error == ""
        assert any(item.item_type == "directory" for item in plan.items)
        assert any(".git" in w for w in plan.warnings)

    def test_dir_not_exists(self, tmp_path):
        plan = plan_uninstall("o", "r", str(tmp_path / "nope"))
        assert "不存在" in plan.error

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix paths")
    def test_unsafe_path(self):
        plan = plan_uninstall("o", "r", "/usr/bin")
        assert "安全" in plan.error

    def test_with_venv(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        venv = project_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").touch()
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = plan_uninstall("o", "r", str(project_dir))
        assert any(item.item_type == "venv" for item in plan.items)

    def test_clean_only(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "build").mkdir()
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = plan_uninstall("o", "r", str(project_dir), clean_only=True)
        assert not any(item.item_type == "directory" for item in plan.items)

    def test_user_data_warning(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / ".env").touch()
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = plan_uninstall("o", "r", str(project_dir), keep_config=True)
        assert any("用户数据" in w for w in plan.warnings)
        assert any("keep-config" in w for w in plan.warnings)

    def test_with_docker_artifacts(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "docker-compose.yml").touch()
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = plan_uninstall("o", "r", str(project_dir))
        assert any(item.item_type == "docker" for item in plan.items)


# ─── execute_uninstall ───


class TestExecuteUninstall:
    def test_execute_basic(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "file.txt").write_text("data")

        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        plan = UninstallPlan(owner="o", repo="r", install_dir=str(project_dir))
        plan.items = [
            CleanupItem(path=str(project_dir), item_type="directory",
                        size_bytes=100, description="主目录"),
        ]
        result = execute_uninstall(plan)
        assert result["success"] is True
        assert not project_dir.exists()

    def test_execute_with_error(self):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp/x",
                             error="test error")
        result = execute_uninstall(plan)
        assert result["success"] is False

    def test_execute_docker_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        plan = UninstallPlan(owner="o", repo="r", install_dir=str(project_dir))
        plan.items = [
            CleanupItem(path="docker-compose:myproj", item_type="docker",
                        description="Docker"),
        ]
        with patch("subprocess.run") as mock_run:
            result = execute_uninstall(plan)
        assert mock_run.called
        assert any("Docker" in r for r in result["removed"])

    def test_execute_docker_timeout(self, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        plan = UninstallPlan(owner="o", repo="r", install_dir=str(project_dir))
        plan.items = [
            CleanupItem(path="docker-compose:myproj", item_type="docker",
                        description="Docker"),
        ]
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 30)):
            result = execute_uninstall(plan)
        assert any("失败" in e for e in result["errors"])

    def test_execute_keep_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / ".env").write_text("SECRET=1")
        (project_dir / "src").mkdir()
        (project_dir / "src" / "main.py").write_text("print()")

        plan = UninstallPlan(owner="o", repo="r", install_dir=str(project_dir))
        plan.items = [
            CleanupItem(path=str(project_dir), item_type="directory",
                        size_bytes=100, description="主目录"),
        ]
        result = execute_uninstall(plan, keep_config=True)
        assert (project_dir / ".env").exists()
        assert not (project_dir / "src").exists()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix paths")
    def test_execute_skip_unsafe(self, tmp_path, monkeypatch):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp")
        plan.items = [
            CleanupItem(path="/usr/bin", item_type="directory",
                        description="unsafe"),
        ]
        result = execute_uninstall(plan)
        assert any("不安全" in e for e in result["errors"])

    def test_execute_nonexistent_path(self, tmp_path):
        plan = UninstallPlan(owner="o", repo="r", install_dir=str(tmp_path))
        plan.items = [
            CleanupItem(path=str(tmp_path / "gone"), item_type="file"),
        ]
        result = execute_uninstall(plan)
        assert result["success"] is True

    def test_execute_permission_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("uninstaller.SAFE_BASES", [tmp_path])
        target = tmp_path / "locked"
        target.mkdir()
        plan = UninstallPlan(owner="o", repo="r", install_dir=str(tmp_path))
        plan.items = [
            CleanupItem(path=str(target), item_type="directory"),
        ]
        with patch("shutil.rmtree", side_effect=PermissionError("no")):
            result = execute_uninstall(plan)
        assert len(result["errors"]) > 0


# ─── _remove_dir_except_configs ───


class TestRemoveDirExceptConfigs:
    def test_keeps_config(self, tmp_path):
        (tmp_path / ".env").write_text("SECRET")
        (tmp_path / ".env.local").write_text("LOCAL")
        (tmp_path / "config.local").write_text("CONFIG")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("code")
        _remove_dir_except_configs(tmp_path, {".env", "config.local"})
        assert (tmp_path / ".env").exists()
        assert (tmp_path / ".env.local").exists()
        assert (tmp_path / "config.local").exists()
        assert not (tmp_path / "src").exists()


# ─── format_uninstall_plan ───


class TestFormatUninstallPlan:
    def test_with_error(self):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp",
                             error="not found")
        output = format_uninstall_plan(plan)
        assert "not found" in output

    def test_empty(self):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp")
        output = format_uninstall_plan(plan)
        assert "无需清理" in output

    def test_full_plan(self):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp/proj",
                             total_size=5000000)
        plan.items = [
            CleanupItem(path="/tmp/proj", item_type="directory",
                        size_bytes=4000000, description="主目录"),
            CleanupItem(path="/tmp/proj/venv", item_type="venv",
                        size_bytes=1000000, description="Python venv"),
            CleanupItem(path="docker-compose:proj", item_type="docker",
                        description="Docker"),
        ]
        plan.warnings = ["有 .git"]
        output = format_uninstall_plan(plan)
        assert "主目录" in output
        assert "Python venv" in output
        assert "Docker" in output
        assert "警告" in output


# ─── uninstall_to_dict ───


class TestUninstallToDict:
    def test_serialization(self):
        plan = UninstallPlan(owner="o", repo="r", install_dir="/tmp/proj",
                             total_size=1000)
        plan.items = [
            CleanupItem(path="/tmp/proj", item_type="directory",
                        size_bytes=1000, description="dir"),
        ]
        plan.warnings = ["warn1"]
        d = uninstall_to_dict(plan)
        assert d["owner"] == "o"
        assert len(d["items"]) == 1
        assert d["warnings"] == ["warn1"]


# ─── _is_safe_path / _dir_size ───


class TestHelpers:
    def test_safe_path_home_subdir(self):
        p = Path.home() / "some" / "dir"
        assert _is_safe_path(p) is True

    def test_unsafe_root(self):
        assert _is_safe_path(Path("/")) is False

    def test_unsafe_system(self):
        assert _is_safe_path(Path("/usr")) is False

    def test_unsafe_home(self):
        assert _is_safe_path(Path.home()) is False

    def test_dir_size(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello" * 100)
        (tmp_path / "b.txt").write_text("world" * 200)
        size = _dir_size(tmp_path)
        assert size > 0
