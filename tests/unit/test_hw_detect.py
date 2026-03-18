"""
test_hw_detect.py - AI 硬件智能检测模块测试
=============================================
"""

from __future__ import annotations

import sys
import platform
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import hw_detect


# ─────────────────────────────────────────────
#  GPU 数据库检索
# ─────────────────────────────────────────────

class TestNvidiaLookup:
    def test_lookup_exact(self):
        r = hw_detect._lookup_nvidia_gpu("NVIDIA GeForce RTX 4090")
        assert r is not None
        assert r["vram_gb"] == 24

    def test_lookup_rtx_3060(self):
        r = hw_detect._lookup_nvidia_gpu("NVIDIA GeForce RTX 3060")
        assert r is not None
        assert r["vram_gb"] == 12  # RTX 3060 有 12GB

    def test_lookup_a100(self):
        r = hw_detect._lookup_nvidia_gpu("NVIDIA A100-SXM4-80GB")
        assert r is not None
        assert r["vram_gb"] == 80

    def test_lookup_unknown(self):
        assert hw_detect._lookup_nvidia_gpu("Some Unknown GPU") is None

    def test_laptop_gpu(self):
        r = hw_detect._lookup_nvidia_gpu("NVIDIA GeForce RTX 4090 Laptop GPU")
        assert r is not None
        assert r["vram_gb"] == 16  # 笔记本版

    def test_t4(self):
        r = hw_detect._lookup_nvidia_gpu("Tesla T4")
        assert r is not None
        assert r["vram_gb"] == 16


# ─────────────────────────────────────────────
#  模型参数量查找
# ─────────────────────────────────────────────

class TestModelParams:
    def test_exact_match(self):
        assert hw_detect._lookup_model_params("llama-3.1-8b") == 8

    def test_case_insensitive(self):
        assert hw_detect._lookup_model_params("Qwen3-32B") == 32

    def test_fuzzy_match(self):
        # "qwen2.5-72b" should match
        assert hw_detect._lookup_model_params("qwen2.5-72b-instruct") == 72

    def test_extract_from_name(self):
        # 从名称中提取参数量
        assert hw_detect._lookup_model_params("my-custom-model-13b") == 13

    def test_unknown_model(self):
        assert hw_detect._lookup_model_params("totally-unknown") is None

    def test_sd_models(self):
        assert hw_detect._lookup_model_params("sdxl") == 6.5
        assert hw_detect._lookup_model_params("flux") == 12.0


# ─────────────────────────────────────────────
#  版本比较
# ─────────────────────────────────────────────

class TestVersionCompare:
    def test_lt(self):
        assert hw_detect._ver_lt("515.43", "520.61") is True

    def test_not_lt(self):
        assert hw_detect._ver_lt("530.30", "520.61") is False

    def test_equal(self):
        assert hw_detect._ver_lt("520.61", "520.61") is False


# ─────────────────────────────────────────────
#  VRAM 推荐
# ─────────────────────────────────────────────

class TestQuantizationRecommendation:
    def test_large_vram_fp16(self):
        result = hw_detect.recommend_quantization(7.0, 24.0)
        assert result["can_run"] is True
        assert result["recommended_quant"] == "fp16"  # 24GB 够跑 7B FP16

    def test_medium_vram_q4(self):
        result = hw_detect.recommend_quantization(70.0, 48.0)
        assert result["can_run"] is True
        # 70B FP16 需要 ~168GB，48GB 能跑 Q3-Q5 级别
        assert result["recommended_quant"] in ("q3_k", "q4_k", "q4_0", "q5_k", "q6_k")

    def test_insufficient_vram(self):
        result = hw_detect.recommend_quantization(405.0, 8.0)
        assert result["can_run"] is False
        assert "不足" in result["advice"]

    def test_small_model_small_vram(self):
        result = hw_detect.recommend_quantization(3.0, 8.0)
        assert result["can_run"] is True
        # 3B 应该能 FP16 或至少 Q8
        assert result["recommended_quant"] in ("fp16", "fp32")

    def test_all_options_present(self):
        result = hw_detect.recommend_quantization(7.0, 16.0)
        assert len(result["all_options"]) > 0
        for opt in result["all_options"]:
            assert "quant" in opt
            assert "vram_gb" in opt
            assert "fits" in opt

    def test_advice_sufficient_vram(self):
        result = hw_detect.recommend_quantization(7.0, 48.0)
        assert "FP16" in result["advice"] or "充足" in result["advice"]


# ─────────────────────────────────────────────
#  PyTorch 兼容性
# ─────────────────────────────────────────────

class TestPyTorchCompat:
    def test_apple_mps(self):
        gpu = {"type": "apple_mps", "vram_gb": 48}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert result["compatible"] is True
        assert result["backend"] == "mps"
        assert "pip3 install torch" in result["install_cmd"]

    def test_nvidia_with_cuda(self):
        gpu = {"type": "nvidia", "cuda_version": "12.4", "driver_version": "555.0", "compute_capability": "8.9"}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert result["compatible"] is True
        assert result["backend"] == "cuda"
        assert "cu12" in result["install_cmd"]

    def test_nvidia_no_cuda(self):
        gpu = {"type": "nvidia", "cuda_version": None, "driver_version": "555.0"}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert any("CUDA" in w for w in result["warnings"])

    def test_nvidia_old_compute(self):
        gpu = {"type": "nvidia", "cuda_version": "11.8", "driver_version": "520.61", "compute_capability": "3.0"}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert any("过低" in w for w in result["warnings"])

    def test_amd_rocm(self):
        gpu = {"type": "amd_rocm", "rocm_version": "6.2"}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert result["backend"] == "rocm"
        assert "rocm" in result["install_cmd"]

    def test_cpu_only(self):
        gpu = {"type": "cpu_only"}
        result = hw_detect.check_pytorch_compatibility(gpu)
        assert result["backend"] == "cpu"
        assert "cpu" in result["install_cmd"]
        assert any("CPU" in w or "cpu" in w.lower() for w in result["warnings"])


# ─────────────────────────────────────────────
#  模型推荐
# ─────────────────────────────────────────────

class TestModelRecommendation:
    def test_recommend_known_model(self):
        gpu = {"type": "apple_mps", "vram_gb": 48, "name": "Apple M3 Ultra"}
        result = hw_detect.recommend_for_model("llama-3.1-8b", gpu)
        assert result["params_b"] == 8
        assert result["recommendation"]["can_run"] is True
        assert result["ollama_tag"] is not None

    def test_recommend_unknown_model(self):
        gpu = {"type": "cpu_only", "vram_gb": 0, "name": "N/A"}
        result = hw_detect.recommend_for_model("totally-unknown-model", gpu)
        assert result["params_b"] is None
        assert result["recommendation"] is None

    def test_recommend_too_large(self):
        gpu = {"type": "nvidia", "vram_gb": 8, "name": "RTX 3060 Ti"}
        result = hw_detect.recommend_for_model("llama-3.1-405b", gpu)
        assert result["recommendation"]["can_run"] is False


# ─────────────────────────────────────────────
#  安装成功率预测
# ─────────────────────────────────────────────

class TestSuccessPrediction:
    def test_known_project_high(self):
        gpu = {"type": "apple_mps", "vram_gb": 48}
        env = {
            "os": {"type": "macos"}, "hardware": {"ram_gb": 512},
            "runtimes": {"git": {"available": True}},
            "disk": {"free_gb": 100},
        }
        result = hw_detect.predict_install_success("ollama/ollama", gpu, env, "known_project")
        assert result["success_probability"] >= 0.85
        assert result["confidence_level"] == "high"

    def test_readme_extract_low(self):
        gpu = {"type": "cpu_only"}
        env = {
            "os": {"type": "windows"},
            "hardware": {"ram_gb": 4},
            "runtimes": {"git": {"available": False}},
            "disk": {"free_gb": 3},
        }
        result = hw_detect.predict_install_success("unknown/project", gpu, env, "readme_extract")
        assert result["success_probability"] < 0.5
        assert len(result["risk_factors"]) > 0

    def test_no_git_risk(self):
        gpu = {"type": "cpu_only"}
        env = {
            "os": {"type": "linux"},
            "hardware": {"ram_gb": 16},
            "runtimes": {"git": {"available": False}},
            "disk": {"free_gb": 50},
        }
        result = hw_detect.predict_install_success("test/repo", gpu, env)
        assert any("Git" in r for r in result["risk_factors"])

    def test_probability_bounds(self):
        gpu = {"type": "cpu_only"}
        env = {
            "os": {"type": "windows"},
            "hardware": {"ram_gb": 2},
            "runtimes": {"git": {"available": False}},
            "disk": {"free_gb": 1},
        }
        result = hw_detect.predict_install_success("x/y", gpu, env, "readme_extract")
        assert 0.0 <= result["success_probability"] <= 1.0


# ─────────────────────────────────────────────
#  缓存
# ─────────────────────────────────────────────

class TestCache:
    def test_cache_works(self):
        hw_detect._cache.clear()
        with patch("hw_detect.detect_gpu_deep") as mock:
            mock.return_value = {"type": "cpu_only", "name": "Test", "vram_gb": 0}
            r1 = hw_detect.get_gpu_info()
            r2 = hw_detect.get_gpu_info()
            assert mock.call_count == 1  # 第二次用缓存
            assert r1 == r2

    def test_force_refresh(self):
        hw_detect._cache.clear()
        with patch("hw_detect.detect_gpu_deep") as mock:
            mock.return_value = {"type": "cpu_only", "name": "Test", "vram_gb": 0}
            hw_detect.get_gpu_info()
            hw_detect.get_gpu_info(force_refresh=True)
            assert mock.call_count == 2


# ─────────────────────────────────────────────
#  完整报告
# ─────────────────────────────────────────────

class TestFullReport:
    def test_report_structure(self):
        hw_detect._cache.clear()
        with patch("hw_detect.detect_gpu_deep") as mock:
            mock.return_value = {
                "type": "apple_mps", "name": "Apple M3 Ultra",
                "vram_gb": 144, "driver_version": None,
                "cuda_version": None, "compute_capability": None,
                "gpu_gen": "m3", "unified_memory": True,
                "total_ram_gb": 192, "mps_available": True,
            }
            report = hw_detect.get_full_ai_hardware_report()
            assert "gpu" in report
            assert "pytorch" in report
            assert "recommended_models" in report
            assert "summary" in report
            assert len(report["recommended_models"]) > 0

    def test_format_report(self):
        hw_detect._cache.clear()
        with patch("hw_detect.detect_gpu_deep") as mock:
            mock.return_value = {
                "type": "nvidia", "name": "RTX 4090",
                "vram_gb": 24, "driver_version": "555.0",
                "cuda_version": "12.4", "compute_capability": "8.9",
                "gpu_gen": "ada", "unified_memory": False,
                "total_ram_gb": 64, "mps_available": False,
            }
            report = hw_detect.get_full_ai_hardware_report()
            text = hw_detect.format_ai_hardware_report(report)
            assert "RTX 4090" in text
            assert "CUDA" in text
            assert "PyTorch" in text


# ─────────────────────────────────────────────
#  数据库完整性
# ─────────────────────────────────────────────

class TestDBIntegrity:
    def test_nvidia_db_vram_positive(self):
        for name, data in hw_detect._NVIDIA_VRAM_DB.items():
            assert data["vram_gb"] > 0, f"{name} has invalid VRAM"

    def test_apple_db_cores_positive(self):
        for name, data in hw_detect._APPLE_SILICON_DB.items():
            assert data["gpu_cores"] > 0, f"{name} has invalid GPU cores"

    def test_pytorch_matrix_sorted(self):
        versions = [entry["pytorch"] for entry in hw_detect._PYTORCH_CUDA_MATRIX]
        # 应该从新到旧排序
        assert versions == sorted(versions, reverse=True)

    def test_model_params_positive(self):
        for name, params in hw_detect._KNOWN_MODEL_PARAMS.items():
            assert params > 0, f"{name} has invalid params"


# ─────────────────────────────────────────────
#  detect_gpu_deep (mock 测试)
# ─────────────────────────────────────────────

class TestDetectGpuDeep:
    @patch("hw_detect.platform.system", return_value="Darwin")
    @patch("hw_detect.platform.machine", return_value="arm64")
    @patch("hw_detect._detect_apple_deep", return_value={"type": "apple_mps"})
    def test_apple_silicon(self, _d, _m, _s):
        result = hw_detect.detect_gpu_deep()
        assert result["type"] == "apple_mps"

    @patch("hw_detect.platform.system", return_value="Linux")
    @patch("hw_detect.platform.machine", return_value="x86_64")
    @patch("hw_detect.shutil.which", return_value="/usr/bin/nvidia-smi")
    @patch("hw_detect._detect_nvidia_deep", return_value={"type": "nvidia"})
    def test_nvidia(self, _d, _w, _m, _s):
        result = hw_detect.detect_gpu_deep()
        assert result["type"] == "nvidia"

    @patch("hw_detect.platform.system", return_value="Linux")
    @patch("hw_detect.platform.machine", return_value="x86_64")
    @patch("hw_detect.shutil.which", side_effect=lambda c: "/usr/bin/rocm-smi" if c == "rocm-smi" else None)
    @patch("hw_detect._detect_nvidia_deep", return_value=None)
    @patch("hw_detect._detect_amd_deep", return_value={"type": "amd_rocm"})
    def test_amd_rocm(self, _a, _n, _w, _m, _s):
        result = hw_detect.detect_gpu_deep()
        assert result["type"] == "amd_rocm"

    @patch("hw_detect.platform.system", return_value="Windows")
    @patch("hw_detect.platform.machine", return_value="AMD64")
    @patch("hw_detect.shutil.which", return_value=None)
    def test_cpu_only(self, _w, _m, _s):
        result = hw_detect.detect_gpu_deep()
        assert result["type"] == "cpu_only"


class TestDetectAppleDeep:
    @patch("hw_detect._run", return_value="Apple M3 Ultra")
    @patch("hw_detect._get_ram_gb", return_value=192.0)
    def test_apple_m3_ultra(self, _ram, _run):
        result = hw_detect._detect_apple_deep()
        assert result["type"] == "apple_mps"
        assert "M3 Ultra" in result["name"]
        assert result["unified_memory"] is True
        assert result["vram_gb"] == round(192.0 * 0.75, 1)
        assert result["mps_available"] is True

    @patch("hw_detect._run", return_value="Unknown chip")
    @patch("hw_detect._get_ram_gb", return_value=16.0)
    def test_unknown_apple_chip(self, _ram, _run):
        result = hw_detect._detect_apple_deep()
        assert result["name"] == "Apple Silicon"


class TestDetectNvidiaDeep:
    @patch("hw_detect._lookup_nvidia_gpu", return_value={"vram_gb": 24, "compute": "8.9", "gen": "Ada Lovelace"})
    @patch("hw_detect._get_ram_gb", return_value=64.0)
    def test_full_nvidia(self, _ram, _lookup):
        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if "query-gpu=name" in joined:
                return "RTX 4090"
            if "memory.total" in joined:
                return "24576"
            if "driver_version" in joined:
                return "535.54.03"
            if "nvcc" in joined:
                return "release 12.2"
            return None
        with patch("hw_detect._run", side_effect=fake_run):
            result = hw_detect._detect_nvidia_deep()
            assert result is not None
            assert result["type"] == "nvidia"
            assert result["vram_gb"] == 24.0
            assert result["cuda_version"] == "12.2"

    @patch("hw_detect._run", return_value=None)
    def test_nvidia_no_gpu(self, _run):
        result = hw_detect._detect_nvidia_deep()
        assert result is None


class TestDetectAmdDeep:
    @patch("hw_detect._get_ram_gb", return_value=64.0)
    def test_amd_full(self, _ram):
        def fake_run_any(cmd, **kw):
            joined = " ".join(cmd)
            if "showproductname" in joined:
                return "GPU[0]: card0: RX 7900 XTX"
            if "showmeminfo" in joined:
                return "Total Memory (B): 25769803776"
            return None
        with patch("hw_detect._run_any", side_effect=fake_run_any), \
             patch("hw_detect._run", return_value="6.0.0"):
            result = hw_detect._detect_amd_deep()
            assert result is not None
            assert result["type"] == "amd_rocm"
            assert result["rocm_version"] == "6.0.0"


class TestGetRamGb:
    @patch("hw_detect.platform.system", return_value="Darwin")
    @patch("hw_detect._run", return_value=str(16 * 1024**3))
    def test_macos(self, _run, _sys):
        result = hw_detect._get_ram_gb()
        assert result == 16.0

    @patch("hw_detect.platform.system", return_value="Linux")
    def test_linux(self, _sys):
        import builtins
        from unittest.mock import mock_open
        m = mock_open(read_data="MemTotal:       16384000 kB\n")
        with patch.object(builtins, 'open', m):
            result = hw_detect._get_ram_gb()
            assert result is not None

    @patch("hw_detect.platform.system", return_value="Windows")
    @patch("hw_detect._run", return_value="TotalPhysicalMemory\n17179869184\n")
    def test_windows(self, _run, _sys):
        result = hw_detect._get_ram_gb()
        assert result == 16.0


class TestRunHelpers:
    @pytest.mark.skipif(platform.system() == "Windows", reason="echo is a shell built-in on Windows")
    def test_run_echo(self):
        result = hw_detect._run(["echo", "hello"])
        assert result == "hello"

    @pytest.mark.skipif(platform.system() == "Windows", reason="false command does not exist on Windows")
    def test_run_fail(self):
        result = hw_detect._run(["false"])
        assert result is None

    def test_run_not_found(self):
        result = hw_detect._run(["nonexistent_cmd_xyz123"])
        assert result is None

    @pytest.mark.skipif(platform.system() == "Windows", reason="echo is a shell built-in on Windows")
    def test_run_any_success(self):
        result = hw_detect._run_any(["echo", "hello"])
        assert "hello" in result

    def test_run_any_not_found(self):
        result = hw_detect._run_any(["nonexistent_cmd_xyz123"])
        assert result is None


class TestPredictInstallSuccess:
    def _gpu(self, **kw):
        defaults = {"type": "nvidia", "vram_gb": 16, "cuda_version": "12.2",
                     "compute_capability": "8.9"}
        defaults.update(kw)
        return defaults

    def _env(self, **kw):
        defaults = {
            "os": {"type": "linux", "arch": "x86_64"},
            "runtimes": {"git": {"available": True}},
            "hardware": {"ram_gb": 64},
            "disk": {"free_gb": 100},
        }
        defaults.update(kw)
        return defaults

    def test_known_project_high_confidence(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="known_project",
            gpu_info=self._gpu(), env=self._env(),
        )
        assert result["confidence_level"] == "high"
        assert result["success_probability"] >= 0.8

    def test_no_git_warning(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="known_project",
            gpu_info=self._gpu(),
            env=self._env(runtimes={"git": {"available": False}}),
        )
        assert any("Git" in r for r in result["risk_factors"])

    def test_low_disk_warning(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="known_project",
            gpu_info=self._gpu(),
            env=self._env(disk={"free_gb": 3}),
        )
        assert any("磁盘" in r for r in result["risk_factors"])

    def test_windows_penalty(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="template",
            gpu_info=self._gpu(),
            env=self._env(os={"type": "windows", "arch": "x86_64"}),
        )
        assert any("Windows" in r for r in result["risk_factors"])

    def test_nvidia_no_cuda(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="template",
            gpu_info=self._gpu(cuda_version=None),
            env=self._env(),
        )
        assert any("CUDA" in r for r in result["risk_factors"])

    def test_amd_no_rocm(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="template",
            gpu_info={"type": "amd_rocm", "vram_gb": 16, "rocm_version": None},
            env=self._env(),
        )
        assert any("ROCm" in r for r in result["risk_factors"])

    def test_low_ram_warning(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="template",
            gpu_info=self._gpu(),
            env=self._env(hardware={"ram_gb": 4}),
        )
        assert any("内存" in r for r in result["risk_factors"])

    def test_old_compute_capability(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="template",
            gpu_info=self._gpu(compute_capability="5.0"),
            env=self._env(),
        )
        assert any("compute capability" in r for r in result["risk_factors"])

    def test_readme_extract_strategy_penalty(self):
        result = hw_detect.predict_install_success(
            project_key="test/proj", strategy="readme_extract",
            gpu_info=self._gpu(), env=self._env(),
        )
        r2 = hw_detect.predict_install_success(
            project_key="test/proj", strategy="known_project",
            gpu_info=self._gpu(), env=self._env(),
        )
        assert result["success_probability"] < r2["success_probability"]


class TestGetGpuInfoCache:
    def test_cache_hit(self):
        import time as t
        hw_detect._cache["gpu"] = (t.time(), {"type": "test_cached"})
        result = hw_detect.get_gpu_info()
        assert result["type"] == "test_cached"
        del hw_detect._cache["gpu"]

    @patch("hw_detect.detect_gpu_deep", return_value={"type": "fresh"})
    def test_force_refresh(self, _d):
        result = hw_detect.get_gpu_info(force_refresh=True)
        assert result["type"] == "fresh"
        if "gpu" in hw_detect._cache:
            del hw_detect._cache["gpu"]


class TestLookupNvidiaGpu:
    def test_exact_match(self):
        result = hw_detect._lookup_nvidia_gpu("NVIDIA GeForce RTX 4090")
        assert result is not None
        assert result["vram_gb"] == 24

    def test_partial_match(self):
        result = hw_detect._lookup_nvidia_gpu("RTX 3080")
        assert result is not None

    def test_no_match(self):
        result = hw_detect._lookup_nvidia_gpu("Unknown GPU XYZ")
        assert result is None
