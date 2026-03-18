"""
planner_helpers.py 额外覆盖 — GPU/平台特殊路径, Haskell/Zig/Maturin 工具函数
"""
import os
import sys
import re
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))

from planner_helpers import (
    _gpu_type, _cuda_major, _has_pm, _has_runtime,
    _python_cmd, _pip_cmd, _venv_activate, _dep_names, _dep_content,
    _is_maturin_project, _preferred_java_version,
    _has_haskell_cabal_file, _stack_resolver, _stack_lts_major,
    _zig_minimum_version, _zig_fallback_version, _version_tuple,
    _zig_uses_legacy_build_api, _haskell_system_packages,
    _haskell_macos_env_prefix, _haskell_repo_template,
    _torch_install_cmd, _node_pm, _make_step, _get_gpu_name,
)


# ─── GPU helpers ─────────────────────────────

class TestGPUHelpers:
    @pytest.mark.parametrize("gpu_type,expected", [
        ("apple_mps", "mps"), ("mps", "mps"),
        ("cuda", "cuda"), ("rocm", "rocm"),
        ("cpu_only", "cpu_only"), ("none", "cpu_only"),
    ])
    def test_gpu_type(self, gpu_type, expected):
        assert _gpu_type({"gpu": {"type": gpu_type}}) == expected

    def test_gpu_type_missing(self):
        assert _gpu_type({}) == "cpu_only"

    @pytest.mark.parametrize("ver,expected", [
        ("12.1", 12), ("11.8", 11), ("0", 0), ("", 0),
    ])
    def test_cuda_major(self, ver, expected):
        assert _cuda_major({"gpu": {"cuda_version": ver}}) == expected

    def test_cuda_major_invalid(self):
        assert _cuda_major({"gpu": {"cuda_version": "abc"}}) == 0


# ─── dep helpers ─────────────────────────────

class TestDepHelpers:
    def test_dep_names(self):
        files = {"requirements.txt": "torch", "setup.py": ""}
        assert _dep_names(files) == {"requirements.txt", "setup.py"}

    def test_dep_content_root_priority(self):
        files = {"requirements.txt": "root", "sub/requirements.txt": "sub"}
        assert _dep_content(files, "requirements.txt") == "root"

    def test_dep_content_fallback_to_subdir(self):
        files = {"sub/requirements.txt": "sub"}
        assert _dep_content(files, "requirements.txt") == "sub"

    def test_dep_content_missing(self):
        assert _dep_content({}, "missing.txt") == ""


# ─── maturin / java / haskell ────────────────

class TestProjectDetection:
    def test_is_maturin_project(self):
        dep = {"pyproject.toml": "[build-system]\nrequires = ['maturin']"}
        assert _is_maturin_project({"python", "rust"}, dep) is True

    def test_not_maturin_missing_rust(self):
        dep = {"pyproject.toml": "maturin"}
        assert _is_maturin_project({"python"}, dep) is False

    @pytest.mark.parametrize("readme,expected", [
        ("requires JDK 11", "11"),
        ("openjdk-17", "17"),
        ("java 21 required", "21"),
        ("no version here", "17"),  # default
    ])
    def test_preferred_java_version(self, readme, expected):
        assert _preferred_java_version(readme) == expected

    def test_has_haskell_cabal_file(self):
        assert _has_haskell_cabal_file({"mylib.cabal": ""}) is True
        assert _has_haskell_cabal_file({"setup.py": ""}) is False


# ─── Stack/Zig ───────────────────────────────

class TestStackZig:
    def test_stack_resolver(self):
        files = {"stack.yaml": "resolver: lts-22.0\n"}
        assert _stack_resolver(files) == "lts-22.0"

    def test_stack_resolver_missing(self):
        assert _stack_resolver({}) == ""

    @pytest.mark.parametrize("resolver,expected", [
        ("lts-22.0", 22), ("lts-18.28", 18), ("nightly-2024-01-01", 0), ("", 0),
    ])
    def test_stack_lts_major(self, resolver, expected):
        assert _stack_lts_major(resolver) == expected

    def test_zig_minimum_version(self):
        files = {"build.zig.zon": '.minimum_zig_version = "0.13.0"'}
        assert _zig_minimum_version(files) == "0.13.0"

    def test_zig_minimum_from_zig_version_file(self):
        files = {".zig-version": "0.12.0\n"}
        assert _zig_minimum_version(files) == "0.12.0"

    def test_zig_fallback_version(self):
        # Old version → use that version
        files = {"build.zig.zon": '.minimum_zig_version = "0.12.0"'}
        assert _zig_fallback_version(files) == "0.12.0"

    def test_zig_fallback_default(self):
        assert _zig_fallback_version({}) == "0.14.0"

    @pytest.mark.parametrize("ver,expected", [
        ("1.2.3", (1, 2, 3)),
        ("0.15.0", (0, 15, 0)),
        ("", ()),
    ])
    def test_version_tuple(self, ver, expected):
        assert _version_tuple(ver) == expected

    def test_zig_uses_legacy_build_api(self):
        files = {"build.zig": "const exe = b.addExecutable(.root_source_file = ...);"}
        assert _zig_uses_legacy_build_api(files) is True

    def test_zig_not_legacy_new_version(self):
        files = {
            "build.zig": "const exe = b.addExecutable(.root_source_file = ...);",
            "build.zig.zon": '.minimum_zig_version = "0.15.0"',
        }
        assert _zig_uses_legacy_build_api(files) is False


# ─── Haskell system packages and env prefix ──

class TestHaskellHelpers:
    def test_system_packages_pcre(self):
        dep = {"mylib.cabal": "build-depends: pcre-light"}
        pkgs = _haskell_system_packages(dep, {"os": {"type": "macos"}})
        assert "pcre" in pkgs

    def test_system_packages_openssl(self):
        dep = {"stack.yaml": "extra-deps:\n  - http-client-tls"}
        pkgs = _haskell_system_packages(dep, {"os": {"type": "linux"}})
        assert "libssl-dev" in pkgs

    def test_system_packages_linux_mapping(self):
        dep = {"x.cabal": "pcre-light\nopenssl\ngtk"}
        pkgs = _haskell_system_packages(dep, {"os": {"type": "linux"}})
        assert "libpcre3-dev" in pkgs
        assert "libssl-dev" in pkgs
        assert "libgtk-3-dev" in pkgs

    def test_macos_env_prefix(self):
        dep = {"x.cabal": "pcre-light\nopenssl"}
        prefix = _haskell_macos_env_prefix(dep, {"os": {"type": "macos"}})
        assert "BREW_PREFIX" in prefix
        assert "PKG_CONFIG_PATH" in prefix

    def test_macos_env_prefix_non_macos(self):
        dep = {"x.cabal": "pcre-light"}
        prefix = _haskell_macos_env_prefix(dep, {"os": {"type": "linux"}})
        assert prefix == ""

    def test_repo_template_yi(self):
        result = _haskell_repo_template("yi-editor", "yi", "ghcup", "")
        assert result is not None
        build_cmd, launch_cmd, notes = result
        assert "stack build" in build_cmd
        assert "pango" in build_cmd

    def test_repo_template_rasa(self):
        result = _haskell_repo_template("chrispenner", "rasa", "ghcup", "")
        assert result is not None
        assert "cabal build" in result[0]

    def test_repo_template_gifcurry(self):
        result = _haskell_repo_template("lettier", "gifcurry", "ghcup", "")
        assert result is not None
        assert "cabal build" in result[0]

    def test_repo_template_unknown(self):
        result = _haskell_repo_template("unknown", "project", "ghcup", "")
        assert result is None


# ─── torch/node/step helpers ─────────────────

class TestStepHelpers:
    def test_torch_rocm(self):
        env = {"os": {"type": "linux"}, "gpu": {"type": "rocm"}, "package_managers": {}}
        cmd = _torch_install_cmd(env)
        assert "rocm" in cmd

    def test_torch_old_cuda(self):
        env = {"os": {"type": "linux"}, "gpu": {"type": "cuda", "cuda_version": "10.2"},
               "package_managers": {}}
        cmd = _torch_install_cmd(env)
        assert "cpu" in cmd

    def test_node_pm_yarn(self):
        env = {"os": {"type": "linux"}, "package_managers": {"yarn": {}}}
        install, dev = _node_pm(env)
        assert install == "yarn"
        assert dev == "yarn dev"

    def test_node_pm_npm(self):
        env = {"os": {"type": "linux"}, "package_managers": {"npm": {}}}
        install, dev = _node_pm(env)
        assert install == "npm install"

    def test_make_step(self):
        s = _make_step("echo hi", "test step")
        assert s["command"] == "echo hi"
        assert s["_warning"] == ""

    def test_make_step_with_warning(self):
        s = _make_step("curl | sh", "risky", warn=True)
        assert "⚠️" in s["_warning"]

    @pytest.mark.parametrize("gpu,expected_sub", [
        ({"os": {"type": "macos", "chip": "M3"}, "gpu": {"type": "apple_mps"}}, "Apple"),
        ({"os": {"type": "linux"}, "gpu": {"type": "cuda", "cuda_version": "12.1"}}, "CUDA"),
        ({"os": {"type": "linux"}, "gpu": {"type": "rocm"}}, "ROCm"),
        ({"os": {"type": "linux"}, "gpu": {"type": "cpu_only"}}, "CPU"),
    ])
    def test_get_gpu_name(self, gpu, expected_sub):
        name = _get_gpu_name(gpu)
        assert expected_sub in name
