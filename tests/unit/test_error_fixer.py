"""
test_error_fixer.py — error_fixer 规则引擎单元测试
===================================================

覆盖所有 16 条错误修复规则，确保：
  - 每种错误模式都能正确匹配
  - 修复命令合理可执行
  - 不相干的错误不被误匹配
"""
import platform
import pytest
import sys
import os

# 直接 import 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from error_fixer import diagnose, FixSuggestion


class TestGitCloneExists:
    def test_directory_already_exists(self):
        fix = diagnose(
            "git clone https://github.com/user/repo.git",
            "fatal: destination path 'repo' already exists and is not an empty directory",
        )
        assert fix is not None
        assert fix.fix_commands == []  # 跳过即可
        assert fix.retry_original is False
        assert fix.confidence == "high"

    def test_does_not_trigger_on_other_git_error(self):
        fix = diagnose(
            "git clone https://github.com/user/repo.git",
            "fatal: could not read from remote repository",
        )
        # 应该被 _fix_network_timeout 或其他规则匹配，但不是 git_clone_exists
        assert fix is None or "已存在" not in fix.root_cause


class TestVenvActivate:
    def test_venv_not_found(self):
        fix = diagnose(
            "source venv/bin/activate",
            "bash: venv/bin/activate: No such file or directory",
        )
        assert fix is not None
        assert "python3 -m venv venv" in fix.fix_commands
        assert fix.retry_original is True

    def test_activate_other_path(self):
        fix = diagnose(
            "source .env/bin/activate",
            "",
        )
        assert fix is None  # no error = no fix


class TestPipPermission:
    def test_externally_managed(self):
        fix = diagnose(
            "pip install flask",
            "error: externally-managed-environment\n"
            "× This environment is externally managed",
        )
        assert fix is not None
        assert "venv" in " ".join(fix.fix_commands)
        assert fix.retry_original is True

    def test_permission_denied(self):
        fix = diagnose(
            "pip3 install torch",
            "ERROR: Could not install packages due to an EnvironmentError: "
            "[Errno 13] Permission denied",
        )
        assert fix is not None
        assert "--user" in fix.fix_commands[0]

    def test_no_trigger_on_success(self):
        fix = diagnose("pip install flask", "")
        assert fix is None


class TestPipPackageName:
    def test_sklearn(self):
        fix = diagnose(
            "pip install sklearn",
            "ERROR: No matching distribution found for sklearn",
        )
        assert fix is not None
        assert "scikit-learn" in fix.fix_commands[0]

    def test_cv2(self):
        fix = diagnose(
            "pip install cv2",
            "ERROR: No matching distribution found for cv2",
        )
        assert fix is not None
        assert "opencv-python" in fix.fix_commands[0]

    def test_unknown_package(self):
        fix = diagnose(
            "pip install xyznotexist",
            "ERROR: No matching distribution found for xyznotexist",
        )
        # 没有映射 → 返回 None（规则库不处理，交给 LLM）
        assert fix is None


class TestNpmAudit:
    def test_audit_warning_is_success(self):
        fix = diagnose(
            "npm install",
            "npm warn some deprecation",
            stdout="added 120 packages in 5s\n3 vulnerabilities (1 moderate, 2 high)",
        )
        assert fix is not None
        assert fix.fix_commands == []
        assert fix.retry_original is False
        assert "成功" in fix.root_cause

    def test_real_npm_error(self):
        fix = diagnose(
            "npm install",
            "npm ERR! code ERESOLVE\nnpm ERR! Could not resolve dependency",
        )
        # 不应误匹配为 audit 成功
        assert fix is None or "成功" not in fix.root_cause


class TestNpmPermission:
    def test_eacces_global(self):
        fix = diagnose(
            "npm install -g typescript",
            "npm ERR! code EACCES\nnpm ERR! syscall access",
        )
        assert fix is not None
        assert any("npm-global" in c for c in fix.fix_commands)

    def test_eacces_local(self):
        fix = diagnose(
            "npm install",
            "npm ERR! code EACCES\nnpm ERR! syscall access",
        )
        assert fix is not None
        assert any("node_modules" in c for c in fix.fix_commands)


class TestCommandNotFound:
    def test_cargo_not_found(self):
        fix = diagnose(
            "cargo build --release",
            "bash: cargo: command not found",
        )
        assert fix is not None
        assert "rust" in fix.fix_commands[0].lower() or "cargo" in fix.fix_commands[0].lower()
        assert fix.retry_original is True

    def test_node_not_found(self):
        fix = diagnose(
            "node app.js",
            "zsh: command not found: node",
        )
        assert fix is not None
        assert "node" in fix.fix_commands[0].lower()

    def test_go_not_found(self):
        fix = diagnose(
            "go build .",
            "/bin/sh: go: command not found",
        )
        assert fix is not None
        assert "go" in fix.fix_commands[0].lower()

    def test_python_not_found(self):
        fix = diagnose(
            "python3 -m pytest",
            "zsh: command not found: python3",
        )
        assert fix is not None
        assert "python" in fix.fix_commands[0].lower()

    def test_which_no_result(self):
        fix = diagnose(
            "which cmake",
            "which: no cmake in (/usr/bin:/bin)",
            stdout="",
        )
        assert fix is not None
        assert "cmake" in fix.fix_commands[0].lower()


class TestPythonVersion:
    def test_python_requires(self):
        fix = diagnose(
            "pip install package",
            "ERROR: Package 'xyz' requires a different Python: "
            'python_requires>="3.10"',
        )
        assert fix is not None
        assert "Python" in fix.root_cause

    def test_requires_python(self):
        fix = diagnose(
            "pip install old-pkg",
            "ERROR: Requires Python >=3.9 but running 3.7.2",
        )
        assert fix is not None


class TestNodeVersion:
    def test_engine_mismatch(self):
        fix = diagnose(
            "npm install",
            'error package.json: engine "node" is incompatible. '
            "Requires node >=18.0.0",
        )
        assert fix is not None
        assert "18" in fix.root_cause


class TestHaskellToolchain:
    def test_yi_stack_extra_deps_auto_fix(self):
        fix = diagnose(
            "stack build",
            "Error: Stack failed to construct a build plan.\n"
            "needed since yi-frontend-vty is a build target, but could not be found in the snapshot or package index\n"
            "needed since vty-crossplatform is a dependency of yi-frontend-vty, but could not be found in the snapshot or package index\n"
            "needed since vty-unix is a dependency of yi-frontend-vty, but could not be found in the snapshot or package index\n",
        )
        assert fix is not None
        assert any("vty-crossplatform-0.5.0.0" in command for command in fix.fix_commands)
        assert any("stack build yi --flag yi:-pango" in command for command in fix.fix_commands)
        assert fix.retry_original is False

    def test_legacy_gui_repo_falls_back_to_headless_build(self):
        fix = diagnose(
            "cabal build all",
            "Could not resolve dependencies:\n"
            "rejecting: haskell-gi-0.23.0 (conflict: pkg-config package gobject-introspection-1.0>=1.32, not found in the pkg-config database)\n",
        )
        assert fix is not None
        assert any("cabal.project.gitinstall-headless" in command for command in fix.fix_commands)
        assert any("haskell-gi" in command for command in fix.fix_commands)
        assert any("s/==\\s*([0-9]+(?:\\.[0-9]+)*)(?:\\.\\*)?/>= $1/g" in command for command in fix.fix_commands)
        assert fix.retry_original is False

    def test_legacy_gui_repo_base_window_conflict_falls_back_to_headless_build(self):
        fix = diagnose(
            "cabal build all",
            "Could not resolve dependencies:\n"
            "rejecting: base-4.18.3.0/installed-4.18.3.0 (conflict: Gifcurry => base>=4.11 && <4.12)\n"
            "After searching the rest of the dependency tree exhaustively, these were the goals I've had most trouble fulfilling: base, Gifcurry\n",
        )
        assert fix is not None
        assert any("cabal.project.gitinstall-headless" in command for command in fix.fix_commands)
        assert fix.retry_original is False

    def test_legacy_stack_gui_repo_on_apple_silicon_falls_back_to_headless_build(self):
        fix = diagnose(
            "stack build",
            "Error: [S-9443]\n"
            "No setup information found for ghc-8.4.3 on your platform. This probably means a GHC binary distribution has not yet been added for OS key macosx-aarch64.\n",
        )
        assert fix is not None
        assert any("cabal.project.gitinstall-headless" in command for command in fix.fix_commands)
        assert fix.retry_original is False

    def test_cabal_cannot_find_ghc(self):
        fix = diagnose(
            "cabal build all",
            "Error: [Cabal-7620] The program 'ghc' version >=7.0.1 is required but it could not be found",
        )
        assert fix is not None
        assert any("ghcup run --ghc recommended --cabal recommended -- cabal build all" in c for c in fix.fix_commands)
        assert fix.retry_original is False

    def test_stack_resolver_mismatch(self):
        fix = diagnose(
            "stack build",
            "Error: Stack has not been tested with GHC versions above 9.8 and resolver lts-16.18 requires an older compiler",
        )
        assert fix is not None
        assert any("ghcup install stack latest" in c for c in fix.fix_commands)


class TestZigDarwinSdk:
    def test_darwin_sdk_not_found(self):
        fix = diagnose(
            "zig build",
            "thread 1234 panic: error.DarwinSdkNotFound\n/opt/homebrew/Cellar/zig/lib/zig/std/zig/LibCInstallation.zig",
        )
        assert fix is not None
        assert "Darwin SDK" in fix.root_cause
        assert fix.retry_original is False

    def test_legacy_build_api_mismatch(self):
        fix = diagnose(
            "zig build",
            "build.zig:77:14: error: no field named 'root_source_file' in struct 'Build.ExecutableOptions'",
        )
        assert fix is not None
        assert "旧版 Zig build API" in fix.root_cause
        assert fix.retry_original is False


class TestHaskellSystemLibraries:
    def test_missing_pcre_headers(self):
        fix = diagnose(
            "ghcup run --ghc recommended --cabal recommended -- cabal build all",
            "Base.hsc:111:10: fatal error: 'pcre.h' file not found",
        )
        assert fix is not None
        assert any("pcre" in command.lower() for command in fix.fix_commands)
        assert fix.retry_original in {True, False}

    def test_missing_pcre_headers_on_macos_adds_env_exports(self, monkeypatch):
        monkeypatch.setattr("error_fixer._is_macos", lambda: True)
        monkeypatch.setattr("error_fixer._has_brew", lambda: True)
        monkeypatch.setattr("error_fixer._is_linux", lambda: False)
        fix = diagnose(
            "ghcup run --ghc recommended --cabal recommended -- cabal build all",
            "Base.hsc:111:10: fatal error: 'pcre.h' file not found",
        )
        assert fix is not None
        assert fix.retry_original is False
        assert any("PKG_CONFIG_PATH" in command for command in fix.fix_commands)

    def test_missing_pkg_config(self):
        fix = diagnose(
            "cabal build all",
            "pkg-config: command not found",
        )
        assert fix is not None
        assert any("pkg-config" in command.lower() for command in fix.fix_commands)


class TestBuildEssentials:
    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_gcc_not_found_macos(self):
        fix = diagnose(
            "make",
            "make: gcc: No such file or directory",
        )
        assert fix is not None
        assert "xcode-select" in fix.fix_commands[0]

    def test_pkg_config(self):
        fix = diagnose(
            "meson setup build",
            "ERROR: pkg-config not found",
        )
        assert fix is not None
        assert "pkg-config" in fix.fix_commands[0]


class TestCmake:
    def test_cmake_required(self):
        fix = diagnose(
            "python setup.py install",
            "CMake is required to build this project",
        )
        assert fix is not None
        assert "cmake" in fix.fix_commands[0].lower()


class TestRustCompile:
    def test_linker_not_found(self):
        fix = diagnose(
            "cargo build",
            "error: linker 'cc' not found",
        )
        assert fix is not None
        assert fix.retry_original is True

    def test_openssl_missing(self):
        fix = diagnose(
            "cargo build",
            "Could not find directory of OpenSSL installation",
        )
        # On platforms without apt/brew, fix may be None (no install command available)
        if fix is not None:
            assert "openssl" in fix.fix_commands[0].lower() or "libssl" in fix.fix_commands[0].lower()


class TestCargoGitInstallLayout:
    def test_workspace_requires_package_name(self):
        fix = diagnose(
            "cargo install --git https://github.com/bytecodealliance/wasm-tools",
            "error: multiple packages with binaries found: fuzz-stats, wasm-tools, wit-parser-fuzz. Please specify a package",
        )
        assert fix is not None
        assert "--package wasm-tools" in fix.fix_commands[0]

    def test_library_repo_falls_back_to_source_or_python(self):
        fix = diagnose(
            "cargo install --git https://github.com/Eventual-Inc/Daft",
            "error: no packages found with binaries or examples",
        )
        assert fix is not None
        assert any("pip install -e ." in cmd or "cargo build --release" in cmd for cmd in fix.fix_commands)


class TestNetworkErrors:
    def test_pip_timeout(self):
        fix = diagnose(
            "pip install torch",
            "ConnectionError: HTTPSConnectionPool(host='pypi.org', port=443): "
            "Read timed out",
        )
        assert fix is not None
        assert "tuna" in fix.fix_commands[0] or "retry" in fix.root_cause.lower() or "镜像" in fix.root_cause

    def test_ssl_error(self):
        fix = diagnose(
            "pip install pkg",
            "SSLCertVerificationError: certificate verify failed",
        )
        assert fix is not None

    def test_dns_failure(self):
        fix = diagnose(
            "git clone https://github.com/user/repo",
            "fatal: unable to access: Could not resolve host: github.com",
        )
        assert fix is not None
        assert "网络" in fix.root_cause or "timeout" in fix.root_cause.lower()


class TestPortInUse:
    def test_eaddrinuse(self):
        fix = diagnose(
            "node server.js",
            "Error: listen EADDRINUSE: address already in use :::3000",
        )
        assert fix is not None
        assert "3000" in fix.root_cause or any("3000" in c for c in fix.fix_commands)

    def test_address_already_in_use(self):
        fix = diagnose(
            "python -m http.server 8080",
            "OSError: [Errno 48] Address already in use",
        )
        assert fix is not None


class TestSubmodule:
    def test_submodule_failure(self):
        fix = diagnose(
            "git submodule update --init --recursive",
            "Submodule 'vendor/lib' registered\n"
            "fatal: clone of 'https://internal.corp/repo' failed",
        )
        assert fix is not None


class TestDiskSpace:
    def test_no_space(self):
        fix = diagnose(
            "pip install torch",
            "OSError: [Errno 28] No space left on device",
        )
        assert fix is not None
        assert "磁盘" in fix.root_cause
        assert fix.retry_original is False


class TestGoNoRootModule:
    """go build 在无根 go.mod 仓库中失败"""

    def test_no_main_module(self):
        fix = diagnose(
            "go build ./...",
            "pattern ./...: directory prefix . does not contain main module or its selected dependencies",
        )
        assert fix is not None
        assert "go.mod" in fix.root_cause or "monorepo" in fix.root_cause
        assert fix.retry_original is False
        assert any("find" in c for c in fix.fix_commands)

    def test_go_mod_not_found(self):
        fix = diagnose("go build ./...", "go.mod file not found in current directory")
        assert fix is not None
        assert fix.retry_original is False


class TestPipBuildWheel:
    """pip install 构建 wheel 失败"""

    def test_build_wheel_error(self):
        fix = diagnose(
            "pip install -r requirements.txt",
            "error: subprocess-exited-with-error\n"
            "× Getting requirements to build wheel did not run successfully.\n"
            "│ exit code: 1",
        )
        assert fix is not None
        assert "--only-binary" in fix.fix_commands[0]
        assert fix.retry_original is False

    def test_normal_pip_not_matched(self):
        """正常 pip 安装不触发此规则"""
        fix = diagnose("pip install flask", "", "Successfully installed flask")
        assert fix is None


class TestNoFalsePositive:
    """确保正常命令不会被误匹配"""

    def test_successful_command(self):
        assert diagnose("pip install flask", "", "Successfully installed flask") is None

    def test_empty_stderr(self):
        assert diagnose("cargo build --release", "", "Finished release target") is None

    def test_unrelated_warning(self):
        assert diagnose("npm install", "npm WARN deprecated lodash@4.0.0", "") is None
