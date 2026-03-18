#!/usr/bin/env python3
"""
validate_top100.py - GitHub Top 100 持续兼容性验证
===================================================

持续验证 github-installer 对 GitHub Top 100 热门项目的安装兼容性。
每次运行自动爬取最新排名，检测新入榜项目，生成兼容性报告。

功能：
  1. 爬取 GitHub Top 100 热门开源项目
  2. 对每个项目生成安装计划（macOS/Linux/Windows 三平台）
  3. 验证计划的安全性与合理性
  4. 检测排名变动，标记新入榜项目并优先验证
  5. 输出覆盖率报告（JSON + 终端可读）

用法：
  python tools/validate_top100.py                   # 完整验证
  python tools/validate_top100.py --quick            # 仅验证新入榜项目
  python tools/validate_top100.py --report           # 仅查看上次报告
  python tools/validate_top100.py --category AI      # 验证指定分类

  或通过 main.py 子命令：
  python tools/main.py validate                      # 完整验证
  python tools/main.py validate --quick              # 仅验证新入榜
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import io
import contextlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径设置 ──
_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_RESULTS_DIR = _ROOT_DIR / "tests" / "results"
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(_THIS_DIR))

from detector import EnvironmentDetector
from fetcher import fetch_project
from planner import SmartPlanner

# ── 颜色 ──
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; C = "\033[36m"
BD = "\033[1m"; DM = "\033[2m"; RS = "\033[0m"

# ── 报告路径 ──
_REPORT_FILE = _RESULTS_DIR / "top100_report.json"
_HISTORY_FILE = _RESULTS_DIR / "top100_history.json"

# ── 三平台模拟环境 ──
def _make_env(os_type: str, arch: str = "x86_64",
              gpu_type: str = "none", chip: str = "") -> dict:
    return {
        "os": {
            "type": os_type,
            "version": "14.0" if os_type == "macos" else "22.04" if os_type == "linux" else "11",
            "arch": arch, "chip": chip,
        },
        "hardware": {"cpu_count": 8, "memory_gb": 32},
        "gpu": {
            "type": gpu_type,
            "name": "NVIDIA RTX 4090" if gpu_type == "nvidia"
                    else "Apple M3" if gpu_type == "apple_mps" else "",
            "cuda_version": "12.1" if gpu_type == "nvidia" else "",
        },
        "runtimes": {
            "python": {"available": True, "version": "3.11.0"},
            "node": {"available": True, "version": "20.0.0"},
        },
        "package_managers": {
            "pip": {"available": True},
            "npm": {"available": True},
            "brew": {"available": os_type == "macos"},
            "apt": {"available": os_type == "linux"},
            "winget": {"available": os_type == "windows"},
            "cargo": {"available": True},
        },
        "disk": {"free_gb": 100},
    }

PLATFORMS = {
    "macOS-ARM":   _make_env("macos", "arm64", "apple_mps", "Apple M3 Ultra"),
    "Linux-CUDA":  _make_env("linux", "x86_64", "nvidia"),
    "Windows-CPU": _make_env("windows", "x86_64", "none"),
}

# ── 安全验证 ──
_DANGEROUS_PATTERNS = ["rm -rf /", "mkfs", "dd if=", ":(){", "fork bomb",
                       "chmod 777 /", "> /dev/sd"]

def _validate_steps(steps: list[dict], os_type: str) -> list[str]:
    """验证安装步骤的合理性，返回问题列表"""
    issues = []
    for i, s in enumerate(steps):
        cmd = s.get("command", "")
        if not cmd:
            continue
        for danger in _DANGEROUS_PATTERNS:
            if danger in cmd:
                issues.append(f"Step {i}: 危险命令 '{danger}'")
        if os_type == "windows":
            if cmd.startswith("sudo "):
                issues.append(f"Step {i}: Windows 不应有 sudo")
            if "apt " in cmd or "apt-get " in cmd:
                issues.append(f"Step {i}: Windows 不应有 apt")
            if "brew " in cmd:
                issues.append(f"Step {i}: Windows 不应有 brew")
        elif os_type == "macos":
            if "apt " in cmd or "apt-get " in cmd:
                issues.append(f"Step {i}: macOS 不应有 apt")
            if "winget " in cmd or "choco " in cmd:
                issues.append(f"Step {i}: macOS 不应有 winget/choco")
        elif os_type == "linux":
            if "winget " in cmd or "choco " in cmd:
                issues.append(f"Step {i}: Linux 不应有 winget/choco")
    return issues


# ── 数据结构 ──
@dataclass
class ProjectResult:
    repo: str
    name: str
    stars: int
    language: str
    tag: str
    platforms: dict = field(default_factory=dict)  # platform → PlatformResult
    fetch_ok: bool = True
    fetch_error: str = ""
    is_new: bool = False  # 新入榜标记

    @property
    def all_pass(self) -> bool:
        return self.fetch_ok and all(
            p.get("pass", False) for p in self.platforms.values()
        )

    @property
    def pass_count(self) -> int:
        return sum(1 for p in self.platforms.values() if p.get("pass", False))


# ═══════════════════════════════════════════════
#  核心流程
# ═══════════════════════════════════════════════

def crawl_top100() -> list[dict]:
    """
    爬取 GitHub Top 100 热门项目。
    复用 trending.py 的 _fetch_all() 逻辑，但不缓存（每次实时爬取）。
    """
    from trending import _fetch_all
    print(f"\n{BD}📡 正在爬取 GitHub Top 100 热门项目...{RS}", flush=True)
    projects = _fetch_all()
    print(f"   获取到 {len(projects)} 个项目", flush=True)
    return projects


def load_history() -> dict:
    """加载上次验证的项目列表（用于增量检测）"""
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {"repos": [], "last_run": None}


def save_history(repos: list[str]):
    """保存本次验证的项目列表"""
    _HISTORY_FILE.write_text(json.dumps({
        "repos": repos,
        "last_run": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), "utf-8")


def detect_new_projects(current: list[dict], history: dict) -> set[str]:
    """检测新入榜项目"""
    old_repos = set(r.lower() for r in history.get("repos", []))
    new_repos = set()
    for p in current:
        repo = p["repo"].lower()
        if repo not in old_repos:
            new_repos.add(repo)
    return new_repos


def validate_project(
    repo: str,
    planner: SmartPlanner,
    project_info=None,
) -> ProjectResult:
    """
    对单个项目执行三平台验证。

    Parameters:
        repo: 如 "owner/repo"
        planner: SmartPlanner 实例
        project_info: 已获取的项目信息（可选，避免重复 fetch）

    Returns:
        ProjectResult (fetch_ok=False 且 fetch_error 以 "RATELIMIT:" 开头
        表示遇到限速，调用方可据此处理)
    """
    result = ProjectResult(
        repo=repo, name="", stars=0, language="", tag=""
    )

    # 1. 获取项目信息
    if project_info is None:
        try:
            project_info = fetch_project(repo)
        except PermissionError:
            # 限速专用标记，让调用方可识别
            result.fetch_ok = False
            result.fetch_error = "RATELIMIT: GitHub API 频率超限"
            return result
        except Exception as e:
            result.fetch_ok = False
            result.fetch_error = str(e)[:200]
            return result

    result.name = project_info.repo
    result.language = project_info.language or ""
    result.stars = project_info.stars or 0

    # 2. 三平台验证
    for plat_name, env in PLATFORMS.items():
        os_type = env["os"]["type"]
        stderr_buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr_buf):
                plan = planner.generate_plan(
                    owner=project_info.owner,
                    repo=project_info.repo,
                    env=env,
                    project_types=project_info.project_type,
                    dependency_files=project_info.dependency_files,
                    readme=project_info.readme,
                )
        except Exception as e:
            result.platforms[plat_name] = {
                "pass": False,
                "error": str(e)[:200],
                "steps": 0,
                "confidence": "error",
                "strategy": "",
            }
            continue

        steps = plan.get("steps", [])
        confidence = plan.get("confidence", "unknown")
        strategy = plan.get("strategy", "unknown")
        issues = _validate_steps(steps, os_type)

        is_pass = bool(steps) and not issues
        result.platforms[plat_name] = {
            "pass": is_pass,
            "steps": len(steps),
            "confidence": confidence,
            "strategy": strategy,
            "issues": issues if issues else [],
            "has_launch": bool(plan.get("launch_command")),
        }

    return result


def run_validation(
    projects: list[dict],
    new_repos: set[str],
    quick_mode: bool = False,
    category_filter: str = None,
) -> list[ProjectResult]:
    """
    执行完整验证流程。

    Parameters:
        projects: 从 crawl_top100() 获取的项目列表
        new_repos: 新入榜项目 repo 集合（小写）
        quick_mode: True 时仅验证新入榜项目
        category_filter: 按标签过滤（如 "AI", "Web"）
    """
    planner = SmartPlanner()
    results = []

    # 过滤
    targets = projects
    if category_filter:
        targets = [p for p in targets if p.get("tag", "").lower() == category_filter.lower()]
    if quick_mode:
        targets = [p for p in targets if p["repo"].lower() in new_repos]

    total = len(targets)
    if total == 0:
        print(f"\n{Y}没有需要验证的项目{RS}")
        if quick_mode:
            print(f"   （quick 模式：没有新入榜项目）")
        return results

    print(f"\n{BD}🔍 开始验证 {total} 个项目（三平台 × 每个项目）{RS}\n", flush=True)

    rate_limited_repos = []  # 因限速而跳过的项目

    for idx, proj in enumerate(targets, 1):
        repo = proj["repo"]
        tag = proj.get("tag", "")
        stars = proj.get("stars", "?")
        is_new = repo.lower() in new_repos
        new_badge = f" {Y}[NEW]{RS}" if is_new else ""

        print(f"[{idx}/{total}] {BD}{repo}{RS}  ⭐{stars}  #{tag}{new_badge}", flush=True)

        result = validate_project(repo, planner)
        result.tag = tag
        result.is_new = is_new

        # 遇到限速：记录并停止继续请求（避免浪费时间）
        if not result.fetch_ok and result.fetch_error.startswith("RATELIMIT:"):
            rate_limited_repos.append(repo)
            if len(rate_limited_repos) == 1:
                print(f"\n  {Y}⚠️  GitHub API 频率超限，后续未缓存的项目将跳过{RS}")
                print(f"  {DM}提示: 设置 GITHUB_TOKEN 环境变量可获得 5000 次/小时{RS}")
            print(f"  {DM}⏭ 跳过 {repo}（限速）{RS}")
            results.append(result)
            continue

        # 尝试从 crawl 数据填充 stars
        if result.stars == 0 and proj.get("_stars_num"):
            result.stars = proj["_stars_num"]

        # 输出单项结果
        if not result.fetch_ok:
            print(f"  {R}❌ 获取失败: {result.fetch_error[:80]}{RS}")
        else:
            for plat, pr in result.platforms.items():
                icon = f"{G}✅{RS}" if pr["pass"] else f"{R}❌{RS}"
                conf = pr["confidence"]
                strat = pr["strategy"]
                n_steps = pr["steps"]
                print(f"  {icon} {plat:14s}  steps={n_steps}  "
                      f"conf={conf:6s}  strategy={strat}")
                if pr.get("issues"):
                    for iss in pr["issues"]:
                        print(f"     {R}⚠ {iss}{RS}")

        results.append(result)

        # GitHub API 限速
        if idx < total:
            time.sleep(1.5)

    if rate_limited_repos:
        print(f"\n{Y}⚠️  因 API 限速跳过了 {len(rate_limited_repos)} 个项目{RS}")
        print(f"   下次运行时已成功获取的项目会使用缓存，无需重新请求")
        print(f"   建议设置: export GITHUB_TOKEN=ghp_xxxx")

    return results


# ═══════════════════════════════════════════════
#  报告生成
# ═══════════════════════════════════════════════

def generate_report(results: list[ProjectResult], new_repos: set[str]) -> dict:
    """生成结构化兼容性报告"""
    total_projects = len(results)
    total_tests = sum(len(r.platforms) for r in results)
    passed_tests = sum(r.pass_count for r in results)
    all_pass_projects = sum(1 for r in results if r.all_pass)
    fetch_fail = sum(1 for r in results if not r.fetch_ok)

    # 按置信度统计
    confidence_breakdown = {"high": 0, "medium": 0, "low": 0, "error": 0}
    for r in results:
        for pr in r.platforms.values():
            conf = pr.get("confidence", "error")
            if conf in confidence_breakdown:
                confidence_breakdown[conf] += 1
            else:
                confidence_breakdown["error"] += 1

    # 新入榜项目验证
    new_results = [r for r in results if r.is_new]
    new_pass = sum(1 for r in new_results if r.all_pass)

    # 失败项目列表
    failed_projects = []
    for r in results:
        if not r.all_pass:
            failed_plats = [
                {"platform": p, **info}
                for p, info in r.platforms.items()
                if not info.get("pass", False)
            ]
            failed_projects.append({
                "repo": r.repo,
                "is_new": r.is_new,
                "fetch_ok": r.fetch_ok,
                "fetch_error": r.fetch_error,
                "failed_platforms": failed_plats,
            })

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_projects": total_projects,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "pass_rate": round(passed_tests / total_tests * 100, 1) if total_tests else 0,
            "all_pass_projects": all_pass_projects,
            "project_pass_rate": round(all_pass_projects / total_projects * 100, 1) if total_projects else 0,
            "fetch_failures": fetch_fail,
            "new_projects": len(new_results),
            "new_projects_pass": new_pass,
        },
        "confidence_breakdown": confidence_breakdown,
        "failed_projects": failed_projects,
        "all_results": [
            {
                "repo": r.repo,
                "name": r.name,
                "stars": r.stars,
                "language": r.language,
                "tag": r.tag,
                "is_new": r.is_new,
                "all_pass": r.all_pass,
                "platforms": r.platforms,
            }
            for r in results
        ],
    }

    return report


def save_report(report: dict):
    """保存报告到 JSON 文件"""
    _REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(f"\n📄 报告已保存: {_REPORT_FILE}")


def print_report(report: dict):
    """终端友好的报告输出"""
    s = report["summary"]
    cb = report["confidence_breakdown"]

    print(f"\n{'═' * 60}")
    print(f"{BD}  GitHub Top 100 兼容性验证报告{RS}")
    print(f"  {DM}{report['timestamp']}{RS}")
    print(f"{'═' * 60}")

    # 核心指标
    rate_color = G if s["pass_rate"] >= 90 else Y if s["pass_rate"] >= 70 else R
    print(f"\n  📊 总体覆盖率:  {rate_color}{BD}{s['pass_rate']}%{RS}"
          f"  ({s['passed_tests']}/{s['total_tests']} 测试通过)")
    print(f"  📦 项目通过率:  {rate_color}{BD}{s['project_pass_rate']}%{RS}"
          f"  ({s['all_pass_projects']}/{s['total_projects']} 全平台通过)")

    if s["fetch_failures"]:
        print(f"  ⚠️  获取失败:    {R}{s['fetch_failures']}{RS} 个项目")

    # 置信度分布
    total_conf = sum(cb.values())
    if total_conf:
        print(f"\n  🎯 置信度分布:")
        print(f"     {G}high{RS}:   {cb['high']:3d}  "
              f"({cb['high']/total_conf*100:.0f}%)")
        print(f"     {Y}medium{RS}: {cb['medium']:3d}  "
              f"({cb['medium']/total_conf*100:.0f}%)")
        print(f"     {R}low{RS}:    {cb['low']:3d}  "
              f"({cb['low']/total_conf*100:.0f}%)")
        if cb["error"]:
            print(f"     error:  {cb['error']:3d}")

    # 新入榜项目
    if s["new_projects"]:
        new_icon = G if s["new_projects_pass"] == s["new_projects"] else Y
        print(f"\n  🆕 新入榜项目: {s['new_projects']} 个  "
              f"({new_icon}{s['new_projects_pass']}/{s['new_projects']} 通过{RS})")

    # 失败列表
    failed = report.get("failed_projects", [])
    if failed:
        print(f"\n  ❌ 失败项目 ({len(failed)}):")
        for fp in failed[:20]:  # 最多显示 20 个
            new_tag = f" {Y}[NEW]{RS}" if fp["is_new"] else ""
            if not fp["fetch_ok"]:
                print(f"     • {fp['repo']}{new_tag}  — 获取失败: {fp['fetch_error'][:60]}")
            else:
                plats = ", ".join(p["platform"] for p in fp["failed_platforms"])
                print(f"     • {fp['repo']}{new_tag}  — 失败平台: {plats}")

    print(f"\n{'═' * 60}\n")


def show_last_report():
    """显示上次验证报告"""
    if not _REPORT_FILE.exists():
        print(f"{Y}没有找到历史报告。请先运行: python tools/validate_top100.py{RS}")
        return
    report = json.loads(_REPORT_FILE.read_text("utf-8"))
    print_report(report)


# ═══════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════

def cmd_validate(quick: bool = False, report_only: bool = False,
                 category: str = None) -> dict:
    """
    validate 子命令入口（供 main.py 调用）。

    Returns:
        dict 格式的报告摘要
    """
    if report_only:
        show_last_report()
        return {"status": "ok", "action": "report_shown"}

    # 1. 爬取 Top 100
    projects = crawl_top100()
    if not projects:
        return {"status": "error", "message": "爬取失败，请检查网络"}

    # 2. 增量检测
    history = load_history()
    new_repos = detect_new_projects(projects, history)

    if new_repos:
        print(f"\n{Y}🆕 发现 {len(new_repos)} 个新入榜项目:{RS}")
        for nr in sorted(new_repos)[:10]:
            print(f"   • {nr}")
        if len(new_repos) > 10:
            print(f"   ...及其他 {len(new_repos) - 10} 个")

    # 3. 执行验证
    t0 = time.time()
    results = run_validation(projects, new_repos,
                             quick_mode=quick, category_filter=category)
    elapsed = time.time() - t0

    if not results:
        return {"status": "ok", "message": "没有需要验证的项目"}

    # 4. 生成报告
    report = generate_report(results, new_repos)
    report["elapsed_seconds"] = round(elapsed, 1)
    save_report(report)
    print_report(report)

    # 5. 保存历史（用于下次增量检测）
    all_repos = [p["repo"] for p in projects]
    save_history(all_repos)

    print(f"{DM}验证完成，耗时 {elapsed:.1f}s{RS}")

    return {
        "status": "ok",
        "summary": report["summary"],
        "elapsed": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(
        description="GitHub Top 100 持续兼容性验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/validate_top100.py                   # 完整验证
  python tools/validate_top100.py --quick            # 仅验证新入榜
  python tools/validate_top100.py --report           # 查看上次报告
  python tools/validate_top100.py --category AI      # 验证 AI 分类
""")
    parser.add_argument("--quick", action="store_true",
                        help="仅验证新入榜项目（增量模式）")
    parser.add_argument("--report", action="store_true",
                        help="仅显示上次验证报告")
    parser.add_argument("--category", default=None,
                        help="按分类过滤: AI/Web/工具/IoT")
    args = parser.parse_args()

    result = cmd_validate(
        quick=args.quick,
        report_only=args.report,
        category=args.category,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
