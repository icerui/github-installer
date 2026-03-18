"""
hw_detect.py - AI 硬件智能检测与推荐引擎
=========================================

功能：
  1. 深度 GPU 分析：型号、VRAM、驱动版本、计算能力
  2. AI 框架兼容矩阵：PyTorch/TF 版本 ↔ CUDA ↔ GPU 驱动
  3. VRAM 智能推荐：根据显存推荐量化方案（Q4/Q8/FP16）
  4. 模型适配器：给定模型参数量 → 推荐最佳运行方式
  5. 安装成功率预测：基于硬件 + 项目特征 → 预估成功概率

设计原则：
  - 零外部依赖（纯标准库）
  - 跨平台（macOS/Linux/Windows）
  - 检测结果可缓存（120秒TTL）
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
#  GPU VRAM 数据库（核心领域知识）
# ─────────────────────────────────────────────

# NVIDIA GPU → VRAM (GB) + compute capability
# 来源：NVIDIA 官方规格 + 实测验证
_NVIDIA_VRAM_DB: dict[str, dict] = {
    # RTX 50 系列 (Blackwell)
    "RTX 5090":     {"vram_gb": 32, "compute": "10.0", "gen": "blackwell"},
    "RTX 5080":     {"vram_gb": 16, "compute": "10.0", "gen": "blackwell"},
    "RTX 5070 Ti":  {"vram_gb": 16, "compute": "10.0", "gen": "blackwell"},
    "RTX 5070":     {"vram_gb": 12, "compute": "10.0", "gen": "blackwell"},
    "RTX 5060 Ti":  {"vram_gb": 16, "compute": "10.0", "gen": "blackwell"},
    "RTX 5060":     {"vram_gb": 8,  "compute": "10.0", "gen": "blackwell"},
    # RTX 40 系列 (Ada Lovelace)
    "RTX 4090":     {"vram_gb": 24, "compute": "8.9", "gen": "ada"},
    "RTX 4080 SUPER": {"vram_gb": 16, "compute": "8.9", "gen": "ada"},
    "RTX 4080":     {"vram_gb": 16, "compute": "8.9", "gen": "ada"},
    "RTX 4070 Ti SUPER": {"vram_gb": 16, "compute": "8.9", "gen": "ada"},
    "RTX 4070 Ti":  {"vram_gb": 12, "compute": "8.9", "gen": "ada"},
    "RTX 4070 SUPER": {"vram_gb": 12, "compute": "8.9", "gen": "ada"},
    "RTX 4070":     {"vram_gb": 12, "compute": "8.9", "gen": "ada"},
    "RTX 4060 Ti":  {"vram_gb": 8,  "compute": "8.9", "gen": "ada"},
    "RTX 4060":     {"vram_gb": 8,  "compute": "8.9", "gen": "ada"},
    # RTX 30 系列 (Ampere)
    "RTX 3090 Ti":  {"vram_gb": 24, "compute": "8.6", "gen": "ampere"},
    "RTX 3090":     {"vram_gb": 24, "compute": "8.6", "gen": "ampere"},
    "RTX 3080 Ti":  {"vram_gb": 12, "compute": "8.6", "gen": "ampere"},
    "RTX 3080":     {"vram_gb": 10, "compute": "8.6", "gen": "ampere"},
    "RTX 3070 Ti":  {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},
    "RTX 3070":     {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},
    "RTX 3060 Ti":  {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},
    "RTX 3060":     {"vram_gb": 12, "compute": "8.6", "gen": "ampere"},
    "RTX 3050":     {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},
    # RTX 20 系列 (Turing)
    "RTX 2080 Ti":  {"vram_gb": 11, "compute": "7.5", "gen": "turing"},
    "RTX 2080 SUPER": {"vram_gb": 8, "compute": "7.5", "gen": "turing"},
    "RTX 2080":     {"vram_gb": 8,  "compute": "7.5", "gen": "turing"},
    "RTX 2070 SUPER": {"vram_gb": 8, "compute": "7.5", "gen": "turing"},
    "RTX 2070":     {"vram_gb": 8,  "compute": "7.5", "gen": "turing"},
    "RTX 2060 SUPER": {"vram_gb": 8, "compute": "7.5", "gen": "turing"},
    "RTX 2060":     {"vram_gb": 6,  "compute": "7.5", "gen": "turing"},
    # GTX 16 系列 (Turing, no RT)
    "GTX 1660 Ti":  {"vram_gb": 6,  "compute": "7.5", "gen": "turing"},
    "GTX 1660 SUPER": {"vram_gb": 6, "compute": "7.5", "gen": "turing"},
    "GTX 1660":     {"vram_gb": 6,  "compute": "7.5", "gen": "turing"},
    "GTX 1650 SUPER": {"vram_gb": 4, "compute": "7.5", "gen": "turing"},
    "GTX 1650":     {"vram_gb": 4,  "compute": "7.5", "gen": "turing"},
    # GTX 10 系列 (Pascal)
    "GTX 1080 Ti":  {"vram_gb": 11, "compute": "6.1", "gen": "pascal"},
    "GTX 1080":     {"vram_gb": 8,  "compute": "6.1", "gen": "pascal"},
    "GTX 1070 Ti":  {"vram_gb": 8,  "compute": "6.1", "gen": "pascal"},
    "GTX 1070":     {"vram_gb": 8,  "compute": "6.1", "gen": "pascal"},
    "GTX 1060":     {"vram_gb": 6,  "compute": "6.1", "gen": "pascal"},
    "GTX 1050 Ti":  {"vram_gb": 4,  "compute": "6.1", "gen": "pascal"},
    "GTX 1050":     {"vram_gb": 2,  "compute": "6.1", "gen": "pascal"},
    # 专业卡
    "A100":         {"vram_gb": 80, "compute": "8.0", "gen": "ampere"},
    "A100 40GB":    {"vram_gb": 40, "compute": "8.0", "gen": "ampere"},
    "A6000":        {"vram_gb": 48, "compute": "8.6", "gen": "ampere"},
    "A5000":        {"vram_gb": 24, "compute": "8.6", "gen": "ampere"},
    "A4000":        {"vram_gb": 16, "compute": "8.6", "gen": "ampere"},
    "H100":         {"vram_gb": 80, "compute": "9.0", "gen": "hopper"},
    "H200":         {"vram_gb": 141,"compute": "9.0", "gen": "hopper"},
    "L40S":         {"vram_gb": 48, "compute": "8.9", "gen": "ada"},
    "L4":           {"vram_gb": 24, "compute": "8.9", "gen": "ada"},
    "T4":           {"vram_gb": 16, "compute": "7.5", "gen": "turing"},
    "V100":         {"vram_gb": 16, "compute": "7.0", "gen": "volta"},
    "V100 32GB":    {"vram_gb": 32, "compute": "7.0", "gen": "volta"},
    "P100":         {"vram_gb": 16, "compute": "6.0", "gen": "pascal"},
    # 笔记本版本（移动端通常 VRAM 较低）
    "RTX 4090 Laptop": {"vram_gb": 16, "compute": "8.9", "gen": "ada"},
    "RTX 4080 Laptop": {"vram_gb": 12, "compute": "8.9", "gen": "ada"},
    "RTX 4070 Laptop": {"vram_gb": 8,  "compute": "8.9", "gen": "ada"},
    "RTX 4060 Laptop": {"vram_gb": 8,  "compute": "8.9", "gen": "ada"},
    "RTX 3080 Laptop": {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},  # 部分16GB
    "RTX 3070 Laptop": {"vram_gb": 8,  "compute": "8.6", "gen": "ampere"},
    "RTX 3060 Laptop": {"vram_gb": 6,  "compute": "8.6", "gen": "ampere"},
}

# Apple Silicon 统一内存（CPU/GPU 共享）
_APPLE_SILICON_DB: dict[str, dict] = {
    # M4 系列
    "M4 Ultra":   {"base_ram_gb": 192, "gpu_cores": 80, "gen": "m4"},
    "M4 Max":     {"base_ram_gb": 36,  "gpu_cores": 40, "gen": "m4"},
    "M4 Pro":     {"base_ram_gb": 24,  "gpu_cores": 20, "gen": "m4"},
    "M4":         {"base_ram_gb": 16,  "gpu_cores": 10, "gen": "m4"},
    # M3 系列
    "M3 Ultra":   {"base_ram_gb": 192, "gpu_cores": 76, "gen": "m3"},
    "M3 Max":     {"base_ram_gb": 36,  "gpu_cores": 40, "gen": "m3"},
    "M3 Pro":     {"base_ram_gb": 18,  "gpu_cores": 14, "gen": "m3"},
    "M3":         {"base_ram_gb": 8,   "gpu_cores": 10, "gen": "m3"},
    # M2 系列
    "M2 Ultra":   {"base_ram_gb": 192, "gpu_cores": 76, "gen": "m2"},
    "M2 Max":     {"base_ram_gb": 32,  "gpu_cores": 38, "gen": "m2"},
    "M2 Pro":     {"base_ram_gb": 16,  "gpu_cores": 19, "gen": "m2"},
    "M2":         {"base_ram_gb": 8,   "gpu_cores": 8,  "gen": "m2"},
    # M1 系列
    "M1 Ultra":   {"base_ram_gb": 128, "gpu_cores": 64, "gen": "m1"},
    "M1 Max":     {"base_ram_gb": 32,  "gpu_cores": 32, "gen": "m1"},
    "M1 Pro":     {"base_ram_gb": 16,  "gpu_cores": 16, "gen": "m1"},
    "M1":         {"base_ram_gb": 8,   "gpu_cores": 8,  "gen": "m1"},
}


# ─────────────────────────────────────────────
#  CUDA ↔ PyTorch ↔ 驱动 兼容矩阵
# ─────────────────────────────────────────────

# PyTorch 版本 → 推荐 CUDA 版本 → 最低驱动版本
_PYTORCH_CUDA_MATRIX: list[dict] = [
    {"pytorch": "2.6", "cuda": ["12.6", "12.4", "11.8"], "min_driver": "525.60"},
    {"pytorch": "2.5", "cuda": ["12.4", "12.1", "11.8"], "min_driver": "525.60"},
    {"pytorch": "2.4", "cuda": ["12.4", "12.1", "11.8"], "min_driver": "525.60"},
    {"pytorch": "2.3", "cuda": ["12.1", "11.8"],         "min_driver": "520.61"},
    {"pytorch": "2.2", "cuda": ["12.1", "11.8"],         "min_driver": "520.61"},
    {"pytorch": "2.1", "cuda": ["12.1", "11.8"],         "min_driver": "515.43"},
    {"pytorch": "2.0", "cuda": ["11.8", "11.7"],         "min_driver": "515.43"},
    {"pytorch": "1.13", "cuda": ["11.7", "11.6"],        "min_driver": "510.39"},
]

# CUDA 版本 → 最低 NVIDIA 驱动
_CUDA_DRIVER_MAP: dict[str, str] = {
    "12.6": "560.28",
    "12.4": "550.54",
    "12.3": "545.23",
    "12.2": "535.54",
    "12.1": "530.30",
    "12.0": "525.60",
    "11.8": "520.61",
    "11.7": "515.43",
    "11.6": "510.39",
    "11.5": "495.29",
}


# ─────────────────────────────────────────────
#  AI 模型 VRAM 需求数据库
# ─────────────────────────────────────────────

# 模型参数量(B) → 不同量化的 VRAM 需求(GB)
# 公式基础：FP16 ≈ params × 2，Q8 ≈ params × 1.1，Q4 ≈ params × 0.6
# 加 20% 运行时开销
_MODEL_VRAM_FORMULA = {
    "fp32": lambda params_b: params_b * 4 * 1.2,
    "fp16": lambda params_b: params_b * 2 * 1.2,
    "q8":   lambda params_b: params_b * 1.1 * 1.2,
    "q6_k": lambda params_b: params_b * 0.85 * 1.2,
    "q5_k": lambda params_b: params_b * 0.72 * 1.2,
    "q4_k": lambda params_b: params_b * 0.63 * 1.2,
    "q4_0": lambda params_b: params_b * 0.6 * 1.2,
    "q3_k": lambda params_b: params_b * 0.52 * 1.2,
    "q2_k": lambda params_b: params_b * 0.42 * 1.2,
}

# 常见模型参数量速查
_KNOWN_MODEL_PARAMS: dict[str, float] = {
    # LLaMA 系列
    "llama-3.3-70b": 70, "llama-3.2-90b": 90,
    "llama-3.1-405b": 405, "llama-3.1-70b": 70,
    "llama-3.1-8b": 8, "llama-3-70b": 70, "llama-3-8b": 8,
    "llama-2-70b": 70, "llama-2-13b": 13, "llama-2-7b": 7,
    # Qwen 系列
    "qwen3-235b": 235, "qwen3-32b": 32, "qwen3-14b": 14,
    "qwen3-8b": 8, "qwen3-4b": 4, "qwen3-1.7b": 1.7, "qwen3-0.6b": 0.6,
    "qwen2.5-72b": 72, "qwen2.5-32b": 32, "qwen2.5-14b": 14,
    "qwen2.5-7b": 7, "qwen2.5-3b": 3,
    # DeepSeek
    "deepseek-r1": 671, "deepseek-r1-distill-qwen-32b": 32,
    "deepseek-r1-distill-llama-70b": 70, "deepseek-r1-distill-llama-8b": 8,
    "deepseek-v3": 671, "deepseek-v2.5": 236,
    # Mistral / Mixtral
    "mixtral-8x22b": 141, "mixtral-8x7b": 47,
    "mistral-large": 123, "mistral-7b": 7,
    # Gemma
    "gemma-2-27b": 27, "gemma-2-9b": 9, "gemma-2-2b": 2,
    # Phi
    "phi-4": 14, "phi-3-14b": 14, "phi-3-7b": 7, "phi-3-mini": 3.8,
    # Stable Diffusion (VRAM for inference, different formula)
    "sdxl": 6.5, "sd-1.5": 2.0, "sd-3": 8.0, "flux": 12.0,
    # Whisper
    "whisper-large-v3": 1.55, "whisper-medium": 0.77, "whisper-small": 0.24,
}


# ─────────────────────────────────────────────
#  底层检测函数
# ─────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 5) -> Optional[str]:
    """运行命令，返回 stdout 或 None"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _run_any(cmd: list[str], timeout: int = 5) -> Optional[str]:
    """运行命令，不管返回码，只要有 stdout/stderr 就返回"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return (result.stdout + result.stderr).strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


# ─────────────────────────────────────────────
#  GPU 深度检测
# ─────────────────────────────────────────────

def detect_gpu_deep() -> dict:
    """
    深度 GPU 检测，返回详细硬件信息。

    Returns:
        {
            "type": "nvidia" | "apple_mps" | "amd_rocm" | "cpu_only",
            "name": str,
            "vram_gb": float | None,
            "driver_version": str | None,
            "cuda_version": str | None,
            "compute_capability": str | None,
            "gpu_gen": str | None,
            "unified_memory": bool,
            "total_ram_gb": float | None,
            "mps_available": bool,
        }
    """
    system = platform.system()
    arch = platform.machine()

    # Apple Silicon → MPS（统一内存）
    if system == "Darwin" and arch == "arm64":
        return _detect_apple_deep()

    # NVIDIA
    if shutil.which("nvidia-smi"):
        result = _detect_nvidia_deep()
        if result:
            return result

    # AMD ROCm (Linux)
    if system == "Linux" and (shutil.which("rocm-smi") or Path("/opt/rocm").exists()):
        result = _detect_amd_deep()
        if result:
            return result

    return {
        "type": "cpu_only",
        "name": "No dedicated GPU",
        "vram_gb": None,
        "driver_version": None,
        "cuda_version": None,
        "compute_capability": None,
        "gpu_gen": None,
        "unified_memory": False,
        "total_ram_gb": _get_ram_gb(),
        "mps_available": False,
    }


def _detect_apple_deep() -> dict:
    """Apple Silicon 深度检测"""
    chip_info = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or ""
    ram_gb = _get_ram_gb() or 8.0

    # 识别具体芯片型号
    chip_name = "Apple Silicon"
    chip_data = None
    for key in _APPLE_SILICON_DB:
        if key.lower().replace(" ", "") in chip_info.lower().replace(" ", ""):
            chip_name = f"Apple {key}"
            chip_data = _APPLE_SILICON_DB[key]
            break

    # Apple Silicon 统一内存 → GPU 可用内存约 75% of total RAM
    gpu_mem = round(ram_gb * 0.75, 1)

    return {
        "type": "apple_mps",
        "name": chip_name,
        "vram_gb": gpu_mem,  # 统一内存中可用于 GPU 的部分
        "driver_version": None,
        "cuda_version": None,
        "compute_capability": None,
        "gpu_gen": chip_data["gen"] if chip_data else None,
        "gpu_cores": chip_data["gpu_cores"] if chip_data else None,
        "unified_memory": True,
        "total_ram_gb": ram_gb,
        "mps_available": True,
    }


def _detect_nvidia_deep() -> Optional[dict]:
    """NVIDIA GPU 深度检测"""
    # 获取 GPU 名称
    gpu_name = _run([
        "nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"
    ])
    if not gpu_name:
        return None
    gpu_name = gpu_name.split("\n")[0].strip()

    # 获取 VRAM（优先 nvidia-smi 实时查询）
    vram_mb = None
    vram_output = _run([
        "nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"
    ])
    if vram_output:
        try:
            vram_mb = int(vram_output.split("\n")[0].strip())
        except ValueError:
            pass

    # 获取驱动版本
    driver_ver = _run([
        "nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"
    ])
    if driver_ver:
        driver_ver = driver_ver.split("\n")[0].strip()

    # 获取 CUDA 版本
    cuda_ver = None
    nvcc_out = _run(["nvcc", "--version"])
    if nvcc_out:
        m = re.search(r'release (\d+\.\d+)', nvcc_out)
        if m:
            cuda_ver = m.group(1)
    if not cuda_ver:
        smi_out = _run(["nvidia-smi"])
        if smi_out:
            m = re.search(r'CUDA Version:\s*([\d.]+)', smi_out)
            if m:
                cuda_ver = m.group(1)

    # 从数据库查找详细信息
    gpu_data = _lookup_nvidia_gpu(gpu_name)
    vram_gb = round(vram_mb / 1024, 1) if vram_mb else (gpu_data["vram_gb"] if gpu_data else None)

    return {
        "type": "nvidia",
        "name": gpu_name,
        "vram_gb": vram_gb,
        "driver_version": driver_ver,
        "cuda_version": cuda_ver,
        "compute_capability": gpu_data["compute"] if gpu_data else None,
        "gpu_gen": gpu_data["gen"] if gpu_data else None,
        "unified_memory": False,
        "total_ram_gb": _get_ram_gb(),
        "mps_available": False,
    }


def _detect_amd_deep() -> Optional[dict]:
    """AMD ROCm GPU 深度检测"""
    rocm_ver = None
    gpu_name = "AMD GPU"

    # 尝试 rocm-smi
    smi_output = _run_any(["rocm-smi", "--showproductname"])
    if smi_output:
        for line in smi_output.split("\n"):
            if "card" in line.lower() or "gpu" in line.lower():
                # 提取 GPU 名称
                parts = line.split(":")
                if len(parts) >= 2:
                    gpu_name = parts[-1].strip()
                    break

    rocm_output = _run(["cat", "/opt/rocm/.info/version"])
    if rocm_output:
        rocm_ver = rocm_output.strip()

    # 尝试获取 VRAM
    vram_gb = None
    mem_output = _run_any(["rocm-smi", "--showmeminfo", "vram"])
    if mem_output:
        m = re.search(r'Total.*?:\s*(\d+)', mem_output)
        if m:
            vram_gb = round(int(m.group(1)) / (1024 * 1024), 1)

    return {
        "type": "amd_rocm",
        "name": gpu_name,
        "vram_gb": vram_gb,
        "driver_version": None,
        "cuda_version": None,
        "compute_capability": None,
        "gpu_gen": None,
        "rocm_version": rocm_ver,
        "unified_memory": False,
        "total_ram_gb": _get_ram_gb(),
        "mps_available": False,
    }


def _lookup_nvidia_gpu(gpu_name: str) -> Optional[dict]:
    """从 VRAM 数据库中查找 GPU 信息（优先最长匹配）"""
    name_upper = gpu_name.upper()
    best_match = None
    best_len = 0
    for key, data in _NVIDIA_VRAM_DB.items():
        key_upper = key.upper()
        if key_upper in name_upper and len(key_upper) > best_len:
            best_match = data
            best_len = len(key_upper)
    if best_match:
        return best_match
    # 模糊匹配：去掉前缀
    for key, data in _NVIDIA_VRAM_DB.items():
        key_clean = key.upper().replace("GEFORCE ", "").replace("NVIDIA ", "")
        if key_clean in name_upper and len(key_clean) > best_len:
            best_match = data
            best_len = len(key_clean)
    return best_match


def _get_ram_gb() -> Optional[float]:
    """获取系统 RAM（GB）"""
    system = platform.system()
    try:
        if system == "Darwin":
            output = _run(["sysctl", "-n", "hw.memsize"])
            if output:
                return round(int(output) / (1024 ** 3), 1)
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / (1024 ** 2), 1)
        elif system == "Windows":
            output = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"])
            if output:
                for line in output.split("\n"):
                    if line.strip().isdigit():
                        return round(int(line.strip()) / (1024 ** 3), 1)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
#  AI 框架兼容性分析
# ─────────────────────────────────────────────

def check_pytorch_compatibility(gpu_info: dict) -> dict:
    """
    检查当前 GPU 与 PyTorch 的兼容性。

    Returns:
        {
            "compatible": bool,
            "recommended_pytorch": str,  # 推荐的 PyTorch 版本
            "recommended_cuda": str | None,
            "install_cmd": str,          # 推荐的安装命令
            "backend": "cuda" | "mps" | "rocm" | "cpu",
            "warnings": [str],
        }
    """
    gpu_type = gpu_info.get("type", "cpu_only")
    warnings = []

    if gpu_type == "apple_mps":
        return {
            "compatible": True,
            "recommended_pytorch": "2.6",
            "recommended_cuda": None,
            "install_cmd": "pip3 install torch torchvision torchaudio",
            "backend": "mps",
            "warnings": [],
        }

    if gpu_type == "amd_rocm":
        rocm_ver = gpu_info.get("rocm_version", "")
        return {
            "compatible": bool(rocm_ver),
            "recommended_pytorch": "2.5",
            "recommended_cuda": None,
            "install_cmd": "pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.2",
            "backend": "rocm",
            "warnings": ["AMD ROCm 支持相比 CUDA 可能存在部分算子不兼容"] if rocm_ver else ["未检测到 ROCm"],
        }

    if gpu_type == "nvidia":
        cuda_ver = gpu_info.get("cuda_version")
        driver_ver = gpu_info.get("driver_version")
        compute = gpu_info.get("compute_capability")

        if not cuda_ver:
            warnings.append("未检测到 CUDA，需要先安装 CUDA Toolkit")

        # 选择最佳 PyTorch + CUDA 组合
        best_pt = None
        best_cuda = None
        for entry in _PYTORCH_CUDA_MATRIX:
            for cuda_opt in entry["cuda"]:
                if cuda_ver and cuda_opt == cuda_ver:
                    best_pt = entry["pytorch"]
                    best_cuda = cuda_opt
                    break
                if not best_pt:
                    best_pt = entry["pytorch"]
                    best_cuda = entry["cuda"][0]
            if best_pt and best_cuda == cuda_ver:
                break

        if not best_pt:
            best_pt = "2.6"
            best_cuda = "12.4"

        # 检查 compute capability
        if compute:
            cc_float = float(compute)
            if cc_float < 3.5:
                warnings.append(f"GPU compute capability {compute} 过低，PyTorch 2.x 不再支持")
            elif cc_float < 7.0:
                warnings.append(f"GPU compute capability {compute} 较旧，部分新特性（如 BF16/Flash Attention）不可用")

        # 检查驱动版本
        if driver_ver and best_cuda:
            min_driver = _CUDA_DRIVER_MAP.get(best_cuda)
            if min_driver and _ver_lt(driver_ver, min_driver):
                warnings.append(f"NVIDIA 驱动 {driver_ver} 低于 CUDA {best_cuda} 要求的最低版本 {min_driver}，请升级驱动")

        cuda_suffix = best_cuda.replace(".", "") if best_cuda else "124"
        install_url = f"https://download.pytorch.org/whl/cu{cuda_suffix[:3]}"

        return {
            "compatible": True,
            "recommended_pytorch": best_pt,
            "recommended_cuda": best_cuda,
            "install_cmd": f"pip3 install torch torchvision torchaudio --index-url {install_url}",
            "backend": "cuda",
            "warnings": warnings,
        }

    # CPU only
    return {
        "compatible": True,
        "recommended_pytorch": "2.6",
        "recommended_cuda": None,
        "install_cmd": "pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu",
        "backend": "cpu",
        "warnings": ["无 GPU，将使用 CPU 模式（推理速度较慢）"],
    }


# ─────────────────────────────────────────────
#  VRAM 智能推荐
# ─────────────────────────────────────────────

def recommend_quantization(
    model_params_b: float,
    available_vram_gb: float,
) -> dict:
    """
    根据模型参数量和可用 VRAM，推荐最佳量化方案。

    Args:
        model_params_b: 模型参数量（十亿）
        available_vram_gb: 可用 GPU 内存（GB）

    Returns:
        {
            "can_run": bool,
            "recommended_quant": str | None,   # "fp16" / "q8" / "q4_k" / ...
            "vram_needed_gb": float,
            "all_options": [
                {"quant": str, "vram_gb": float, "fits": bool, "quality": str},
                ...
            ],
            "advice": str,
        }
    """
    quality_labels = {
        "fp32": "完美（无损）",
        "fp16": "极好（几乎无损）",
        "q8": "优秀（推荐）",
        "q6_k": "很好",
        "q5_k": "良好",
        "q4_k": "良好（推荐性价比最佳）",
        "q4_0": "可用",
        "q3_k": "一般（明显降质）",
        "q2_k": "较差（仅在 VRAM 极有限时使用）",
    }

    options = []
    recommended = None

    for quant, formula in _MODEL_VRAM_FORMULA.items():
        vram_needed = round(formula(model_params_b), 1)
        fits = vram_needed <= available_vram_gb
        options.append({
            "quant": quant,
            "vram_gb": vram_needed,
            "fits": fits,
            "quality": quality_labels.get(quant, ""),
        })
        if fits and recommended is None:
            recommended = quant

    # 从高质量到低质量排序（options 已按 _MODEL_VRAM_FORMULA 的 dict 顺序）
    can_run = recommended is not None

    if not can_run:
        # 即使最小量化也不行
        min_vram = options[-1]["vram_gb"] if options else 0
        advice = f"当前 VRAM {available_vram_gb}GB 不足以运行 {model_params_b}B 模型（最低需 {min_vram}GB）。建议使用更小的模型或增加内存。"
    elif recommended in ("fp32", "fp16"):
        advice = f"VRAM 充足！可以 {recommended.upper()} 全精度运行 {model_params_b}B 模型，效果最佳。"
    elif recommended in ("q8", "q6_k"):
        advice = f"推荐使用 {recommended.upper()} 量化运行 {model_params_b}B 模型，质量损失极小。"
    elif recommended in ("q4_k", "q5_k"):
        advice = f"推荐使用 {recommended.upper()} 量化，这是 VRAM 和质量的最佳平衡点。"
    else:
        advice = f"VRAM 有限，使用 {recommended.upper()} 量化。如需更好效果，考虑换用更小的模型。"

    return {
        "can_run": can_run,
        "recommended_quant": recommended,
        "vram_needed_gb": options[0]["vram_gb"] if options else 0,  # FP32 需求
        "all_options": options,
        "advice": advice,
    }


def recommend_for_model(
    model_name: str,
    gpu_info: dict,
) -> dict:
    """
    给定模型名称和 GPU 信息，返回完整推荐方案。

    Args:
        model_name: 模型名称（如 "llama-3.1-8b", "qwen2.5-72b"）
        gpu_info: detect_gpu_deep() 的返回值

    Returns:
        {
            "model": str,
            "params_b": float | None,
            "gpu": str,
            "vram_gb": float,
            "recommendation": dict,    # recommend_quantization() 结果
            "pytorch_compat": dict,     # check_pytorch_compatibility() 结果
            "ollama_tag": str | None,   # 推荐的 ollama 模型 tag
        }
    """
    # 查找模型参数量
    params_b = _lookup_model_params(model_name)
    vram_gb = gpu_info.get("vram_gb") or 0

    recommendation = recommend_quantization(params_b, vram_gb) if params_b else None
    pytorch_compat = check_pytorch_compatibility(gpu_info)

    # 生成 ollama 推荐 tag
    ollama_tag = None
    if params_b and recommendation and recommendation.get("can_run"):
        quant = recommendation["recommended_quant"]
        if quant in ("fp16", "fp32"):
            ollama_tag = f"{model_name}"
        else:
            ollama_tag = f"{model_name}:{quant}"

    return {
        "model": model_name,
        "params_b": params_b,
        "gpu": gpu_info.get("name", "Unknown"),
        "vram_gb": vram_gb,
        "recommendation": recommendation,
        "pytorch_compat": pytorch_compat,
        "ollama_tag": ollama_tag,
    }


def _lookup_model_params(model_name: str) -> Optional[float]:
    """从已知模型数据库查找参数量"""
    name_lower = model_name.lower().strip()
    # 精确匹配
    if name_lower in _KNOWN_MODEL_PARAMS:
        return _KNOWN_MODEL_PARAMS[name_lower]
    # 模糊匹配（去除前缀）
    for key, params in _KNOWN_MODEL_PARAMS.items():
        if key in name_lower or name_lower in key:
            return params
    # 从名称中提取参数量（如 "xxx-7b", "xxx-70B"）
    m = re.search(r'(\d+(?:\.\d+)?)\s*[bB]', model_name)
    if m:
        return float(m.group(1))
    return None


# ─────────────────────────────────────────────
#  安装成功率预测
# ─────────────────────────────────────────────

def predict_install_success(
    project_key: str,
    gpu_info: dict,
    env: dict,
    strategy: str = "unknown",
) -> dict:
    """
    基于硬件和项目特征预测安装成功概率。

    Args:
        project_key: "owner/repo" 格式
        gpu_info: detect_gpu_deep() 结果
        env: EnvironmentDetector.detect() 结果
        strategy: 使用的安装策略

    Returns:
        {
            "success_probability": float,  # 0.0 ~ 1.0
            "risk_factors": [str],
            "recommendations": [str],
            "confidence_level": "high" | "medium" | "low",
        }
    """
    probability = 0.9  # 基准
    risk_factors = []
    recommendations = []

    # 策略因素
    strategy_scores = {
        "known_project": 0.0,       # 已知项目，高度可靠
        "type_template_python": -0.05,
        "type_template_python_ml": -0.10,
        "type_template_node": -0.05,
        "type_template_docker": -0.03,
        "type_template_rust": -0.05,
        "type_template_go": -0.03,
        "type_template_cmake": -0.15,
        "type_template_make": -0.15,
        "readme_extract": -0.25,    # README 提取最不可靠
    }
    adjustment = strategy_scores.get(strategy, -0.15)
    probability += adjustment
    if adjustment < -0.10:
        risk_factors.append(f"安装策略 '{strategy}' 可靠性较低")

    # GPU 相关风险
    os_info = env.get("os", {})
    gpu_type = gpu_info.get("type", "cpu_only")

    if gpu_type == "nvidia":
        if not gpu_info.get("cuda_version"):
            probability -= 0.15
            risk_factors.append("NVIDIA GPU 未安装 CUDA")
            recommendations.append("安装 CUDA Toolkit: https://developer.nvidia.com/cuda-toolkit")
        elif gpu_info.get("compute_capability"):
            cc = float(gpu_info["compute_capability"])
            if cc < 6.0:
                probability -= 0.20
                risk_factors.append(f"GPU compute capability {cc} 过低")

    elif gpu_type == "amd_rocm":
        if not gpu_info.get("rocm_version"):
            probability -= 0.20
            risk_factors.append("AMD GPU 未安装 ROCm")

    # 操作系统因素
    os_type = os_info.get("type", "unknown")
    if os_type == "windows":
        probability -= 0.10
        risk_factors.append("Windows 平台编译工具链配置较复杂")
        recommendations.append("考虑使用 WSL2 获得更好的兼容性")
    elif os_type == "linux" and os_info.get("is_wsl"):
        probability -= 0.03
        risk_factors.append("WSL2 环境可能存在 GPU 直通限制")

    # 运行时因素
    runtimes = env.get("runtimes", {})
    if not runtimes.get("git", {}).get("available"):
        probability -= 0.30
        risk_factors.append("未安装 Git")
        recommendations.append("安装 Git: https://git-scm.com/")

    # 磁盘空间
    disk = env.get("disk", {})
    free_gb = disk.get("free_gb", 999)
    if free_gb < 5:
        probability -= 0.20
        risk_factors.append(f"磁盘空间不足（{free_gb}GB）")
    elif free_gb < 20:
        probability -= 0.05
        risk_factors.append(f"磁盘空间较少（{free_gb}GB），大型 AI 项目可能不足")

    # RAM 因素
    hw = env.get("hardware", {})
    ram_gb = hw.get("ram_gb", 0)
    if ram_gb and ram_gb < 8:
        probability -= 0.15
        risk_factors.append(f"内存仅 {ram_gb}GB，可能不足以编译大型项目")

    # 限制范围
    probability = max(0.05, min(0.98, probability))

    # 置信度分级
    if strategy == "known_project":
        confidence = "high"
    elif probability >= 0.75:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "success_probability": round(probability, 2),
        "risk_factors": risk_factors,
        "recommendations": recommendations,
        "confidence_level": confidence,
    }


# ─────────────────────────────────────────────
#  版本比较辅助
# ─────────────────────────────────────────────

def _ver_lt(v1: str, v2: str) -> bool:
    """版本号比较：v1 < v2"""
    def _parts(v):
        return [int(x) for x in re.findall(r'\d+', v)]
    return _parts(v1) < _parts(v2)


# ─────────────────────────────────────────────
#  缓存层
# ─────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 120  # 2 分钟


def get_gpu_info(force_refresh: bool = False) -> dict:
    """获取 GPU 信息（带缓存）"""
    now = time.time()
    if not force_refresh and "gpu" in _cache:
        ts, data = _cache["gpu"]
        if now - ts < _CACHE_TTL:
            return data
    data = detect_gpu_deep()
    _cache["gpu"] = (now, data)
    return data


def get_full_ai_hardware_report(env: dict | None = None) -> dict:
    """
    生成完整的 AI 硬件报告。

    Returns:
        {
            "gpu": dict,                     # GPU 深度检测
            "pytorch": dict,                 # PyTorch 兼容性
            "vram_gb": float,                # 可用 GPU 内存
            "recommended_models": list,       # 推荐可运行的模型规模
            "summary": str,                   # 人类可读摘要
        }
    """
    gpu = get_gpu_info()
    pytorch = check_pytorch_compatibility(gpu)
    vram = gpu.get("vram_gb") or 0

    # 推荐可运行的模型规模
    model_tiers = []
    for label, params_b in [
        ("小型（1-3B）", 3), ("中型（7-8B）", 8), ("大型（13-14B）", 14),
        ("超大（32-34B）", 32), ("巨型（70B）", 70), ("旗舰（405B）", 405),
    ]:
        rec = recommend_quantization(params_b, vram)
        if rec["can_run"]:
            model_tiers.append({
                "tier": label,
                "params_b": params_b,
                "best_quant": rec["recommended_quant"],
                "vram_needed": rec["all_options"][0]["vram_gb"] if rec["all_options"] else 0,
            })

    # 生成摘要
    gpu_name = gpu.get("name", "Unknown")
    backend = pytorch.get("backend", "cpu")
    summary_parts = [f"GPU: {gpu_name}"]

    if vram:
        summary_parts.append(f"可用 GPU 内存: {vram}GB")
    summary_parts.append(f"推理后端: {backend.upper()}")

    if model_tiers:
        max_tier = model_tiers[-1]["tier"]
        summary_parts.append(f"最大可运行: {max_tier}")
    else:
        summary_parts.append("VRAM 不足以运行任何模型")

    if pytorch.get("warnings"):
        summary_parts.extend(pytorch["warnings"])

    return {
        "gpu": gpu,
        "pytorch": pytorch,
        "vram_gb": vram,
        "recommended_models": model_tiers,
        "summary": " | ".join(summary_parts),
    }


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_ai_hardware_report(report: dict) -> str:
    """将 AI 硬件报告格式化为人类可读文本"""
    lines = []
    gpu = report.get("gpu", {})
    pytorch = report.get("pytorch", {})
    vram = report.get("vram_gb", 0)

    lines.append("🎮 AI 硬件报告")
    lines.append("=" * 40)

    # GPU 信息
    gpu_type = gpu.get("type", "cpu_only")
    lines.append(f"  GPU: {gpu.get('name', 'N/A')}")
    if vram:
        lines.append(f"  可用 GPU 内存: {vram} GB")
    if gpu.get("cuda_version"):
        lines.append(f"  CUDA: {gpu['cuda_version']}")
    if gpu.get("driver_version"):
        lines.append(f"  驱动: {gpu['driver_version']}")
    if gpu.get("compute_capability"):
        lines.append(f"  计算能力: {gpu['compute_capability']}")
    if gpu.get("unified_memory"):
        lines.append(f"  统一内存: ✅ (总 RAM: {gpu.get('total_ram_gb', '?')} GB)")

    # PyTorch
    lines.append("")
    lines.append(f"🔥 PyTorch 推荐")
    lines.append(f"  后端: {pytorch.get('backend', 'N/A').upper()}")
    lines.append(f"  推荐版本: {pytorch.get('recommended_pytorch', 'N/A')}")
    lines.append(f"  安装命令: {pytorch.get('install_cmd', 'N/A')}")
    for w in pytorch.get("warnings", []):
        lines.append(f"  ⚠️  {w}")

    # 可运行的模型规模
    models = report.get("recommended_models", [])
    if models:
        lines.append("")
        lines.append("🤖 可运行模型规模")
        for m in models:
            lines.append(f"  ✅ {m['tier']} → {m['best_quant'].upper()}")
    else:
        lines.append("")
        lines.append("❌ 当前硬件 VRAM 不足以运行 AI 模型")

    return "\n".join(lines)
