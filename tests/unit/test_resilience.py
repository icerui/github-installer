"""
test_resilience.py - 安装韧性层完整测试
=========================================
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from resilience import (
    _dep_content,
    _dep_names,
    _is_maturin_project,
    _run_quiet,
    _version_tuple,
    _zig_download_info,
    _zig_fallback_version,
    _zig_minimum_version,
    _zig_uses_legacy_build_api,
    enhance_plan_with_preflight,
    generate_fallback_plans,
    get_apt_name,
    get_brew_name,
    get_fallback_plan_for_failure,
    preflight_check,
    PreflightResult,
)


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────

class TestDepHelpers:
    def test_dep_names_none(self):
        assert _dep_names(None) == set()

    def test_dep_names_extracts_filenames(self):
        files = {"src/requirements.txt": "flask", "setup.py": "setup()"}
        assert _dep_names(files) == {"requirements.txt", "setup.py"}

    def test_dep_content_root_first(self):
        files = {"package.json": "root", "sub/package.json": "sub"}
        assert _dep_content(files, "package.json") == "root"

    def test_dep_content_fallback_subdir(self):
        files = {"sub/package.json": "sub"}
        assert _dep_content(files, "package.json") == "sub"

    def test_dep_content_none(self):
        assert _dep_content(None, "x") == ""

    def test_dep_content_not_found(self):
        assert _dep_content({"a.txt": "hi"}, "b.txt") == ""


class TestIsMaturin:
    def test_maturin_project(self):
        files = {"pyproject.toml": "[build-system]\nrequires = ['maturin']"}
        assert _is_maturin_project({"python", "rust"}, files) is True

    def test_not_maturin_no_rust(self):
        files = {"pyproject.toml": "maturin"}
        assert _is_maturin_project({"python"}, files) is False

    def test_not_maturin_no_keyword(self):
        files = {"pyproject.toml": "setuptools"}
        assert _is_maturin_project({"python", "rust"}, files) is False


class TestVersionTuple:
    def test_normal(self):
        assert _version_tuple("0.14.0") == (0, 14, 0)

    def test_empty(self):
        assert _version_tuple("") == ()

    def test_two_parts(self):
        assert _version_tuple("1.2") == (1, 2)


class TestZigHelpers:
    def test_uses_legacy_build_api(self):
        files = {"build.zig": ".root_source_file"}
        assert _zig_uses_legacy_build_api(files) is True

    def test_no_build_zig(self):
        assert _zig_uses_legacy_build_api({}) is False

    def test_modern_zig_not_legacy(self):
        files = {
            "build.zig": "modern code",
            "build.zig.zon": '.minimum_zig_version = "0.15.0"',
        }
        assert _zig_uses_legacy_build_api(files) is False

    def test_minimum_version_from_zon(self):
        files = {"build.zig.zon": '.minimum_zig_version = "0.13.0"'}
        assert _zig_minimum_version(files) == "0.13.0"

    def test_minimum_version_from_zig_version(self):
        files = {".zig-version": "0.12.0\n"}
        assert _zig_minimum_version(files) == "0.12.0"

    def test_fallback_version_defaults(self):
        assert _zig_fallback_version({}) == "0.14.0"

    def test_fallback_version_uses_legacy(self):
        files = {"build.zig.zon": '.minimum_zig_version = "0.13.0"'}
        assert _zig_fallback_version(files) == "0.13.0"

    def test_download_info_macos_arm(self):
        env = {"os": {"type": "macos", "arch": "arm64"}}
        result = _zig_download_info(env, "0.14.0")
        assert result is not None
        assert "aarch64" in result[0]

    def test_download_info_linux_x86(self):
        env = {"os": {"type": "linux", "arch": "x86_64"}}
        result = _zig_download_info(env, "0.14.0")
        assert result is not None
        assert "linux" in result[0]

    def test_download_info_windows(self):
        env = {"os": {"type": "windows", "arch": "x86_64"}}
        result = _zig_download_info(env, "0.14.0")
        assert result is None


class TestNameMaps:
    def test_brew_mapped(self):
        assert get_brew_name("delta") == "git-delta"

    def test_brew_unmapped(self):
        assert get_brew_name("flask") == "flask"

    def test_apt_mapped(self):
        assert get_apt_name("ninja") == "ninja-build"

    def test_apt_unmapped(self):
        assert get_apt_name("flask") == "flask"


# ─────────────────────────────────────────────
#  _run_quiet
# ─────────────────────────────────────────────

class TestRunQuiet:
    @pytest.mark.skipif(platform.system() == "Windows", reason="echo is a shell built-in on Windows")
    def test_success(self):
        code, out = _run_quiet(["echo", "hello"])
        assert code == 0
        assert "hello" in out

    def test_not_found(self):
        code, out = _run_quiet(["nonexistent_command_xyz123"])
        assert code == -1

    @pytest.mark.skipif(platform.system() == "Windows", reason="sleep command differs on Windows")
    def test_timeout(self):
        code, out = _run_quiet(["sleep", "30"], timeout=1)
        assert code == -1


# ─────────────────────────────────────────────
#  Preflight Check
# ─────────────────────────────────────────────

class TestPreflightCheck:
    def test_all_ready(self):
        steps = [{"command": "echo hello"}, {"command": "ls -la"}]
        result = preflight_check(steps)
        assert result.all_ready is True

    def test_empty_steps(self):
        result = preflight_check([])
        assert result.all_ready is True

    def test_skip_builtins(self):
        steps = [{"command": "cd /tmp && echo hi && export FOO=bar"}]
        result = preflight_check(steps)
        assert result.all_ready is True

    @patch("resilience._has_command", side_effect=lambda c: c != "cargo")
    @patch("resilience._is_macos", return_value=True)
    @patch("resilience._is_linux", return_value=False)
    def test_missing_cargo_macos(self, _l, _m, _h):
        steps = [{"command": "cargo build --release"}]
        result = preflight_check(steps)
        assert "cargo" in result.missing_tools
        assert not result.all_ready
        assert any("brew install rust" in c["command"] for c in result.install_commands)

    @patch("resilience._has_command", side_effect=lambda c: c != "npm")
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=True)
    def test_missing_npm_linux(self, _l, _m, _h):
        steps = [{"command": "npm install"}]
        result = preflight_check(steps)
        assert "npm" in result.missing_tools
        assert any("apt" in c["command"] for c in result.install_commands)


# ─────────────────────────────────────────────
#  enhance_plan_with_preflight
# ─────────────────────────────────────────────

class TestEnhancePlan:
    def test_no_missing(self):
        plan = {"steps": [{"command": "echo hi"}]}
        result = enhance_plan_with_preflight(plan)
        assert "_preflight" not in result

    @patch("resilience._has_command", side_effect=lambda c: c != "cargo")
    @patch("resilience._is_macos", return_value=True)
    @patch("resilience._is_linux", return_value=False)
    def test_inserts_preflight_steps(self, _l, _m, _h):
        plan = {"steps": [{"command": "cargo build"}]}
        result = enhance_plan_with_preflight(plan)
        assert "_preflight" in result
        assert result["_preflight"]["install_count"] > 0
        assert "brew install" in result["steps"][0]["command"]


# ─────────────────────────────────────────────
#  generate_fallback_plans
# ─────────────────────────────────────────────

class TestGenerateFallbacks:
    def test_generate_legacy_zig_fallback_plan(self):
        env = {"os": {"type": "macos", "arch": "arm64"}}
        dependency_files = {
            "build.zig": 'const exe = b.addExecutable(.{ .name = "rl", .root_source_file = b.path("src/main.zig") });',
        }
        plans = generate_fallback_plans(
            owner="kiedtl", repo="roguelike",
            project_types=["zig"], env=env,
            dependency_files=dependency_files,
        )
        legacy = next((p for p in plans if p.strategy == "zig_legacy_0_14_0_build"), None)
        assert legacy is not None
        assert any("--recurse-submodules" in s["command"] for s in legacy.steps)
        assert any("0.14.0" in s["command"] for s in legacy.steps)

    def test_generate_legacy_zig_fallback_uses_declared_minimum_version(self):
        env = {"os": {"type": "macos", "arch": "arm64"}}
        dependency_files = {
            "build.zig": '.root_source_file = b.path("src/main.zig")',
            "build.zig.zon": '.{ .name = "legacy", .minimum_zig_version = "0.13.0" }',
        }
        plans = generate_fallback_plans(
            owner="legacy", repo="zig-app",
            project_types=["zig"], env=env,
            dependency_files=dependency_files,
        )
        legacy = next((p for p in plans if p.strategy == "zig_legacy_0_14_0_build"), None)
        assert legacy is not None
        assert any("0.13.0" in s["command"] for s in legacy.steps)

    def test_does_not_generate_legacy_zig_for_015_plus(self):
        env = {"os": {"type": "macos", "arch": "arm64"}}
        dependency_files = {
            "build.zig": '.root_source_file = b.path("src/movycat.zig")',
            "build.zig.zon": '.{ .name = "movycat", .minimum_zig_version = "0.15.2" }',
        }
        plans = generate_fallback_plans(
            owner="M64GitHub", repo="movycat",
            project_types=["zig"], env=env,
            dependency_files=dependency_files,
        )
        assert all(p.strategy != "zig_legacy_0_14_0_build" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=True)
    @patch("resilience._is_linux", return_value=False)
    @patch("resilience.brew_has_package", return_value=True)
    def test_brew_tier1(self, _b, _l, _m, _h):
        env = {"os": {"type": "macos", "arch": "arm64"}}
        plans = generate_fallback_plans("sharkdp", "fd", ["rust"], env)
        tier1 = [p for p in plans if p.tier == 1]
        assert len(tier1) >= 1
        assert tier1[0].strategy == "brew_install"

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=True)
    @patch("resilience.apt_has_package", return_value=True)
    def test_apt_tier1(self, _a, _l, _m, _h):
        env = {"os": {"type": "linux", "arch": "x86_64"}}
        plans = generate_fallback_plans("sharkdp", "fd", ["rust"], env)
        tier1 = [p for p in plans if p.tier == 1]
        assert len(tier1) >= 1
        assert tier1[0].strategy == "apt_install"

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_rust_cargo_install(self, _l, _m, _h):
        env = {"os": {"type": "linux", "arch": "x86_64"}}
        plans = generate_fallback_plans("sharkdp", "fd", ["rust"], env)
        assert any(p.strategy == "cargo_install" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_python_pip_install(self, _l, _m, _h):
        env = {}
        plans = generate_fallback_plans("pallets", "flask", ["python"], env)
        assert any(p.strategy == "pip_install" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_go_build(self, _l, _m, _h):
        env = {}
        plans = generate_fallback_plans("junegunn", "fzf", ["go"], env)
        assert any(p.strategy == "go_build" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_node_npm_install(self, _l, _m, _h):
        env = {}
        plans = generate_fallback_plans("vercel", "next.js", ["node"], env,
                                        dependency_files={"package.json": "{}"})
        assert any(p.strategy == "npm_install" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_ruby_gem_install(self, _l, _m, _h):
        env = {}
        plans = generate_fallback_plans("jekyll", "jekyll", ["ruby"], env)
        assert any(p.strategy == "gem_install" for p in plans)

    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_cmake_build(self, _l, _m, _h):
        env = {}
        plans = generate_fallback_plans("llvm", "llvm", ["cmake", "cpp"], env)
        assert any(p.strategy == "source_cmake_build" for p in plans)


# ─────────────────────────────────────────────
#  get_fallback_plan_for_failure
# ─────────────────────────────────────────────

class TestGetFallback:
    @patch("resilience._has_command", return_value=True)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_returns_next_plan(self, _l, _m, _h):
        env = {}
        result = get_fallback_plan_for_failure(
            "sharkdp", "fd", ["rust"], env,
            failed_strategy="cargo_install",
        )
        assert result is not None
        assert result.strategy != "cargo_install"

    @patch("resilience._has_command", return_value=False)
    @patch("resilience._is_macos", return_value=False)
    @patch("resilience._is_linux", return_value=False)
    def test_returns_none_when_no_alternatives(self, _l, _m, _h):
        env = {}
        result = get_fallback_plan_for_failure(
            "sharkdp", "fd", ["rust"], env,
            failed_strategy="nonexistent",
        )
        assert result is None
