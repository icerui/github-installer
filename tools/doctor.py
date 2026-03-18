"""
doctor.py - gitinstall 诊断系统
================================

灵感来源：OpenClaw `openclaw doctor` 诊断工具

功能：
  1. 系统环境全面检查（OS、Python、Git、包管理器）
  2. GitHub API 连通性 + 配额检测
  3. LLM API Key 可用性验证
  4. 缓存健康度检查（大小、过期条目）
  5. 数据库完整性校验
  6. GPU / AI 硬件就绪度
  7. 安全配置审计
  8. 已知问题自动修复建议

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 诊断结果等级 ──
LEVEL_OK = "ok"           # ✅ 正常
LEVEL_WARN = "warn"       # ⚠️  警告（不阻塞，但建议修复）
LEVEL_ERROR = "error"     # ❌ 错误（会影响功能）
LEVEL_INFO = "info"       # ℹ️  纯信息


@dataclass
class CheckResult:
    """单项检查结果"""
    name: str
    level: str
    message: str
    detail: str = ""
    fix_hint: str = ""


@dataclass
class DoctorReport:
    """完整诊断报告"""
    checks: list[CheckResult] = field(default_factory=list)
    timestamp: float = 0.0
    duration_ms: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.level == LEVEL_OK)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.level == LEVEL_WARN)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if c.level == LEVEL_ERROR)

    @property
    def all_ok(self) -> bool:
        return self.error_count == 0


# ─────────────────────────────────────────────
#  各项检查
# ─────────────────────────────────────────────

def _check_python() -> CheckResult:
    """检查 Python 版本"""
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver >= (3, 10):
        return CheckResult("Python 版本", LEVEL_OK, f"Python {ver_str}")
    elif ver >= (3, 8):
        return CheckResult("Python 版本", LEVEL_WARN, f"Python {ver_str}（推荐 3.10+）",
                          fix_hint="升级 Python: brew install python3 / apt install python3.12")
    else:
        return CheckResult("Python 版本", LEVEL_ERROR, f"Python {ver_str} 版本过低",
                          fix_hint="需要 Python 3.8+，推荐 3.10+")


def _check_git() -> CheckResult:
    """检查 Git 可用性"""
    git = shutil.which("git")
    if not git:
        return CheckResult("Git", LEVEL_ERROR, "未安装 Git",
                          fix_hint="安装: brew install git / apt install git / winget install Git.Git")
    try:
        result = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=5)
        ver = result.stdout.strip()
        return CheckResult("Git", LEVEL_OK, ver)
    except Exception as e:
        return CheckResult("Git", LEVEL_WARN, f"Git 存在但无法运行: {e}")


def _check_package_managers() -> list[CheckResult]:
    """检查包管理器"""
    results = []
    managers = {
        "brew": ("Homebrew", "macOS/Linux 包管理器"),
        "apt": ("APT", "Debian/Ubuntu 包管理器"),
        "yum": ("YUM", "RHEL/CentOS 包管理器"),
        "dnf": ("DNF", "Fedora 包管理器"),
        "pacman": ("Pacman", "Arch Linux 包管理器"),
        "pip": ("pip", "Python 包管理器"),
        "npm": ("npm", "Node.js 包管理器"),
        "cargo": ("Cargo", "Rust 包管理器"),
        "go": ("Go", "Go 语言工具链"),
        "docker": ("Docker", "容器运行时"),
        "conda": ("Conda", "科学计算环境管理器"),
    }
    found_any = False
    for cmd, (display_name, desc) in managers.items():
        path = shutil.which(cmd)
        if path:
            found_any = True
            results.append(CheckResult(display_name, LEVEL_OK, f"已安装 ({path})"))

    if not found_any:
        results.append(CheckResult("包管理器", LEVEL_ERROR,
                                   "未检测到任何包管理器",
                                   fix_hint="至少需要一个包管理器才能安装项目依赖"))
    return results


def _check_github_api() -> CheckResult:
    """检查 GitHub API 连通性和配额"""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"User-Agent": "gitinstall-doctor/1.0"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        req = urllib.request.Request("https://api.github.com/rate_limit", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            core = data.get("resources", {}).get("core", {})
            remaining = core.get("remaining", 0)
            limit = core.get("limit", 0)
            reset_ts = core.get("reset", 0)

            if token:
                auth_info = f"已认证（GITHUB_TOKEN），配额 {remaining}/{limit}"
            else:
                auth_info = f"未认证，配额 {remaining}/{limit}"

            if remaining < 5:
                reset_time = time.strftime("%H:%M:%S", time.localtime(reset_ts))
                return CheckResult("GitHub API", LEVEL_WARN,
                                   f"{auth_info}，配额即将耗尽，{reset_time} 重置",
                                   fix_hint="设置 GITHUB_TOKEN 环境变量提升到 5000 次/小时，或使用 --local 模式")
            if not token:
                return CheckResult("GitHub API", LEVEL_WARN,
                                   f"{auth_info}（限制 60 次/小时）",
                                   fix_hint="设置 GITHUB_TOKEN=ghp_xxx 提升到 5000 次/小时")
            return CheckResult("GitHub API", LEVEL_OK, auth_info)
    except urllib.error.URLError as e:
        return CheckResult("GitHub API", LEVEL_ERROR, f"无法连接 GitHub API: {e}",
                          fix_hint="检查网络连接，或设置 HTTP_PROXY 环境变量")
    except Exception as e:
        return CheckResult("GitHub API", LEVEL_ERROR, f"检查失败: {e}")


def _check_llm_keys() -> list[CheckResult]:
    """检查 LLM API Key 配置"""
    results = []
    keys = {
        "ANTHROPIC_API_KEY": ("Anthropic Claude", "sk-ant-"),
        "OPENAI_API_KEY": ("OpenAI GPT", "sk-"),
        "OPENROUTER_API_KEY": ("OpenRouter", "sk-or-"),
        "GEMINI_API_KEY": ("Google Gemini", "AI"),
        "GROQ_API_KEY": ("Groq Llama", "gsk_"),
        "DEEPSEEK_API_KEY": ("DeepSeek", "sk-"),
    }
    configured = []
    for env_var, (display, prefix) in keys.items():
        val = os.getenv(env_var, "").strip()
        if val:
            # 简单格式校验（不发送请求）
            if prefix and not val.startswith(prefix):
                results.append(CheckResult(display, LEVEL_WARN,
                                           f"{env_var} 已设置但格式可能有误（期望 {prefix}... 开头）"))
            else:
                configured.append(display)
                results.append(CheckResult(display, LEVEL_OK, f"{env_var} 已配置"))

    # 检查本地 LLM
    local_llms = [
        ("localhost:11434", "Ollama"),
        ("localhost:1234", "LM Studio"),
    ]
    for addr, name in local_llms:
        try:
            req = urllib.request.Request(f"http://{addr}/")
            with urllib.request.urlopen(req, timeout=3):
                configured.append(name)
                results.append(CheckResult(name, LEVEL_OK, f"{name} 本地服务运行中"))
        except Exception:
            pass  # 不报错，本地 LLM 是可选的

    if not configured:
        results.append(CheckResult("LLM 配置", LEVEL_INFO,
                                   "未配置任何 LLM API Key（gitinstall 无需 AI 也能工作）",
                                   detail="SmartPlanner 内置 80+ 已知项目 + 50+ 语言模板，覆盖大部分场景",
                                   fix_hint="如需 AI 增强，设置任一 API Key: ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY 等"))

    return results


def _check_cache() -> CheckResult:
    """检查缓存健康度"""
    cache_dir = Path.home() / ".cache" / "gitinstall"
    if not cache_dir.exists():
        return CheckResult("缓存", LEVEL_OK, "缓存目录尚未创建（首次使用时自动创建）")

    total_size = 0
    file_count = 0
    expired_count = 0
    now = time.time()
    ttl = int(os.getenv("GITINSTALL_CACHE_TTL", str(24 * 3600)))

    for f in cache_dir.rglob("*"):
        if f.is_file():
            file_count += 1
            total_size += f.stat().st_size
            if now - f.stat().st_mtime > ttl:
                expired_count += 1

    size_mb = total_size / (1024 * 1024)

    if size_mb > 100:
        return CheckResult("缓存", LEVEL_WARN,
                           f"缓存较大: {size_mb:.1f}MB ({file_count} 文件, {expired_count} 已过期)",
                           fix_hint=f"清理缓存: rm -rf {cache_dir}")
    return CheckResult("缓存", LEVEL_OK,
                       f"{size_mb:.1f}MB, {file_count} 文件" +
                       (f" ({expired_count} 已过期)" if expired_count else ""))


def _check_database() -> CheckResult:
    """检查数据库完整性"""
    try:
        from db_backend import get_backend
        backend = get_backend()
    except Exception:
        return CheckResult("数据库", LEVEL_OK, "尚未创建（首次安装时自动初始化）")

    db_path = Path.home() / ".gitinstall" / "data.db"
    if backend.backend_type == "sqlite" and not db_path.exists():
        return CheckResult("数据库", LEVEL_OK, "尚未创建（首次安装时自动初始化）")

    try:
        # 完整性检查
        integrity = backend.integrity_check()
        if integrity != "ok":
            return CheckResult("数据库", LEVEL_ERROR, f"数据库损坏: {integrity}",
                              fix_hint=f"备份后删除: mv {db_path} {db_path}.bak")

        # 统计数据
        tables = {}
        for table in ["events", "install_telemetry", "plans_history", "users"]:
            try:
                tables[table] = backend.table_row_count(table)
            except Exception:
                pass

        # 文件权限检查（仅 SQLite）
        perm_ok = True
        if backend.backend_type == "sqlite" and db_path.exists():
            mode = oct(db_path.stat().st_mode)[-3:]
            perm_ok = mode in ("600", "644", "700")

        stats = ", ".join(f"{k}: {v}" for k, v in tables.items() if v > 0)
        msg = f"正常 ({stats})" if stats else "正常（空数据库）"
        if not perm_ok:
            return CheckResult("数据库", LEVEL_WARN, f"{msg}，权限 {mode} 不安全",
                              fix_hint=f"修复: chmod 600 {db_path}")
        return CheckResult("数据库", LEVEL_OK, msg)

    except Exception as e:
        return CheckResult("数据库", LEVEL_ERROR, f"无法打开: {e}")


def _check_gpu() -> CheckResult:
    """检查 GPU / AI 硬件"""
    try:
        from hw_detect import get_gpu_info
        gpu = get_gpu_info()
        gpu_type = gpu.get("type", "cpu_only")
        gpu_name = gpu.get("name", "")
        vram = gpu.get("vram_gb")

        if gpu_type == "apple_mps":
            return CheckResult("GPU / AI 硬件", LEVEL_OK,
                               f"Apple Silicon: {gpu_name} (MPS 已就绪)")
        elif gpu_type == "nvidia_cuda":
            cuda = gpu.get("cuda_version", "?")
            vram_str = f", {vram}GB VRAM" if vram else ""
            return CheckResult("GPU / AI 硬件", LEVEL_OK,
                               f"NVIDIA: {gpu_name} (CUDA {cuda}{vram_str})")
        elif gpu_type == "amd_rocm":
            return CheckResult("GPU / AI 硬件", LEVEL_OK,
                               f"AMD: {gpu_name} (ROCm)")
        else:
            return CheckResult("GPU / AI 硬件", LEVEL_INFO,
                               "仅 CPU（AI/ML 项目可能较慢）",
                               detail="大部分项目不需要 GPU，AI/ML 项目推荐 GPU 加速")
    except Exception:
        return CheckResult("GPU / AI 硬件", LEVEL_INFO, "检测跳过")


def _check_disk_space() -> CheckResult:
    """检查磁盘空间"""
    try:
        total, used, free = shutil.disk_usage(str(Path.home()))
        free_gb = free / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        pct = (used / total) * 100

        if free_gb < 1:
            return CheckResult("磁盘空间", LEVEL_ERROR,
                               f"仅剩 {free_gb:.1f}GB 可用空间",
                               fix_hint="磁盘空间不足，安装大项目可能失败")
        elif free_gb < 5:
            return CheckResult("磁盘空间", LEVEL_WARN,
                               f"剩余 {free_gb:.1f}GB / {total_gb:.0f}GB ({pct:.0f}% 已用)",
                               fix_hint="建议保留至少 5GB 空间用于项目安装")
        return CheckResult("磁盘空间", LEVEL_OK,
                           f"剩余 {free_gb:.1f}GB / {total_gb:.0f}GB ({pct:.0f}% 已用)")
    except Exception:
        return CheckResult("磁盘空间", LEVEL_INFO, "检测跳过")


def _check_security() -> CheckResult:
    """安全配置审计"""
    issues = []
    # 检查 .gitinstall 目录权限
    gi_dir = Path.home() / ".gitinstall"
    if gi_dir.exists():
        mode = oct(gi_dir.stat().st_mode)[-3:]
        if mode not in ("700", "755"):
            issues.append(f"~/.gitinstall 目录权限 {mode}，建议 700")

    # 检查是否存在不安全的 API key 存储
    shell_files = [".bashrc", ".zshrc", ".bash_profile", ".profile"]
    for sf in shell_files:
        p = Path.home() / sf
        if p.exists():
            try:
                content = p.read_text(errors="ignore")
                for key_name in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"]:
                    if f"export {key_name}=" in content:
                        # 只是信息提示，不算错误
                        pass
            except Exception:
                pass

    if issues:
        return CheckResult("安全", LEVEL_WARN, f"发现 {len(issues)} 项安全建议",
                          detail="; ".join(issues))
    return CheckResult("安全", LEVEL_OK, "配置安全")


def _check_skills() -> CheckResult:
    """检查 Skills 插件目录"""
    skills_dir = Path.home() / ".gitinstall" / "skills"
    if not skills_dir.exists():
        return CheckResult("Skills 插件", LEVEL_INFO,
                           "未安装任何 Skill（使用 gitinstall skills install 安装）")

    skills = [d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "skill.json").exists()]
    if skills:
        return CheckResult("Skills 插件", LEVEL_OK, f"已安装 {len(skills)} 个: {', '.join(skills[:5])}")
    return CheckResult("Skills 插件", LEVEL_INFO, "Skills 目录存在但无已安装插件")


# ─────────────────────────────────────────────
#  主诊断入口
# ─────────────────────────────────────────────

def run_doctor(verbose: bool = False) -> DoctorReport:
    """运行完整诊断，返回报告"""
    start = time.time()
    report = DoctorReport(timestamp=start)

    # 基础环境
    report.checks.append(_check_python())
    report.checks.append(_check_git())
    report.checks.append(_check_disk_space())

    # 包管理器
    report.checks.extend(_check_package_managers())

    # GPU
    report.checks.append(_check_gpu())

    # 网络与 API
    report.checks.append(_check_github_api())

    # LLM
    report.checks.extend(_check_llm_keys())

    # 数据存储
    report.checks.append(_check_cache())
    report.checks.append(_check_database())

    # 安全
    report.checks.append(_check_security())

    # Skills
    report.checks.append(_check_skills())

    report.duration_ms = (time.time() - start) * 1000
    return report


def format_doctor_report(report: DoctorReport) -> str:
    """格式化诊断报告为终端可读字符串"""
    lines = []
    lines.append("")
    lines.append("🩺 gitinstall doctor — 系统诊断报告")
    lines.append("═" * 55)

    icon_map = {
        LEVEL_OK: "✅",
        LEVEL_WARN: "⚠️ ",
        LEVEL_ERROR: "❌",
        LEVEL_INFO: "ℹ️ ",
    }

    # 按类型分组显示
    for check in report.checks:
        icon = icon_map.get(check.level, "?")
        lines.append(f"  {icon} {check.name}: {check.message}")
        if check.detail:
            lines.append(f"      {check.detail}")
        if check.fix_hint and check.level in (LEVEL_WARN, LEVEL_ERROR):
            lines.append(f"      💡 {check.fix_hint}")

    # 汇总
    lines.append("")
    lines.append("─" * 55)
    total = len(report.checks)
    summary_parts = [f"{report.ok_count} 通过"]
    if report.warn_count:
        summary_parts.append(f"{report.warn_count} 警告")
    if report.error_count:
        summary_parts.append(f"{report.error_count} 错误")

    status = "✅ 系统就绪" if report.all_ok else "⚠️  存在需要关注的问题"
    lines.append(f"  {status} — {total} 项检查: {', '.join(summary_parts)}")
    lines.append(f"  诊断耗时: {report.duration_ms:.0f}ms")
    lines.append("")

    return "\n".join(lines)


def doctor_to_dict(report: DoctorReport) -> dict:
    """将诊断报告转为 JSON 可序列化的 dict"""
    return {
        "status": "ok" if report.all_ok else "warning" if report.error_count == 0 else "error",
        "timestamp": report.timestamp,
        "duration_ms": report.duration_ms,
        "summary": {
            "total": len(report.checks),
            "ok": report.ok_count,
            "warn": report.warn_count,
            "error": report.error_count,
        },
        "checks": [
            {
                "name": c.name,
                "level": c.level,
                "message": c.message,
                "detail": c.detail,
                "fix_hint": c.fix_hint,
            }
            for c in report.checks
        ],
    }
