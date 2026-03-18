"""
多语言模板验证测试
==================

验证 SmartPlanner 的所有主流语言模板能正确生成安装计划。
覆盖：Python, Node, Rust, Go, C/C++ (cmake/make), Java, Ruby, PHP,
      .NET, Swift, Kotlin, Scala, Dart, Elixir, Zig, Haskell, Lua,
      Perl, R, Julia, Clojure, Meson, Shell, Docker, Conda
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from planner import SmartPlanner


# ── 共用环境 ──────────────────────────────────


def _mac_env() -> dict:
    return {
        "os": {"type": "macos", "arch": "arm64", "is_apple_silicon": True, "chip": "M3"},
        "gpu": {"type": "mps"},
        "package_managers": {"brew": {"available": True}},
        "runtimes": {},
    }


def _linux_env() -> dict:
    return {
        "os": {"type": "linux", "arch": "x86_64", "is_apple_silicon": False},
        "gpu": {"type": "nvidia", "name": "RTX 4090", "cuda_version": "12.4"},
        "package_managers": {"apt": {"available": True}},
        "runtimes": {},
    }


def _plan(project_types, dep_files, readme="", env=None):
    """生成计划的快捷方法"""
    planner = SmartPlanner()
    return planner.generate_plan(
        owner="test",
        repo="project",
        env=env or _mac_env(),
        project_types=project_types,
        dependency_files=dep_files,
        readme=readme,
    )


def _commands(plan):
    return [step["command"] for step in plan["steps"]]


# ── Python ────────────────────────────────────


class TestPythonTemplate:
    def test_requirements_txt(self):
        plan = _plan(["python"], {"requirements.txt": "flask\nrequests\n"})
        assert plan["strategy"] == "type_template_python"
        cmds = _commands(plan)
        assert any("venv" in c for c in cmds)
        assert any("pip install" in c for c in cmds)

    def test_setup_py(self):
        plan = _plan(["python"], {"setup.py": "from setuptools import setup\nsetup(name='x')\n"})
        assert plan["strategy"] == "type_template_python"

    def test_pyproject_toml(self):
        plan = _plan(["python"], {"pyproject.toml": "[project]\nname = 'x'\n"})
        assert plan["strategy"] == "type_template_python"


class TestPythonMLTemplate:
    def test_torch_requirements(self):
        plan = _plan(
            ["python", "pytorch"],
            {"requirements.txt": "torch\ntorchvision\nnumpy\n"},
        )
        assert plan["strategy"] == "type_template_python_ml"
        cmds = _commands(plan)
        assert any("torch" in c for c in cmds)

    def test_ml_on_nvidia(self):
        plan = _plan(
            ["python", "pytorch"],
            {"requirements.txt": "torch\ntransformers\n"},
            env=_linux_env(),
        )
        assert plan["strategy"] == "type_template_python_ml"
        cmds = _commands(plan)
        assert any("torch" in c for c in cmds)


# ── Node.js ───────────────────────────────────


class TestNodeTemplate:
    def test_package_json(self):
        plan = _plan(["node"], {"package.json": '{"name":"x","scripts":{"start":"node index.js"}}\n'})
        assert plan["strategy"] == "type_template_node"
        cmds = _commands(plan)
        assert any("npm install" in c for c in cmds)

    def test_yarn_lock(self):
        plan = _plan(["node"], {"package.json": '{"name":"x"}\n', "yarn.lock": ""})
        assert plan["strategy"] == "type_template_node"


# ── Rust ──────────────────────────────────────


class TestRustTemplate:
    def test_cargo_toml(self):
        plan = _plan(["rust"], {"Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n'})
        assert plan["strategy"] == "type_template_rust"
        cmds = _commands(plan)
        assert any("cargo" in c for c in cmds)

    def test_workspace_cargo(self):
        plan = _plan(["rust"], {"Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n'})
        assert plan["strategy"] == "type_template_rust"


# ── Go ────────────────────────────────────────


class TestGoTemplate:
    def test_go_mod(self):
        plan = _plan(["go"], {"go.mod": "module github.com/test/project\ngo 1.21\n"})
        assert plan["strategy"] == "type_template_go"
        cmds = _commands(plan)
        assert any("go build ./..." in c for c in cmds)

    def test_go_no_root_mod(self):
        """无根 go.mod 时应使用 find 查找子模块"""
        plan = _plan(["go"], {})
        assert plan["strategy"] == "type_template_go"
        cmds = _commands(plan)
        assert any("find . -name go.mod" in c for c in cmds)


# ── C/C++ (cmake) ────────────────────────────


class TestCMakeTemplate:
    def test_cmakelists(self):
        plan = _plan(["cmake"], {"CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\nproject(x)\n"})
        assert plan["strategy"] == "type_template_cmake"
        cmds = _commands(plan)
        assert any("cmake" in c for c in cmds)


# ── C/C++ (make) ─────────────────────────────


class TestMakeTemplate:
    def test_makefile(self):
        plan = _plan(["make"], {"Makefile": "all:\n\tgcc -o app main.c\n"})
        assert plan["strategy"] == "type_template_make"
        cmds = _commands(plan)
        assert any("make" in c for c in cmds)


# ── Java ──────────────────────────────────────


class TestJavaTemplate:
    def test_pom_xml(self):
        plan = _plan(["java"], {"pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n"})
        assert plan["strategy"] == "type_template_java"
        cmds = _commands(plan)
        assert any("mvn" in c or "maven" in c.lower() for c in cmds)

    def test_gradle(self):
        plan = _plan(["java"], {"build.gradle": "apply plugin: 'java'\n"})
        assert plan["strategy"] == "type_template_java"
        cmds = _commands(plan)
        assert any("gradle" in c for c in cmds)


# ── Ruby ──────────────────────────────────────


class TestRubyTemplate:
    def test_gemfile(self):
        plan = _plan(["ruby"], {"Gemfile": "source 'https://rubygems.org'\ngem 'rails'\n"})
        assert plan["strategy"] == "type_template_ruby"
        cmds = _commands(plan)
        assert any("bundle install" in c for c in cmds)


# ── PHP ───────────────────────────────────────


class TestPHPTemplate:
    def test_composer_json(self):
        plan = _plan(["php"], {"composer.json": '{"require":{"php":">=8.0"}}\n'})
        assert plan["strategy"] == "type_template_php"
        cmds = _commands(plan)
        assert any("composer install" in c for c in cmds)


# ── .NET ──────────────────────────────────────


class TestDotNetTemplate:
    def test_csproj(self):
        plan = _plan(["dotnet"], {"project.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n'})
        assert plan["strategy"] == "type_template_dotnet"
        cmds = _commands(plan)
        assert any("dotnet" in c for c in cmds)


# ── Swift ─────────────────────────────────────


class TestSwiftTemplate:
    def test_package_swift(self):
        plan = _plan(["swift"], {"Package.swift": "import PackageDescription\n"})
        assert plan["strategy"] == "type_template_swift"
        cmds = _commands(plan)
        assert any("swift build" in c for c in cmds)


# ── Kotlin ────────────────────────────────────


class TestKotlinTemplate:
    def test_build_gradle_kts(self):
        plan = _plan(["kotlin"], {"build.gradle.kts": "plugins { kotlin(\"jvm\") }\n"})
        assert plan["strategy"] == "type_template_kotlin"
        cmds = _commands(plan)
        assert any("gradle" in c for c in cmds)


# ── Scala ─────────────────────────────────────


class TestScalaTemplate:
    def test_build_sbt(self):
        plan = _plan(["scala"], {"build.sbt": 'name := "x"\nscalaVersion := "3.3.1"\n'})
        assert plan["strategy"] == "type_template_scala"
        cmds = _commands(plan)
        assert any("sbt" in c for c in cmds)


# ── Dart/Flutter ──────────────────────────────


class TestDartTemplate:
    def test_pubspec_yaml(self):
        plan = _plan(["dart"], {"pubspec.yaml": "name: x\nenvironment:\n  sdk: '>=3.0.0'\n"})
        assert plan["strategy"] == "type_template_dart"
        cmds = _commands(plan)
        assert any("dart" in c or "flutter" in c for c in cmds)


# ── Elixir ────────────────────────────────────


class TestElixirTemplate:
    def test_mix_exs(self):
        plan = _plan(["elixir"], {"mix.exs": "defmodule X.MixProject do\n  use Mix.Project\nend\n"})
        assert plan["strategy"] == "type_template_elixir"
        cmds = _commands(plan)
        assert any("mix" in c for c in cmds)


# ── Zig ───────────────────────────────────────


class TestZigTemplate:
    def test_build_zig(self):
        plan = _plan(["zig"], {"build.zig": "const std = @import(\"std\");\n"})
        assert plan["strategy"] == "type_template_zig"
        cmds = _commands(plan)
        assert any("zig build" in c for c in cmds)


# ── Lua ───────────────────────────────────────


class TestLuaTemplate:
    def test_rockspec(self):
        plan = _plan(["lua"], {"x-1.0-1.rockspec": "package = 'x'\n"})
        assert plan["strategy"] == "type_template_lua"
        cmds = _commands(plan)
        assert any("luarocks" in c for c in cmds)


# ── Perl ──────────────────────────────────────


class TestPerlTemplate:
    def test_makefile_pl(self):
        plan = _plan(["perl"], {"Makefile.PL": "use ExtUtils::MakeMaker;\n"})
        assert plan["strategy"] == "type_template_perl"
        cmds = _commands(plan)
        assert any("cpanm" in c or "cpan" in c for c in cmds)


# ── R ─────────────────────────────────────────


class TestRTemplate:
    def test_description(self):
        plan = _plan(["r"], {"DESCRIPTION": "Package: x\nType: Package\nVersion: 0.1\n"})
        assert plan["strategy"] == "type_template_r"
        cmds = _commands(plan)
        assert any("Rscript" in c or "install.packages" in c for c in cmds)


# ── Julia ─────────────────────────────────────


class TestJuliaTemplate:
    def test_project_toml(self):
        plan = _plan(["julia"], {"Project.toml": "name = \"X\"\nuuid = \"abc\"\n"})
        assert plan["strategy"] == "type_template_julia"
        cmds = _commands(plan)
        assert any("julia" in c.lower() for c in cmds)


# ── Clojure ───────────────────────────────────


class TestClojureTemplate:
    def test_deps_edn(self):
        plan = _plan(["clojure"], {"deps.edn": "{:deps {}}\n"})
        assert plan["strategy"] == "type_template_clojure"
        cmds = _commands(plan)
        assert any("lein" in c or "clj" in c or "clojure" in c for c in cmds)


# ── Meson ─────────────────────────────────────


class TestMesonTemplate:
    def test_meson_build(self):
        plan = _plan(["meson"], {"meson.build": "project('x', 'c')\n"})
        assert plan["strategy"] == "type_template_meson"
        cmds = _commands(plan)
        assert any("meson" in c for c in cmds)


# ── Docker ────────────────────────────────────


class TestDockerTemplate:
    def test_dockerfile(self):
        plan = _plan(["docker"], {"Dockerfile": "FROM python:3.12\nRUN pip install flask\n"})
        assert plan["strategy"] == "type_template_docker"
        cmds = _commands(plan)
        assert any("docker" in c for c in cmds)


# ── Shell ─────────────────────────────────────


class TestShellTemplate:
    def test_shell_script(self):
        plan = _plan(["shell"], {"install.sh": "#!/bin/bash\necho hello\n"})
        assert plan["strategy"] == "type_template_shell"


# ── Conda ─────────────────────────────────────


class TestCondaTemplate:
    def test_environment_yml(self):
        plan = _plan(["python", "conda"], {"environment.yml": "name: env\ndependencies:\n  - numpy\n"})
        assert plan["strategy"] == "type_template_conda"
        cmds = _commands(plan)
        assert any("conda" in c for c in cmds)
