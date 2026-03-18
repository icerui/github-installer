"""
onboard.py - gitinstall 交互式引导向导
========================================

灵感来源：OpenClaw `openclaw onboard` 交互式设置流程

功能：
  1. 首次使用引导：检测环境 → 配置推荐 → API Key 设置
  2. 自动生成 ~/.gitinstall/config.json 配置文件
  3. 初始化内建 Skills
  4. 可选：安装示例项目验证环境

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


# ── 配置文件路径 ──
CONFIG_DIR = Path.home() / ".gitinstall"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _input_with_default(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        display = f"{prompt} [{default}]: "
    else:
        display = f"{prompt}: "
    try:
        val = input(display).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def _yes_no(prompt: str, default: bool = True) -> bool:
    """是/否确认"""
    hint = "Y/n" if default else "y/N"
    try:
        val = input(f"{prompt} ({hint}): ").strip().lower()
        if not val:
            return default
        return val in ("y", "yes", "是")
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def _step_header(step: int, total: int, title: str):
    """步骤标题"""
    print(f"\n{'─' * 50}")
    print(f"  📍 步骤 {step}/{total}: {title}")
    print(f"{'─' * 50}")


def run_onboard():
    """运行交互式引导向导"""
    total_steps = 5

    print()
    print("🦀 欢迎使用 gitinstall！")
    print("═" * 50)
    print("  让你轻松安装 GitHub/GitLab/Bitbucket/Gitee 开源项目")
    print("  本向导将帮助您完成初始配置")
    print("═" * 50)

    config = {}

    # ──────────── 步骤 1：环境检测 ────────────
    _step_header(1, total_steps, "环境检测")
    print("  正在检测您的系统环境...")
    print()

    try:
        from detector import EnvironmentDetector
        env = EnvironmentDetector().detect()

        os_info = env.get("os", {})
        gpu_info = env.get("gpu", {})
        runtimes = env.get("runtimes", {})

        print(f"  🖥️  系统: {os_info.get('type', '?')} {os_info.get('version', '')}")
        print(f"  🏗️  架构: {os_info.get('arch', '?')}")
        if os_info.get("chip"):
            print(f"  💻 芯片: {os_info['chip']}")
        if gpu_info.get("name"):
            print(f"  🎮 GPU: {gpu_info['name']} ({gpu_info.get('type', '')})")
        else:
            print(f"  🎮 GPU: 仅 CPU")

        # 检测已有工具
        tools_found = []
        for name, info in runtimes.items():
            if isinstance(info, dict) and info.get("available"):
                ver = info.get("version", "")
                tools_found.append(f"{name} {ver}".strip())
        if tools_found:
            print(f"  🔧 已安装: {', '.join(tools_found[:8])}")

        config["detected_env"] = {
            "os": os_info.get("type"),
            "arch": os_info.get("arch"),
            "gpu": gpu_info.get("type"),
        }
    except Exception as e:
        print(f"  ⚠️  环境检测出错: {e}")
        print("  （不影响使用，继续配置）")

    print("\n  ✅ 环境检测完成！")

    # ──────────── 步骤 2：API Key 配置 ────────────
    _step_header(2, total_steps, "API Key 配置（可选）")
    print("  gitinstall 无需任何 API Key 即可工作！")
    print("  内置 SmartPlanner 覆盖 80+ 已知项目 + 50+ 语言模板")
    print()
    print("  可选增强：")
    print("    • GITHUB_TOKEN  → 提升 API 配额 60→5000 次/小时")
    print("    • LLM API Key   → AI 增强安装计划")
    print()

    if _yes_no("  是否现在配置 GITHUB_TOKEN？", default=False):
        token = _input_with_default("  输入 GitHub Token (ghp_...)")
        if token:
            config["github_token"] = token
            print("  ✅ 已记录（将写入配置文件，不写入 shell profile）")

    print()
    if _yes_no("  是否配置 LLM API Key？（用于 AI 增强安装分析）", default=False):
        print()
        print("  支持的 LLM 服务：")
        print("    1. Anthropic Claude  (推荐)")
        print("    2. OpenAI GPT")
        print("    3. Groq Llama 3.3   (免费)")
        print("    4. DeepSeek          (便宜)")
        print("    5. Google Gemini")
        print("    6. 本地 Ollama       (完全免费)")
        print("    7. 跳过")
        print()
        choice = _input_with_default("  选择 (1-7)", "7")

        key_map = {
            "1": ("ANTHROPIC_API_KEY", "Anthropic API Key (sk-ant-...)"),
            "2": ("OPENAI_API_KEY", "OpenAI API Key (sk-...)"),
            "3": ("GROQ_API_KEY", "Groq API Key (gsk_...)"),
            "4": ("DEEPSEEK_API_KEY", "DeepSeek API Key"),
            "5": ("GEMINI_API_KEY", "Google Gemini API Key"),
        }

        if choice in key_map:
            env_var, prompt = key_map[choice]
            key_val = _input_with_default(f"  输入 {prompt}")
            if key_val:
                config["llm_key"] = {env_var: key_val}
                print(f"  ✅ 已记录 {env_var}")
        elif choice == "6":
            print("  ℹ️  确保 Ollama 运行中: ollama serve")
            print("  gitinstall 会自动检测 localhost:11434")
            config["llm_preference"] = "ollama"
        else:
            print("  ⏭️  跳过 LLM 配置")

    # ──────────── 步骤 3：安装偏好 ────────────
    _step_header(3, total_steps, "安装偏好")

    print("  默认安装目录:")
    install_dir = _input_with_default("  项目安装目录", "~/projects")
    config["default_install_dir"] = install_dir

    print()
    print("  安装模式:")
    print("    1. 安全模式（默认）— 危险命令需确认")
    print("    2. 快速模式 — 自动跳过确认（信任所有项目）")
    print("    3. 严格模式 — 仅允许已知安全项目")
    mode = _input_with_default("  选择 (1-3)", "1")
    config["install_mode"] = {"1": "safe", "2": "fast", "3": "strict"}.get(mode, "safe")

    print()
    if _yes_no("  是否启用安装遥测？（匿名统计，帮助改进 SmartPlanner）", default=True):
        config["telemetry"] = True
    else:
        config["telemetry"] = False

    # ──────────── 步骤 4：初始化 Skills ────────────
    _step_header(4, total_steps, "Skills 插件初始化")
    print("  Skills 是社区贡献的安装策略扩展")
    print("  内建 Skills: auto-venv, docker-prefer, gpu-optimizer, batch-install 等")
    print()

    if _yes_no("  是否初始化内建 Skills？", default=True):
        try:
            from skills import ensure_builtin_skills, SkillManager
            ensure_builtin_skills()
            mgr = SkillManager()
            skills = mgr.list_skills()
            print(f"  ✅ 已初始化 {len(skills)} 个内建 Skills")
            config["skills_initialized"] = True
        except Exception as e:
            print(f"  ⚠️  Skills 初始化失败: {e}")
    else:
        print("  ⏭️  跳过（稍后可用 'gitinstall skills init' 初始化）")

    # ──────────── 步骤 5：保存 + 验证 ────────────
    _step_header(5, total_steps, "保存配置")

    # 保存配置
    config["version"] = "1.0"
    config["onboard_completed"] = True
    config["onboard_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 安全权限
    try:
        os.chmod(CONFIG_DIR, 0o700)
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass

    print(f"  ✅ 配置已保存到 {CONFIG_FILE}")

    # 验证安装
    print()
    if _yes_no("  是否运行 Doctor 诊断验证环境？", default=True):
        try:
            from doctor import run_doctor, format_doctor_report
            report = run_doctor()
            print(format_doctor_report(report))
        except Exception as e:
            print(f"  ⚠️  诊断失败: {e}")

    # 完成
    print()
    print("🎉 配置完成！")
    print("═" * 50)
    print()
    print("  快速开始：")
    print("    gitinstall install comfyanonymous/ComfyUI    # 安装项目")
    print("    gitinstall plan pytorch/pytorch              # 查看安装计划")
    print("    gitinstall doctor                            # 诊断系统")
    print("    gitinstall skills list                       # 查看 Skills")
    print("    gitinstall web                               # 启动 Web UI")
    print()
    print("  支持多平台：")
    print("    gitinstall install gitlab.com/user/repo      # GitLab")
    print("    gitinstall install gitee.com/user/repo       # Gitee 国内")
    print("    gitinstall install bitbucket.org/user/repo   # Bitbucket")
    print()
    print("  文档：https://github.com/yourusername/gitinstall")
    print()


def load_config() -> dict:
    """加载用户配置（如无则返回空 dict）"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def is_first_run() -> bool:
    """检查是否首次运行"""
    config = load_config()
    return not config.get("onboard_completed", False)
