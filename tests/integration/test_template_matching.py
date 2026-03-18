#!/usr/bin/env python3
"""
Level 1-Sim: 模拟 API 的模板匹配验证
======================================

用模拟的项目数据验证 SmartPlanner 对所有 15 类项目的模板匹配和步骤生成是否正确。
不消耗 GitHub API 配额，但验证的是 generate_plan() 的完整逻辑。

与 Level 0 的区别：
  Level 0 只检查"模板方法是否存在"
  Level 1-Sim 实际调用 generate_plan()，验证生成的步骤、策略、置信度

用法：
  python3 test_template_matching.py
"""

from __future__ import annotations
import sys
from pathlib import Path
from dataclasses import dataclass, field

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from planner import SmartPlanner
from detector import EnvironmentDetector

# 颜色
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; BD = "\033[1m"
DM = "\033[2m"; RS = "\033[0m"; M = "\033[35m"


@dataclass
class SimProject:
    owner: str
    repo: str
    language: str
    dep_files: dict[str, str]   # 模拟的依赖文件名→内容
    project_types: list[str]    # 模拟检测到的类型标签
    readme: str = ""
    expect_strategy: str = ""   # 期望的 strategy 前缀
    expect_confidence: str = "" # 期望的 confidence
    expect_cmds_any: list[str] = field(default_factory=list)
    is_known: bool = False


# ══════════════════════════════════════════════════════════════════════════════
#  72 个模拟项目（每类用最简依赖文件触发模板选择）
# ══════════════════════════════════════════════════════════════════════════════

SIM_PROJECTS: dict[str, list[SimProject]] = {
    # ── Python 通用 ──
    "python_general": [
        SimProject("pallets", "flask", "Python",
                   {"requirements.txt": "flask>=2.0\nJinja2\n"},
                   ["python"], expect_strategy="type_template_python",
                   expect_confidence="medium", expect_cmds_any=["pip install", "venv"]),
        SimProject("django", "django", "Python",
                   {"setup.py": "setup(name='django')\n", "requirements.txt": "Django>=4.0\n"},
                   ["python"], expect_strategy="type_template_python",
                   expect_confidence="medium", expect_cmds_any=["pip install", "venv"]),
        SimProject("tiangolo", "fastapi", "Python",
                   {"pyproject.toml": "[project]\nname='fastapi'\n", "requirements.txt": "fastapi\nuvicorn\n"},
                   ["python", "fastapi"], expect_strategy="type_template_python",
                   expect_confidence="medium"),
        SimProject("psf", "requests", "Python",
                   {"setup.py": "setup(name='requests')\n"},
                   ["python"], expect_strategy="type_template_python",
                   expect_confidence="medium"),
        SimProject("scrapy", "scrapy", "Python",
                   {"requirements.txt": "Twisted\nlxml\n", "setup.py": "setup()\n"},
                   ["python"], expect_strategy="type_template_python",
                   expect_confidence="medium"),
    ],

    # ── Python ML ──
    "python_ml": [
        SimProject("huggingface", "transformers", "Python",
                   {"requirements.txt": "torch\ntransformers\n"},
                   ["python", "pytorch"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("Lightning-AI", "pytorch-lightning", "Python",
                   {"requirements.txt": "torch>=2.0\nlightning\n"},
                   ["python", "pytorch"],
                   expect_strategy="type_template_python_ml", expect_confidence="medium",
                   expect_cmds_any=["torch"]),
        SimProject("openai", "whisper", "Python",
                   {"requirements.txt": "torch\ntorchaudio\n", "setup.py": ""},
                   ["python", "pytorch"],
                   expect_strategy="type_template_python_ml", expect_confidence="medium"),
        SimProject("langchain-ai", "langchain", "Python",
                   {"pyproject.toml": "[project]\nname='langchain'\n"},
                   ["python"],
                   expect_strategy="type_template_python", expect_confidence="medium"),
        SimProject("Eventual-Inc", "Daft", "Python",
                   {
                       "pyproject.toml": "[build-system]\nbuild-backend='maturin'\nrequires=['maturin>=1.5.0']\n",
                       "Cargo.toml": "[workspace]\nmembers=['src/daft-core']\n[lib]\nname='daft'\n",
                   },
                   ["python", "rust"],
                   expect_strategy="type_template_python_rust", expect_confidence="medium",
                   expect_cmds_any=["pip install -e ."]),
    ],

    # ── JavaScript / Node.js ──
    "javascript_node": [
        SimProject("facebook", "react", "JavaScript",
                   {"package.json": '{"name":"react","scripts":{"build":"rollup"}}\n'},
                   ["node"], expect_strategy="type_template_node",
                   expect_confidence="medium", expect_cmds_any=["npm install", "yarn"]),
        SimProject("vuejs", "core", "TypeScript",
                   {"package.json": '{"name":"vue","scripts":{"dev":"node"}}\n'},
                   ["node"], expect_strategy="type_template_node",
                   expect_confidence="medium"),
        SimProject("vercel", "next.js", "JavaScript",
                   {"package.json": '{"name":"next","scripts":{"dev":"next dev"}}\n'},
                   ["node", "nextjs"], expect_strategy="type_template_node",
                   expect_confidence="medium"),
        SimProject("expressjs", "express", "JavaScript",
                   {"package.json": '{"name":"express"}\n'},
                   ["node"], expect_strategy="type_template_node",
                   expect_confidence="medium"),
        SimProject("lobehub", "lobe-chat", "TypeScript",
                   {"package.json": '{"name":"lobe-chat"}\n'},
                   ["node"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
    ],

    # ── Java ──
    "java": [
        SimProject("spring-projects", "spring-boot", "Java",
                   {"pom.xml": "<project><artifactId>spring-boot</artifactId></project>\n"},
                   ["java"], expect_strategy="type_template_java",
                   expect_confidence="medium", expect_cmds_any=["mvn", "gradlew"]),
        SimProject("elastic", "elasticsearch", "Java",
                   {"build.gradle": "apply plugin: 'java'\n"},
                   ["java"], expect_strategy="type_template_java",
                   expect_confidence="medium", expect_cmds_any=["gradlew", "gradle"]),
        SimProject("apache", "kafka", "Java",
                   {"build.gradle": "plugins { id 'java' }\n"},
                   ["java"], expect_strategy="type_template_java",
                   expect_confidence="medium"),
        SimProject("google", "guava", "Java",
                   {"pom.xml": "<project><artifactId>guava</artifactId></project>\n"},
                   ["java"], expect_strategy="type_template_java",
                   expect_confidence="medium"),
    ],

    # ── Docker ──
    "docker": [
        SimProject("portainer", "portainer", "Go",
                   {"Dockerfile": "FROM golang:1.21\n"},
                   ["docker", "go"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("traefik", "traefik", "Go",
                   {"Dockerfile": "FROM golang:1.21\n", "docker-compose.yml": "services:\n  traefik:\n"},
                   ["docker"], expect_strategy="type_template_docker",
                   expect_confidence="medium", expect_cmds_any=["docker"]),
        SimProject("grafana", "grafana", "TypeScript",
                   {"Dockerfile": "FROM node:18\n", "docker-compose.yaml": "version: '3'\n"},
                   ["docker"], expect_strategy="type_template_docker",
                   expect_confidence="medium"),
    ],

    # ── Go ──
    "go": [
        SimProject("cli", "cli", "Go",
                   {"go.mod": "module github.com/cli/cli\n"},
                   ["go"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("gohugoio", "hugo", "Go",
                   {"go.mod": "module github.com/gohugoio/hugo\n"},
                   ["go"], expect_strategy="type_template_go",
                   expect_confidence="medium", expect_cmds_any=["go build ./..."]),
        SimProject("junegunn", "fzf", "Go",
                   {"go.mod": "module github.com/junegunn/fzf\n"},
                   ["go"], expect_strategy="type_template_go",
                   expect_confidence="medium"),
    ],

    # ── Rust ──
    "rust": [
        SimProject("BurntSushi", "ripgrep", "Rust",
                   {"Cargo.toml": "[package]\nname='ripgrep'\n"},
                   ["rust"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("sharkdp", "bat", "Rust",
                   {"Cargo.toml": "[package]\nname='bat'\n"},
                   ["rust"], expect_strategy="type_template_rust",
                   expect_confidence="medium", expect_cmds_any=["cargo install"]),
        SimProject("sharkdp", "fd", "Rust",
                   {"Cargo.toml": "[package]\nname='fd-find'\n"},
                   ["rust"], expect_strategy="type_template_rust",
                   expect_confidence="medium"),
    ],

    # ── C/C++ CMake ──
    "cpp_cmake": [
        SimProject("ggerganov", "llama.cpp", "C++",
                   {"CMakeLists.txt": "cmake_minimum_required(VERSION 3.14)\n"},
                   ["cpp", "cmake"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("opencv", "opencv", "C++",
                   {"CMakeLists.txt": "cmake_minimum_required(VERSION 3.5)\nproject(OpenCV)\n"},
                   ["cpp", "cmake"], expect_strategy="type_template_cmake",
                   expect_confidence="medium", expect_cmds_any=["cmake"]),
        SimProject("nlohmann", "json", "C++",
                   {"CMakeLists.txt": "project(nlohmann_json)\n"},
                   ["cpp", "cmake"], expect_strategy="type_template_cmake",
                   expect_confidence="medium"),
    ],

    # ── C/C++ Makefile ──
    "c_make": [
        SimProject("redis", "redis", "C",
                   {"Makefile": "all: redis-server\n"},
                   ["c", "make"], expect_strategy="type_template_make",
                   expect_confidence="medium", expect_cmds_any=["make"]),
        SimProject("nginx", "nginx", "C",
                   {"Makefile": "all: nginx\n", "configure": "#!/bin/sh\n"},
                   ["c", "make", "autotools"], expect_strategy="type_template_make",
                   expect_confidence="medium"),
        SimProject("vim", "vim", "C",
                   {"Makefile": "all: vim\n", "configure": "#!/bin/sh\n"},
                   ["c", "make", "autotools"], expect_strategy="type_template_make",
                   expect_confidence="medium"),
    ],

    # ── Ruby ──
    "ruby": [
        SimProject("rails", "rails", "Ruby",
                   {"Gemfile": "source 'https://rubygems.org'\ngem 'rails'\n"},
                   ["ruby"], expect_strategy="type_template_ruby",
                   expect_confidence="medium", expect_cmds_any=["bundle install"]),
        SimProject("jekyll", "jekyll", "Ruby",
                   {"Gemfile": "source 'https://rubygems.org'\ngem 'jekyll'\n"},
                   ["ruby"], expect_strategy="type_template_ruby",
                   expect_confidence="medium"),
    ],

    # ── PHP ──
    "php": [
        SimProject("laravel", "laravel", "PHP",
                   {"composer.json": '{"name":"laravel/laravel","require":{"php":"^8.1"}}\n'},
                   ["php"], expect_strategy="type_template_php",
                   expect_confidence="medium", expect_cmds_any=["composer install"]),
        SimProject("symfony", "symfony", "PHP",
                   {"composer.json": '{"name":"symfony/symfony"}\n'},
                   ["php"], expect_strategy="type_template_php",
                   expect_confidence="medium"),
    ],

    # ── .NET / C# ──
    "dotnet": [
        SimProject("dotnet", "runtime", "C#",
                   {},  # .csproj 通常在子目录
                   ["dotnet"], expect_strategy="type_template_dotnet",
                   expect_confidence="medium", expect_cmds_any=["dotnet"]),
        SimProject("jellyfin", "jellyfin", "C#",
                   {},
                   ["dotnet"], expect_strategy="type_template_dotnet",
                   expect_confidence="medium"),
    ],

    # ── Swift ──
    "swift": [
        SimProject("Alamofire", "Alamofire", "Swift",
                   {"Package.swift": "// swift-tools-version:5.7\n"},
                   ["swift"], expect_strategy="type_template_swift",
                   expect_confidence="medium", expect_cmds_any=["swift build"]),
        SimProject("vapor", "vapor", "Swift",
                   {"Package.swift": "// swift-tools-version:5.9\n"},
                   ["swift"], expect_strategy="type_template_swift",
                   expect_confidence="medium"),
    ],

    # ── Conda ──
    "conda": [
        SimProject("huggingface", "diffusers", "Python",
                   {"environment.yml": "name: diffusers\ndependencies:\n  - pytorch\n"},
                   ["python", "conda", "diffusers"],
                   expect_strategy="type_template_conda", expect_confidence="medium",
                   expect_cmds_any=["conda"]),
    ],

    # ── 已知 AI 工具 ──
    "known_ai": [
        SimProject("comfyanonymous", "ComfyUI", "Python",
                   {"requirements.txt": "torch\n"}, ["python", "pytorch", "comfyui"],
                   is_known=True, expect_strategy="known_project", expect_confidence="high"),
        SimProject("AUTOMATIC1111", "stable-diffusion-webui", "Python",
                   {"requirements.txt": "torch\n"}, ["python", "pytorch", "diffusers"],
                   is_known=True, expect_strategy="known_project", expect_confidence="high"),
        SimProject("ollama", "ollama", "Go",
                   {"go.mod": "module github.com/ollama/ollama\n"}, ["go", "ollama"],
                   is_known=True, expect_strategy="known_project", expect_confidence="high"),
        SimProject("open-webui", "open-webui", "Python",
                   {"requirements.txt": "fastapi\n", "package.json": "{}"},
                   ["python", "node"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
        SimProject("hiyouga", "LLaMA-Factory", "Python",
                   {"requirements.txt": "torch\n", "setup.py": ""},
                   ["python", "pytorch"], is_known=True,
                   expect_strategy="known_project", expect_confidence="high"),
    ],

    "haskell": [
        SimProject("Airsequel", "SQLiteDAV", "Haskell",
                   {"stack.yaml": "resolver: lts-21.15\n", "SQLiteDAV.cabal": "name: SQLiteDAV\n"},
                   ["haskell"], expect_strategy="type_template_haskell",
                   expect_confidence="medium",
                   expect_cmds_any=["ghcup run --ghc recommended --cabal recommended -- cabal build all"]),
        SimProject("KMahoney", "squee", "Haskell",
                   {"stack.yaml": "resolver: lts-16.18\n", "package.yaml": "name: squee\n"},
                   ["haskell"], expect_strategy="type_template_haskell",
                   expect_confidence="medium",
                   expect_cmds_any=["ghcup run --stack latest -- stack build"]),
    ],

    "zig": [
        SimProject("rockorager", "prise", "Zig",
                   {"build.zig": "const std = @import(\"std\");\n", "build.zig.zon": '.{ .name = .prise, .minimum_zig_version = "0.15.2" }\n'},
                   ["zig"], expect_strategy="type_template_zig",
                   expect_confidence="medium",
                   expect_cmds_any=["zig build", "sdkroot"]),
        SimProject("JacobCrabill", "zigdown", "Zig",
                   {"build.zig": "const std = @import(\"std\");\n", ".zig-version": "0.15.1\n"},
                   ["zig"], expect_strategy="type_template_zig",
                   expect_confidence="medium",
                   expect_cmds_any=["zig build"]),
    ],
}


def run_all_sim_tests():
    """运行全部模拟模板匹配测试"""
    try:
        env = EnvironmentDetector().detect()
    except Exception as e:
        print(f"⚠️ EnvironmentDetector 失败: {e}，使用最小化环境")
        env = {
            "os": {"type": "linux", "arch": "x86_64", "is_apple_silicon": False},
            "gpu": {"type": "cpu_only"},
            "package_managers": {"pip3": {"available": True}},
            "runtimes": {"python3": {"available": True}, "git": {"available": True}},
            "hardware": {"cpu_count": 2, "ram_gb": 4.0},
            "disk": {"free_gb": 10.0, "total_gb": 20.0},
            "network": {"github": True, "pypi": True},
        }
    planner = SmartPlanner()

    total = 0
    passed = 0
    failed_details = []
    cat_stats = []

    print(f"\n{BD}{'═'*72}{RS}")
    print(f"{BD}  Level 1-Sim: 模拟模板匹配验证（实际调用 generate_plan）{RS}")
    print(f"{'═'*72}")

    for cat_name, projects in SIM_PROJECTS.items():
        cat_pass = 0
        cat_total = len(projects)
        print(f"\n  {BD}{M}【{cat_name}】{RS} ({cat_total} 项目)")

        for p in projects:
            total += 1
            plan = planner.generate_plan(
                owner=p.owner,
                repo=p.repo,
                env=env,
                project_types=p.project_types,
                dependency_files=p.dep_files,
                readme=p.readme,
            )

            strategy = plan.get("strategy", "")
            confidence = plan.get("confidence", "")
            steps = plan.get("steps", [])
            all_cmds = " ".join(s.get("command", "") for s in steps).lower()

            errors = []

            # 检查策略
            if p.expect_strategy and not strategy.startswith(p.expect_strategy):
                errors.append(f"strategy={strategy}（期望 {p.expect_strategy}）")

            # 检查置信度
            if p.expect_confidence and confidence != p.expect_confidence:
                errors.append(f"confidence={confidence}（期望 {p.expect_confidence}）")

            # 检查步骤数
            if len(steps) == 0:
                errors.append("无步骤生成")

            # 检查关键命令
            if p.expect_cmds_any:
                found = any(kw.lower() in all_cmds for kw in p.expect_cmds_any)
                if not found:
                    errors.append(f"步骤中缺少 {p.expect_cmds_any} 中任一")

            if errors:
                icon = f"{R}❌{RS}"
                failed_details.append((p, errors, strategy, confidence, len(steps)))
            else:
                icon = f"{G}✅{RS}"
                cat_pass += 1
                passed += 1

            detail = f"strategy={strategy} conf={confidence} steps={len(steps)}"
            print(f"    {icon} {p.owner}/{p.repo:30s} {DM}{detail}{RS}")
            for err in errors:
                print(f"       {R}✗ {err}{RS}")

        rate = cat_pass / cat_total * 100 if cat_total else 0
        rc = G if rate >= 80 else (Y if rate >= 60 else R)
        print(f"    {'─'*60}")
        print(f"    {rc}{BD}{cat_pass}/{cat_total} 通过 ({rate:.0f}%){RS}")
        cat_stats.append((cat_name, cat_pass, cat_total, rate))

    # 汇总
    total_rate = passed / total * 100 if total else 0
    rc = G if total_rate >= 80 else (Y if total_rate >= 60 else R)
    print(f"\n{'═'*72}")
    print(f"{BD}  总覆盖率：{rc}{passed}/{total} = {total_rate:.1f}%{RS}")
    print(f"  目标: ≥80%  {'✅ 达标' if total_rate >= 80 else '❌ 未达标'}")

    if failed_details:
        print(f"\n  {R}失败项目详情：{RS}")
        for p, errors, strat, conf, n in failed_details:
            print(f"  {R}• {p.owner}/{p.repo}: {', '.join(errors)}{RS}")

    print(f"{'═'*72}\n")
    return total_rate >= 80


if __name__ == "__main__":
    ok = run_all_sim_tests()
    sys.exit(0 if ok else 1)
