"""
planner.py - 智能安装计划生成器（零 AI、零 API Key）
======================================================

核心设计原则：
  不是所有人都有 AI Key，也不是所有人都能跑本地大模型。
  本模块在完全没有任何 LLM 的情况下，生成高质量的安装计划。

三层生成策略（按质量递减）：
  1. 已知项目数据库   ── 精确匹配 80+ 热门项目，质量最高（confidence=high）
  2. 依赖文件类型模板 ── Python/Node/Docker/Rust/Go，GPU/平台自适应（confidence=medium）
  3. README 规则提取  ── 从代码块提取命令，保底可用（confidence=low）

代码组织（拆分为四个模块）：
  planner.py                → SmartPlanner 主类 + 路由逻辑（本文件）
  planner_helpers.py        → 平台适配辅助函数（纯函数，无状态）
  planner_known_projects.py → 已知项目数据库
  planner_templates.py      → 各语言安装模板（Mixin 类）
"""

from __future__ import annotations

import re
import json
import sys
from pathlib import Path
from typing import Any

# 将 tools 目录加入路径（兼容 from tools.planner 和直接执行）
_THIS_DIR = str(Path(__file__).parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# 导入拆分出去的模块
from planner_helpers import (
    _os_type, _is_apple_silicon, _gpu_type, _cuda_major,
    _has_pm, _has_runtime, _python_cmd, _pip_cmd,
    _venv_activate, _dep_names, _dep_content,
    _is_maturin_project, _preferred_java_version,
    _has_haskell_cabal_file, _stack_resolver, _stack_lts_major,
    _zig_minimum_version, _zig_fallback_version, _version_tuple,
    _zig_uses_legacy_build_api, _haskell_system_packages,
    _haskell_macos_env_prefix, _haskell_repo_template,
    _torch_install_cmd, _node_pm,
    _make_step, _get_gpu_name,
)
from planner_known_projects import _KNOWN_PROJECTS
from planner_templates import PlanTemplateMixin


class SmartPlanner(PlanTemplateMixin):
    """
    零 AI、零 API Key 的智能安装计划生成器。

    设计目标：
      - 没有 AI Key    → 依然可以生成正确的安装步骤
      - 没有本地大模型  → 依然可以自动适配 GPU 和操作系统
      - 没有网络       → 使用缓存的已知项目知识库
    """

    def generate_plan(
        self,
        owner: str,
        repo: str,
        env: dict,
        project_types: list[str],
        dependency_files: dict[str, str],
        readme: str,
        clone_url: str = "",
        source_path: str = "",
    ) -> dict[str, Any]:
        """
        生成完整安装计划，不依赖任何外部 AI 服务。

        Args:
            clone_url:   自定义 clone URL（非 GitHub 项目时使用）
            source_path: 本地路径（已存在于文件系统的项目，跳过 git clone）

        Returns:
            {
                "project_name": str,
                "steps": [{"command": str, "description": str, "_warning": str}, ...],
                "launch_command": str,
                "notes": str,
                "confidence": "high" | "medium" | "low",
                "strategy": "known_project" | "type_template_*" | "readme_extract",
                "mode": "smart_planner",
            }
        """
        # 设置源信息（供模板方法使用）
        self._clone_url = clone_url
        self._source_path = source_path

        key = f"{owner}/{repo}".lower()

        # ── 策略 1：已知项目精确匹配（本地路径跳过，因为可能是私有项目） ──
        if not source_path and key in _KNOWN_PROJECTS:
            plan = self._plan_from_known(key, env)
            plan.update({"confidence": "high", "strategy": "known_project", "mode": "smart_planner"})
            return plan

        # ── 策略 2：依赖文件类型模板 ─────────────────
        # 优先级设计原则：
        #   - 语言特有的信号（如 composer.json=PHP）比通用文件（如 package.json、Dockerfile）更准确
        #   - 很多项目同时有 Dockerfile（容器化）和 package.json（前端）但不是 Docker/Node 项目
        #   - environment.yml 可能只是 CI 配置（如 redis），不代表是 conda 项目
        types = set(project_types)
        dep_keys = _dep_names(dependency_files)

        # ── 优先级设计原则 ──────────────────────────────────────
        # 1. 语言特有信号（如 mix.exs=Elixir, stack.yaml=Haskell）比通用文件更准确
        # 2. 通用文件（Makefile, package.json, Dockerfile）几乎所有项目都可能有
        # 3. 语言类型检测必须优先于通用构建系统
        # 4. 排除规则：当多类型共存时，选择最"核心"的那个
        # ──────────────────────────────────────────────────────

        # conda：仅当确实是 Python 项目 + environment.yml 是唯一构建描述
        if ("python" in types
                and "environment.yml" in dep_keys
                and not (dep_keys & {"requirements.txt", "setup.py", "pyproject.toml", "setup.cfg", "Pipfile"})):
            plan = self._plan_conda(owner, repo, env)
        # docker：仅当 docker 是唯一的核心技术（排除所有语言项目）
        elif "docker" in types and not (types & {
            "python", "node", "dotnet", "java", "go", "rust",
            "ruby", "php", "swift", "c", "cpp", "cmake", "make",
            "kotlin", "scala", "dart", "elixir", "haskell", "lua",
            "perl", "r", "julia", "zig", "clojure", "meson", "shell",
        }):
            plan = self._plan_docker(owner, repo, env)
        # ML 框架（最强信号）
        elif types & {"pytorch", "diffusers", "tensorflow", "gradio"}:
            plan = self._plan_python_ml(owner, repo, env, dep_keys, types)

        # ── 语言特有依赖文件检测（必须在通用 Makefile/package.json 之前） ──
        # Elixir (mix.exs 是唯一标识，比 Makefile/package.json 更准确)
        elif "elixir" in types or "mix.exs" in dep_keys:
            plan = self._plan_elixir(owner, repo, env)
        # Haskell (stack.yaml / .cabal 文件)
        elif "haskell" in types or "stack.yaml" in dep_keys or any(f.endswith(".cabal") for f in dep_keys):
            plan = self._plan_haskell(owner, repo, env, dependency_files)
        # Scala (build.sbt 是唯一标识)
        elif "scala" in types or "build.sbt" in dep_keys:
            plan = self._plan_scala(owner, repo, env)
        # Dart / Flutter (pubspec.yaml 是唯一标识)
        elif "dart" in types or "pubspec.yaml" in dep_keys:
            plan = self._plan_dart(owner, repo, env)
        # Clojure (project.clj / deps.edn)
        elif "clojure" in types or "project.clj" in dep_keys:
            plan = self._plan_clojure(owner, repo, env)
        # Julia (Project.toml)
        elif "julia" in types or "Project.toml" in dep_keys:
            plan = self._plan_julia(owner, repo, env)
        # Zig (build.zig)
        elif "zig" in types or "build.zig" in dep_keys:
            plan = self._plan_zig(owner, repo, env, dependency_files)
        # Lua（语言检测跳过含 Makefile 的多类型项目时用类型字段判断）
        elif "lua" in types and not (types & {"python", "node", "go", "rust", "java"}):
            plan = self._plan_lua(owner, repo, env)
        # Perl（语言检测为 perl 且无更强信号）
        elif "perl" in types and not (types & {"python", "node", "go", "rust", "java"}):
            plan = self._plan_perl(owner, repo, env)
        # R
        elif "r" in types and not (types & {"python", "node"}):
            plan = self._plan_r(owner, repo, env)

        # ── 主流语言（通用构建文件） ──
        # Python（跳过 Rust+Python 混合 / C++项目含 Python 绑定如 grpc）
        elif _is_maturin_project(types, dependency_files):
            plan = self._plan_python_rust_package(owner, repo, env)
        elif ("python" in types or dep_keys & {"requirements.txt", "setup.py", "pyproject.toml"}) and "rust" not in types and not (types & {"c", "cpp", "cmake"}):
            plan = self._plan_python(owner, repo, env, dep_keys)
        # PHP（优先于 Node——PHP 项目常有 package.json 做前端资源）
        elif "php" in types or "composer.json" in dep_keys:
            plan = self._plan_php(owner, repo, env)
        # .NET（优先于 Docker——.NET 项目常有 Dockerfile）
        elif "dotnet" in types:
            plan = self._plan_dotnet(owner, repo, env)
        # Swift（排除 C/C++ 库提供 Package.swift 做 SPM 兼容的情况）
        elif "swift" in types and not (types & {"cpp", "c", "cmake"}):
            plan = self._plan_swift(owner, repo, env)
        # Kotlin（独立 Kotlin 项目，非 Java 混合）
        elif "kotlin" in types and not (types & {"java"}):
            plan = self._plan_kotlin(owner, repo, env)
        # Node.js（排除 Ruby/Elixir/Shell 项目的前端或测试 package.json）
        elif ("node" in types or dep_keys & {"package.json"}) and not (types & {"ruby", "elixir", "lua", "perl", "shell", "c", "cpp", "cmake", "go", "java", "rust"}):
            plan = self._plan_node(owner, repo, env)
        # Rust（排除 C/C++ 项目中的辅助 Cargo.toml）
        elif ("rust" in types or "Cargo.toml" in dep_keys) and not (types & {"c", "cpp"}):
            plan = self._plan_rust(owner, repo, env)
        # Go
        elif "go" in types or "go.mod" in dep_keys:
            plan = self._plan_go(owner, repo, env, dep_keys)
        # CMake
        elif "cmake" in types or "CMakeLists.txt" in dep_keys:
            plan = self._plan_cmake(owner, repo, env)
        # Java
        elif "java" in types or dep_keys & {"pom.xml", "build.gradle", "build.gradle.kts"}:
            plan = self._plan_java(owner, repo, env, dep_keys, readme)
        # Makefile/autotools（跳过 shell 脚本项目——其 Makefile 只是安装/测试辅助）
        elif ("make" in types or "autotools" in types or dep_keys & {"Makefile", "configure", "configure.ac", "Makefile.am"}) and not ("shell" in types and not (types & {"c", "cpp", "cmake"})) and not (types & {"ruby", "python", "node", "go", "php"}):
            plan = self._plan_make(owner, repo, env, dep_keys)
        # Meson 构建系统（在 cmake/make 之后——meson.build 常作为替代构建系统）
        elif "meson" in types or "meson.build" in dep_keys:
            plan = self._plan_meson(owner, repo, env)
        # Ruby（Gemfile 经常是辅助工具而非主项目）
        elif "ruby" in types or "Gemfile" in dep_keys:
            plan = self._plan_ruby(owner, repo, env)
        # Shell 脚本项目（语言检测到 shell，允许含 Makefile/package.json 的项目）
        elif "shell" in types and not (types & {"python", "go", "rust", "java", "cmake", "c", "cpp"}):
            plan = self._plan_shell(owner, repo, env)
        # PlatformIO / Arduino 嵌入式项目
        elif "platformio" in types or "arduino" in types:
            plan = self._plan_platformio(owner, repo, env)
        # C/C++ 通用保底（没有 CMakeLists.txt / Makefile 等构建文件的纯 C/C++ 项目）
        elif types & {"c", "cpp"}:
            plan = self._plan_c_cpp(owner, repo, env)
        else:
            # ── 策略 3：README 规则提取（保底） ──────
            plan = self._plan_from_readme(owner, repo, readme, project_types=project_types)

        plan["mode"] = "smart_planner"
        return plan
