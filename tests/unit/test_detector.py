"""
test_detector.py - 全平台环境检测器测试
=========================================
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from detector import EnvironmentDetector, format_env_summary, _run, _which, _version


# ─────────────────────────────────────────────
#  底层工具函数
# ─────────────────────────────────────────────

class TestRunFunction:
    @pytest.mark.skipif(platform.system() == "Windows", reason="echo is a shell built-in on Windows")
    def test_run_success(self):
        result = _run(["echo", "hello"])
        assert result == "hello"

    @pytest.mark.skipif(platform.system() == "Windows", reason="false command not available on Windows")
    def test_run_failure(self):
        result = _run(["false"])
        assert result is None

    def test_run_nonexistent(self):
        result = _run(["nonexistent_binary_12345"])
        assert result is None

    @pytest.mark.skipif(platform.system() == "Windows", reason="sleep command not available on Windows")
    def test_run_timeout(self):
        result = _run(["sleep", "10"], timeout=1)
        assert result is None


class TestWhichFunction:
    def test_which_python(self):
        assert _which("python3") is not None or _which("python") is not None

    def test_which_nonexistent(self):
        assert _which("nonexistent_binary_12345") is None


class TestVersionFunction:
    def test_version_python(self):
        ver = _version("python3")
        if ver:
            assert "." in ver  # 应该是 X.Y.Z 格式

    def test_version_nonexistent(self):
        assert _version("nonexistent_binary_12345") is None


# ─────────────────────────────────────────────
#  环境检测主类
# ─────────────────────────────────────────────

class TestEnvironmentDetector:
    def test_detect_returns_dict(self):
        det = EnvironmentDetector()
        env = det.detect()
        assert isinstance(env, dict)
        assert "os" in env
        assert "hardware" in env
        assert "gpu" in env
        assert "package_managers" in env
        assert "runtimes" in env
        assert "disk" in env
        assert "llm_configured" in env
        assert "network" in env

    def test_os_type_valid(self):
        det = EnvironmentDetector()
        env = det.detect()
        assert env["os"]["type"] in ("macos", "linux", "windows", "unknown")


# ─────────────────────────────────────────────
#  OS 检测（mock 测试各平台）
# ─────────────────────────────────────────────

class TestOSDetection:
    def test_detect_macos(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Darwin"), \
             patch("detector.platform.machine", return_value="arm64"), \
             patch("detector.platform.mac_ver", return_value=("14.5", ("", "", ""), "")), \
             patch("detector._run", return_value="Apple M3 Ultra"):
            result = det._detect_os()
            assert result["type"] == "macos"
            assert result["is_apple_silicon"] is True
            assert "M3" in result["chip"]

    def test_detect_macos_intel(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Darwin"), \
             patch("detector.platform.machine", return_value="x86_64"), \
             patch("detector.platform.mac_ver", return_value=("13.0", ("", "", ""), "")), \
             patch("detector._run", return_value="Intel Core i9"):
            result = det._detect_os()
            assert result["type"] == "macos"
            assert result["is_apple_silicon"] is False

    def test_detect_linux(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Linux"), \
             patch("detector.platform.machine", return_value="x86_64"), \
             patch("builtins.open", MagicMock(side_effect=FileNotFoundError)):
            result = det._detect_os()
            assert result["type"] == "linux"
            assert result["arch"] == "x86_64"

    def test_detect_windows(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Windows"), \
             patch("detector.platform.machine", return_value="AMD64"), \
             patch("detector.platform.version", return_value="10.0.22631"), \
             patch("detector.platform.release", return_value="11"):
            result = det._detect_os()
            assert result["type"] == "windows"
            assert result["arch"] == "AMD64"

    def test_detect_unknown_os(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="FreeBSD"):
            result = det._detect_os()
            assert result["type"] == "unknown"


# ─────────────────────────────────────────────
#  硬件检测
# ─────────────────────────────────────────────

class TestHardwareDetection:
    def test_hardware_has_cpu_count(self):
        det = EnvironmentDetector()
        result = det._detect_hardware()
        assert "cpu_count" in result
        assert result["cpu_count"] is None or result["cpu_count"] > 0

    def test_ram_detection_macos(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Darwin"), \
             patch("detector._run", return_value=str(16 * 1024**3)):
            ram = det._detect_ram_gb()
            assert ram == 16.0

    def test_ram_detection_failure(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Unknown"):
            ram = det._detect_ram_gb()
            assert ram is None


# ─────────────────────────────────────────────
#  GPU 检测
# ─────────────────────────────────────────────

class TestGPUDetection:
    def test_apple_mps(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Darwin"), \
             patch("detector.platform.machine", return_value="arm64"):
            result = det._detect_gpu()
            assert result["type"] == "apple_mps"
            assert result["cuda_available"] is False

    def test_cpu_only(self):
        det = EnvironmentDetector()
        with patch("detector.platform.system", return_value="Linux"), \
             patch("detector.platform.machine", return_value="x86_64"), \
             patch("detector._which", return_value=None):
            result = det._detect_gpu()
            assert result["type"] == "cpu_only"

    def test_nvidia_detection(self):
        det = EnvironmentDetector()
        def _mock_run(cmd, timeout=5):
            joined = " ".join(cmd)
            if "query-gpu" in joined:
                return "RTX 4090"
            if cmd[0] == "nvcc":
                return "release 12.4, V12.4.131"
            return None
        with patch("detector._which", side_effect=lambda b: "/usr/bin/nvidia-smi" if b == "nvidia-smi" else None), \
             patch("detector._run", side_effect=_mock_run):
            result = det._detect_nvidia()
            assert result is not None
            assert result["type"] == "nvidia_cuda"
            assert "4090" in result["name"]
            assert result["cuda_version"] == "12.4"

    def test_rocm_not_present(self):
        det = EnvironmentDetector()
        with patch("detector._which", return_value=None), \
             patch("detector.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = det._detect_rocm()
            assert result is None


# ─────────────────────────────────────────────
#  包管理器检测
# ─────────────────────────────────────────────

class TestPackageManagers:
    def test_returns_dict(self):
        det = EnvironmentDetector()
        result = det._detect_package_managers()
        assert isinstance(result, dict)

    def test_pip_detected(self):
        det = EnvironmentDetector()
        result = det._detect_package_managers()
        # pip 应该存在（我们在 Python 环境中）
        assert "pip" in result or "pip3" in result


# ─────────────────────────────────────────────
#  运行时检测
# ─────────────────────────────────────────────

class TestRuntimes:
    def test_python_always_present(self):
        det = EnvironmentDetector()
        result = det._detect_runtimes()
        assert "python" in result
        assert result["python"]["available"] is True

    def test_git_detected(self):
        det = EnvironmentDetector()
        result = det._detect_runtimes()
        assert "git" in result


# ─────────────────────────────────────────────
#  磁盘检测
# ─────────────────────────────────────────────

class TestDisk:
    def test_disk_detection(self):
        det = EnvironmentDetector()
        result = det._detect_disk()
        assert "free_gb" in result
        assert "total_gb" in result
        assert result["free_gb"] > 0
        assert result["total_gb"] > 0


# ─────────────────────────────────────────────
#  LLM 环境变量检测
# ─────────────────────────────────────────────

class TestLLMEnv:
    def test_detects_keys(self):
        det = EnvironmentDetector()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test123"}):
            result = det._detect_llm_env()
            assert result["anthropic"] is True

    def test_no_keys(self):
        det = EnvironmentDetector()
        with patch.dict(os.environ, {}, clear=True):
            result = det._detect_llm_env()
            for v in result.values():
                assert v is False

    def test_empty_key_treated_as_missing(self):
        det = EnvironmentDetector()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "  "}):
            result = det._detect_llm_env()
            assert result["openai"] is False


# ─────────────────────────────────────────────
#  网络检测
# ─────────────────────────────────────────────

class TestNetwork:
    def test_network_returns_dict(self):
        det = EnvironmentDetector()
        result = det._detect_network()
        assert "github" in result
        assert "pypi" in result
        assert isinstance(result["github"], bool)


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

class TestFormatSummary:
    def test_format_macos(self):
        env = {
            "os": {"type": "macos", "chip": "Apple M3 Ultra", "version": "14.5", "arch": "arm64"},
            "hardware": {"cpu_count": 24, "ram_gb": 192.0},
            "gpu": {"type": "apple_mps", "name": "Apple MPS"},
            "disk": {"free_gb": 500.0, "total_gb": 1000.0},
            "runtimes": {"python": {"version": "3.13"}, "git": {"available": True}},
            "package_managers": {"brew": {"available": True}},
        }
        text = format_env_summary(env)
        assert "M3 Ultra" in text
        assert "MPS" in text

    def test_format_linux(self):
        env = {
            "os": {"type": "linux", "distro_name": "Ubuntu 22.04", "arch": "x86_64", "is_wsl": False},
            "hardware": {"cpu_count": 16, "ram_gb": 64.0},
            "gpu": {"type": "nvidia_cuda", "name": "RTX 4090", "cuda_version": "12.4"},
            "disk": {"free_gb": 200.0, "total_gb": 500.0},
            "runtimes": {"python": {"version": "3.11"}, "git": {"available": True}, "docker": {"available": True, "daemon_running": True}},
            "package_managers": {"apt": {"available": True}},
        }
        text = format_env_summary(env)
        assert "Ubuntu" in text
        assert "RTX 4090" in text
        assert "CUDA 12.4" in text

    def test_format_windows(self):
        env = {
            "os": {"type": "windows", "release": "11", "arch": "AMD64"},
            "hardware": {"cpu_count": 8, "ram_gb": 32.0},
            "gpu": {"type": "cpu_only"},
            "disk": {"free_gb": 100.0, "total_gb": 512.0},
            "runtimes": {"python": {"version": "3.12"}},
            "package_managers": {},
        }
        text = format_env_summary(env)
        assert "Windows" in text

    def test_format_no_gpu(self):
        env = {
            "os": {"type": "linux", "distro_name": "Arch", "arch": "x86_64", "is_wsl": False},
            "hardware": {"cpu_count": 4, "ram_gb": 8.0},
            "gpu": {"type": "cpu_only"},
            "disk": {"free_gb": 50.0},
            "runtimes": {},
            "package_managers": {},
        }
        text = format_env_summary(env)
        assert "CPU" in text or "无独立显卡" in text

    def test_format_wsl(self):
        env = {
            "os": {"type": "linux", "distro_name": "Ubuntu 22.04", "arch": "x86_64", "is_wsl": True},
            "hardware": {"cpu_count": 8, "ram_gb": 16.0},
            "gpu": {"type": "cpu_only"},
            "disk": {"free_gb": 50.0},
            "runtimes": {},
            "package_managers": {},
        }
        text = format_env_summary(env)
        assert "WSL" in text
