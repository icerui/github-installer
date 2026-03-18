"""
detector.py - 全平台环境检测器
=====================================

覆盖：
  - macOS (Intel / Apple Silicon M1-M4)
  - Linux (Ubuntu/Debian/Arch/Fedora/openSUSE)
  - Windows 10/11
  - Windows WSL2

检测内容：
  - OS + 发行版 + 版本 + 架构
  - CPU + 内存 + 磁盘空间
  - GPU (NVIDIA CUDA / AMD ROCm / Apple MPS)
  - 已安装的包管理器
  - 已安装的运行时 (Python/Node/Go/Rust/Docker/Git)
  - 已配置的 LLM 环境变量
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 5) -> Optional[str]:
    """运行命令，返回 stdout 或 None（失败时不抛异常）"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Windows 安全：不通过 shell 执行
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _which(binary: str) -> Optional[str]:
    """查找可执行文件路径"""
    return shutil.which(binary)


def _version(binary: str, version_flag: str = "--version") -> Optional[str]:
    """获取工具版本号"""
    if not _which(binary):
        return None
    output = _run([binary, version_flag])
    if not output:
        return None
    # 提取第一行的版本号
    first_line = output.split("\n")[0]
    match = re.search(r'[\d]+\.[\d]+(?:\.[\d]+)?', first_line)
    return match.group(0) if match else first_line[:50]


# ─────────────────────────────────────────────
#  环境检测主类
# ─────────────────────────────────────────────

class EnvironmentDetector:

    def detect(self) -> dict:
        """执行完整环境检测，返回结构化结果"""
        return {
            "os": self._detect_os(),
            "hardware": self._detect_hardware(),
            "gpu": self._detect_gpu(),
            "package_managers": self._detect_package_managers(),
            "runtimes": self._detect_runtimes(),
            "disk": self._detect_disk(),
            "llm_configured": self._detect_llm_env(),
            "network": self._detect_network(),
        }

    # ── OS ──────────────────────────────────

    def _detect_os(self) -> dict:
        system = platform.system()

        if system == "Darwin":
            return self._detect_macos()
        elif system == "Linux":
            return self._detect_linux()
        elif system == "Windows":
            return self._detect_windows()
        else:
            return {"type": "unknown", "system": system}

    def _detect_macos(self) -> dict:
        arch = platform.machine()  # "arm64" 或 "x86_64"
        mac_ver = platform.mac_ver()[0]
        
        chip = "unknown"
        if arch == "arm64":
            # 检测 Apple Silicon 代别
            chip_info = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or ""
            if "M4" in chip_info:
                chip = "Apple M4"
            elif "M3" in chip_info:
                chip = "Apple M3"
            elif "M2" in chip_info:
                chip = "Apple M2"
            elif "M1" in chip_info:
                chip = "Apple M1"
            else:
                chip = "Apple Silicon"
        else:
            chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Intel"

        return {
            "type": "macos",
            "version": mac_ver,
            "arch": arch,
            "chip": chip,
            "is_apple_silicon": arch == "arm64",
            "shell": os.environ.get("SHELL", "/bin/zsh"),
            "home": str(Path.home()),
        }

    def _detect_linux(self) -> dict:
        arch = platform.machine()
        distro = "unknown"
        distro_version = ""

        # 读取 /etc/os-release
        os_release = {}
        for path in ["/etc/os-release", "/usr/lib/os-release"]:
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line:
                            k, v = line.split("=", 1)
                            os_release[k] = v.strip('"')
                break
            except FileNotFoundError:
                continue

        distro = os_release.get("ID", "linux")
        distro_version = os_release.get("VERSION_ID", "")
        distro_name = os_release.get("PRETTY_NAME", distro)

        # 检测 WSL
        is_wsl = False
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    is_wsl = True
        except FileNotFoundError:
            pass

        return {
            "type": "linux",
            "distro": distro,               # ubuntu / arch / fedora / ...
            "distro_name": distro_name,
            "version": distro_version,
            "arch": arch,
            "is_wsl": is_wsl,
            "shell": os.environ.get("SHELL", "/bin/bash"),
            "home": str(Path.home()),
        }

    def _detect_windows(self) -> dict:
        arch = platform.machine()  # "AMD64" 或 "ARM64"
        win_ver = platform.version()
        release = platform.release()

        return {
            "type": "windows",
            "version": win_ver,
            "release": release,             # "10" 或 "11"
            "arch": arch,
            "is_wsl": False,
            "shell": os.environ.get("COMSPEC", "cmd.exe"),
            "home": str(Path.home()),
            "powershell": bool(_which("powershell") or _which("pwsh")),
        }

    # ── 硬件 ──────────────────────────────────

    def _detect_hardware(self) -> dict:
        result = {
            "cpu_count": os.cpu_count(),
            "ram_gb": self._detect_ram_gb(),
        }
        return result

    def _detect_ram_gb(self) -> Optional[float]:
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
                            kb = int(line.split()[1])
                            return round(kb / (1024 ** 2), 1)
            elif system == "Windows":
                # 优先 PowerShell（wmic 已弃用）
                output = _run(["powershell", "-NoProfile", "-Command",
                               "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
                if output and output.strip().isdigit():
                    return round(int(output.strip()) / (1024 ** 3), 1)
                # 回退到 wmic（兼容旧系统）
                output = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"])
                if output:
                    for line in output.split("\n"):
                        line = line.strip()
                        if line.isdigit():
                            return round(int(line) / (1024 ** 3), 1)
        except Exception:
            pass
        return None

    # ── GPU ──────────────────────────────────

    def _detect_gpu(self) -> dict:
        system = platform.system()
        arch = platform.machine()

        # Apple Silicon → MPS
        if system == "Darwin" and arch == "arm64":
            return {
                "type": "apple_mps",
                "name": "Apple Neural Engine + GPU",
                "pytorch_flag": "mps",
                "cuda_available": False,
            }

        # NVIDIA CUDA
        nvidia = self._detect_nvidia()
        if nvidia:
            return nvidia

        # AMD ROCm（Linux only）
        if system == "Linux":
            rocm = self._detect_rocm()
            if rocm:
                return rocm

        # 集成显卡 / 无独显
        return {
            "type": "cpu_only",
            "name": "No dedicated GPU",
            "pytorch_flag": "cpu",
            "cuda_available": False,
        }

    def _detect_nvidia(self) -> Optional[dict]:
        """检测 NVIDIA GPU 和 CUDA 版本"""
        nvidia_smi = _which("nvidia-smi")
        if not nvidia_smi:
            return None

        gpu_name = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"])
        
        # 解析 CUDA 版本
        cuda_ver = None
        nvcc_output = _run(["nvcc", "--version"])
        if nvcc_output:
            match = re.search(r'release (\d+\.\d+)', nvcc_output)
            if match:
                cuda_ver = match.group(1)

        if not cuda_ver:
            # 从 nvidia-smi 尝试解析
            smi_output = _run(["nvidia-smi"])
            if smi_output:
                match = re.search(r'CUDA Version:\s*([\d.]+)', smi_output)
                if match:
                    cuda_ver = match.group(1)

        return {
            "type": "nvidia_cuda",
            "name": (gpu_name or "NVIDIA GPU").split("\n")[0].strip(),
            "cuda_version": cuda_ver,
            "pytorch_flag": f"cu{cuda_ver.replace('.', '')[:3]}" if cuda_ver else "cu121",
            "cuda_available": True,
        }

    def _detect_rocm(self) -> Optional[dict]:
        """检测 AMD ROCm"""
        if not _which("rocm-smi") and not Path("/opt/rocm").exists():
            return None
        
        rocm_version = None
        rocm_info = _run(["rocm-smi", "--showfwinfo"])
        if rocm_info:
            match = re.search(r'ROCm\s+([\d.]+)', rocm_info)
            if match:
                rocm_version = match.group(1)

        return {
            "type": "amd_rocm",
            "name": "AMD GPU (ROCm)",
            "rocm_version": rocm_version,
            "pytorch_flag": "rocm",
            "cuda_available": False,
        }

    # ── 包管理器 ──────────────────────────────

    def _detect_package_managers(self) -> dict:
        """检测所有平台的包管理器"""
        managers = {}
        
        checks = [
            # 通用
            ("pip",     ["pip", "--version"]),
            ("pip3",    ["pip3", "--version"]),
            ("conda",   ["conda", "--version"]),
            ("uv",      ["uv", "--version"]),   # 新一代 Python 包管理
            # macOS
            ("brew",    ["brew", "--version"]),
            # Linux
            ("apt",     ["apt", "--version"]),
            ("apt-get", ["apt-get", "--version"]),
            ("dnf",     ["dnf", "--version"]),
            ("pacman",  ["pacman", "--version"]),
            ("yay",     ["yay", "--version"]),   # Arch AUR
            ("zypper",  ["zypper", "--version"]),
            ("snap",    ["snap", "--version"]),
            # Windows
            ("winget",  ["winget", "--version"]),
            ("choco",   ["choco", "--version"]),
            ("scoop",   ["scoop", "--version"]),
            # 语言级
            ("npm",     ["npm", "--version"]),
            ("pnpm",    ["pnpm", "--version"]),
            ("yarn",    ["yarn", "--version"]),
            ("bun",     ["bun", "--version"]),
            ("cargo",   ["cargo", "--version"]),
            ("go",      ["go", "version"]),
        ]

        for name, cmd in checks:
            if _which(cmd[0]):
                ver = _version(cmd[0], cmd[1] if len(cmd) > 1 else "--version")
                managers[name] = {"available": True, "version": ver}

        return managers

    # ── 运行时 ──────────────────────────────────

    def _detect_runtimes(self) -> dict:
        """检测主要开发运行时"""
        runtimes = {}

        # Python（最重要）—— 独立检测系统可用的 python3，而不是当前解释器
        py_version = sys.version.split()[0]
        py_executable = sys.executable
        py_path = _which("python3") or _which("python")
        # 如果系统 python3 与当前解释器不同，优先报告系统的
        if py_path and os.path.realpath(py_path) != os.path.realpath(sys.executable):
            sys_py_ver = _version("python3") or _version("python")
            if sys_py_ver:
                py_version = sys_py_ver
                py_executable = py_path
        runtimes["python"] = {
            "available": True,
            "version": py_version,
            "executable": py_executable,
            "path": py_path,
        }

        # Node.js
        node_ver = _version("node")
        if node_ver:
            runtimes["node"] = {"available": True, "version": node_ver}

        # Git（安装几乎所有项目都需要）
        git_ver = _version("git")
        runtimes["git"] = {
            "available": bool(git_ver),
            "version": git_ver,
        }

        # Docker
        docker_ver = _version("docker")
        if docker_ver:
            # 检测 Docker 是否真正运行
            docker_running = _run(["docker", "ps"]) is not None
            runtimes["docker"] = {
                "available": True,
                "version": docker_ver,
                "daemon_running": docker_running,
            }

        # Rust
        rust_ver = _version("rustc")
        if rust_ver:
            runtimes["rust"] = {"available": True, "version": rust_ver}

        # Go
        go_ver = _version("go", "version")
        if go_ver:
            runtimes["go"] = {"available": True, "version": go_ver}

        # Java（部分工具需要）
        java_ver = _version("java", "-version")
        if java_ver:
            runtimes["java"] = {"available": True, "version": java_ver}

        # ffmpeg（视频处理类项目）
        ffmpeg_ver = _version("ffmpeg", "-version")
        if ffmpeg_ver:
            runtimes["ffmpeg"] = {"available": True, "version": ffmpeg_ver}

        return runtimes

    # ── 磁盘空间 ──────────────────────────────

    def _detect_disk(self) -> dict:
        """检测 home 目录所在分区的可用空间"""
        try:
            stat = os.statvfs(str(Path.home()))
            free_gb = round((stat.f_frsize * stat.f_bavail) / (1024 ** 3), 1)
            total_gb = round((stat.f_frsize * stat.f_blocks) / (1024 ** 3), 1)
            return {"free_gb": free_gb, "total_gb": total_gb, "path": str(Path.home())}
        except AttributeError:
            # Windows 不支持 statvfs
            import shutil as sh
            usage = sh.disk_usage(str(Path.home()))
            return {
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "path": str(Path.home()),
            }

    # ── LLM 环境变量检测 ──────────────────────

    def _detect_llm_env(self) -> dict:
        """检测已配置的 LLM API Keys（只检测是否存在，不暴露值）"""
        keys = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        return {name: bool(os.getenv(env_var, "").strip()) for name, env_var in keys.items()}

    # ── 网络检测 ──────────────────────────────

    def _detect_network(self) -> dict:
        """检测网络可达性（主要检测 GitHub）"""
        import socket
        result = {}
        targets = [
            ("github", "github.com", 443),
            ("pypi", "pypi.org", 443),
        ]
        for name, host, port in targets:
            try:
                socket.create_connection((host, port), timeout=10).close()
                result[name] = True
            except (socket.timeout, OSError):
                result[name] = False
        return result


# ─────────────────────────────────────────────
#  格式化输出（供 CLI 使用）
# ─────────────────────────────────────────────

def format_env_summary(env: dict) -> str:
    """格式化环境信息为人类可读的摘要"""
    lines = []
    os_info = env.get("os", {})
    gpu_info = env.get("gpu", {})
    hw = env.get("hardware", {})
    disk = env.get("disk", {})

    # OS 行
    if os_info.get("type") == "macos":
        lines.append(f"💻 {os_info.get('chip', 'Mac')} / macOS {os_info.get('version', '')} ({os_info.get('arch', '')})")
    elif os_info.get("type") == "linux":
        wsl = " [WSL2]" if os_info.get("is_wsl") else ""
        lines.append(f"🐧 {os_info.get('distro_name', 'Linux')}{wsl} ({os_info.get('arch', '')})")
    elif os_info.get("type") == "windows":
        lines.append(f"🪟 Windows {os_info.get('release', '')} ({os_info.get('arch', '')})")

    # 硬件
    ram = hw.get("ram_gb")
    cpu = hw.get("cpu_count")
    if ram and cpu:
        lines.append(f"⚙️  {cpu} 核 / {ram} GB RAM / 磁盘剩余 {disk.get('free_gb', '?')} GB")

    # GPU
    gpu_type = gpu_info.get("type", "cpu_only")
    if gpu_type == "apple_mps":
        lines.append("🎮 GPU: Apple MPS ✅")
    elif gpu_type == "nvidia_cuda":
        cuda = gpu_info.get("cuda_version", "未知")
        lines.append(f"🎮 GPU: {gpu_info.get('name', 'NVIDIA')} / CUDA {cuda} ✅")
    elif gpu_type == "amd_rocm":
        lines.append(f"🎮 GPU: {gpu_info.get('name', 'AMD')} / ROCm ✅")
    else:
        lines.append("🎮 GPU: 无独立显卡（将使用 CPU 模式）")

    # 运行时
    runtimes = env.get("runtimes", {})
    rt_parts = []
    if "python" in runtimes:
        rt_parts.append(f"Python {runtimes['python']['version']}")
    if "node" in runtimes:
        rt_parts.append(f"Node {runtimes['node']['version']}")
    if "git" in runtimes and runtimes["git"]["available"]:
        rt_parts.append("git ✓")
    if "docker" in runtimes:
        running = "✅" if runtimes["docker"].get("daemon_running") else "⚠️(未运行)"
        rt_parts.append(f"Docker {running}")
    if rt_parts:
        lines.append("🔧 运行时：" + " | ".join(rt_parts))

    # 包管理器
    pms = env.get("package_managers", {})
    available_pms = [name for name, info in pms.items() if info.get("available")]
    if available_pms:
        lines.append("📦 包管理器：" + " | ".join(available_pms[:6]))

    return "\n".join(lines)


if __name__ == "__main__":
    detector = EnvironmentDetector()
    env = detector.detect()
    print(json.dumps(env, ensure_ascii=False, indent=2))
    print("\n" + "─" * 50)
    print(format_env_summary(env))
