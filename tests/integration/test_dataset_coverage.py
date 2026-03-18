#!/usr/bin/env python3
"""
Level 2-Dataset: 离线数据集覆盖率测试
======================================

基于 crawl_github.py 爬取的本地 JSON 数据集运行类型检测 + 计划生成。
全部离线执行，无需网络，秒级完成（即使 750+ 个项目）。

前置条件：
    先运行一次爬虫（约 20 分钟，只需一次）：
    export GITHUB_TOKEN=ghp_xxxx
    python3 ../tools/crawl_github.py

用法：
    python3 test_dataset_coverage.py                     # 全部跑
    python3 test_dataset_coverage.py --language python    # 只测某语言
    python3 test_dataset_coverage.py --verbose            # 显示每个项目详情
    python3 test_dataset_coverage.py --failures-only      # 只显示失败项
    python3 test_dataset_coverage.py --dataset path.json  # 指定数据集
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# ─── 导入项目代码 ────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from fetcher import detect_project_types, RepoInfo
from planner import SmartPlanner
from detector import EnvironmentDetector

# ─── 颜色 ────────────────────────────────────────────────────

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; C = "\033[36m"
BD = "\033[1m"; DM = "\033[2m"; RS = "\033[0m"; M = "\033[35m"

# ─── 默认路径 ────────────────────────────────────────────────

DEFAULT_DATASET = Path(__file__).parent.parent / "fixtures" / "github_projects.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── GitHub 语言 → 可接受的检测类型 ─────────────────────────

# 当 GitHub 报告主语言为 X 时，我们的检测结果应包含以下类型之一
LANGUAGE_ACCEPTABLE_TYPES = {
    "Python":       {"python", "conda"},
    "JavaScript":   {"node"},
    "TypeScript":   {"node"},
    "Java":         {"java"},
    "Kotlin":       {"kotlin", "java"},     # Gradle 项目可能走 Java 模板
    "Go":           {"go"},
    "Rust":         {"rust"},
    "C++":          {"cpp", "cmake", "meson", "make", "autotools"},
    "C":            {"c", "cmake", "make", "autotools", "meson"},
    "Ruby":         {"ruby"},
    "PHP":          {"php"},
    "C#":           {"dotnet"},
    "Swift":        {"swift"},
    "Dart":         {"dart"},
    "Scala":        {"scala", "java"},      # SBT 或 Gradle
    "Shell":        {"shell", "make"},      # Shell 项目常有 Makefile
    "Elixir":       {"elixir"},
    "Haskell":      {"haskell"},
    "Lua":          {"lua", "make"},        # Lua 项目常有 Makefile
    "Perl":         {"perl", "make"},       # Perl 项目常有 Makefile
    "R":            {"r"},
    "Julia":        {"julia"},
    "Clojure":      {"clojure"},
    "Zig":          {"zig"},
    "Dockerfile":   {"docker", "node", "python", "go", "ruby", "php"}, # 多语言项目常标为Dockerfile
}

# 策略 → 描述（用于报告）
STRATEGY_LABELS = {
    "known_project": "已知项目",
    "type_template": "类型模板",
    "readme_extract": "README提取",
}


# ─── 数据集加载 ──────────────────────────────────────────────

def load_dataset(path: Path) -> list[dict]:
    """加载 JSON 数据集"""
    if not path.exists():
        print(f"{R}错误: 数据集不存在: {path}{RS}")
        print(f"\n请先运行爬虫：")
        print(f"  export GITHUB_TOKEN=ghp_xxxx")
        print(f"  cd {Path(__file__).parent.parent / 'tools'}")
        print(f"  python3 crawl_github.py\n")
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    projects = data.get("projects", [])
    meta = data.get("metadata", {})
    print(f"  📂 数据集: {path.name}")
    print(f"  📊 项目总数: {meta.get('total_projects', len(projects))}")
    if "languages" in meta:
        print(f"  🌐 语言数: {len(meta['languages'])}")
    return projects


# ─── 单个项目测试 ─────────────────────────────────────────────

@dataclass
class TestResult:
    owner: str
    repo: str
    github_language: str
    stars: int
    detected_types: list[str]
    strategy: str
    confidence: str
    step_count: int
    type_match: bool        # 检测类型是否与 GitHub 语言一致
    has_plan: bool          # 是否生成了有效计划
    error: str | None = None


def test_project(project: dict, planner: SmartPlanner, env: dict) -> TestResult:
    """对单个缓存项目运行检测 + 计划生成"""
    owner = project["owner"]
    repo = project["repo"]
    github_lang = project.get("language", "Unknown")
    stars = project.get("stars", 0)

    try:
        # 1) 类型检测（使用与生产代码完全相同的函数）
        repo_data = {"language": github_lang}
        dep_files = project.get("dep_files", {})
        readme = project.get("readme", "")

        detected_types = detect_project_types(repo_data, readme, dep_files)

        # 2) 检查类型是否与 GitHub 语言一致
        acceptable = LANGUAGE_ACCEPTABLE_TYPES.get(github_lang, set())
        type_match = bool(acceptable & set(detected_types)) if acceptable else True

        # 3) 计划生成
        plan = planner.generate_plan(
            owner=owner,
            repo=repo,
            env=env,
            project_types=detected_types,
            dependency_files=dep_files,
            readme=readme,
        )

        strategy = plan.get("strategy", "")
        confidence = plan.get("confidence", "")
        steps = plan.get("steps", [])
        has_plan = len(steps) > 0

        return TestResult(
            owner=owner, repo=repo,
            github_language=github_lang, stars=stars,
            detected_types=detected_types,
            strategy=strategy, confidence=confidence,
            step_count=len(steps),
            type_match=type_match, has_plan=has_plan,
        )

    except Exception as e:
        return TestResult(
            owner=owner, repo=repo,
            github_language=github_lang, stars=stars,
            detected_types=[], strategy="", confidence="",
            step_count=0, type_match=False, has_plan=False,
            error=str(e),
        )


# ─── 主测试流程 ──────────────────────────────────────────────

def run_tests(
    dataset_path: Path,
    language_filter: str | None = None,
    verbose: bool = False,
    failures_only: bool = False,
):
    """运行离线数据集覆盖率测试"""

    print(f"\n{BD}═══════════════════════════════════════════════════════════════{RS}")
    print(f"  Level 2-Dataset: 离线数据集覆盖率测试")
    print(f"{BD}═══════════════════════════════════════════════════════════════{RS}\n")

    # 加载数据集
    projects = load_dataset(dataset_path)

    # 语言过滤
    if language_filter:
        lf = language_filter.lower()
        projects = [p for p in projects if (p.get("language") or "").lower() == lf]
        if not projects:
            print(f"{R}错误: 未找到语言为 '{language_filter}' 的项目{RS}")
            sys.exit(1)
        print(f"  🔍 过滤: 只测试 {language_filter} ({len(projects)} 个)")

    print(f"  🏃 开始测试 {len(projects)} 个项目...\n")

    # 初始化 planner
    try:
        env = EnvironmentDetector().detect()
    except Exception:
        env = {
            "os": {"type": "linux", "distro": "ubuntu", "distro_name": "Ubuntu", "version": "22.04", "arch": "x86_64", "is_wsl": False, "shell": "/bin/bash", "home": "/root"},
            "hardware": {"cpu_count": 2, "ram_gb": 8.0},
            "gpu": {"type": "cpu_only", "name": "No dedicated GPU", "pytorch_flag": "cpu", "cuda_available": False},
            "package_managers": {"pip": {"available": True, "version": "23.0"}},
            "runtimes": {"python": {"available": True, "version": "3.12.0", "executable": "/usr/bin/python3", "path": "/usr/bin/python3"}, "git": {"available": True, "version": "2.40.0"}},
            "disk": {"free_gb": 50.0, "total_gb": 100.0, "path": "/root"},
            "llm_configured": {},
            "network": {"github": True, "pypi": True},
        }
    planner = SmartPlanner()

    # 运行测试
    t0 = time.time()
    results: list[TestResult] = []
    for i, project in enumerate(projects):
        result = test_project(project, planner, env)
        results.append(result)

        # 输出进度
        if verbose or failures_only:
            if verbose or not (result.type_match and result.has_plan):
                icon = f"{G}✅{RS}" if (result.type_match and result.has_plan) else f"{R}❌{RS}"
                types_str = ",".join(result.detected_types) or "无"
                print(f"    {icon} {result.owner}/{result.repo:30s} "
                      f"lang={result.github_language:12s} types=[{types_str}] "
                      f"strategy={result.strategy} steps={result.step_count}")
                if result.error:
                    print(f"       {R}错误: {result.error}{RS}")
        elif (i + 1) % 100 == 0:
            print(f"    ...已测试 {i+1}/{len(projects)}")

    elapsed = time.time() - t0

    # ── 统计 ──

    total = len(results)
    type_ok = sum(1 for r in results if r.type_match)
    plan_ok = sum(1 for r in results if r.has_plan)
    both_ok = sum(1 for r in results if r.type_match and r.has_plan)
    errors = sum(1 for r in results if r.error)

    # 按语言分组统计
    by_lang: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_lang[r.github_language].append(r)

    # 按策略统计
    strategy_counts: dict[str, int] = defaultdict(int)
    for r in results:
        key = r.strategy.split("_")[0] + "_" + r.strategy.split("_")[1] if "_" in r.strategy else r.strategy
        strategy_counts[r.strategy] += 1

    # ── 报告 ──

    print(f"\n{BD}═══════════════════════════════════════════════════════════════{RS}")
    print(f"  测试完成: {total} 个项目, 耗时 {elapsed:.1f}s")
    print(f"{BD}═══════════════════════════════════════════════════════════════{RS}\n")

    # 总览
    pct_type = type_ok / total * 100 if total else 0
    pct_plan = plan_ok / total * 100 if total else 0
    pct_both = both_ok / total * 100 if total else 0

    print(f"  📊 总览:")
    c1 = G if pct_type >= 90 else (Y if pct_type >= 80 else R)
    c2 = G if pct_plan >= 95 else (Y if pct_plan >= 90 else R)
    c3 = G if pct_both >= 85 else (Y if pct_both >= 75 else R)
    print(f"    类型匹配率:   {c1}{type_ok}/{total} = {pct_type:.1f}%{RS}")
    print(f"    计划生成率:   {c2}{plan_ok}/{total} = {pct_plan:.1f}%{RS}")
    print(f"    完全正确率:   {c3}{both_ok}/{total} = {pct_both:.1f}%{RS}")
    if errors:
        print(f"    {R}异常: {errors} 个{RS}")

    # 按语言统计
    print(f"\n  📋 按语言统计:")
    print(f"  {'语言':<14s} {'项目数':>6s}  {'类型✓':>5s}  {'计划✓':>5s}  {'准确率':>6s}")
    print(f"  {'─' * 50}")

    for lang_name in sorted(by_lang, key=lambda l: -len(by_lang[l])):
        items = by_lang[lang_name]
        n = len(items)
        t_ok = sum(1 for r in items if r.type_match)
        p_ok = sum(1 for r in items if r.has_plan)
        pct = t_ok / n * 100 if n else 0
        color = G if pct >= 90 else (Y if pct >= 75 else R)
        print(f"  {lang_name:<14s} {n:>6d}  {t_ok:>5d}  {p_ok:>5d}  {color}{pct:>5.1f}%{RS}")

    # 策略分布
    print(f"\n  📋 策略分布:")
    for strat in sorted(strategy_counts, key=lambda s: -strategy_counts[s]):
        cnt = strategy_counts[strat]
        print(f"    {strat:<35s} {cnt:>4d} ({cnt/total*100:.1f}%)")

    # 失败详情（最多显示 30 个）
    failures = [r for r in results if not (r.type_match and r.has_plan)]
    if failures and not verbose:
        print(f"\n  ❌ 失败项目 ({len(failures)} 个):")
        for r in failures[:30]:
            types_str = ",".join(r.detected_types) or "无类型"
            reason = []
            if not r.type_match:
                acceptable = LANGUAGE_ACCEPTABLE_TYPES.get(r.github_language, set())
                reason.append(f"类型不匹配(期望{acceptable},实际[{types_str}])")
            if not r.has_plan:
                reason.append("无计划")
            if r.error:
                reason.append(f"异常:{r.error}")
            reason_str = "; ".join(reason)
            print(f"    • {r.owner}/{r.repo} ({r.github_language}, {r.stars}⭐): {reason_str}")
        if len(failures) > 30:
            print(f"    ... 还有 {len(failures) - 30} 个")

    # 保存 JSON 报告
    report_name = f"dataset_coverage_{time.strftime('%Y%m%d_%H%M%S')}.json"
    report_path = RESULTS_DIR / report_name
    report = {
        "test_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "total": total,
        "type_match": type_ok,
        "plan_generated": plan_ok,
        "both_correct": both_ok,
        "errors": errors,
        "by_language": {
            lang: {
                "total": len(items),
                "type_match": sum(1 for r in items if r.type_match),
                "plan_ok": sum(1 for r in items if r.has_plan),
            }
            for lang, items in by_lang.items()
        },
        "failures": [
            {
                "owner": r.owner, "repo": r.repo,
                "language": r.github_language, "stars": r.stars,
                "detected_types": r.detected_types,
                "strategy": r.strategy,
                "type_match": r.type_match,
                "has_plan": r.has_plan,
                "error": r.error,
            }
            for r in failures
        ],
    }
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  📄 报告已保存: {report_path}")

    print(f"\n{BD}═══════════════════════════════════════════════════════════════{RS}")

    return both_ok == total


# ─── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="离线数据集覆盖率测试（基于 crawl_github.py 生成的 JSON）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--dataset", "-d", type=Path, default=DEFAULT_DATASET,
        help=f"数据集路径 (默认: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--language", "-l", type=str, default=None,
        help="只测某种语言 (如: Python, Go, Rust)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="显示每个项目的详细结果",
    )
    parser.add_argument(
        "--failures-only", "-f", action="store_true",
        help="只显示失败的项目",
    )
    args = parser.parse_args()

    success = run_tests(
        dataset_path=args.dataset,
        language_filter=args.language,
        verbose=args.verbose,
        failures_only=args.failures_only,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
