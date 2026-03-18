"""
error_fixer.py — 无需 LLM 的规则化错误自动修复引擎
====================================================

覆盖安装场景 90%+ 的常见报错：
  - 缺依赖（python/node/rust/go 等未安装）
  - 权限不足（pip 需要 --user）
  - 端口占用 / 网络问题
  - 虚拟环境激活失败
  - Python 版本不匹配
  - npm 审计 / peer dependency 问题
  - 编译缺依赖（cmake/gcc/make 等）

返回 FixSuggestion 供 executor 执行，无需任何 API Key 或模型。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from dataclasses import dataclass


@dataclass
class FixSuggestion:
    """一个修复建议"""
    root_cause: str          # 根本原因（一句话）
    fix_commands: list[str]  # 修复命令列表
    retry_original: bool     # 修复后是否重试原命令
    confidence: str          # high / medium / low
    outcome: str = "fixed"  # fixed / trusted_failure


# ─── macOS / Linux / Windows 包管理器检测 ───

def _is_macos() -> bool:
    return platform.system() == "Darwin"

def _is_linux() -> bool:
    return platform.system() == "Linux"

def _is_windows() -> bool:
    return platform.system() == "Windows"

def _has_brew() -> bool:
    return os.path.exists("/opt/homebrew/bin/brew") or os.path.exists("/usr/local/bin/brew")

def _has_apt() -> bool:
    return os.path.exists("/usr/bin/apt-get")

def _has_choco() -> bool:
    return shutil.which("choco") is not None

def _install_pkg_cmd(pkg: str, brew_pkg: str = "", apt_pkg: str = "") -> str:
    """根据平台返回安装系统包的命令"""
    if _is_macos() and _has_brew():
        return f"brew install {brew_pkg or pkg}"
    if _is_linux() and _has_apt():
        return f"sudo apt-get install -y {apt_pkg or pkg}"
    if _is_windows() and _has_choco():
        return f"choco install {pkg} -y"
    return ""


def _haskell_macos_env_exports(formulas: list[str]) -> str:
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
    return "; ".join(lines)


# ─── 错误模式规则库 ───────────────────────────

# 每条规则：(stderr/stdout 正则, 生成 fix 的函数)
# 函数签名: (command, stderr, stdout) -> Optional[FixSuggestion]

def _fix_command_not_found(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """命令未找到：自动安装缺失工具"""
    # "command not found", "not recognized", "No such file"
    patterns = [
        (r'(?:command not found|not found):\s*(\w[\w.-]*)', 1),
        (r"(\w[\w.-]*):\s*command not found", 1),
        (r"'(\w[\w.-]*)' is not recognized", 1),
        (r'No such file or directory.*?[/\\](\w+)', 1),
        (r'which:\s+no\s+(\w+)', 1),
    ]
    combined = stderr + "\n" + stdout
    for pattern, group in patterns:
        m = re.search(pattern, combined, re.IGNORECASE)
        if m:
            missing = m.group(group).lower()
            return _suggest_install_tool(missing, cmd)
    return None


def _suggest_install_tool(tool: str, original_cmd: str) -> FixSuggestion | None:
    """为缺失的工具生成安装命令"""
    # 工具 → (brew_name, apt_name, description)
    TOOL_MAP = {
        "python3":   ("python@3.12", "python3",          "Python 3"),
        "python":    ("python@3.12", "python3",          "Python 3"),
        "pip":       ("python@3.12", "python3-pip",      "pip"),
        "pip3":      ("python@3.12", "python3-pip",      "pip3"),
        "node":      ("node",        "nodejs",           "Node.js"),
        "npm":       ("node",        "npm",              "npm"),
        "npx":       ("node",        "npm",              "npx"),
        "yarn":      ("yarn",        "yarn",             "yarn"),
        "pnpm":      ("pnpm",        "pnpm",             "pnpm"),
        "cargo":     ("rust",        "cargo",            "Rust/Cargo"),
        "rustc":     ("rust",        "rustc",            "Rust"),
        "rustup":    ("rustup",      "rustup",           "rustup"),
        "go":        ("go",          "golang",           "Go"),
        "java":      ("openjdk",     "default-jdk",      "Java JDK"),
        "javac":     ("openjdk",     "default-jdk",      "Java JDK"),
        "mvn":       ("maven",       "maven",            "Maven"),
        "gradle":    ("gradle",      "gradle",           "Gradle"),
        "cmake":     ("cmake",       "cmake",            "CMake"),
        "make":      ("make",        "build-essential",  "make/gcc"),
        "gcc":       ("gcc",         "build-essential",  "GCC"),
        "g++":       ("gcc",         "build-essential",  "G++"),
        "git":       ("git",         "git",              "Git"),
        "curl":      ("curl",        "curl",             "curl"),
        "wget":      ("wget",        "wget",             "wget"),
        "docker":    ("docker",      "docker.io",        "Docker"),
        "ruby":      ("ruby",        "ruby-full",        "Ruby"),
        "gem":       ("ruby",        "ruby-full",        "RubyGems"),
        "bundle":    ("ruby",        "ruby-bundler",     "Bundler"),
        "php":       ("php",         "php",              "PHP"),
        "composer":  ("composer",    "composer",         "Composer"),
        "swift":     ("swift",       "",                 "Swift"),
        "mix":       ("elixir",      "elixir",           "Elixir"),
        "stack":     ("haskell-stack", "haskell-stack",  "Haskell Stack"),
        "sbt":       ("sbt",        "",                  "SBT"),
        "zig":       ("zig",        "",                  "Zig"),
        "lein":      ("leiningen",  "leiningen",         "Leiningen"),
        "uv":        ("uv",         "",                  "uv"),
    }

    if tool in TOOL_MAP:
        brew_name, apt_name, desc = TOOL_MAP[tool]
        install_cmd = _install_pkg_cmd(tool, brew_name, apt_name)
        if install_cmd:
            return FixSuggestion(
                root_cause=f"{desc} 未安装",
                fix_commands=[install_cmd],
                retry_original=True,
                confidence="high",
            )
    return None


def _fix_pip_permission(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """pip 权限问题 → 加 --user 或用 venv"""
    if not re.search(r'pip3?\s+install', cmd, re.IGNORECASE):
        return None
    if re.search(r'Permission denied|Could not install packages.*user|externally-managed-environment', stderr, re.IGNORECASE):
        # PEP 668 externally-managed-environment（macOS Sonoma+, Ubuntu 23.04+）
        if "externally-managed-environment" in stderr:
            return FixSuggestion(
                root_cause="系统 Python 受保护（PEP 668），需使用虚拟环境",
                fix_commands=[
                    "python3 -m venv venv",
                    "source venv/bin/activate",
                ],
                retry_original=True,
                confidence="high",
            )
        # 普通权限问题 → --user
        new_cmd = cmd.rstrip() + " --user"
        return FixSuggestion(
            root_cause="pip 安装权限不足",
            fix_commands=[new_cmd],
            retry_original=False,  # fix_commands 本身就是替代原命令
            confidence="high",
        )
    return None


def _fix_pip_not_found_package(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """pip 找不到包 → 检查包名拼写或提示 extras"""
    if not re.search(r'pip3?\s+install', cmd, re.IGNORECASE):
        return None

    # "No matching distribution found for xxx"
    m = re.search(r'No matching distribution found for (\S+)', stderr)
    if m:
        pkg = m.group(1)
        # 常见拼写错误映射
        typos = {
            "sklearn": "scikit-learn",
            "cv2": "opencv-python",
            "PIL": "Pillow",
            "yaml": "PyYAML",
            "dotenv": "python-dotenv",
            "attr": "attrs",
        }
        if pkg in typos:
            new_cmd = cmd.replace(pkg, typos[pkg])
            return FixSuggestion(
                root_cause=f"包名 '{pkg}' 不正确，应为 '{typos[pkg]}'",
                fix_commands=[new_cmd],
                retry_original=False,
                confidence="high",
            )
    return None


def _fix_npm_permission(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """npm EACCES / permission denied → 根据具体场景修复"""
    if not re.search(r'\bnpm\b', cmd, re.IGNORECASE):
        return None
    if not re.search(r'EACCES|permission denied', stderr, re.IGNORECASE):
        return None

    # npm 缓存目录的 root 文件（历史遗留问题）
    if re.search(r'cache folder contains root-owned files|\.npm/_cacache', stderr):
        return FixSuggestion(
            root_cause="npm 缓存目录权限异常，清理缓存后重试",
            fix_commands=["npm cache clean --force"],
            retry_original=True,
            confidence="high",
        )

    # 全局安装权限问题
    if re.search(r'-g\b|--global', cmd):
        return FixSuggestion(
            root_cause="npm 全局安装权限不足",
            fix_commands=["npm config set prefix ~/.npm-global"],
            retry_original=True,
            confidence="high",
        )

    # 本地安装权限问题
    return FixSuggestion(
        root_cause="npm 安装目录权限不足，清理 node_modules 重试",
        fix_commands=["rm -rf node_modules package-lock.json"],
        retry_original=True,
        confidence="medium",
    )


def _fix_npm_audit(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """npm install 有 audit 问题但实际成功"""
    if not re.search(r'npm\s+install', cmd, re.IGNORECASE):
        return None
    # npm install 报 audit 漏洞但 exit code != 0
    combined = stderr + stdout
    if re.search(r'added \d+ packages', combined) and re.search(r'vulnerabilities', combined):
        return FixSuggestion(
            root_cause="npm 安装成功但有安全审计警告（可忽略）",
            fix_commands=[],
            retry_original=False,
            confidence="high",
        )
    return None


def _fix_npm_workspace_protocol(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """npm 不支持 workspace: 协议 → 切换 pnpm"""
    if not re.search(r'\bnpm\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    if re.search(r'EUNSUPPORTEDPROTOCOL.*workspace:', combined, re.IGNORECASE):
        # 项目使用 pnpm/yarn workspace 协议，npm 不支持
        new_cmd = cmd.replace("npm install", "pnpm install").replace("npm i", "pnpm install")
        return FixSuggestion(
            root_cause="项目使用 workspace 协议，需 pnpm 而非 npm",
            fix_commands=[new_cmd] if new_cmd != cmd else ["pnpm install"],
            retry_original=False,
            confidence="high",
        )
    return None


def _fix_npm_eexist(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """npm EEXIST 文件冲突 → 清理缓存重试"""
    if not re.search(r'\bnpm\b', cmd, re.IGNORECASE):
        return None
    if re.search(r'EEXIST', stderr):
        return FixSuggestion(
            root_cause="npm 缓存文件冲突",
            fix_commands=["npm cache clean --force"],
            retry_original=True,
            confidence="medium",
        )
    return None


def _fix_node_version(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Node.js 版本过低"""
    combined = stderr + stdout
    m = re.search(r'(?:engine|requires|need)\s*node\s*[><=]+\s*([\d.]+)', combined, re.IGNORECASE)
    if m:
        required_ver = m.group(1)
        return FixSuggestion(
            root_cause=f"Node.js 版本过低，需要 >= {required_ver}",
            fix_commands=[_install_pkg_cmd("node", "node", "nodejs") or "brew install node"],
            retry_original=True,
            confidence="medium",
        )
    return None


def _fix_python_version(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Python 版本不匹配"""
    combined = stderr + stdout
    m = re.search(r'python_requires\s*[><=]+\s*"?([\d.]+)', combined, re.IGNORECASE)
    if not m:
        m = re.search(r'requires\s+(?:a\s+different\s+)?Python\s*[><=]+\s*([\d.]+)', combined, re.IGNORECASE)
    if m:
        required_ver = m.group(1)
        major_minor = required_ver.rsplit(".", 1)[0] if "." in required_ver else required_ver
        return FixSuggestion(
            root_cause=f"Python 版本过低，需要 >= {required_ver}",
            fix_commands=[_install_pkg_cmd("python", f"python@{major_minor}", f"python{major_minor}")
                         or f"brew install python@{major_minor}"],
            retry_original=True,
            confidence="medium",
        )
    return None


def _fix_rust_compile_error(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Rust/Cargo 编译依赖缺失"""
    if not re.search(r'\bcargo\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    # "linker 'cc' not found" or "failed to run custom build command"
    if re.search(r"linker.*not found|can't find cc", combined, re.IGNORECASE):
        install_cmd = _install_pkg_cmd("gcc", "gcc", "build-essential")
        if install_cmd:
            return FixSuggestion(
                root_cause="C 编译器未安装（Rust 链接需要）",
                fix_commands=[install_cmd],
                retry_original=True,
                confidence="high",
            )
    # OpenSSL 缺失
    if re.search(r'openssl.*not found|Could not find directory of OpenSSL', combined, re.IGNORECASE):
        install_cmd = _install_pkg_cmd("openssl", "openssl", "libssl-dev")
        if install_cmd:
            return FixSuggestion(
                root_cause="OpenSSL 开发库未安装",
                fix_commands=[install_cmd],
                retry_original=True,
                confidence="high",
            )
    return None


def _fix_cargo_git_install_layout(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """cargo install --git 遇到 workspace/库仓库时给出更合适的替代命令。"""
    url_match = re.search(r'cargo\s+install\s+--git\s+(https?://\S+)', cmd, re.IGNORECASE)
    if not url_match:
        return None

    combined = stderr + stdout
    repo_url = url_match.group(1).rstrip()
    repo_name = repo_url.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')

    multiple = re.search(r'multiple packages with binaries found:\s*([^\.]+)', combined, re.IGNORECASE)
    if multiple:
        packages = [part.strip() for part in multiple.group(1).split(',') if part.strip()]
        target_pkg = None
        for pkg in packages:
            if pkg == repo_name or pkg == repo_name.replace('_', '-'):
                target_pkg = pkg
                break
        if target_pkg:
            return FixSuggestion(
                root_cause="Rust workspace 含多个可执行包，需要指定 package",
                fix_commands=[f"cargo install --git {repo_url} --package {target_pkg}"],
                retry_original=False,
                confidence="high",
            )

    if re.search(r'no packages found with binarie', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="该 Rust 仓库不是可直接 cargo install 的 CLI，回退到源码/Python 包安装",
            fix_commands=[
                f"git clone --depth 1 {repo_url}.git 2>/dev/null || git clone --depth 1 {repo_url}",
                f"cd {repo_name}",
                "test -f pyproject.toml && (python3 -m venv venv && source venv/bin/activate && pip install -e .) || cargo build --release",
            ],
            retry_original=False,
            confidence="medium",
        )

    return None


def _fix_go_no_root_module(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """go build ./... 在无根 go.mod 的仓库中失败（monorepo 或旧项目）"""
    if "go build" not in cmd and "go install" not in cmd:
        return None
    combined = stderr + stdout
    if ("does not contain main module" not in combined
            and "go.mod file not found" not in combined
            and "cannot find module providing" not in combined):
        return None
    return FixSuggestion(
        root_cause="仓库根目录没有 go.mod，可能是 Go monorepo 或旧 GOPATH 项目",
        fix_commands=[
            "find . -name go.mod -maxdepth 3 -not -path '*/vendor/*' "
            "-exec dirname {} \\; | head -5 | "
            "while read d; do echo \"Building $d\"; (cd \"$d\" && go build ./...); done"
        ],
        retry_original=False,
        confidence="medium",
    )


def _fix_pip_build_wheel(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """pip install 时 C 扩展构建 wheel 失败"""
    if "pip install" not in cmd and "pip3 install" not in cmd:
        return None
    combined = stderr + stdout
    if not re.search(r'Getting requirements to build wheel did not run successfully|'
                     r'Failed building wheel for|'
                     r'error: subprocess-exited-with-error.*build', combined, re.DOTALL):
        return None
    # 提取失败的包名
    pkg_match = re.search(r'(?:Failed building wheel for|error:.*for )(\S+)', combined)
    pkg_name = pkg_match.group(1).strip("'\"") if pkg_match else "某些包"
    return FixSuggestion(
        root_cause=f"{pkg_name} 的 C 扩展编译失败，尝试仅安装预编译包或跳过有问题的依赖",
        fix_commands=[
            cmd + " --only-binary :all: --ignore-installed",
        ],
        retry_original=False,
        confidence="medium",
    )


def _fix_cmake_not_found(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """CMake 编译缺依赖"""
    combined = stderr + stdout
    if re.search(r'cmake.*not found|CMake.*is required', combined, re.IGNORECASE):
        install_cmd = _install_pkg_cmd("cmake", "cmake", "cmake")
        if install_cmd:
            return FixSuggestion(
                root_cause="CMake 未安装",
                fix_commands=[install_cmd],
                retry_original=True,
                confidence="high",
            )
    return None


def _fix_build_essentials(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """编译缺工具链（gcc/make/pkg-config）"""
    combined = stderr + stdout
    # "gcc: command not found" / "make: command not found"
    for tool in ("gcc", "g++", "make", "cc"):
        escaped = re.escape(tool)
        if re.search(rf'(?<!\w){escaped}(?!\w).*(?:not found|No such file)', combined, re.IGNORECASE):
            if _is_macos():
                return FixSuggestion(
                    root_cause=f"编译工具链未安装（{tool}）",
                    fix_commands=["xcode-select --install"],
                    retry_original=True,
                    confidence="high",
                )
            if _is_linux() and _has_apt():
                return FixSuggestion(
                    root_cause=f"编译工具链未安装（{tool}）",
                    fix_commands=["sudo apt-get install -y build-essential"],
                    retry_original=True,
                    confidence="high",
                )
    # pkg-config（排除 cabal/stack 的依赖解析错误，那些由 Haskell 专用规则处理）
    if (re.search(r'pkg-config.*not found(?! in the pkg-config database)', combined, re.IGNORECASE)
            and not re.search(r'\bcabal\b|\bstack\b', cmd, re.IGNORECASE)):
        install_cmd = _install_pkg_cmd("pkg-config", "pkg-config", "pkg-config")
        if install_cmd:
            return FixSuggestion(
                root_cause="pkg-config 未安装",
                fix_commands=[install_cmd],
                retry_original=True,
                confidence="high",
            )
    return None


def _fix_port_in_use(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """端口占用"""
    combined = stderr + stdout
    # EADDRINUSE :::3000 / address already in use / port 8080 in use
    m = re.search(r'(?:EADDRINUSE[^\d]*(\d+)|address already in use[^\d]*(\d+)|port\s+(\d+).*(?:in use|occupied))', combined, re.IGNORECASE)
    if not m:
        # "Address already in use" without port number — try to extract from command
        if re.search(r'address already in use', combined, re.IGNORECASE):
            # Try extracting port from the command itself
            port_m = re.search(r'(?:-p\s*|--port[= ]\s*|:)(\d{2,5})\b', cmd)
            if not port_m:
                port_m = re.search(r'\b(\d{4,5})\b', cmd)
            port = port_m.group(1) if port_m else "8080"
            return FixSuggestion(
                root_cause=f"端口 {port} 已被占用",
                fix_commands=[f"lsof -ti:{port} | xargs kill -9 2>/dev/null || true"],
                retry_original=True,
                confidence="medium",
            )
    if m:
        port = next((g for g in m.groups() if g), "8080")
        return FixSuggestion(
            root_cause=f"端口 {port} 已被占用",
            fix_commands=[f"lsof -ti:{port} | xargs kill -9 2>/dev/null || true"],
            retry_original=True,
            confidence="medium",
        )
    return None


def _fix_venv_activate(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """虚拟环境激活失败"""
    if "source" in cmd and ("venv" in cmd or "activate" in cmd):
        combined = stderr + stdout
        if re.search(r'No such file|not found', combined, re.IGNORECASE):
            return FixSuggestion(
                root_cause="虚拟环境不存在，需要先创建",
                fix_commands=["python3 -m venv venv"],
                retry_original=True,
                confidence="high",
            )
    return None


def _fix_git_clone_exists(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """git clone 目标目录已存在"""
    if not re.search(r'git\s+clone', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    m = re.search(r"destination path '([^']+)' already exists", combined)
    if m:
        dirname = m.group(1)
        return FixSuggestion(
            root_cause=f"目录 '{dirname}' 已存在，无需重新克隆",
            fix_commands=[],  # 跳过此步骤即可
            retry_original=False,
            confidence="high",
        )
    return None


def _fix_gradle_error(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Gradle/Maven 构建失败（Java 项目常见问题）"""
    combined = stderr + stdout
    if not re.search(r'gradlew|gradle|mvnw|mvn', cmd, re.IGNORECASE):
        return None

    # gradlew 没有执行权限或不存在
    if re.search(r'gradlew.*No such file|gradlew.*Permission denied|mvnw.*No such file|mvnw.*Permission denied',
                 combined, re.IGNORECASE):
        if "gradlew" in cmd:
            return FixSuggestion(
                root_cause="gradlew 不存在或无执行权限，回退到全局 gradle",
                fix_commands=["gradle build -x test" if "build" in cmd else "gradle " + cmd.split("gradlew")[-1].strip()],
                retry_original=False,
                confidence="medium",
            )
        if "mvnw" in cmd:
            return FixSuggestion(
                root_cause="mvnw 不存在或无执行权限，回退到全局 mvn",
                fix_commands=["mvn clean package -DskipTests"],
                retry_original=False,
                confidence="medium",
            )

    # Gradle daemon 内部错误（常见于版本不匹配）
    if re.search(r'DaemonCommandExecution|Could not create service|Daemon.*expired', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="Gradle Daemon 异常，清理缓存重试",
            fix_commands=["gradle --stop 2>/dev/null; gradle build -x test --no-daemon"],
            retry_original=False,
            confidence="medium",
        )

    # Java 版本不兼容
    if re.search(r'Unsupported class file major version|source release \d+ requires target release',
                 combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="Java 版本不兼容（项目需要更新的 JDK）",
            fix_commands=["brew install openjdk@21 2>/dev/null || sudo apt-get install -y openjdk-21-jdk 2>/dev/null",
                          cmd],
            retry_original=False,
            confidence="medium",
        )

    # BUILD FAILED 但非编译错误（通常是缺乏子模块或目录问题）
    if re.search(r'FAILURE:.*Build failed|BUILD FAILED', combined, re.IGNORECASE):
        # 成功标志在 stdout 中（部分成功的多模块项目）
        if re.search(r'BUILD SUCCESSFUL', stdout, re.IGNORECASE):
            return FixSuggestion(
                root_cause="部分模块构建成功，整体标记为失败（可接受）",
                fix_commands=[],
                retry_original=False,
                confidence="low",
            )

    return None


def _fix_haskell_toolchain(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Haskell 构建时 ghc/cabal/stack 环境不一致"""
    if not re.search(r'\bcabal\b|\bstack\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    if re.search(r"The program 'ghc'.*could not be found|ghc.*could not be found|No compiler found", combined, re.IGNORECASE):
        if re.search(r'\bcabal\b', cmd, re.IGNORECASE):
            return FixSuggestion(
                root_cause="Haskell 工具链未在同一执行环境内，cabal 看不到 ghc",
                fix_commands=[
                    "ghcup install ghc recommended",
                    "ghcup install cabal recommended",
                    "ghcup run --ghc recommended --cabal recommended -- cabal build all",
                ],
                retry_original=False,
                confidence="high",
            )
        return FixSuggestion(
            root_cause="Haskell 编译器未准备好，先由 ghcup 安装并再试一次",
            fix_commands=[
                "ghcup install ghc recommended",
                "ghcup install stack latest",
            ],
            retry_original=True,
            confidence="medium",
        )
    if re.search(r'resolver|snapshot|wanted compiler|requires.*ghc', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="Stack resolver 与当前 Haskell 工具链不兼容，优先切到 ghcup 受控环境或 cabal 路径",
            fix_commands=[
                "ghcup install ghc recommended",
                "ghcup install cabal recommended",
                "ghcup install stack latest",
                "ghcup run --ghc recommended --cabal recommended -- cabal build all",
            ],
            retry_original=False,
            confidence="medium",
        )
    return None


def _fix_haskell_stack_extra_deps(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Stack 旧项目在新 resolver 下缺少 extra-deps，优先注入已验证依赖集"""
    if not re.search(r'\bstack\b', cmd, re.IGNORECASE):
        return None

    combined = stderr + stdout
    markers = [
        r'yi-frontend-vty',
        r'vty-crossplatform',
        r'vty-unix',
        r'needed.*but not found in snapshot',
        r'build plan',
    ]
    if not all(re.search(marker, combined, re.IGNORECASE) for marker in markers[:3]):
        return None
    if not re.search(markers[3], combined, re.IGNORECASE) and not re.search(markers[4], combined, re.IGNORECASE):
        return None

    patch_stack_yaml = (
        "perl -0pi -e 's/extra-deps:\\n  - Hclip-3\\.0\\.0\\.4\\n/"
        "extra-deps:\\n  - Hclip-3.0.0.4\\n"
        "  - vty-6.5\\@sha256:43a4137de7e55cf438a8334cc525fb0e0b4efe78d2ed8bd31b0716eb34993059,3425\\n"
        "  - vty-crossplatform-0.5.0.0\\@sha256:6d057fd8a5582eac3be28c91e99ed3730b729078e107ad19107af46bbb2ea65d,3146\\n"
        "  - vty-unix-0.2.0.0\\@sha256:2af3d0bdae3c4b7b7e567ee374efe32c7439fabdf9096465ce011a6c6736e9ae,2932\\n/' stack.yaml"
    )
    rebuild_cmd = "ghcup run --stack latest -- stack build yi --flag yi:-pango"
    return FixSuggestion(
        root_cause="Stack 旧项目缺少 yi/vty 相关 extra-deps，需补齐固定版本并关闭 pango 前端",
        fix_commands=[patch_stack_yaml, rebuild_cmd],
        retry_original=False,
        confidence="high",
    )


def _fix_haskell_legacy_gui_headless(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """旧 GUI 目标拖垮整包时，尝试剥离 GUI stanza 并转向 headless/CLI 构建"""
    if not re.search(r'\bcabal\b|\bstack\b', cmd, re.IGNORECASE):
        return None

    combined = stderr + stdout
    old_ghc_marker = re.search(
        r'No setup information found for ghc-\d+\.\d+\.\d+.*(?:macosx-aarch64|your platform)',
        combined,
        re.IGNORECASE,
    )
    gui_marker = re.search(
        r'haskell-gi|gi-gtk|gi-gdk|gi-gio|gi-glib|gi-gobject|gi-pango|gi-cairo|gi-gst|gobject-introspection-1\.0',
        combined,
        re.IGNORECASE,
    )
    exact_pin_marker = re.search(
        r'Could not resolve dependencies.*conflict:.*=>\s*base>=\d+\.\d+\s*&&\s*<\d+\.\d+|excluded by constraint .*base|\bbase == \d+\.\d+',
        combined,
        re.IGNORECASE | re.DOTALL,
    )
    legacy_resolution_marker = re.search(
        r'Could not resolve dependencies|not found in the pkg-config database|explicit-setup-deps',
        combined,
        re.IGNORECASE,
    )

    if not old_ghc_marker and not ((gui_marker or exact_pin_marker) and legacy_resolution_marker):
        return None

    guard_cmd = (
        "cabal_file=\"$(find . -maxdepth 1 -name '*.cabal' | head -n 1)\""
        " && [ -n \"$cabal_file\" ]"
        " && grep -Eq '^\\s*(library|executable)\\b' \"$cabal_file\""
        " && grep -Eq 'haskell-gi|gi-gtk|gi-gdk|gi-gio|gi-glib|gi-gobject|gi-pango|gi-cairo|gi-gst|gobject-introspection|gtk' \"$cabal_file\""
    )
    patch_and_build_cmd = (
        "cabal_file=\"$(find . -maxdepth 1 -name '*.cabal' | head -n 1)\""
        " && cp \"$cabal_file\" \"$cabal_file.gitinstall.bak\""
        " && perl -0pi -e 'my @chunks = split(/(?=^(?:library|executable|test-suite|benchmark|foreign-library|common|flag|source-repository)\\b)/ms, $_);"
        " $_ = q{};"
        " for my $chunk (@chunks) {"
        " if ($chunk =~ /^executable\\b/ms && $chunk =~ /\\b(?:haskell-gi(?:-base)?|gi-gobject|gi-gio|gi-glib|gi-pango|gi-gdk(?:pixbuf)?|gi-gtk|gi-cairo|gi-gst(?:base|video)?|gobject-introspection|webkit)\\b/ms) { next; }"
        " $_ .= $chunk;"
        " }' \"$cabal_file\""
        " && perl -0pi -e 's/==\\s*([0-9]+(?:\\.[0-9]+)*)(?:\\.\\*)?/>= $1/g' \"$cabal_file\""
        " && printf '%s\\n' 'packages: .' 'allow-newer: true' > cabal.project.gitinstall-headless"
        " && ghcup run --ghc recommended --cabal recommended -- cabal build all --project-file=cabal.project.gitinstall-headless"
    )
    return FixSuggestion(
        root_cause="旧 GUI 目标依赖过时的 GHC/GTK 绑定，先剥离 GUI stanza 并转向 headless/CLI 构建更可靠",
        fix_commands=[guard_cmd, patch_and_build_cmd],
        retry_original=False,
        confidence="medium",
    )


def _fix_zig_darwin_sdk(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Zig 在 macOS 上找不到 Darwin SDK"""
    if not re.search(r'\bzig\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    if re.search(r'DarwinSdkNotFound|unable to find libSystem|SDK.*macosx.*not found|xcrun: error', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="macOS Apple SDK 未就绪，Zig 无法定位 Darwin SDK；继续重试价值很低，应先修复 Xcode/Command Line Tools",
            fix_commands=[],
            retry_original=False,
            confidence="high",
            outcome="trusted_failure",
        )
    return None


def _fix_zig_legacy_build_api(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Zig 老版本 build API 与当前 Zig 版本不兼容"""
    if not re.search(r'\bzig\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    if re.search(r"no field named 'root_source_file'|no field named 'source_file'|Build\.ExecutableOptions", combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="项目使用旧版 Zig build API，当前 Zig 0.15+ 不兼容；这类项目应优先归类为需要旧 Zig 版本的可信失败，而不是继续重试当前工具链",
            fix_commands=[],
            retry_original=False,
            confidence="high",
            outcome="trusted_failure",
        )
    return None


def _fix_haskell_system_libraries(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """Haskell 构建过程中缺少系统级开发库"""
    if not re.search(r'\bcabal\b|\bstack\b|\bghc\b', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    packages: list[str] = []
    root_causes: list[str] = []

    if re.search(r'pkg-config.*not found', combined, re.IGNORECASE):
        packages.append(_install_pkg_cmd("pkg-config", "pkg-config", "pkg-config"))
        root_causes.append("pkg-config")
    if re.search(r"pcre\.h.*not found|cannot find -lpcre|libpcre", combined, re.IGNORECASE):
        packages.append(_install_pkg_cmd("pcre", "pcre", "libpcre3-dev"))
        root_causes.append("PCRE")
    if re.search(r"openssl.*not found|ssl\.h.*not found|cannot find -lssl|cannot find -lcrypto", combined, re.IGNORECASE):
        packages.append(_install_pkg_cmd("openssl", "openssl", "libssl-dev"))
        root_causes.append("OpenSSL")
    if re.search(r"gtk\+[-]?[23]\.0.*not found|gtk\+-2\.0-any.*not found|pkg-config package .*gtk", combined, re.IGNORECASE):
        packages.append(_install_pkg_cmd("gtk+3", "gtk+3", "libgtk-3-dev"))
        root_causes.append("GTK")

    commands = [cmd for cmd in packages if cmd]
    if commands:
        if _is_macos():
            formulas = []
            if "PCRE" in root_causes:
                formulas.append("pcre")
            if "OpenSSL" in root_causes:
                formulas.append("openssl")
            if "GTK" in root_causes:
                formulas.append("gtk+3")
            env_exports = _haskell_macos_env_exports(formulas)
            return FixSuggestion(
                root_cause=f"Haskell 编译缺少系统库依赖或 macOS 头文件路径未导出：{', '.join(root_causes)}",
                fix_commands=commands + [f"{env_exports}; {cmd}"],
                retry_original=False,
                confidence="high",
            )
        return FixSuggestion(
            root_cause=f"Haskell 编译缺少系统库依赖：{', '.join(root_causes)}",
            fix_commands=commands,
            retry_original=True,
            confidence="high",
        )
    return None


def _fix_npm_no_package_json(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """npm install 但当前目录没有 package.json"""
    if not re.search(r'npm\s+install', cmd, re.IGNORECASE):
        return None
    combined = stderr + stdout
    if re.search(r'ENOENT.*package\.json|no such file.*package\.json', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="当前目录没有 package.json（可能是多模块项目或非 Node.js 项目）",
            fix_commands=[],
            retry_original=False,
            confidence="high",
            outcome="trusted_failure",
        )
    return None


def _fix_network_timeout(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """网络超时 / DNS / SSL 错误"""
    combined = stderr + stdout
    if re.search(r'timed? ?out|ConnectionReset|ConnectionRefused|ETIMEOUT|DNS.*fail|SSL.*error|SSLCertVerification|Could not resolve host',
                 combined, re.IGNORECASE):
        # 对 pip 加镜像源
        if re.search(r'pip3?\s+install', cmd, re.IGNORECASE):
            new_cmd = cmd.rstrip() + " -i https://pypi.tuna.tsinghua.edu.cn/simple"
            return FixSuggestion(
                root_cause="网络连接失败，切换镜像源重试",
                fix_commands=[new_cmd],
                retry_original=False,
                confidence="medium",
            )
        # 通用：直接重试
        return FixSuggestion(
            root_cause="网络连接超时，重试中",
            fix_commands=[],
            retry_original=True,
            confidence="low",
        )
    return None


def _fix_disk_space(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """磁盘空间不足"""
    combined = stderr + stdout
    if re.search(r'No space left on device|ENOSPC|disk full', combined, re.IGNORECASE):
        return FixSuggestion(
            root_cause="磁盘空间不足，请清理后重试",
            fix_commands=[],
            retry_original=False,
            confidence="high",
        )
    return None


def _fix_submodule_error(cmd: str, stderr: str, stdout: str) -> FixSuggestion | None:
    """git submodule 初始化失败"""
    combined = stderr + stdout
    if re.search(
        r'submodule.*(?:fatal|error|failed)'
        r'|(?:fatal|error).*submodule'
        r'|Cloning into.*fatal'
        r'|Failed to clone.*submodule'
        r'|Unable to checkout.*submodule',
        combined, re.IGNORECASE | re.DOTALL
    ):
        return FixSuggestion(
            root_cause="Git submodule 拉取失败",
            fix_commands=["git submodule deinit --all -f", "git submodule update --init --recursive"],
            retry_original=False,
            confidence="medium",
        )
    return None


# ─── 规则链注册 ──────────────────────────────

# 按优先级排序（高频/简单的规则优先）
# 排序逻辑：
#   1. 可跳过/假阳性（不需要修复，直接标记成功）
#   2. 高频通用故障（command not found、权限、网络）
#   3. 语言/构建系统特定错误
#   4. 兜底规则
ERROR_FIX_RULES = [
    _fix_git_clone_exists,      # 目录已存在（可跳过）
    _fix_npm_audit,             # npm audit 警告（假阳性）
    _fix_npm_no_package_json,   # npm 找不到 package.json
    _fix_command_not_found,     # 通用命令缺失（高频！提前）
    _fix_venv_activate,         # venv 激活失败
    _fix_pip_permission,        # pip 权限
    _fix_pip_not_found_package, # pip 包名错误
    _fix_npm_workspace_protocol,# npm workspace: 协议不支持
    _fix_npm_eexist,            # npm 缓存冲突
    _fix_npm_permission,        # npm 权限
    _fix_python_version,        # Python 版本
    _fix_node_version,          # Node 版本
    _fix_build_essentials,      # 编译工具链
    _fix_cmake_not_found,       # CMake
    _fix_gradle_error,          # Gradle/Maven 构建失败
    _fix_cargo_git_install_layout,  # cargo install --git 的 workspace/库仓库问题
    _fix_go_no_root_module,     # go build 在无根 go.mod 仓库中失败
    _fix_pip_build_wheel,       # pip install C 扩展构建失败
    _fix_rust_compile_error,    # Rust 编译依赖
    _fix_haskell_legacy_gui_headless,  # 旧 GUI 目标拖垮整包时转向 headless/CLI 构建
    _fix_haskell_stack_extra_deps,  # yi/vty 类 Stack 旧项目缺少 extra-deps
    _fix_haskell_toolchain,     # Haskell ghc/cabal/stack 工具链错位
    _fix_haskell_system_libraries,  # Haskell 缺少 pkg-config/PCRE/OpenSSL 等系统库
    _fix_zig_darwin_sdk,        # Zig 在 macOS 上找不到 Darwin SDK
    _fix_zig_legacy_build_api,  # Zig 旧 build API 与当前版本不兼容
    _fix_network_timeout,       # 网络超时
    _fix_port_in_use,           # 端口占用
    _fix_submodule_error,       # git submodule
    _fix_disk_space,            # 磁盘空间
]


def diagnose(command: str, stderr: str, stdout: str = "") -> FixSuggestion | None:
    """
    主入口：给一个失败的命令 + 其 stderr/stdout，返回修复建议。
    
    遍历所有规则，返回第一个匹配的 FixSuggestion，
    如果全部不匹配返回 None（需要 LLM 介入）。
    
    示例：
        fix = diagnose("pip install torch", "externally-managed-environment")
        if fix:
            print(fix.root_cause)       # "系统 Python 受保护..."
            print(fix.fix_commands)     # ["python3 -m venv venv", ...]
            print(fix.retry_original)   # True
    """
    for rule_fn in ERROR_FIX_RULES:
        try:
            result = rule_fn(command, stderr, stdout)
            if result is not None:
                return result
        except Exception:
            continue
    return None
