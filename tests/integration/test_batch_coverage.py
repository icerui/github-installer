#!/usr/bin/env python3
"""
批量覆盖率测试：自动验证 80%+ 开源项目可正常部署
=================================================

核心思路（分类抽样 + 静态验证 + 可选 API 验证）：

  不需要真的安装几百个项目，而是：
  1. 按 GitHub 语言/框架分 15 个大类
  2. 每类选 5-10 个代表性项目（按 star 数排序取热门）
  3. 三级验证：
     Level 0: 静态验证 — 模板匹配 + 步骤合理性检查（无网络，秒级）
     Level 1: API 验证 — 真实 fetch + SmartPlanner（需 GitHub API，1-10s/项目）
     Level 2: LLM 验证 — 加 Ollama 小模型增强（需 Ollama，10-30s/项目）

  Level 0 就能验证模板覆盖率，不消耗任何 API。
  Level 1 用于验证真实项目匹配率。
  Level 2 验证 LLM 对 medium/low 置信度项目的增强效果。

使用方式：
  python3 test_batch_coverage.py                  # Level 0 静态验证
  python3 test_batch_coverage.py --level 1        # Level 1 API 验证
  python3 test_batch_coverage.py --level 2        # Level 2 LLM 增强
  python3 test_batch_coverage.py --category python # 单独测试某分类
  python3 test_batch_coverage.py --summary        # 仅输出覆盖率汇总

什么时候用 LLM？决策树：
  ┌─ 已知项目？ → YES → 直接用数据库（100%准确）→ 不需要LLM
  │               NO ↓
  ├─ 类型模板匹配？ → YES → 用模板（~85%准确）→ 不需要LLM
  │                    NO ↓
  ├─ README可提取命令？ → YES → 提取+LLM增强（~70%准确）→ 建议用LLM
  │                       NO ↓
  └─ 无线索 → LLM必须（~50%准确）→ 必须用LLM
"""

from __future__ import annotations
import argparse
import io
import json
import os
import sys
import time
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── 路径设置 ──────────────────────────────────────
TESTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TESTS_DIR.parent.parent
TOOLS_DIR = ROOT_DIR / "tools"
RESULTS_DIR = TESTS_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(TOOLS_DIR))

# ── 颜色 ──────────────────────────────────────
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; M = "\033[35m"
C = "\033[36m"; BD = "\033[1m"; DM = "\033[2m"; RS = "\033[0m"
PASS = f"{G}✅{RS}"; FAIL = f"{R}❌{RS}"; SKIP = f"{Y}⏭{RS}"
INFO = f"{C}ℹ️{RS}"


# ══════════════════════════════════════════════════════════════════════════════
#  项目分类体系：15 大类 × 每类 5-10 代表项目
#
#  分类依据：GitHub 2024-2025 年度报告语言分布
#  占比来源：GitHub Octoverse 报告 + Stack Overflow 2025 Survey
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProjectSample:
    """一个待测试的项目样本"""
    owner: str
    repo: str
    expect_template: str          # 期望命中的模板类型
    expect_commands_any: list[str] = field(default_factory=list)  # 步骤中至少包含一个
    is_known: bool = False        # 是否在已知项目数据库中
    notes: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


# ── 分类定义 ──────────────────────────────────
CATEGORIES: dict[str, dict[str, Any]] = {
    # ====================================================================
    # 第一梯队：覆盖 GitHub 80%+ 项目
    # ====================================================================
    "python_general": {
        "name": "Python 通用",
        "github_share": "22%",
        "desc": "非 ML 的 Python 项目（Web/CLI/工具/自动化）",
        "template": "type_template_python",
        "samples": [
            ProjectSample("pallets", "flask", "type_template_python",
                          ["pip install", "requirements"]),
            ProjectSample("django", "django", "type_template_python",
                          ["pip install"]),
            ProjectSample("tiangolo", "fastapi", "type_template_python",
                          ["pip install"]),
            ProjectSample("psf", "requests", "type_template_python",
                          ["pip install"]),
            ProjectSample("scrapy", "scrapy", "type_template_python",
                          ["pip install"]),
            ProjectSample("celery", "celery", "type_template_python",
                          ["pip install"]),
            ProjectSample("encode", "httpx", "type_template_python",
                          ["pip install"]),
        ],
    },
    "python_ml": {
        "name": "Python ML/AI",
        "github_share": "8%",
        "desc": "深度学习、AI 相关 Python 项目",
        "template": "type_template_python_ml",
        "samples": [
            ProjectSample("huggingface", "transformers", "known_project",
                          ["pip install", "torch"], is_known=True),
            ProjectSample("ultralytics", "ultralytics", "known_project",
                          ["pip install"], is_known=True),
            ProjectSample("Lightning-AI", "pytorch-lightning", "type_template_python_ml",
                          ["pip install", "torch"]),
            ProjectSample("langchain-ai", "langchain", "type_template_python",
                          ["pip install"]),
            ProjectSample("openai", "whisper", "type_template_python_ml",
                          ["pip install", "torch"]),
            ProjectSample("huggingface", "diffusers", "type_template_conda",
                          ["git clone"]),
        ],
    },
    "javascript_node": {
        "name": "JavaScript / Node.js",
        "github_share": "20%",
        "desc": "前后端 JS/TS 项目",
        "template": "type_template_node",
        "samples": [
            ProjectSample("facebook", "react", "type_template_node",
                          ["npm install", "yarn"]),
            ProjectSample("vuejs", "core", "type_template_node",
                          ["npm install", "pnpm"]),
            ProjectSample("vercel", "next.js", "type_template_node",
                          ["npm install"]),
            ProjectSample("expressjs", "express", "type_template_node",
                          ["npm install"]),
            ProjectSample("tailwindlabs", "tailwindcss", "type_template_node",
                          ["npm install"]),
            ProjectSample("lobehub", "lobe-chat", "known_project",
                          ["git clone"], is_known=True),
            ProjectSample("n8n-io", "n8n", "known_project",
                          ["npm install", "docker"], is_known=True),
        ],
    },
    "java": {
        "name": "Java / Kotlin",
        "github_share": "9%",
        "desc": "Maven/Gradle 项目（Spring Boot, Android 等）",
        "template": "type_template_java",
        "samples": [
            ProjectSample("spring-projects", "spring-boot", "type_template_java",
                          ["mvn", "gradlew", "gradle"]),
            ProjectSample("elastic", "elasticsearch", "type_template_java",
                          ["gradlew", "gradle"]),
            ProjectSample("apache", "kafka", "type_template_java",
                          ["gradlew", "gradle"]),
            ProjectSample("google", "guava", "type_template_java",
                          ["mvn", "gradlew"]),
            ProjectSample("ReactiveX", "RxJava", "type_template_java",
                          ["gradlew"]),
        ],
    },
    "docker_compose": {
        "name": "Docker / 容器项目",
        "github_share": "5%",
        "desc": "以 Docker 为主要运行方式的项目",
        "template": "type_template_docker",
        "samples": [
            ProjectSample("portainer", "portainer", "known_project",
                          ["docker"], is_known=True),
            ProjectSample("searxng", "searxng", "known_project",
                          ["docker"], is_known=True),
            ProjectSample("immich-app", "immich", "known_project",
                          ["docker"], is_known=True),
            ProjectSample("traefik", "traefik", "type_template_docker",
                          ["docker"]),
            ProjectSample("grafana", "grafana", "type_template_docker",
                          ["docker"]),
        ],
    },
    "go": {
        "name": "Go",
        "github_share": "5%",
        "desc": "Go 语言工具/服务",
        "template": "type_template_go",
        "samples": [
            ProjectSample("cli", "cli", "known_project",
                          ["brew install", "go install"], is_known=True),
            ProjectSample("gohugoio", "hugo", "type_template_go",
                          ["go build ./..."]),
            ProjectSample("junegunn", "fzf", "type_template_go",
                          ["go install", "brew"]),
            ProjectSample("derailed", "k9s", "type_template_go",
                          ["go install", "brew"]),
            ProjectSample("FiloSottile", "mkcert", "type_template_go",
                          ["go install", "brew"]),
        ],
    },
    "rust": {
        "name": "Rust",
        "github_share": "3%",
        "desc": "Rust 命令行工具/系统软件",
        "template": "type_template_rust",
        "samples": [
            ProjectSample("BurntSushi", "ripgrep", "known_project",
                          ["brew install", "cargo install"], is_known=True),
            ProjectSample("sharkdp", "bat", "type_template_rust",
                          ["cargo install"]),
            ProjectSample("sharkdp", "fd", "type_template_rust",
                          ["cargo install"]),
            ProjectSample("astral-sh", "ruff", "type_template_rust",
                          ["cargo install", "pip install"]),
            ProjectSample("denoland", "deno", "type_template_rust",
                          ["cargo install", "curl"]),
        ],
    },
    # ====================================================================
    # 第二梯队：覆盖长尾项目
    # ====================================================================
    "cpp_cmake": {
        "name": "C/C++ (CMake)",
        "github_share": "6%",
        "desc": "使用 CMake 构建的 C/C++ 项目",
        "template": "type_template_cmake",
        "samples": [
            ProjectSample("ggerganov", "llama.cpp", "known_project",
                          ["git clone", "cmake", "make"], is_known=True),
            ProjectSample("opencv", "opencv", "type_template_cmake",
                          ["cmake"]),
            ProjectSample("nlohmann", "json", "type_template_cmake",
                          ["cmake"]),
            ProjectSample("grpc", "grpc", "type_template_cmake",
                          ["cmake"]),
        ],
    },
    "c_make": {
        "name": "C/C++ (Makefile/autotools)",
        "github_share": "3%",
        "desc": "使用 Makefile/configure 构建的 C/C++ 项目",
        "template": "type_template_make",
        "samples": [
            ProjectSample("redis", "redis", "type_template_make",
                          ["make"]),
            ProjectSample("nginx", "nginx", "type_template_make",
                          ["make", "configure"]),
            ProjectSample("vim", "vim", "type_template_make",
                          ["make"]),
            ProjectSample("git", "git", "type_template_make",
                          ["make"]),
        ],
    },
    "ruby": {
        "name": "Ruby",
        "github_share": "3%",
        "desc": "Ruby / Rails 项目",
        "template": "type_template_ruby",
        "samples": [
            ProjectSample("rails", "rails", "type_template_ruby",
                          ["bundle install"]),
            ProjectSample("jekyll", "jekyll", "type_template_ruby",
                          ["bundle install"]),
            ProjectSample("Homebrew", "brew", "type_template_ruby",
                          ["bundle install", "ruby"]),
            ProjectSample("hashicorp", "vagrant", "type_template_ruby",
                          ["bundle install"]),
        ],
    },
    "php": {
        "name": "PHP",
        "github_share": "4%",
        "desc": "PHP / Laravel / WordPress 项目",
        "template": "type_template_php",
        "samples": [
            ProjectSample("laravel", "laravel", "type_template_php",
                          ["composer install"]),
            ProjectSample("WordPress", "WordPress", "type_template_php",
                          ["composer"]),
            ProjectSample("symfony", "symfony", "type_template_php",
                          ["composer install"]),
            ProjectSample("filamentphp", "filament", "type_template_php",
                          ["composer install"]),
        ],
    },
    "dotnet": {
        "name": ".NET / C#",
        "github_share": "3%",
        "desc": ".NET / C# / ASP.NET 项目",
        "template": "type_template_dotnet",
        "samples": [
            ProjectSample("dotnet", "runtime", "type_template_dotnet",
                          ["dotnet"]),
            ProjectSample("dotnet", "aspnetcore", "type_template_dotnet",
                          ["dotnet"]),
            ProjectSample("jellyfin", "jellyfin", "type_template_dotnet",
                          ["dotnet"]),
            ProjectSample("bitwarden", "server", "type_template_dotnet",
                          ["dotnet"]),
        ],
    },
    "swift": {
        "name": "Swift",
        "github_share": "2%",
        "desc": "Swift 开源项目（SPM）",
        "template": "type_template_swift",
        "samples": [
            ProjectSample("Alamofire", "Alamofire", "type_template_swift",
                          ["swift build"]),
            ProjectSample("vapor", "vapor", "type_template_swift",
                          ["swift build"]),
            ProjectSample("apple", "swift-nio", "type_template_swift",
                          ["swift build"]),
        ],
    },
    # ====================================================================
    # 特殊类型
    # ====================================================================
    "known_ai_tools": {
        "name": "热门 AI/LLM 工具（已知数据库）",
        "github_share": "N/A",
        "desc": "Star 数最高的 AI 工具，必须精确覆盖",
        "template": "known_project",
        "samples": [
            ProjectSample("comfyanonymous", "ComfyUI", "known_project",
                          ["git clone", "torch"], is_known=True),
            ProjectSample("AUTOMATIC1111", "stable-diffusion-webui", "known_project",
                          ["git clone"], is_known=True),
            ProjectSample("ollama", "ollama", "known_project",
                          ["brew install", "curl"], is_known=True),
            ProjectSample("open-webui", "open-webui", "known_project",
                          ["pip install", "docker"], is_known=True),
            ProjectSample("hiyouga", "LLaMA-Factory", "known_project",
                          ["git clone", "torch"], is_known=True),
            ProjectSample("zhayujie", "chatgpt-on-wechat", "known_project",
                          ["git clone", "requirements"], is_known=True),
            ProjectSample("home-assistant", "core", "known_project",
                          ["docker", "pip install"], is_known=True),
        ],
    },
    "conda_projects": {
        "name": "Conda 环境项目",
        "github_share": "2%",
        "desc": "使用 environment.yml 的科学计算项目",
        "template": "type_template_conda",
        "samples": [
            ProjectSample("huggingface", "diffusers", "type_template_conda",
                          ["conda", "git clone"]),
            ProjectSample("facebookresearch", "detectron2", "known_project",
                          ["git clone"], is_known=True),
        ],
    },
}

# 统计总占比
TOTAL_SHARE = sum(
    float(cat["github_share"].replace("%", "").replace("N/A", "0"))
    for cat in CATEGORIES.values()
)


# ══════════════════════════════════════════════════════════════════════════════
#  Level 0: 静态验证（无网络）
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SampleResult:
    sample: ProjectSample
    category: str
    level: int = 0
    passed: bool = False
    strategy: str = ""
    confidence: str = ""
    steps_count: int = 0
    has_expected_cmd: bool = False
    llm_used: str = ""
    error: str = ""
    duration: float = 0.0
    needs_llm: bool = False  # 是否需要 LLM 才能达到好的结果


def verify_level0_static(sample: ProjectSample, category_info: dict) -> SampleResult:
    """
    Level 0: 纯静态验证 — 不调 API，检查项目是否能被模板体系覆盖。

    验证规则：
      1. 已知项目 → 必须在 _KNOWN_PROJECTS 中
      2. 类型模板项目 → 检查模板是否存在
    """
    from planner import SmartPlanner, _KNOWN_PROJECTS

    r = SampleResult(sample=sample, category=category_info["name"], level=0)
    key = sample.full_name.lower()

    if sample.is_known:
        # 验证：是否在已知数据库
        found = key in _KNOWN_PROJECTS
        r.passed = found
        r.strategy = "known_project" if found else "MISSING from known_projects"
        r.confidence = "high" if found else "ERROR"
        r.needs_llm = False
        if not found:
            r.error = f"{sample.full_name} 标记为 is_known 但不在 _KNOWN_PROJECTS"
    else:
        # 验证：对应模板方法是否存在
        template = sample.expect_template
        planner = SmartPlanner()
        method_map = {
            "type_template_python": "_plan_python",
            "type_template_python_ml": "_plan_python_ml",
            "type_template_conda": "_plan_conda",
            "type_template_node": "_plan_node",
            "type_template_docker": "_plan_docker",
            "type_template_rust": "_plan_rust",
            "type_template_go": "_plan_go",
            "type_template_cmake": "_plan_cmake",
            "type_template_java": "_plan_java",
            "type_template_make": "_plan_make",
            "type_template_ruby": "_plan_ruby",
            "type_template_php": "_plan_php",
            "type_template_dotnet": "_plan_dotnet",
            "type_template_swift": "_plan_swift",
        }
        method_name = method_map.get(template)
        if method_name and hasattr(planner, method_name):
            r.passed = True
            r.strategy = template
            r.confidence = "medium"
            r.needs_llm = False  # 模板存在，不强制需要 LLM
        else:
            r.passed = False
            r.strategy = f"MISSING template: {template}"
            r.confidence = "low"
            r.needs_llm = True
            r.error = f"模板方法 {method_name} 不存在"
    return r


# ══════════════════════════════════════════════════════════════════════════════
#  Level 1: API 验证（真实 GitHub + SmartPlanner）
# ══════════════════════════════════════════════════════════════════════════════

def verify_level1_api(sample: ProjectSample, category_info: dict) -> SampleResult:
    """Level 1: 真实 fetch + SmartPlanner（无 LLM）"""
    import main as _main

    r = SampleResult(sample=sample, category=category_info["name"], level=1)
    t0 = time.time()

    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            result = _main.cmd_plan(sample.full_name, llm_force="none")
        r.duration = time.time() - t0

        if result.get("status") != "ok":
            msg = result.get("message", "")
            if any(k in msg.lower() for k in ["rate limit", "频率超限", "403"]):
                r.error = "RATE_LIMITED"
                return r
            r.error = msg
            return r

        plan = result.get("plan", {})
        r.strategy = plan.get("strategy", "")
        r.confidence = result.get("confidence", "")
        r.steps_count = len(plan.get("steps", []))

        # 检查步骤中是否有期望的命令
        all_cmds = " ".join(s.get("command", "") for s in plan.get("steps", [])).lower()
        if sample.expect_commands_any:
            r.has_expected_cmd = any(kw.lower() in all_cmds for kw in sample.expect_commands_any)
        else:
            r.has_expected_cmd = True  # 无期望则通过

        # 通过判定
        r.passed = (
            r.steps_count > 0
            and r.has_expected_cmd
            and r.confidence in ("high", "medium")
        )

        # 判断是否需要 LLM
        r.needs_llm = r.confidence == "low" or not r.passed

    except Exception as e:
        r.duration = time.time() - t0
        r.error = str(e)
    return r


# ══════════════════════════════════════════════════════════════════════════════
#  Level 2: LLM 增强验证（Ollama 小模型）
# ══════════════════════════════════════════════════════════════════════════════

def verify_level2_llm(sample: ProjectSample, category_info: dict) -> SampleResult:
    """Level 2: 真实 fetch + SmartPlanner + Ollama LLM 增强"""
    import main as _main

    r = SampleResult(sample=sample, category=category_info["name"], level=2)
    t0 = time.time()

    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            result = _main.cmd_plan(sample.full_name, llm_force="ollama")
        r.duration = time.time() - t0

        if result.get("status") != "ok":
            msg = result.get("message", "")
            if any(k in msg.lower() for k in ["rate limit", "频率超限", "403"]):
                r.error = "RATE_LIMITED"
                return r
            r.error = msg
            return r

        plan = result.get("plan", {})
        r.strategy = plan.get("strategy", "")
        r.confidence = result.get("confidence", "")
        r.steps_count = len(plan.get("steps", []))
        r.llm_used = result.get("llm_used", "")

        all_cmds = " ".join(s.get("command", "") for s in plan.get("steps", [])).lower()
        if sample.expect_commands_any:
            r.has_expected_cmd = any(kw.lower() in all_cmds for kw in sample.expect_commands_any)
        else:
            r.has_expected_cmd = True

        r.passed = r.steps_count > 0 and r.confidence in ("high", "medium")
        r.needs_llm = "llm_enhanced" in r.strategy

    except Exception as e:
        r.duration = time.time() - t0
        r.error = str(e)
    return r


# ══════════════════════════════════════════════════════════════════════════════
#  覆盖率报告生成
# ══════════════════════════════════════════════════════════════════════════════

def print_coverage_report(all_results: dict[str, list[SampleResult]], level: int):
    """打印分类覆盖率报告"""

    total_samples = 0
    total_passed = 0
    total_rate_limited = 0
    total_need_llm = 0
    cat_stats = []

    print(f"\n{BD}{'═'*72}{RS}")
    print(f"{BD}  覆盖率报告 — Level {level}"
          f" {'(静态验证)' if level == 0 else '(API验证)' if level == 1 else '(LLM增强)'}{RS}")
    print(f"{'═'*72}")

    for cat_key, results in all_results.items():
        cat = CATEGORIES[cat_key]
        n = len(results)
        rate_limited = sum(1 for r in results if r.error == "RATE_LIMITED")
        effective = n - rate_limited
        passed = sum(1 for r in results if r.passed)
        need_llm = sum(1 for r in results if r.needs_llm and r.passed)
        rate = (passed / effective * 100) if effective > 0 else 0

        total_samples += n
        total_passed += passed
        total_rate_limited += rate_limited
        total_need_llm += need_llm

        # 颜色
        rc = G if rate >= 80 else (Y if rate >= 60 else R)
        share = cat.get("github_share", "?")

        print(f"\n  {BD}{cat['name']}{RS} {DM}(GitHub ~{share}){RS}")

        for r in results:
            if r.error == "RATE_LIMITED":
                icon = SKIP
                detail = "API 限速"
            elif r.passed:
                icon = PASS
                detail = f"strategy={r.strategy}"
                if r.needs_llm:
                    detail += f" {Y}+LLM{RS}"
            else:
                icon = FAIL
                detail = r.error or f"strategy={r.strategy} confidence={r.confidence}"
            print(f"    {icon} {r.sample.full_name:40s} {DM}{detail}{RS}")

        print(f"    {'─'*60}")
        print(f"    {rc}{BD}{passed}/{effective}{RS} 通过"
              f"  ({rc}{rate:.0f}%{RS})"
              + (f"  {DM}+{rate_limited} 限速跳过{RS}" if rate_limited else "")
              + (f"  {Y}{need_llm} 需LLM{RS}" if need_llm else ""))
        cat_stats.append((cat_key, cat["name"], share, passed, effective, rate, need_llm))

    # ── 总汇总 ──
    effective_total = total_samples - total_rate_limited
    total_rate = (total_passed / effective_total * 100) if effective_total > 0 else 0
    rc = G if total_rate >= 80 else (Y if total_rate >= 60 else R)

    print(f"\n{'═'*72}")
    print(f"{BD}  总覆盖率：{rc}{total_passed}/{effective_total} = {total_rate:.1f}%{RS}")
    print(f"  目标: ≥80%  {'✅ 达标' if total_rate >= 80 else '❌ 未达标'}")
    if total_rate_limited:
        print(f"  {DM}{total_rate_limited} 个样本因 API 限速跳过（不计入通过率）{RS}")
    if total_need_llm:
        print(f"  {Y}{total_need_llm} 个样本需要 LLM 增强才有好结果{RS}")

    # ── LLM 使用决策总结 ──
    print(f"\n{'─'*72}")
    print(f"{BD}  📊 LLM 使用决策总结{RS}")
    print(f"  ┌{'─'*68}┐")
    print(f"  │ {'分类':20s} {'无需LLM':>8s} {'建议LLM':>8s} {'必须LLM':>8s} {'通过率':>8s} │")
    print(f"  ├{'─'*68}┤")
    for cat_key, name, share, passed, eff, rate, need_llm in cat_stats:
        no_llm = passed - need_llm
        print(f"  │ {name:20s} {no_llm:>8d} {need_llm:>8d} {'':>8s} {rate:>7.0f}% │")
    print(f"  └{'─'*68}┘")
    print(f"\n  决策规则：")
    print(f"  • confidence=high（已知项目）→ {G}不需要 LLM{RS}")
    print(f"  • confidence=medium（类型模板）→ {G}不需要 LLM{RS}（模板已足够）")
    print(f"  • confidence=medium + 复杂项目 → {Y}建议用 LLM{RS}（提升步骤质量）")
    print(f"  • confidence=low（README提取）→ {R}需要 LLM{RS}（仅提取不完整）")
    print(f"{'═'*72}\n")

    return total_rate


def save_coverage_report(all_results: dict[str, list[SampleResult]], level: int) -> Path:
    """保存 JSON 格式报告"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"coverage_L{level}_{ts}.json"

    report = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "categories": {},
    }
    total_s, total_p = 0, 0
    for cat_key, results in all_results.items():
        cat = CATEGORIES[cat_key]
        eff = [r for r in results if r.error != "RATE_LIMITED"]
        passed = sum(1 for r in eff if r.passed)
        report["categories"][cat_key] = {
            "name": cat["name"],
            "github_share": cat.get("github_share", ""),
            "total": len(results),
            "passed": passed,
            "effective": len(eff),
            "rate": round(passed / len(eff) * 100, 1) if eff else 0,
            "samples": [
                {
                    "project": r.sample.full_name,
                    "passed": r.passed,
                    "strategy": r.strategy,
                    "confidence": r.confidence,
                    "steps": r.steps_count,
                    "needs_llm": r.needs_llm,
                    "error": r.error,
                    "duration": round(r.duration, 2),
                }
                for r in results
            ],
        }
        total_s += len(eff)
        total_p += passed

    report["total_samples"] = total_s
    report["total_passed"] = total_p
    report["total_rate"] = round(total_p / total_s * 100, 1) if total_s else 0
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return path


# ══════════════════════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="批量覆盖率测试")
    parser.add_argument("--level", type=int, default=0, choices=[0, 1, 2],
                        help="验证级别: 0=静态, 1=API, 2=LLM")
    parser.add_argument("--category", help="只测试某个分类（如 python_general）")
    parser.add_argument("--summary", action="store_true", help="仅输出覆盖率汇总")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Level 1/2 每个项目间隔秒（默认1.5）")
    args = parser.parse_args()

    # 选择分类
    if args.category:
        if args.category not in CATEGORIES:
            print(f"{R}未知分类: {args.category}{RS}")
            print(f"可选: {', '.join(CATEGORIES.keys())}")
            sys.exit(1)
        cats = {args.category: CATEGORIES[args.category]}
    else:
        cats = CATEGORIES

    # 选择验证函数
    verify_fn = {
        0: verify_level0_static,
        1: verify_level1_api,
        2: verify_level2_llm,
    }[args.level]

    # 头部信息
    total_count = sum(len(cat["samples"]) for cat in cats.values())
    print(f"\n{BD}{'═'*72}{RS}")
    print(f"{BD}  github-installer 批量覆盖率测试{RS}")
    print(f"  验证级别：Level {args.level}"
          f" {'(静态/无网络)' if args.level == 0 else '(API/需网络)' if args.level == 1 else '(LLM/需Ollama)'}")
    print(f"  分类数量：{len(cats)}")
    print(f"  项目总数：{total_count}")
    print(f"  GitHub 语言分布覆盖率：~{TOTAL_SHARE:.0f}%")
    if args.level >= 1:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://api.github.com/rate_limit",
                headers={"User-Agent": "github-installer-test/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                quota = json.loads(resp.read())["resources"]["core"]
                rem = quota["remaining"]
                color = G if rem > total_count * 3 else (Y if rem > total_count else R)
                print(f"  GitHub API：{color}{rem}/{quota['limit']}{RS} 剩余")
                if rem < total_count * 2:
                    print(f"  {Y}⚠️ API 配额可能不够，部分测试会被跳过{RS}")
        except Exception:
            pass
    print(f"{'═'*72}")

    # 运行测试
    all_results: dict[str, list[SampleResult]] = {}
    for cat_key, cat in cats.items():
        print(f"\n{BD}{M}【{cat['name']}】{RS} {DM}{cat['desc']}{RS}")

        results = []
        for i, sample in enumerate(cat["samples"]):
            print(f"  {DM}→ {sample.full_name}{RS}", end="", flush=True)
            r = verify_fn(sample, cat)
            results.append(r)

            if r.error == "RATE_LIMITED":
                print(f" {SKIP} 限速")
            elif r.passed:
                extra = f" {Y}+LLM{RS}" if r.needs_llm else ""
                print(f" {PASS} {r.strategy}{extra} ({r.duration:.1f}s)" if r.duration else f" {PASS} {r.strategy}")
            else:
                print(f" {FAIL} {r.error or r.strategy}")

            # API 间隔
            if args.level >= 1 and i < len(cat["samples"]) - 1 and r.error != "RATE_LIMITED":
                time.sleep(args.delay)

        all_results[cat_key] = results

    # 报告
    total_rate = print_coverage_report(all_results, args.level)
    report_path = save_coverage_report(all_results, args.level)
    print(f"  {INFO} 详细报告：{report_path.relative_to(ROOT_DIR)}")

    sys.exit(0 if total_rate >= 80 else 1)


if __name__ == "__main__":
    main()
