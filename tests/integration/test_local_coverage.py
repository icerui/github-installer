#!/usr/bin/env python3
"""
Level 1-Local: 真实项目本地覆盖率测试
======================================

使用 git clone --depth 1 + 本地文件分析 + SmartPlanner 端到端验证。
完全不消耗 GitHub API 配额，可以测试无限量项目。

与其他测试的区别：
  Level 0:     只检查模板方法是否存在（纯静态）
  Level 1-Sim: 用模拟数据调 generate_plan()（无网络）
  Level 1-API: 用 GitHub API（受 60 次/小时限制）
  Level 1-Local: git clone + 本地分析（无限制，真实数据）  ← 本文件

用法：
  python3 test_local_coverage.py                    # 跑全部
  python3 test_local_coverage.py --category python  # 只跑某类
  python3 test_local_coverage.py --quick             # 每类只取1个（快速验证）
"""

from __future__ import annotations
import sys
import time
import argparse
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from fetcher import fetch_project_local, parse_repo_identifier
from planner import SmartPlanner
from detector import EnvironmentDetector

# 颜色
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; C = "\033[36m"
BD = "\033[1m"; DM = "\033[2m"; RS = "\033[0m"; M = "\033[35m"

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TestProject:
    owner: str
    repo: str
    expect_types_any: list[str]        # project_types 中应至少包含其一
    expect_strategy_prefix: str = ""   # strategy 应以此开头
    expect_confidence: str = ""        # 期望的 confidence
    expect_cmds_any: list[str] = field(default_factory=list)  # 步骤中应包含至少一个
    is_known: bool = False             # 是否在已知项目数据库中


# ═══════════════════════════════════════════════════════════════════
#  测试项目库：覆盖 GitHub 上 ~99% 的语言生态
#  每个分类包含 3-7 个真实项目，共 20+ 分类 100+ 项目
# ═══════════════════════════════════════════════════════════════════

CATEGORIES: dict[str, dict] = {
    "python_general": {
        "share": "22%",
        "projects": [
            TestProject("pallets", "flask", ["python"], "type_template_python", "medium", ["pip install", "venv"]),
            TestProject("django", "django", ["python"], "type_template_python", "medium"),
            TestProject("tiangolo", "fastapi", ["python"], "type_template_python", "medium"),
            TestProject("psf", "requests", ["python"], "type_template_python", "medium"),
            TestProject("scrapy", "scrapy", ["python"], "type_template_python", "medium"),
            TestProject("encode", "httpx", ["python"], "type_template_python", "medium"),
            TestProject("celery", "celery", ["python"], "type_template_python", "medium"),
            TestProject("pallets", "click", ["python"], "type_template_python", "medium"),
            TestProject("psf", "black", ["python"], "type_template_python", "medium"),
        ],
    },
    "python_ml": {
        "share": "8%",
        "projects": [
            TestProject("huggingface", "transformers", ["python"], "known_project", "high", is_known=True),
            TestProject("Lightning-AI", "pytorch-lightning", ["python"], "", ""),
            TestProject("openai", "whisper", ["python"], "", ""),
            TestProject("langchain-ai", "langchain", ["python"], "type_template_python", "medium"),
            TestProject("ultralytics", "ultralytics", ["python"], "known_project", "high", is_known=True),
            TestProject("huggingface", "diffusers", ["python"], "", ""),
            TestProject("keras-team", "keras", ["python"], "", ""),
        ],
    },
    "javascript_node": {
        "share": "20%",
        "projects": [
            TestProject("expressjs", "express", ["node"], "type_template_node", "medium", ["npm install"]),
            TestProject("axios", "axios", ["node"], "type_template_node", "medium"),
            TestProject("vercel", "next.js", ["node"], "type_template_node", "medium"),
            TestProject("vuejs", "core", ["node"], "type_template_node", "medium"),
            TestProject("sveltejs", "svelte", ["node"], "type_template_node", "medium"),
            TestProject("lobehub", "lobe-chat", ["node"], "known_project", "high", is_known=True),
            TestProject("n8n-io", "n8n", ["node"], "known_project", "high", is_known=True),
            TestProject("facebook", "react", ["node"], "type_template_node", "medium"),
            TestProject("denoland", "deno", ["rust"], "", ""),  # Deno 用 Rust 写的
        ],
    },
    "java": {
        "share": "9%",
        "projects": [
            TestProject("spring-projects", "spring-boot", ["java"], "type_template_java", "medium", ["mvn", "gradlew"]),
            TestProject("elastic", "elasticsearch", ["java"], "type_template_java", "medium"),
            TestProject("apache", "kafka", ["java"], "type_template_java", "medium"),
            TestProject("google", "guava", ["java"], "type_template_java", "medium"),
            TestProject("apache", "dubbo", ["java"], "type_template_java", "medium"),
            TestProject("iluwatar", "java-design-patterns", ["java"], "type_template_java", "medium"),
        ],
    },
    "kotlin": {
        "share": "1.5%",
        "projects": [
            TestProject("square", "okhttp", ["kotlin"], "", ""),
            TestProject("square", "leakcanary", ["kotlin"], "", ""),
            TestProject("JetBrains", "kotlin", ["kotlin"], "", ""),
        ],
    },
    "docker": {
        "share": "5%",
        "projects": [
            TestProject("portainer", "portainer", ["docker", "go"], "known_project", "high", is_known=True),
            TestProject("traefik", "traefik", ["docker", "go"], "", ""),
            TestProject("nginx-proxy", "nginx-proxy", ["docker"], "", ""),
        ],
    },
    "go": {
        "share": "5%",
        "projects": [
            TestProject("cli", "cli", ["go"], "known_project", "high", is_known=True),
            TestProject("gohugoio", "hugo", ["go"], "type_template_go", "medium", ["go build ./..."]),
            TestProject("junegunn", "fzf", ["go"], "type_template_go", "medium"),
            TestProject("containerd", "containerd", ["go"], "type_template_go", "medium"),
            TestProject("minio", "minio", ["go"], "type_template_go", "medium"),
            TestProject("prometheus", "prometheus", ["go"], "type_template_go", "medium"),
            TestProject("etcd-io", "etcd", ["go"], "type_template_go", "medium"),
        ],
    },
    "rust": {
        "share": "3%",
        "projects": [
            TestProject("BurntSushi", "ripgrep", ["rust"], "known_project", "high", is_known=True),
            TestProject("sharkdp", "bat", ["rust"], "type_template_rust", "medium", ["cargo"]),
            TestProject("sharkdp", "fd", ["rust"], "type_template_rust", "medium"),
            TestProject("astral-sh", "uv", ["rust"], "type_template_rust", "medium"),
            TestProject("astral-sh", "ruff", ["rust"], "type_template_rust", "medium"),
            TestProject("starship", "starship", ["rust"], "type_template_rust", "medium"),
            TestProject("bevyengine", "bevy", ["rust"], "type_template_rust", "medium"),
        ],
    },
    "cpp_cmake": {
        "share": "6%",
        "projects": [
            TestProject("ggerganov", "llama.cpp", ["cpp", "cmake"], "known_project", "high", is_known=True),
            TestProject("opencv", "opencv", ["cpp", "cmake"], "type_template_cmake", "medium", ["cmake"]),
            TestProject("nlohmann", "json", ["cpp", "cmake"], "type_template_cmake", "medium"),
            TestProject("gabime", "spdlog", ["cpp", "cmake"], "type_template_cmake", "medium"),
            TestProject("grpc", "grpc", ["cpp", "python", "cmake"], "type_template_cmake|type_template_python", "medium"),
            TestProject("protocolbuffers", "protobuf", ["cpp", "cmake"], "type_template_cmake", "medium"),
        ],
    },
    "c_make": {
        "share": "3%",
        "projects": [
            TestProject("redis", "redis", ["c", "make"], "type_template_make", "medium", ["make"]),
            TestProject("git", "git", ["c", "make"], "type_template_make", "medium"),
            TestProject("curl", "curl", ["c"], "", "medium"),
            TestProject("jqlang", "jq", ["c"], "", ""),
            TestProject("tmux", "tmux", ["c", "autotools"], "type_template_make", "medium"),
        ],
    },
    "ruby": {
        "share": "3%",
        "projects": [
            TestProject("jekyll", "jekyll", ["ruby"], "type_template_ruby", "medium", ["bundle install"]),
            TestProject("rails", "rails", ["ruby"], "type_template_ruby", "medium"),
            TestProject("Homebrew", "brew", ["ruby"], "", ""),
            TestProject("discourse", "discourse", ["ruby"], "", ""),
            TestProject("forem", "forem", ["ruby"], "", ""),
        ],
    },
    "php": {
        "share": "4%",
        "projects": [
            TestProject("laravel", "laravel", ["php"], "type_template_php", "medium", ["composer"]),
            TestProject("symfony", "symfony", ["php"], "type_template_php", "medium"),
            TestProject("WordPress", "WordPress", ["php"], "", ""),
            TestProject("nextcloud", "server", ["php"], "", ""),
            TestProject("matomo-org", "matomo", ["php"], "", ""),
        ],
    },
    "dotnet": {
        "share": "3%",
        "projects": [
            TestProject("jellyfin", "jellyfin", ["dotnet"], "type_template_dotnet", "medium", ["dotnet"]),
            TestProject("bitwarden", "server", ["dotnet"], "", ""),
            TestProject("dotnet", "runtime", ["dotnet"], "", ""),
            TestProject("dotnet", "aspnetcore", ["dotnet"], "", ""),
            TestProject("ShareX", "ShareX", ["dotnet"], "", ""),
        ],
    },
    "swift": {
        "share": "2%",
        "projects": [
            TestProject("Alamofire", "Alamofire", ["swift"], "type_template_swift", "medium", ["swift build"]),
            TestProject("vapor", "vapor", ["swift"], "type_template_swift", "medium"),
            TestProject("onevcat", "Kingfisher", ["swift"], "", ""),
            TestProject("ReactiveX", "RxSwift", ["swift"], "", ""),
        ],
    },
    # ── 新增语言分类 ──
    "dart_flutter": {
        "share": "1%",
        "projects": [
            TestProject("flame-engine", "flame", ["dart"], "type_template_dart", "medium", ["dart pub get", "flutter pub get"]),
            TestProject("pichillilorenzo", "flutter_inappwebview", ["dart"], "type_template_dart", "medium"),
            TestProject("abuanwar072", "Flutter-Responsive-Admin-Panel-or-Dashboard", ["dart"], "", ""),
        ],
    },
    "scala_sbt": {
        "share": "0.5%",
        "projects": [
            TestProject("apache", "spark", ["scala"], "type_template_scala", "medium", ["sbt"]),
            TestProject("playframework", "playframework", ["scala"], "type_template_scala", "medium"),
            TestProject("akka", "akka", ["scala"], "type_template_scala", "medium"),
        ],
    },
    "elixir": {
        "share": "0.3%",
        "projects": [
            TestProject("phoenixframework", "phoenix", ["elixir"], "type_template_elixir", "medium", ["mix"]),
            TestProject("elixir-lang", "elixir", ["elixir"], "type_template_elixir", "medium"),
            TestProject("plausible", "analytics", ["elixir"], "type_template_elixir", "medium"),
        ],
    },
    "haskell": {
        "share": "0.2%",
        "projects": [
            TestProject("jgm", "pandoc", ["haskell"], "type_template_haskell", "medium", ["stack", "cabal"]),
            TestProject("koalaman", "shellcheck", ["haskell"], "type_template_haskell", "medium"),
            TestProject("PostgREST", "postgrest", ["haskell"], "type_template_haskell", "medium"),
        ],
    },
    "lua": {
        "share": "0.3%",
        "projects": [
            TestProject("kong", "kong", ["lua"], "type_template_lua", "medium"),
            TestProject("nvim-lua", "kickstart.nvim", ["lua"], "type_template_lua", "medium"),
            TestProject("folke", "lazy.nvim", ["lua"], "type_template_lua", "medium"),
        ],
    },
    "perl": {
        "share": "0.2%",
        "projects": [
            TestProject("mojolicious", "mojo", ["perl"], "type_template_perl", "medium"),
            TestProject("PerlDancer", "Dancer2", ["perl"], "type_template_perl", "medium"),
        ],
    },
    "shell_script": {
        "share": "2%",
        "projects": [
            TestProject("ohmyzsh", "ohmyzsh", ["shell"], "type_template_shell", "medium"),
            TestProject("nvm-sh", "nvm", ["shell", "node"], "type_template_shell", "medium"),
            TestProject("acmesh-official", "acme.sh", ["shell"], "type_template_shell", "medium"),
            TestProject("dylanaraps", "neofetch", ["make"], "type_template_make", "medium"),
        ],
    },
    "known_ai_tools": {
        "share": "N/A",
        "projects": [
            TestProject("comfyanonymous", "ComfyUI", ["python"], "known_project", "high", is_known=True),
            TestProject("AUTOMATIC1111", "stable-diffusion-webui", ["python"], "known_project", "high", is_known=True),
            TestProject("ollama", "ollama", ["go"], "known_project", "high", is_known=True),
            TestProject("open-webui", "open-webui", ["python", "node"], "known_project", "high", is_known=True),
            TestProject("hiyouga", "LLaMA-Factory", ["python"], "known_project", "high", is_known=True),
            TestProject("mudler", "LocalAI", ["go"], "known_project", "high", is_known=True),
            TestProject("oobabooga", "text-generation-webui", ["python"], "known_project", "high", is_known=True),
        ],
    },
}


def run_test(p: TestProject, env: dict, planner: SmartPlanner) -> dict:
    """对单个项目进行完整的本地模式端到端测试"""
    t0 = time.time()
    result = {
        "owner": p.owner, "repo": p.repo, "passed": False,
        "errors": [], "strategy": "", "confidence": "",
        "steps": 0, "types_detected": [], "time": 0,
    }

    try:
        info = fetch_project_local(f"{p.owner}/{p.repo}")
    except FileNotFoundError:
        result["errors"].append(f"项目不存在：{p.owner}/{p.repo}")
        result["time"] = time.time() - t0
        return result
    except Exception as e:
        result["errors"].append(f"clone 失败：{e}")
        result["time"] = time.time() - t0
        return result

    result["types_detected"] = info.project_type
    result["language"] = info.language
    result["dep_files"] = list(info.dependency_files.keys())

    # 生成安装计划
    plan = planner.generate_plan(
        owner=info.owner, repo=info.repo, env=env,
        project_types=info.project_type,
        dependency_files=info.dependency_files,
        readme=info.readme,
    )

    result["strategy"] = plan.get("strategy", "")
    result["confidence"] = plan.get("confidence", "")
    result["steps"] = len(plan.get("steps", []))
    all_cmds = " ".join(s.get("command", "") for s in plan.get("steps", [])).lower()

    errors = []

    # 验证 1：项目类型检测
    if p.expect_types_any:
        found = any(t in info.project_type for t in p.expect_types_any)
        if not found:
            errors.append(f"类型检测: 期望含 {p.expect_types_any} 之一, 实际 {info.project_type}")

    # 验证 2：策略匹配（支持 | 分隔的多策略，任一匹配即可）
    if p.expect_strategy_prefix:
        prefixes = [s.strip() for s in p.expect_strategy_prefix.split("|")]
        if not any(result["strategy"].startswith(pf) for pf in prefixes):
            errors.append(f"策略: 期望 {p.expect_strategy_prefix}*, 实际 {result['strategy']}")

    # 验证 3：置信度
    if p.expect_confidence:
        if result["confidence"] != p.expect_confidence:
            errors.append(f"置信度: 期望 {p.expect_confidence}, 实际 {result['confidence']}")

    # 验证 4：步骤数
    if result["steps"] == 0:
        errors.append("无安装步骤生成")

    # 验证 5：关键命令
    if p.expect_cmds_any:
        if not any(kw.lower() in all_cmds for kw in p.expect_cmds_any):
            errors.append(f"步骤缺少命令: {p.expect_cmds_any}")

    result["errors"] = errors
    result["passed"] = len(errors) == 0
    result["time"] = time.time() - t0
    return result


def main():
    parser = argparse.ArgumentParser(description="Level 1-Local: 真实项目本地覆盖率测试")
    parser.add_argument("--category", default=None, help="只测某个分类")
    parser.add_argument("--quick", action="store_true", help="每类只取 1 个项目（快速验证）")
    parser.add_argument("--json-out", default=None, help="输出 JSON 报告路径")
    args = parser.parse_args()

    env = EnvironmentDetector().detect()
    planner = SmartPlanner()

    categories = CATEGORIES
    if args.category:
        categories = {k: v for k, v in CATEGORIES.items() if args.category.lower() in k.lower()}
        if not categories:
            print(f"{R}未找到分类: {args.category}{RS}")
            sys.exit(1)

    total = 0
    passed = 0
    failed_list = []
    cat_stats = []
    all_results = []

    total_projects = sum(
        min(1, len(v["projects"])) if args.quick else len(v["projects"])
        for v in categories.values()
    )

    print(f"\n{BD}{'═' * 72}{RS}")
    print(f"{BD}  Level 1-Local: 真实项目本地覆盖率测试{RS}")
    print(f"  模式: git clone --depth 1 + 本地文件分析（无 API 限额限制）")
    print(f"  项目数: {total_projects}  分类数: {len(categories)}")
    print(f"{'═' * 72}")

    for cat_name, cat_data in categories.items():
        projects = cat_data["projects"]
        if args.quick:
            projects = projects[:1]

        cat_pass = 0
        cat_total = len(projects)
        print(f"\n  {BD}{M}【{cat_name}】{RS} ({cat_data['share']}, {cat_total} 项目)")

        for p in projects:
            total += 1
            r = run_test(p, env, planner)
            all_results.append(r)

            if r["passed"]:
                cat_pass += 1
                passed += 1
                icon = f"{G}✅{RS}"
            else:
                icon = f"{R}❌{RS}"
                failed_list.append((p, r))

            name = f"{p.owner}/{p.repo}"
            detail = f"strategy={r['strategy']} conf={r['confidence']} steps={r['steps']} {r['time']:.1f}s"
            print(f"    {icon} {name:40s} {DM}{detail}{RS}")
            for err in r["errors"]:
                print(f"       {R}✗ {err}{RS}")

        rate = cat_pass / cat_total * 100 if cat_total else 0
        rc = G if rate >= 80 else (Y if rate >= 60 else R)
        print(f"    {'─' * 60}")
        print(f"    {rc}{BD}{cat_pass}/{cat_total} 通过 ({rate:.0f}%){RS}")
        cat_stats.append((cat_name, cat_data["share"], cat_pass, cat_total, rate))

    # 汇总
    total_rate = passed / total * 100 if total else 0
    rc = G if total_rate >= 95 else (Y if total_rate >= 80 else R)

    print(f"\n{'═' * 72}")
    print(f"  {BD}总覆盖率：{rc}{passed}/{total} = {total_rate:.1f}%{RS}")
    print(f"  目标: ≥95%  {'✅ 达标' if total_rate >= 95 else '❌ 未达标'}")

    # LLM 决策汇总
    llm_needed = sum(1 for r in all_results if r["confidence"] == "low")
    no_llm = sum(1 for r in all_results if r["confidence"] in ("high", "medium"))
    print(f"\n  {BD}LLM 使用决策：{RS}")
    print(f"    ✅ 无需 LLM（high/medium）: {no_llm}/{total} ({no_llm/total*100:.0f}%)")
    if llm_needed:
        print(f"    🤖 需要 LLM（low）:          {llm_needed}/{total} ({llm_needed/total*100:.0f}%)")

    if failed_list:
        print(f"\n  {R}{BD}失败项目：{RS}")
        for p, r in failed_list:
            print(f"    {R}• {p.owner}/{p.repo}: {'; '.join(r['errors'])}{RS}")

    # 分类统计表
    print(f"\n  {BD}分类统计：{RS}")
    print(f"  {'分类':<25s} {'占比':>6s} {'通过率':>10s}")
    print(f"  {'─' * 45}")
    for name, share, p, t, rate in cat_stats:
        rc = G if rate >= 80 else R
        print(f"  {name:<25s} {share:>6s} {rc}{p}/{t} ({rate:.0f}%){RS}")

    print(f"{'═' * 72}\n")

    # 保存 JSON 报告
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = args.json_out or str(RESULTS_DIR / f"local_coverage_{ts}.json")
    report = {
        "timestamp": ts,
        "mode": "local_git_clone",
        "total": total, "passed": passed, "rate": total_rate,
        "target": 95,
        "categories": [
            {"name": n, "share": s, "passed": p, "total": t, "rate": r}
            for n, s, p, t, r in cat_stats
        ],
        "results": all_results,
    }
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  📄 报告已保存：{report_path}")

    return total_rate >= 95


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
