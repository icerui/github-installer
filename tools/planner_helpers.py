"""
planner_helpers.py - 平台适配辅助函数
======================================

从 planner.py 拆分出来的纯函数，无状态。
提供 OS/GPU/工具链检测、命令生成等基础设施。

GPU 自适应（自动选择正确的 PyTorch 安装命令）：
  - Apple MPS (M1/M2/M3/M4)     → pip install torch（原生支持 MPS）
  - NVIDIA CUDA 12+              → --index-url .../cu121
  - NVIDIA CUDA 11               → --index-url .../cu118
  - AMD ROCm                     → --index-url .../rocm5.6
  - CPU 纯算                     → --index-url .../cpu

平台自适应：
  - macOS  → brew / python3 -m venv / source .../activate
  - Linux  → apt/snap / python3 / source .../activate
  - Windows → winget/choco / python -m venv / .\\venv\\Scripts\\activate
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────
#  平台适配辅助函数（纯函数，无状态）
# ─────────────────────────────────────────────

def _os_type(env: dict) -> str:
    return env.get("os", {}).get("type", "linux")


def _is_apple_silicon(env: dict) -> bool:
    return (
        env.get("os", {}).get("is_apple_silicon", False)
        or env.get("os", {}).get("chip", "").startswith("M")
        or (env.get("os", {}).get("type") == "macos"
            and env.get("os", {}).get("arch") == "arm64")
    )


def _gpu_type(env: dict) -> str:
    t = env.get("gpu", {}).get("type", "cpu_only")
    # 兼容 detector.py 的不同写法
    if t in ("apple_mps", "mps"):
        return "mps"
    if t == "cuda":
        return "cuda"
    if t == "rocm":
        return "rocm"
    return "cpu_only"


def _cuda_major(env: dict) -> int:
    ver = env.get("gpu", {}).get("cuda_version", "0")
    try:
        return int(str(ver).split(".")[0])
    except Exception:
        return 0


def _has_pm(env: dict, pm: str) -> bool:
    return pm in env.get("package_managers", {})


def _has_runtime(env: dict, rt: str) -> bool:
    return rt in env.get("runtimes", {})


def _python_cmd(env: dict) -> str:
    """Linux 上优先用 python3，其他用 python"""
    if _os_type(env) == "linux":
        return "python3"
    return "python"


def _pip_cmd(env: dict) -> str:
    if _os_type(env) == "linux":
        return "pip3"
    return "pip"


def _venv_activate(env: dict, venv_name: str = "venv") -> str:
    """返回平台正确的 venv 激活命令"""
    if _os_type(env) == "windows":
        return f"{venv_name}\\Scripts\\activate"
    return f"source {venv_name}/bin/activate"


def _dep_names(dependency_files: dict[str, str]) -> set[str]:
    return {Path(path).name for path in dependency_files}


def _dep_content(dependency_files: dict[str, str], name: str) -> str:
    for path, content in dependency_files.items():
        if Path(path).name == name and "/" not in path:
            return content
    for path, content in dependency_files.items():
        if Path(path).name == name:
            return content
    return ""


def _is_maturin_project(types: set[str], dependency_files: dict[str, str]) -> bool:
    if not ({"python", "rust"} <= types):
        return False
    pyproject = _dep_content(dependency_files, "pyproject.toml")
    return bool(pyproject and re.search(r"maturin|setuptools-rust", pyproject, re.IGNORECASE))


def _preferred_java_version(readme: str) -> str:
    match = re.search(r"(?:openjdk|jdk|java)[^\d]{0,8}(8|11|17|21)", readme, re.IGNORECASE)
    return match.group(1) if match else "17"


def _has_haskell_cabal_file(dependency_files: dict[str, str]) -> bool:
    return any(Path(path).name.endswith(".cabal") for path in dependency_files)


def _stack_resolver(dependency_files: dict[str, str]) -> str:
    stack_yaml = _dep_content(dependency_files, "stack.yaml")
    match = re.search(r'^\s*resolver\s*:\s*["\']?([^\s"\']+)', stack_yaml, re.MULTILINE)
    return match.group(1) if match else ""


def _stack_lts_major(resolver: str) -> int:
    match = re.match(r'lts-(\d+)(?:\.\d+)?', resolver, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _zig_minimum_version(dependency_files: dict[str, str]) -> str:
    build_zon = _dep_content(dependency_files, "build.zig.zon")
    match = re.search(r'\.minimum_zig_version\s*=\s*"([^"]+)"', build_zon)
    if match:
        return match.group(1)
    return _dep_content(dependency_files, ".zig-version").strip()


def _zig_fallback_version(dependency_files: dict[str, str]) -> str:
    min_version = _zig_minimum_version(dependency_files)
    if min_version and _version_tuple(min_version) < (0, 15, 0):
        return min_version
    return "0.14.0"


def _version_tuple(version: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", version)]
    return tuple(numbers[:3]) if numbers else ()


def _zig_uses_legacy_build_api(dependency_files: dict[str, str]) -> bool:
    build_zig = _dep_content(dependency_files, "build.zig")
    min_version = _zig_minimum_version(dependency_files)
    if _version_tuple(min_version) >= (0, 15, 0):
        return False
    legacy_markers = [
        ".root_source_file",
        ".source_file",
        "Build.ExecutableOptions",
    ]
    return any(marker in build_zig for marker in legacy_markers)


def _haskell_system_packages(dependency_files: dict[str, str], env: dict) -> list[str]:
    combined = "\n".join(dependency_files.values()).lower()
    packages: list[str] = ["pkg-config"]

    if any(token in combined for token in ["pcre-light", "pcre-heavy", "libpcre", "pcre "]):
        packages.append("pcre")
    if any(token in combined for token in ["openssl", "libssl", "http-client-tls", "tls", "hsopenssl"]):
        packages.append("openssl")
    if any(token in combined for token in ["gtk", "pango", "gtk+-", "yi-frontend-pango"]):
        packages.append("gtk+3")

    deduped = list(dict.fromkeys(packages))
    if _os_type(env) == "macos":
        return deduped
    if _os_type(env) == "linux":
        mapping = {
            "pkg-config": "pkg-config",
            "pcre": "libpcre3-dev",
            "openssl": "libssl-dev",
            "gtk+3": "libgtk-3-dev",
        }
        return [mapping[pkg] for pkg in deduped if pkg in mapping]
    return []


def _haskell_macos_env_prefix(dependency_files: dict[str, str], env: dict) -> str:
    if _os_type(env) != "macos":
        return ""

    formulas = [pkg for pkg in _haskell_system_packages(dependency_files, env) if pkg in {"pcre", "openssl", "gtk+3"}]
    lines = [
        'BREW_PREFIX="$(brew --prefix)"',
        'export PKG_CONFIG_PATH="$BREW_PREFIX/lib/pkgconfig:$BREW_PREFIX/share/pkgconfig:${PKG_CONFIG_PATH:-}"',
        'export CPATH="$BREW_PREFIX/include:${CPATH:-}"',
        'export LIBRARY_PATH="$BREW_PREFIX/lib:${LIBRARY_PATH:-}"',
        'export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1',
    ]
    for formula in formulas:
        lines.append(
            f'if brew --prefix {formula} >/dev/null 2>&1; then FORMULA_PREFIX="$(brew --prefix {formula})"; '
            'export PKG_CONFIG_PATH="$FORMULA_PREFIX/lib/pkgconfig:$PKG_CONFIG_PATH"; '
            'export CPATH="$FORMULA_PREFIX/include:$CPATH"; '
            'export LIBRARY_PATH="$FORMULA_PREFIX/lib:$LIBRARY_PATH"; fi'
        )
    return "; ".join(lines) + ";"


def _haskell_repo_template(owner: str, repo: str, ghcup_cmd: str, env_prefix: str) -> tuple[str, str, list[str]] | None:
    key = f"{owner}/{repo}".lower()

    if key == "yi-editor/yi":
        build_cmd = (
            "perl -0pi -e 's/extra-deps:\\n  - Hclip-3\\.0\\.0\\.4\\n/"
            "extra-deps:\\n  - Hclip-3.0.0.4\\n"
            "  - vty-6.5\\@sha256:43a4137de7e55cf438a8334cc525fb0e0b4efe78d2ed8bd31b0716eb34993059,3425\\n"
            "  - vty-crossplatform-0.5.0.0\\@sha256:6d057fd8a5582eac3be28c91e99ed3730b729078e107ad19107af46bbb2ea65d,3146\\n"
            "  - vty-unix-0.2.0.0\\@sha256:2af3d0bdae3c4b7b7e567ee374efe32c7439fabdf9096465ce011a6c6736e9ae,2932\\n/' stack.yaml"
            f" && {env_prefix}{ghcup_cmd} run --stack latest -- stack build yi --flag yi:-pango"
        )
        launch_cmd = f"{env_prefix}{ghcup_cmd} run --stack latest -- stack exec yi"
        notes = [
            "检测到 yi 的 Apple Silicon/新 resolver 兼容问题：模板会补齐 vty、vty-crossplatform、vty-unix 的 extra-deps。",
            "模板默认关闭 yi 的 pango 前端，优先构建 VTY CLI 版本，避免 GTK/text 版本窗口冲突。",
        ]
        return build_cmd, launch_cmd, notes

    if key == "chrispenner/rasa":
        build_cmd = (
            "rm -rf .gitinstall-patches/eve-0.1.9.0"
            f" && {env_prefix}{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal get eve-0.1.9.0 --destdir .gitinstall-patches"
            " && perl -0pi -e 's/import Control\\.Monad\\.State/import Control.Monad (join, void)\\nimport Control.Monad.State/'"
            " .gitinstall-patches/eve-0.1.9.0/src/Eve/Internal/Actions.hs .gitinstall-patches/eve-0.1.9.0/src/Eve/Internal/Listeners.hs"
            " && perl -0pi -e 's/\\} deriving \\(Eq\\)/} deriving (Eq, Functor)/g' rasa/src/Rasa/Internal/Range.hs"
            " && perl -0pi -e 's/import Control\\.Monad\\.State/import Control.Monad (void)\\nimport Control.Monad.State/' rasa-ext-cursors/src/Rasa/Ext/Cursors/Internal/Base.hs"
            " && printf '%s\\n' 'packages:' ' ./rasa' ' ./rasa-ext-cmd' ' ./rasa-ext-cursors' ' ./rasa-ext-files'"
            " ' ./rasa-ext-logger' ' ./rasa-ext-views' ' ./rasa-ext-vim' ' ./text-lens' ' ./.gitinstall-patches/eve-0.1.9.0' 'allow-newer: true'"
            " > cabal.project.gitinstall-core"
            f" && {env_prefix}{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal build all --project-file=cabal.project.gitinstall-core"
        )
        launch_cmd = f"{env_prefix}{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal repl rasa --project-file=cabal.project.gitinstall-core"
        notes = [
            "检测到 rasa 的旧生态约束：模板会自动下载并补丁 eve-0.1.9.0，再对 rasa 核心源码打现代 GHC 兼容补丁。",
            "模板默认构建 rasa 的核心包集合，并剥离已知与现代 vty API 不兼容的 slate/example 可执行目标。",
        ]
        return build_cmd, launch_cmd, notes

    if key == "lettier/gifcurry":
        build_cmd = f"{env_prefix}{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal build all"
        launch_cmd = f"{env_prefix}{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal run gifcurry_cli"
        notes = [
            "检测到 gifcurry 在 Apple Silicon 上优先应走 ghcup run + cabal 路径，避免旧 stack resolver 直接落到不受支持的 GHC 二进制。",
            "若后续仍被旧 GUI 目标和精确版本窗拖垮，交由通用 Haskell fixer 自动切换到 headless/CLI 构建。",
        ]
        return build_cmd, launch_cmd, notes

    return None


def _torch_install_cmd(env: dict) -> str:
    """返回适配当前 GPU 的 PyTorch 安装命令（这是 AI 项目最容易装错的一步）"""
    pip = _pip_cmd(env)
    gpu = _gpu_type(env)

    if gpu == "mps":
        # Apple Silicon：PyTorch 原生支持 MPS，直接 pip install
        return f"{pip} install torch torchvision torchaudio"

    if gpu == "cuda":
        major = _cuda_major(env)
        if major >= 12:
            idx = "https://download.pytorch.org/whl/cu121"
        elif major >= 11:
            idx = "https://download.pytorch.org/whl/cu118"
        else:
            # CUDA 10 及更早版本不受 PyTorch 2.x 支持，降级到 CPU
            idx = "https://download.pytorch.org/whl/cpu"
        return f"{pip} install torch torchvision torchaudio --index-url {idx}"

    if gpu == "rocm":
        return f"{pip} install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm5.6"

    # CPU-only（包含 Intel Mac）
    return f"{pip} install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu"


def _node_pm(env: dict) -> tuple[str, str]:
    """返回 (install 命令, dev 启动命令)，优先 pnpm > yarn > npm"""
    if _has_pm(env, "pnpm"):
        return "pnpm install", "pnpm dev"
    if _has_pm(env, "yarn"):
        return "yarn", "yarn dev"
    return "npm install", "npm run dev"



# ─────────────────────────────────────────────
#  模块级工具函数
# ─────────────────────────────────────────────

def _make_step(cmd: str, desc: str, warn: bool = False) -> dict:
    return {
        "command": cmd,
        "description": desc,
        "_warning": "⚠️ 执行前请确认命令来源可信" if warn else "",
    }


def _get_gpu_name(env: dict) -> str:
    gpu = _gpu_type(env)
    if gpu == "mps":
        return f"Apple {env.get('os', {}).get('chip', 'Silicon')} MPS"
    if gpu == "cuda":
        return f"NVIDIA CUDA {env.get('gpu', {}).get('cuda_version', '')}"
    if gpu == "rocm":
        return "AMD ROCm"
    return "CPU（无 GPU 加速）"
