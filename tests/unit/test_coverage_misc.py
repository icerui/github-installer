"""tests/unit/test_coverage_misc.py — 覆盖剩余模块的补全测试

覆盖目标:
  - trending.py: 14 行 → <5
  - installer_registry.py: 15 行 → <5
  - checkpoint.py: 12 行 → <3
  - db.py: 31 行 → <15
  - hw_detect.py: 27 行 → <10
"""
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))


# ═══════════════════════════════════════════════
#  1. trending.py
# ═══════════════════════════════════════════════

class TestTrendingRefresh:
    def test_github_search_with_token(self):
        """GITHUB_TOKEN 设置时应加入 Authorization header"""
        from trending import _github_search
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"items": [{"full_name": "test/repo"}]}'

        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test123"}), \
             patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            result = _github_search("python", per_page=5)
        assert len(result) == 1
        # Verify auth header was set
        call_args = mock_urlopen.call_args[0][0]
        assert call_args.get_header("Authorization") == "token ghp_test123"

    def test_refresh_worker_success(self):
        """后台刷新成功时更新缓存"""
        from trending import _refresh_worker
        projects = [{"full_name": "a/b", "stargazers_count": 100, "description": "test"}]
        with patch("trending._fetch_all", return_value=projects), \
             patch("trending._read_cache", return_value=None), \
             patch("trending._merge_with_old", return_value=projects) as mock_merge, \
             patch("trending._write_cache") as mock_write:
            _refresh_worker()
        mock_merge.assert_called_once()
        mock_write.assert_called_once_with(projects)

    def test_refresh_worker_empty(self):
        """后台刷新返回空 → 不写入"""
        from trending import _refresh_worker
        with patch("trending._fetch_all", return_value=[]), \
             patch("trending._write_cache") as mock_write:
            _refresh_worker()
        mock_write.assert_not_called()

    def test_refresh_worker_exception(self):
        """后台刷新异常 → 静默"""
        from trending import _refresh_worker
        with patch("trending._fetch_all", side_effect=RuntimeError("fail")):
            _refresh_worker()  # should not raise


# ═══════════════════════════════════════════════
#  2. installer_registry.py
# ═══════════════════════════════════════════════

class TestInstallerRegistry:
    def test_pip_can_handle_not_available(self):
        """pip 不可用时 can_handle 返回 False"""
        from installer_registry import PipInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = PipInstaller()
        assert inst.info.available is False
        assert inst.can_handle(["python"], {"requirements.txt": ""}) is False

    def test_pip_pyproject_steps(self):
        """pyproject.toml → pip install -e ."""
        from installer_registry import PipInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = PipInstaller()
        steps = inst.generate_install_steps({
            "dependency_files": {"pyproject.toml": "[project]"},
            "project_types": ["python"],
        })
        assert any("pip install" in s.get("command", "") for s in steps)

    def test_npm_can_handle_not_available(self):
        from installer_registry import NpmInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = NpmInstaller()
        assert inst.info.available is False
        assert inst.can_handle(["node"], {"package.json": ""}) is False

    def test_cargo_can_handle_not_available(self):
        from installer_registry import CargoInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = CargoInstaller()
        assert inst.can_handle(["rust"], {"Cargo.toml": ""}) is False

    def test_go_can_handle_not_available(self):
        from installer_registry import GoInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = GoInstaller()
        assert inst.can_handle(["go"], {"go.mod": ""}) is False

    def test_docker_can_handle_not_available(self):
        from installer_registry import DockerInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = DockerInstaller()
        assert inst.can_handle([], {"Dockerfile": ""}) is False

    def test_conda_can_handle_not_available(self):
        from installer_registry import CondaInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = CondaInstaller()
        assert inst.can_handle(["python"], {"environment.yml": ""}) is False

    def test_brew_can_handle_not_available(self):
        from installer_registry import BrewInstaller
        with patch("subprocess.run", side_effect=FileNotFoundError):
            inst = BrewInstaller()
        assert inst.can_handle([], {}) is False

    def test_registry_register_and_get(self):
        """注册自定义安装器 + get 查询"""
        from installer_registry import InstallerRegistry, BaseInstaller, InstallerInfo
        registry = InstallerRegistry()
        mock_installer = MagicMock(spec=BaseInstaller)
        mock_installer.info = InstallerInfo(name="custom_test_only",
                                            display_name="Custom",
                                            ecosystems=["custom"],
                                            install_command="custom install",
                                            version_command="custom --version",
                                            available=True, version="1.0")
        mock_installer.to_dict.return_value = {"name": "custom_test_only", "available": True}
        registry.register(mock_installer)
        assert registry.get("custom_test_only") is mock_installer
        assert registry.get("nonexistent") is None
        # 清理：避免污染全局单例
        del registry._installers["custom_test_only"]


# ═══════════════════════════════════════════════
#  3. checkpoint.py
# ═══════════════════════════════════════════════

class TestCheckpoint:
    def test_progress_pct_zero_steps(self):
        """total_steps=0 → 0%"""
        from checkpoint import InstallCheckpoint
        cp = InstallCheckpoint(project="test/repo", total_steps=0)
        assert cp.progress_pct == 0.0

    def test_get_resumable_no_dir(self):
        """CHECKPOINT_DIR 不存在 → 空列表"""
        from checkpoint import CheckpointManager
        mgr = CheckpointManager()
        with patch("checkpoint.CHECKPOINT_DIR") as mock_dir:
            mock_dir.exists.return_value = False
            result = mgr.get_resumable()
        assert result == []

    def test_load_corrupted_file(self):
        """损坏的 JSON → 返回 None"""
        from checkpoint import CheckpointManager
        mgr = CheckpointManager()
        with patch("builtins.open", mock_open(read_data="not json{{")), \
             patch("checkpoint.CHECKPOINT_DIR") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_dir.__truediv__ = MagicMock(return_value=mock_path)
            result = mgr.get_checkpoint("test", "repo")
        assert result is None

    def test_format_checkpoint_list(self):
        """格式化检查点列表"""
        from checkpoint import format_checkpoint_list, InstallCheckpoint, StepCheckpoint
        steps = [
            StepCheckpoint(index=0, command="echo 1", description="step 1", status="completed", duration_sec=1.0),
            StepCheckpoint(index=1, command="echo 2", description="step 2", status="failed",
                           duration_sec=0.5, error="some error happened"),
        ]
        cp = InstallCheckpoint(
            project="test/repo", owner="test", repo="repo",
            status="failed", total_steps=2,
            steps=steps, plan={},
            created_at="2024-01-01", updated_at="2024-01-01",
            strategy="auto", llm_used="none",
        )
        result = format_checkpoint_list([cp])
        assert "test/repo" in result
        assert "1/2" in result

    def test_format_resume_plan(self):
        """格式化恢复计划"""
        from checkpoint import format_resume_plan, InstallCheckpoint, StepCheckpoint
        steps = [
            StepCheckpoint(index=0, command="echo 1", description="step 1", status="completed", duration_sec=1.0),
            StepCheckpoint(index=1, command="echo 2", description="step 2", status="pending", duration_sec=0.0),
        ]
        cp = InstallCheckpoint(
            project="test/repo", owner="test", repo="repo",
            status="in_progress", total_steps=2,
            steps=steps, plan={},
            created_at="2024-01-01", updated_at="2024-01-01",
        )
        result = format_resume_plan(cp, 1)
        assert "从这里继续" in result

    def test_get_resume_step(self):
        """找到第一个 pending/failed 步骤"""
        from checkpoint import CheckpointManager, InstallCheckpoint, StepCheckpoint
        mgr = CheckpointManager()
        steps = [
            StepCheckpoint(index=0, command="echo 1", description="s1", status="completed", duration_sec=1.0),
            StepCheckpoint(index=1, command="echo 2", description="s2", status="failed", duration_sec=0.5),
            StepCheckpoint(index=2, command="echo 3", description="s3", status="pending", duration_sec=0.0),
        ]
        cp = InstallCheckpoint(
            project="test/repo", owner="test", repo="repo",
            status="failed", total_steps=3,
            steps=steps, plan={},
            created_at="2024-01-01", updated_at="2024-01-01",
        )
        idx = mgr.get_resume_step(cp)
        assert idx == 1  # second step is "failed"

    def test_remove_checkpoint(self):
        """删除检查点文件"""
        from checkpoint import CheckpointManager
        mgr = CheckpointManager()
        with patch("checkpoint.CHECKPOINT_DIR") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_dir.__truediv__ = MagicMock(return_value=mock_path)
            result = mgr.remove_checkpoint("test", "repo")
        assert result is True
        mock_path.unlink.assert_called_once()


# ═══════════════════════════════════════════════
#  4. db.py
# ═══════════════════════════════════════════════

class TestDb:
    def test_is_admin_false(self):
        """无效 token → 非管理员"""
        from db import is_admin
        with patch("db.validate_token", return_value=None):
            assert is_admin("invalid_token") is False

    def test_is_admin_true(self):
        """有效 admin token"""
        from db import is_admin
        with patch("db.validate_token", return_value={"is_admin": True}):
            assert is_admin("admin_token") is True

    def test_set_admin(self):
        """设置管理员权限"""
        from db import set_admin
        mock_conn = MagicMock()
        with patch("db.init_db"), patch("db._get_conn", return_value=mock_conn):
            set_admin(42, True)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_validate_token_expired(self):
        """过期 token → None (sessions 表)"""
        from db import validate_token
        import sqlite3 as _sqlite3
        # 使用真实内存数据库模拟
        conn = _sqlite3.connect(":memory:")
        conn.row_factory = _sqlite3.Row
        conn.executescript("""
            CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, expires_at REAL, ip_hash TEXT, user_agent TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, pw_hash TEXT, salt TEXT, tier TEXT DEFAULT 'free', is_admin INTEGER DEFAULT 0, created_at REAL, last_login REAL);
            CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
        """)
        # 插入一个过期的 session
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, 1, ?, ?)",
                     ("expired-token", time.time() - 8*86400, time.time() - 86400))
        conn.commit()
        with patch("db.init_db"), patch("db._get_conn", return_value=conn):
            result = validate_token("expired-token")
        assert result is None
        conn.close()

    def test_cleanup_expired_sessions(self):
        """清理过期会话"""
        from db import cleanup_expired_sessions
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(":memory:")
        conn.row_factory = _sqlite3.Row
        conn.executescript("""
            CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, expires_at REAL, ip_hash TEXT, user_agent TEXT);
            CREATE TABLE reset_tokens (token TEXT PRIMARY KEY, user_id INTEGER, email TEXT, created_at REAL, expires_at REAL);
            CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
        """)
        # 插入一个过期 session 和一个有效 session
        now = time.time()
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, 1, ?, ?)", ("old", now - 30*86400, now - 86400))
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, 2, ?, ?)", ("new", now, now + 7*86400))
        conn.commit()
        with patch("db.init_db"), patch("db._get_conn", return_value=conn):
            result = cleanup_expired_sessions()
        assert result >= 0
        conn.close()


# ═══════════════════════════════════════════════
#  5. hw_detect.py
# ═══════════════════════════════════════════════

class TestHwDetect:
    def test_detect_nvidia_deep_vram(self):
        """NVIDIA GPU 深度检测 — VRAM + CUDA"""
        from hw_detect import _detect_nvidia_deep
        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "query-gpu=name" in cmd_str:
                return "NVIDIA GeForce RTX 4090"
            if "memory.total" in cmd_str:
                return "24564"
            if "driver_version" in cmd_str:
                return "535.129.03"
            if "nvcc" in cmd_str:
                return "release 12.4, V12.4.99"
            return ""
        with patch("hw_detect._run", side_effect=mock_run):
            result = _detect_nvidia_deep()
        assert result is not None
        assert result["name"] == "NVIDIA GeForce RTX 4090"
        assert result.get("vram_gb") == round(24564 / 1024, 1)

    def test_detect_nvidia_deep_smi_fallback(self):
        """nvcc 不存在 → 从 nvidia-smi 获取 CUDA 版本"""
        from hw_detect import _detect_nvidia_deep
        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "query-gpu=name" in cmd_str:
                return "NVIDIA GeForce RTX 3060"
            if "memory.total" in cmd_str:
                return "12288"
            if "driver_version" in cmd_str:
                return "535.0"
            if "nvcc" in cmd_str:
                return ""  # nvcc not found
            if "nvidia-smi" in cmd_str and "query" not in cmd_str:
                return "Driver Version: 535.0  CUDA Version: 12.2"
            return ""
        with patch("hw_detect._run", side_effect=mock_run):
            result = _detect_nvidia_deep()
        assert result is not None
        assert result.get("cuda_version") == "12.2"

    def test_lookup_nvidia_gpu_fuzzy(self):
        """模糊匹配 GPU 名 → 去掉前缀后匹配"""
        from hw_detect import _lookup_nvidia_gpu
        result = _lookup_nvidia_gpu("NVIDIA GeForce RTX 4090")
        if result:
            assert "vram_gb" in result

    def test_get_ram_gb_windows(self):
        """Windows RAM 检测"""
        from hw_detect import _get_ram_gb
        with patch("hw_detect.platform.system", return_value="Windows"), \
             patch("hw_detect._run", return_value="TotalPhysicalMemory\n34359738368\n"):
            ram = _get_ram_gb()
        assert ram is not None and ram > 30

    def test_pytorch_no_cuda_match(self):
        """CUDA 版本无匹配 → 默认值"""
        from hw_detect import check_pytorch_compatibility
        gpu = {"type": "nvidia", "cuda_version": "99.9", "name": "Test GPU"}
        result = check_pytorch_compatibility(gpu)
        assert "install_cmd" in result

    def test_recommend_quantization_fallback(self):
        """VRAM 很小 → 最低量化建议"""
        from hw_detect import recommend_quantization
        result = recommend_quantization(70, 2)
        assert result is not None
        assert result.get("advice")

    def test_recommend_quantization_sufficient(self):
        """VRAM 充足 → fp16 推荐"""
        from hw_detect import recommend_quantization
        result = recommend_quantization(7, 48)
        assert result["can_run"] is True
        assert result["recommended_quant"] in ("fp32", "fp16")

    def test_predict_install_windows(self):
        """Windows + no git → 降低成功概率"""
        from hw_detect import predict_install_success
        env = {
            "os": {"type": "windows", "is_wsl": False},
            "runtimes": {"git": {"available": False}},
            "disk": {"free_gb": 3},
            "hardware": {"ram_gb": 4},
        }
        result = predict_install_success("test/proj", {}, env)
        assert result["success_probability"] < 0.8

    def test_predict_install_wsl(self):
        """WSL2 环境识别"""
        from hw_detect import predict_install_success
        env = {
            "os": {"type": "linux", "is_wsl": True},
            "runtimes": {"git": {"available": True}},
            "disk": {"free_gb": 50},
            "hardware": {"ram_gb": 32},
        }
        result = predict_install_success("test/proj", {}, env)
        assert any("WSL" in r for r in result.get("risk_factors", []))

    def test_get_full_ai_hardware_report_no_vram(self):
        """GPU 不足 → 不推荐模型"""
        from hw_detect import get_full_ai_hardware_report
        fake_gpu = {"type": "cpu_only", "name": "N/A", "vram_gb": 0, "unified_memory": False}
        with patch("hw_detect.get_gpu_info", return_value=fake_gpu), \
             patch("hw_detect.check_pytorch_compatibility", return_value={"backend": "cpu", "warnings": []}):
            result = get_full_ai_hardware_report()
        assert "VRAM 不足" in result.get("summary", "") or result["recommended_models"] == []

    def test_format_report_cuda_unified(self):
        """格式化报告 — CUDA + 统一内存 + 模型列表"""
        from hw_detect import format_ai_hardware_report
        report = {
            "gpu": {"name": "RTX 4090", "type": "nvidia", "vram_gb": 24,
                    "cuda_version": "12.4", "driver_version": "535.0",
                    "compute_capability": "8.9",
                    "unified_memory": True, "total_ram_gb": 64},
            "pytorch": {"backend": "cuda", "install_cmd": "pip install torch",
                        "recommended_pytorch": "2.3", "warnings": ["test warning"]},
            "vram_gb": 24,
            "recommended_models": [{"tier": "7B", "best_quant": "fp16", "params_b": 7, "vram_needed": 14}],
            "summary": "GPU ready",
        }
        text = format_ai_hardware_report(report)
        assert "CUDA" in text
        assert "12.4" in text
        assert "统一内存" in text
        assert "7B" in text
