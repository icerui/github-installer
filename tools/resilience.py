"""
resilience.py — 安装韧性层（系统性解决任意 GitHub 项目安装问题）
================================================================

核心思想：
  不需要测试 GitHub 上每一个项目。只需要让安装策略足够聪明——
  像浏览器不需要测试每个网页，只要 HTML 解析器足够健壮。

三大机制：
  1. 预检层 (Preflight)    — 执行前检测缺失工具，提前安装
  2. 多策略回退 (Fallback) — Plan A 失败 → 自动 Plan B → Plan C
  3. Brew 探测 (BrewProbe) — 自动探测 brew/apt 是否有现成包

策略优先级（可靠性从高到低）：
  Tier 1 — 包管理器安装 (brew install X)     → 99% 成功率
  Tier 2 — 语言包管理器 (cargo/pip/go install) → 90% 成功率  
  Tier 3 — 源码编译 (git clone + build)       → 70% 成功率
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────
#  Brew / Apt 包探测
# ─────────────────────────────────────────────

def _run_quiet(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """安全执行命令，返回 (exit_code, stdout)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def brew_has_package(name: str) -> bool:
    """检查 brew 中是否有该包（不下载，只查询）"""
    code, _ = _run_quiet(["brew", "info", "--json=v2", name])
    return code == 0


def apt_has_package(name: str) -> bool:
    """检查 apt 中是否有该包"""
    code, _ = _run_quiet(["apt-cache", "show", name])
    return code == 0


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _has_command(cmd: str) -> bool:
    """检查系统命令是否可用"""
    return shutil.which(cmd) is not None


def _dep_names(dependency_files: dict[str, str] | None) -> set[str]:
    if not dependency_files:
        return set()
    return {Path(path).name for path in dependency_files}


def _dep_content(dependency_files: dict[str, str] | None, name: str) -> str:
    if not dependency_files:
        return ""
    for path, content in dependency_files.items():
        if Path(path).name == name and "/" not in path:
            return content
    for path, content in dependency_files.items():
        if Path(path).name == name:
            return content
    return ""


def _is_maturin_project(project_types: set[str], dependency_files: dict[str, str] | None) -> bool:
    if not ({"python", "rust"} <= project_types):
        return False
    pyproject = _dep_content(dependency_files, "pyproject.toml")
    return bool(pyproject and re.search(r"maturin|setuptools-rust", pyproject, re.IGNORECASE))


def _zig_uses_legacy_build_api(dependency_files: dict[str, str] | None) -> bool:
    build_zig = _dep_content(dependency_files, "build.zig")
    if not build_zig:
        return False
    build_zon = _dep_content(dependency_files, "build.zig.zon")
    match = re.search(r'\.minimum_zig_version\s*=\s*"([^"]+)"', build_zon)
    if match:
        version = tuple(int(part) for part in re.findall(r"\d+", match.group(1))[:3])
        if version >= (0, 15, 0):
            return False
    legacy_markers = [
        ".root_source_file",
        ".source_file",
        "Build.ExecutableOptions",
    ]
    return any(marker in build_zig for marker in legacy_markers)


def _zig_minimum_version(dependency_files: dict[str, str] | None) -> str:
    build_zon = _dep_content(dependency_files, "build.zig.zon")
    match = re.search(r'\.minimum_zig_version\s*=\s*"([^"]+)"', build_zon)
    if match:
        return match.group(1)
    return _dep_content(dependency_files, ".zig-version").strip()


def _version_tuple(version: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", version)]
    return tuple(numbers[:3]) if numbers else ()


def _zig_fallback_version(dependency_files: dict[str, str] | None) -> str:
    min_version = _zig_minimum_version(dependency_files)
    if min_version and _version_tuple(min_version) < (0, 15, 0):
        return min_version
    return "0.14.0"


def _zig_download_info(env: dict, version: str) -> tuple[str, str] | None:
    os_info = env.get("os", {})
    os_type = os_info.get("type", "")
    arch = os_info.get("arch", "")

    if os_type == "macos" and arch == "arm64":
        artifact = f"zig-macos-aarch64-{version}"
    elif os_type == "macos" and arch in {"x86_64", "amd64"}:
        artifact = f"zig-macos-x86_64-{version}"
    elif os_type == "linux" and arch == "arm64":
        artifact = f"zig-linux-aarch64-{version}"
    elif os_type == "linux" and arch in {"x86_64", "amd64"}:
        artifact = f"zig-linux-x86_64-{version}"
    else:
        return None

    return artifact, f"https://ziglang.org/download/{version}/{artifact}.tar.xz"


# ─────────────────────────────────────────────
#  Repo 名称到包管理器名称的智能映射
# ─────────────────────────────────────────────

# GitHub repo 名 → brew/apt 包名（很多时候就是 repo 名本身）
# 只列出 repo 名 ≠ brew 名 的映射
_BREW_NAME_MAP = {
    "fd": "fd",
    "bat": "bat",
    "ripgrep": "ripgrep",
    "exa": "exa",
    "fzf": "fzf",
    "jq": "jq",
    "yq": "yq",
    "gh": "gh",
    "lazygit": "lazygit",
    "delta": "git-delta",
    "difftastic": "difftastic",
    "hyperfine": "hyperfine",
    "tokei": "tokei",
    "dust": "dust",
    "procs": "procs",
    "bottom": "bottom",
    "zoxide": "zoxide",
    "starship": "starship",
    "nushell": "nushell",
    "helix": "helix",
    "neovim": "neovim",
    "tmux": "tmux",
    "hugo": "hugo",
    "caddy": "caddy",
    "traefik": "traefik",
    "k9s": "k9s",
    "terraform": "terraform",
    "httpie": "httpie",
    "wget": "wget",
    "curl": "curl",
    "tree-sitter": "tree-sitter",
    "cmake": "cmake",
    "ninja": "ninja",
    "meson": "meson",
    "lsd": "lsd",
    "gitui": "gitui",
    "just": "just",
    "watchexec": "watchexec",
    "miniserve": "miniserve",
    "dog": "dog",
    "glow": "glow",
    "broot": "broot",
    "xh": "xh",
    "choose": "choose-rust",
    "sd": "sd",
    "grex": "grex",
    "pastel": "pastel",
    "vivid": "vivid",
    "tealdeer": "tealdeer",
    "bandwhich": "bandwhich",
    "navi": "navi",
    "zellij": "zellij",
    "atuin": "atuin",
    "yazi": "yazi",
}

_APT_NAME_MAP = {
    "fd": "fd-find",
    "bat": "bat",
    "ripgrep": "ripgrep",
    "fzf": "fzf",
    "jq": "jq",
    "yq": "yq",
    "neovim": "neovim",
    "tmux": "tmux",
    "hugo": "hugo",
    "cmake": "cmake",
    "ninja": "ninja-build",
    "meson": "meson",
    "httpie": "httpie",
    "tree-sitter": "tree-sitter-cli",
}


def get_brew_name(repo_name: str) -> str:
    """GitHub repo 名转 brew 包名"""
    lower = repo_name.lower()
    return _BREW_NAME_MAP.get(lower, lower)


def get_apt_name(repo_name: str) -> str:
    """GitHub repo 名转 apt 包名"""
    lower = repo_name.lower()
    return _APT_NAME_MAP.get(lower, lower)


# ─────────────────────────────────────────────
#  预检层 (Preflight)
# ─────────────────────────────────────────────

# 命令 → 安装方式
_TOOL_INSTALL = {
    "git":     {"brew": "git",       "apt": "git"},
    "python3": {"brew": "python@3",  "apt": "python3"},
    "pip":     {"brew": "python@3",  "apt": "python3-pip"},
    "pip3":    {"brew": "python@3",  "apt": "python3-pip"},
    "node":    {"brew": "node",      "apt": "nodejs"},
    "npm":     {"brew": "node",      "apt": "npm"},
    "npx":     {"brew": "node",      "apt": "npm"},
    "yarn":    {"brew": "yarn",      "apt": "yarn"},
    "pnpm":    {"brew": "pnpm",      "apt": "pnpm"},
    "cargo":   {"brew": "rust",      "apt": "cargo"},
    "rustc":   {"brew": "rust",      "apt": "rustc"},
    "go":      {"brew": "go",        "apt": "golang"},
    "java":    {"brew": "openjdk",   "apt": "default-jdk"},
    "javac":   {"brew": "openjdk",   "apt": "default-jdk"},
    "mvn":     {"brew": "maven",     "apt": "maven"},
    "gradle":  {"brew": "gradle",    "apt": "gradle"},
    "cmake":   {"brew": "cmake",     "apt": "cmake"},
    "make":    {"brew": "make",      "apt": "build-essential"},
    "gcc":     {"brew": "gcc",       "apt": "build-essential"},
    "g++":     {"brew": "gcc",       "apt": "build-essential"},
    "ruby":    {"brew": "ruby",      "apt": "ruby-full"},
    "gem":     {"brew": "ruby",      "apt": "ruby-full"},
    "bundle":  {"brew": "ruby",      "apt": "ruby-bundler"},
    "php":     {"brew": "php",       "apt": "php"},
    "composer": {"brew": "composer", "apt": "composer"},
    "mix":     {"brew": "elixir",    "apt": "elixir"},
    "swift":   {"brew": "swift",     "apt": ""},
    "stack":   {"brew": "haskell-stack", "apt": "haskell-stack"},
    "sbt":     {"brew": "sbt",       "apt": ""},
    "zig":     {"brew": "zig",       "apt": ""},
    "lein":    {"brew": "leiningen", "apt": "leiningen"},
    "docker":  {"brew": "docker",    "apt": "docker.io"},
    "uv":      {"brew": "uv",       "apt": ""},
}


@dataclass
class PreflightResult:
    """预检结果"""
    missing_tools: list[str] = field(default_factory=list)
    install_commands: list[dict] = field(default_factory=list)
    all_ready: bool = True


def preflight_check(plan_steps: list[dict]) -> PreflightResult:
    """
    扫描计划中所有命令，检测需要的工具是否已安装。
    
    返回缺失工具列表和对应的安装命令。
    这在执行前调用，比执行中报错再修复更可靠。
    """
    result = PreflightResult()
    needed_tools = set()

    for step in plan_steps:
        cmd = step.get("command", "")
        if not cmd:
            continue

        # 提取命令中可能用到的工具名
        # 支持：tool args, cd dir && tool args, source activate && tool args
        for segment in re.split(r'\s*&&\s*|\s*\|\|\s*|\s*;\s*', cmd):
            segment = segment.strip()
            if not segment:
                continue
            # 跳过 cd, source, export 等 shell 内置
            first_word = segment.split()[0] if segment.split() else ""
            if first_word in ("cd", "source", "export", "echo", "mkdir", "test",
                              "cat", "ls", "pwd", "true", "false", "set", "if",
                              "then", "else", "fi", "for", "while", "do", "done"):
                continue
            needed_tools.add(first_word)

    # 检查哪些工具缺失
    for tool in sorted(needed_tools):
        if _has_command(tool):
            continue
        if tool not in _TOOL_INSTALL:
            continue  # 不认识的工具跳过
        
        result.missing_tools.append(tool)
        info = _TOOL_INSTALL[tool]
        
        if _is_macos() and info.get("brew"):
            result.install_commands.append({
                "command": f"brew install {info['brew']}",
                "description": f"安装 {tool}（预检发现缺失）",
            })
        elif _is_linux() and info.get("apt"):
            result.install_commands.append({
                "command": f"sudo apt-get install -y {info['apt']}",
                "description": f"安装 {tool}（预检发现缺失）",
            })

    result.all_ready = len(result.missing_tools) == 0
    return result


# ─────────────────────────────────────────────
#  多策略回退引擎
# ─────────────────────────────────────────────

@dataclass
class FallbackPlan:
    """一个回退方案"""
    tier: int                    # 1=包管理器, 2=语言包管理器, 3=源码编译
    strategy: str                # 策略名称
    steps: list[dict]            # 执行步骤
    confidence: str              # high/medium/low


def generate_fallback_plans(
    owner: str,
    repo: str,
    project_types: list[str],
    env: dict,
    dependency_files: dict[str, str] | None = None,
) -> list[FallbackPlan]:
    """
    为一个项目生成多个安装策略（按可靠性排序）。
    
    主执行器按顺序尝试，Plan A 失败后自动切换到 Plan B。
    这就是系统不需要测试每个项目的关键——
    即使 Plan A 失败了，Plan B/C 通常能兜住。
    """
    plans = []
    types = set(project_types)
    repo_lower = repo.lower()
    dep_names = _dep_names(dependency_files)
    is_maturin = _is_maturin_project(types, dependency_files)
    os_type = "macos" if _is_macos() else ("linux" if _is_linux() else "windows")
    clone_url = f"https://github.com/{owner}/{repo}.git"

    # ── Tier 1: 包管理器安装（最可靠，~99% 成功率）──
    # brew (macOS) / apt (Linux) / winget (Windows)
    if _is_macos() and _has_command("brew"):
        brew_name = get_brew_name(repo_lower)
        if brew_has_package(brew_name):
            plans.append(FallbackPlan(
                tier=1,
                strategy="brew_install",
                steps=[{
                    "command": f"brew install {brew_name}",
                    "description": f"用 Homebrew 安装 {repo}（最可靠方式）",
                }],
                confidence="high",
            ))
    elif _is_linux() and _has_command("apt-get"):
        apt_name = get_apt_name(repo_lower)
        if apt_has_package(apt_name):
            plans.append(FallbackPlan(
                tier=1,
                strategy="apt_install",
                steps=[{
                    "command": f"sudo apt-get install -y {apt_name}",
                    "description": f"用 apt 安装 {repo}（最可靠方式）",
                }],
                confidence="high",
            ))

    # ── Tier 2: 语言包管理器安装（无需编译，通常很快）──
    if is_maturin and _has_command("pip3"):
        plans.append(FallbackPlan(
            tier=2,
            strategy="python_editable_install",
            steps=[
                {"command": f"git clone --depth 1 https://github.com/{owner}/{repo}.git", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": "python3 -m venv venv && source venv/bin/activate && pip install -e .", "description": "按 Python 包安装（含 Rust 扩展）"},
            ],
            confidence="medium",
        ))

    if types & {"rust"} and _has_command("cargo") and not is_maturin:
        plans.append(FallbackPlan(
            tier=2,
            strategy="cargo_install",
            steps=[{
                "command": f"cargo install --git https://github.com/{owner}/{repo}",
                "description": f"用 Cargo 编译安装 {repo}",
            }],
            confidence="medium",
        ))
    
    if types & {"go"} and _has_command("go"):
        plans.append(FallbackPlan(
            tier=2,
            strategy="go_build",
            steps=[
                {"command": f"git clone --depth 1 https://github.com/{owner}/{repo}.git", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": (
                    "test -f go.mod && go build ./... || "
                    "find . -name go.mod -maxdepth 3 -not -path '*/vendor/*' "
                    "-exec dirname {} \\; | head -5 | "
                    "while read d; do echo \"Building $d\"; (cd \"$d\" && go build ./...); done"
                ), "description": f"编译 {repo}（自动查找 Go 模块）"},
            ],
            confidence="medium",
        ))
    
    if types & {"python"} and _has_command("pip3"):
        plans.append(FallbackPlan(
            tier=2,
            strategy="pip_install",
            steps=[
                {"command": "python3 -m venv venv", "description": "创建虚拟环境"},
                {"command": "source venv/bin/activate", "description": "激活虚拟环境"},
                {"command": f"pip install {repo_lower}", "description": f"用 pip 安装 {repo} 的已发布包"},
            ],
            confidence="medium",
        ))
    
    if types & {"node"} and "package.json" in dep_names and _has_command("npm"):
        plans.append(FallbackPlan(
            tier=2,
            strategy="npm_install",
            steps=[
                {"command": f"git clone --depth 1 https://github.com/{owner}/{repo}.git", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": "npm install", "description": "安装依赖"},
            ],
            confidence="medium",
        ))

    if types & {"zig"} and _zig_uses_legacy_build_api(dependency_files):
        legacy_version = _zig_fallback_version(dependency_files)
        download_info = _zig_download_info(env, legacy_version)
        if download_info:
            artifact, url = download_info
            zig_bin = f'$PWD/.gitinstall-zig/{artifact}/zig'
            if os_type == "macos":
                build_cmd = (
                    f'ZIG_BIN="{zig_bin}"; '
                    'if [ -d /Applications/Xcode.app/Contents/Developer ]; then '
                    'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; '
                    'fi; export SDKROOT="${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)}"; "$ZIG_BIN" build'
                )
            else:
                build_cmd = f'ZIG_BIN="{zig_bin}"; "$ZIG_BIN" build'
            plans.append(FallbackPlan(
                tier=2,
                strategy="zig_legacy_0_14_0_build",
                steps=[
                    {"command": f"git clone --depth 1 --recurse-submodules {clone_url}", "description": "递归克隆代码与 Zig 子模块依赖"},
                    {"command": f"cd {repo}", "description": "进入目录"},
                    {"command": "git submodule update --init --recursive", "description": "补齐子模块依赖（兼容目录已存在场景）"},
                    {"command": "mkdir -p .gitinstall-zig", "description": "创建 Zig 兼容工具链目录"},
                    {"command": f"curl -L {url} -o .gitinstall-zig/zig.tar.xz && tar -xf .gitinstall-zig/zig.tar.xz -C .gitinstall-zig", "description": "下载 Zig 0.14.0 兼容工具链"},
                    {"command": build_cmd, "description": "使用 Zig 0.14.0 编译旧 build API 项目"},
                ],
                confidence="medium",
            ))

    if types & {"ruby"} and _has_command("gem"):
        plans.append(FallbackPlan(
            tier=2,
            strategy="gem_install",
            steps=[{
                "command": f"gem install {repo_lower}",
                "description": f"用 RubyGems 安装 {repo}",
            }],
            confidence="medium",
        ))

    # ── Tier 3: 源码编译（最不可靠，但覆盖面最广）──
    
    if types & {"rust"} and _has_command("cargo"):
        plans.append(FallbackPlan(
            tier=3,
            strategy="source_cargo_build",
            steps=[
                {"command": f"git clone --depth 1 {clone_url}", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": "cargo build --release", "description": "编译（Release 模式）"},
            ],
            confidence="low",
        ))
    
    if types & {"cmake", "cpp", "c"} and _has_command("cmake"):
        plans.append(FallbackPlan(
            tier=3,
            strategy="source_cmake_build",
            steps=[
                {"command": f"git clone --depth 1 {clone_url}", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": "mkdir -p build && cd build", "description": "创建构建目录"},
                {"command": "cmake .. && make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu)", "description": "编译"},
            ],
            confidence="low",
        ))

    if types & {"make", "autotools", "c"} and _has_command("make"):
        plans.append(FallbackPlan(
            tier=3,
            strategy="source_make_build",
            steps=[
                {"command": f"git clone --depth 1 {clone_url}", "description": "克隆代码"},
                {"command": f"cd {repo}", "description": "进入目录"},
                {"command": "./configure && make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu)", "description": "编译"},
            ],
            confidence="low",
        ))

    return plans


# ─────────────────────────────────────────────
#  统一对外接口
# ─────────────────────────────────────────────

def enhance_plan_with_preflight(plan: dict) -> dict:
    """
    增强现有安装计划：在所有步骤前插入预检安装步骤。
    """
    steps = plan.get("steps", [])
    pf = preflight_check(steps)
    
    if pf.install_commands:
        # 在所有步骤前插入预检安装步骤
        plan["steps"] = pf.install_commands + steps
        plan["_preflight"] = {
            "missing_tools": pf.missing_tools,
            "install_count": len(pf.install_commands),
        }
    
    return plan


def get_fallback_plan_for_failure(
    owner: str,
    repo: str,
    project_types: list[str],
    env: dict,
    failed_strategy: str,
    dependency_files: dict[str, str] | None = None,
) -> FallbackPlan | None:
    """
    当前策略失败后，获取下一个可用的回退策略。
    
    参数 failed_strategy 是已经失败的策略名称，
    返回列表中下一个不同的策略（跳过同一 tier 中已失败的）。
    """
    all_plans = generate_fallback_plans(owner, repo, project_types, env, dependency_files=dependency_files)
    
    # 找到当前失败策略的 tier
    failed_tier = 0
    for p in all_plans:
        if p.strategy == failed_strategy:
            failed_tier = p.tier
            break
    
    # 返回比失败策略 tier 更高（数字更大＝更低可靠性）或同 tier 但不同策略的第一个
    for p in all_plans:
        if p.strategy != failed_strategy:
            if p.tier >= failed_tier:
                return p
    
    return None
