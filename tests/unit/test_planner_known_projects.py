"""
test_planner_known_projects.py - 已知项目硬件需求测试
=====================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from planner_known_projects import (
    _AI_CATEGORIES,
    _AI_HARDWARE_REQS,
    check_hardware_compatibility,
    get_hardware_req,
)


class TestGetHardwareReq:
    def test_known_project(self):
        req = get_hardware_req("ollama/ollama")
        assert req is not None
        assert req["category"] == "llm_inference"

    def test_unknown_project(self):
        assert get_hardware_req("nonexistent/project") is None

    def test_case_insensitive(self):
        req = get_hardware_req("Ollama/Ollama")
        assert req is not None


class TestCheckHardwareCompatibility:
    def test_unknown_project_always_compatible(self):
        result = check_hardware_compatibility("unknown/proj", {}, {})
        assert result["compatible"] is True
        assert result["category"] == "unknown"

    def test_gpu_required_but_missing(self):
        result = check_hardware_compatibility(
            "comfyanonymous/comfyui",
            {"type": "cpu_only", "vram_gb": 0},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 50}},
        )
        assert result["compatible"] is False
        assert any("GPU" in w for w in result["warnings"])

    def test_sufficient_hardware(self):
        result = check_hardware_compatibility(
            "ollama/ollama",
            {"type": "nvidia", "vram_gb": 16},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 50}},
        )
        assert result["compatible"] is True
        assert len(result["warnings"]) == 0

    def test_low_vram_warning(self):
        result = check_hardware_compatibility(
            "comfyanonymous/comfyui",
            {"type": "nvidia", "vram_gb": 6},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 50}},
        )
        # 6GB is between min (4) and rec (8), so should warn
        assert any("显存" in w for w in result["warnings"])

    def test_insufficient_vram(self):
        result = check_hardware_compatibility(
            "comfyanonymous/comfyui",
            {"type": "nvidia", "vram_gb": 2},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 50}},
        )
        assert result["compatible"] is False

    def test_low_ram_warning(self):
        result = check_hardware_compatibility(
            "ollama/ollama",
            {"type": "apple_mps", "vram_gb": 16},
            {"hardware": {"ram_gb": 4}, "disk": {"free_gb": 50}},
        )
        assert any("内存" in w for w in result["warnings"])

    def test_low_disk_warning(self):
        result = check_hardware_compatibility(
            "ollama/ollama",
            {"type": "nvidia", "vram_gb": 16},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 1}},
        )
        assert any("磁盘" in w for w in result["warnings"])

    def test_incompatible_backend_warning(self):
        # llama-factory only supports cuda, MPS user gets warning
        result = check_hardware_compatibility(
            "hiyouga/llama-factory",
            {"type": "apple_mps", "vram_gb": 24},
            {"hardware": {"ram_gb": 64}, "disk": {"free_gb": 50}},
        )
        assert any("后端" in w for w in result["warnings"])

    def test_category_translated(self):
        result = check_hardware_compatibility(
            "ollama/ollama",
            {"type": "nvidia", "vram_gb": 16},
            {"hardware": {"ram_gb": 32}, "disk": {"free_gb": 50}},
        )
        assert result["category"] == "LLM 推理引擎"


class TestStaticData:
    def test_hardware_reqs_not_empty(self):
        assert len(_AI_HARDWARE_REQS) >= 20

    def test_all_categories_mapped(self):
        for key, req in _AI_HARDWARE_REQS.items():
            assert req["category"] in _AI_CATEGORIES, f"{key} has unmapped category"

    def test_required_fields(self):
        for key, req in _AI_HARDWARE_REQS.items():
            for field in ("min_vram_gb", "rec_vram_gb", "gpu_required", "gpu_backends", "disk_gb", "ram_gb"):
                assert field in req, f"{key} missing {field}"
