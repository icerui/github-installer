"""
monorepo.py — Monorepo 子项目安装引擎
=======================================

目标市场：Monorepo 子项目安装（Top 1000 的 30%，★★★☆☆）

功能：
  1. Monorepo 检测（识别 workspace/lerna/turborepo/nx/cargo workspace/go workspace）
  2. 子项目发现 & 依赖图谱
  3. 选择性安装（只装需要的子项目）
  4. 子项目间依赖解析
  5. 增量安装（只安装变更的子项目）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class MonorepoInfo:
    """Monorepo 元信息"""
    is_monorepo: bool = False
    monorepo_type: str = ""     # npm_workspaces | lerna | turborepo | nx | pnpm | cargo | go | bazel | pants
    root_dir: str = ""
    packages: list["SubProject"] = field(default_factory=list)
    total_packages: int = 0
    shared_deps: dict[str, str] = field(default_factory=dict)


@dataclass
class SubProject:
    """子项目"""
    name: str = ""
    path: str = ""              # 相对路径
    project_type: str = ""      # python | node | rust | go | java | ...
    version: str = ""
    dependencies: list[str] = field(default_factory=list)     # 外部依赖
    internal_deps: list[str] = field(default_factory=list)    # 内部依赖（其他子项目）
    scripts: dict[str, str] = field(default_factory=dict)     # npm scripts / Makefile targets
    has_tests: bool = False
    size_files: int = 0


@dataclass
class DependencyEdge:
    """子项目间依赖关系"""
    source: str = ""
    target: str = ""
    dep_type: str = "runtime"   # runtime | dev | peer | build


# ─────────────────────────────────────────────
#  Monorepo 检测
# ─────────────────────────────────────────────

def detect_monorepo(project_dir: str) -> MonorepoInfo:
    """
    检测目录是否为 monorepo，识别类型和子项目。

    支持的 monorepo 类型：
      - npm workspaces (package.json workspaces)
      - pnpm workspaces (pnpm-workspace.yaml)
      - Lerna (lerna.json)
      - Turborepo (turbo.json)
      - Nx (nx.json)
      - Cargo workspaces (Cargo.toml [workspace])
      - Go workspaces (go.work)
      - Bazel (BUILD / WORKSPACE)
      - Pants (pants.toml / BUILD)
    """
    root = Path(project_dir)
    if not root.is_dir():
        return MonorepoInfo()

    # 按优先级检测
    detectors = [
        _detect_pnpm_workspaces,
        _detect_npm_workspaces,
        _detect_lerna,
        _detect_turborepo,
        _detect_nx,
        _detect_cargo_workspaces,
        _detect_go_workspaces,
        _detect_bazel,
        _detect_pants,
    ]

    for detector in detectors:
        info = detector(root)
        if info.is_monorepo:
            info.root_dir = str(root)
            info.total_packages = len(info.packages)
            return info

    # 通用检测：多个独立子项目
    info = _detect_generic_multi_project(root)
    if info.is_monorepo:
        info.root_dir = str(root)
        info.total_packages = len(info.packages)
    return info


def _detect_npm_workspaces(root: Path) -> MonorepoInfo:
    """检测 npm workspaces"""
    pkg_json = root / "package.json"
    if not pkg_json.exists():
        return MonorepoInfo()

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return MonorepoInfo()

    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])
    if not workspaces:
        return MonorepoInfo()

    packages = _resolve_workspace_globs(root, workspaces, "node")
    return MonorepoInfo(
        is_monorepo=True,
        monorepo_type="npm_workspaces",
        packages=packages,
        shared_deps=_extract_shared_deps_node(data),
    )


def _detect_pnpm_workspaces(root: Path) -> MonorepoInfo:
    """检测 pnpm workspaces"""
    pnpm_ws = root / "pnpm-workspace.yaml"
    if not pnpm_ws.exists():
        return MonorepoInfo()

    try:
        content = pnpm_ws.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return MonorepoInfo()

    # 简易 YAML 解析 — 只提取 packages 列表
    patterns = []
    in_packages = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "packages:":
            in_packages = True
            continue
        if in_packages:
            if stripped.startswith("- "):
                pat = stripped[2:].strip().strip("'\"")
                patterns.append(pat)
            elif not stripped.startswith("#") and stripped and not stripped.startswith("-"):
                break

    if not patterns:
        return MonorepoInfo()

    packages = _resolve_workspace_globs(root, patterns, "node")
    return MonorepoInfo(
        is_monorepo=True,
        monorepo_type="pnpm",
        packages=packages,
    )


def _detect_lerna(root: Path) -> MonorepoInfo:
    """检测 Lerna"""
    lerna_json = root / "lerna.json"
    if not lerna_json.exists():
        return MonorepoInfo()

    try:
        data = json.loads(lerna_json.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return MonorepoInfo()

    patterns = data.get("packages", ["packages/*"])
    packages = _resolve_workspace_globs(root, patterns, "node")
    return MonorepoInfo(
        is_monorepo=True,
        monorepo_type="lerna",
        packages=packages,
    )


def _detect_turborepo(root: Path) -> MonorepoInfo:
    """检测 Turborepo"""
    turbo_json = root / "turbo.json"
    if not turbo_json.exists():
        return MonorepoInfo()

    # Turborepo 通常配合 npm/pnpm workspaces
    info = _detect_pnpm_workspaces(root)
    if not info.is_monorepo:
        info = _detect_npm_workspaces(root)

    if info.is_monorepo:
        info.monorepo_type = "turborepo"
    return info


def _detect_nx(root: Path) -> MonorepoInfo:
    """检测 Nx"""
    nx_json = root / "nx.json"
    if not nx_json.exists():
        return MonorepoInfo()

    # Nx 项目在 apps/ 和 libs/ 下
    packages = []
    for subdir in ("apps", "libs", "packages"):
        d = root / subdir
        if d.is_dir():
            for child in sorted(d.iterdir()):
                if child.is_dir() and (child / "package.json").exists():
                    pkg = _parse_node_package(child)
                    if pkg:
                        packages.append(pkg)
                elif child.is_dir() and (child / "project.json").exists():
                    packages.append(SubProject(
                        name=child.name,
                        path=str(child.relative_to(root)),
                        project_type="node",
                    ))

    return MonorepoInfo(
        is_monorepo=bool(packages),
        monorepo_type="nx",
        packages=packages,
    )


def _detect_cargo_workspaces(root: Path) -> MonorepoInfo:
    """检测 Cargo workspace"""
    cargo_toml = root / "Cargo.toml"
    if not cargo_toml.exists():
        return MonorepoInfo()

    try:
        content = cargo_toml.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return MonorepoInfo()

    # 简易 TOML 解析 [workspace] members
    if "[workspace]" not in content:
        return MonorepoInfo()

    members = []
    in_members = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("members"):
            in_members = True
            # members = ["a", "b"]
            m = re.search(r'\[(.+)\]', stripped)
            if m:
                members = [s.strip().strip('"\'') for s in m.group(1).split(",")]
                break
            continue
        if in_members:
            if stripped == "]":
                break
            cleaned = stripped.strip(',"\'')
            if cleaned:
                members.append(cleaned)

    packages = []
    for pattern in members:
        # Cargo workspace 支持 glob
        if "*" in pattern:
            parent = root / pattern.split("*")[0]
            if parent.is_dir():
                for child in sorted(parent.iterdir()):
                    if child.is_dir() and (child / "Cargo.toml").exists():
                        packages.append(_parse_cargo_package(child, root))
        else:
            member_dir = root / pattern
            if member_dir.is_dir() and (member_dir / "Cargo.toml").exists():
                packages.append(_parse_cargo_package(member_dir, root))

    return MonorepoInfo(
        is_monorepo=bool(packages),
        monorepo_type="cargo",
        packages=packages,
    )


def _detect_go_workspaces(root: Path) -> MonorepoInfo:
    """检测 Go workspace"""
    go_work = root / "go.work"
    if not go_work.exists():
        return MonorepoInfo()

    try:
        content = go_work.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return MonorepoInfo()

    packages = []
    in_use = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("use"):
            in_use = True
            # use (
            if "(" not in stripped:
                # single module: use ./cmd
                mod = stripped[3:].strip()
                if mod:
                    mod_dir = root / mod
                    if mod_dir.is_dir():
                        packages.append(SubProject(
                            name=mod_dir.name,
                            path=mod,
                            project_type="go",
                        ))
            continue
        if in_use:
            if stripped == ")":
                break
            if stripped and not stripped.startswith("//"):
                mod_dir = root / stripped
                if mod_dir.is_dir():
                    packages.append(SubProject(
                        name=mod_dir.name,
                        path=stripped,
                        project_type="go",
                    ))

    return MonorepoInfo(
        is_monorepo=bool(packages),
        monorepo_type="go",
        packages=packages,
    )


def _detect_bazel(root: Path) -> MonorepoInfo:
    """检测 Bazel monorepo"""
    workspace = root / "WORKSPACE"
    workspace_bzl = root / "WORKSPACE.bazel"
    if not workspace.exists() and not workspace_bzl.exists():
        return MonorepoInfo()

    # Bazel 子项目通过 BUILD 文件标识
    packages = []
    for build_file in root.rglob("BUILD"):
        if build_file.parent == root:
            continue
        rel = str(build_file.parent.relative_to(root))
        depth = rel.count(os.sep)
        if depth <= 2:  # 只看前两层
            packages.append(SubProject(
                name=build_file.parent.name,
                path=rel,
                project_type="bazel",
            ))

    # 也查 BUILD.bazel
    for build_file in root.rglob("BUILD.bazel"):
        if build_file.parent == root:
            continue
        rel = str(build_file.parent.relative_to(root))
        depth = rel.count(os.sep)
        if depth <= 2:
            name = build_file.parent.name
            if not any(p.name == name for p in packages):
                packages.append(SubProject(
                    name=name,
                    path=rel,
                    project_type="bazel",
                ))

    return MonorepoInfo(
        is_monorepo=bool(packages),
        monorepo_type="bazel",
        packages=packages[:50],  # 限制数量
    )


def _detect_pants(root: Path) -> MonorepoInfo:
    """检测 Pants build system"""
    if not (root / "pants.toml").exists():
        return MonorepoInfo()

    packages = []
    for build_file in root.rglob("BUILD"):
        if build_file.parent == root:
            continue
        rel = str(build_file.parent.relative_to(root))
        depth = rel.count(os.sep)
        if depth <= 2:
            packages.append(SubProject(
                name=build_file.parent.name,
                path=rel,
                project_type="pants",
            ))

    return MonorepoInfo(
        is_monorepo=bool(packages),
        monorepo_type="pants",
        packages=packages[:50],
    )


def _detect_generic_multi_project(root: Path) -> MonorepoInfo:
    """通用多项目检测 — 目录下有多个独立项目"""
    packages = []

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue

        # 检查是否是独立项目
        indicators = [
            ("package.json", "node"),
            ("setup.py", "python"), ("pyproject.toml", "python"),
            ("Cargo.toml", "rust"), ("go.mod", "go"),
            ("pom.xml", "java"), ("build.gradle", "java"),
            ("CMakeLists.txt", "cpp"), ("Makefile", "make"),
        ]

        for fname, ptype in indicators:
            if (child / fname).exists():
                packages.append(SubProject(
                    name=child.name,
                    path=str(child.relative_to(root)),
                    project_type=ptype,
                ))
                break

    # 至少2个子项目才算 monorepo
    return MonorepoInfo(
        is_monorepo=len(packages) >= 2,
        monorepo_type="generic",
        packages=packages,
    )


# ─────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────

def _resolve_workspace_globs(root: Path, patterns: list[str], ptype: str) -> list[SubProject]:
    """解析 workspace glob 模式，返回子项目列表"""
    packages = []

    for pattern in patterns:
        pattern = pattern.strip().rstrip("/")

        if "*" in pattern:
            # packages/* → 查找匹配的目录
            parent = pattern.split("*")[0].rstrip("/")
            parent_dir = root / parent if parent else root

            if parent_dir.is_dir():
                for child in sorted(parent_dir.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        pkg = _parse_subproject(child, root, ptype)
                        if pkg:
                            packages.append(pkg)
        else:
            # 具体路径
            target = root / pattern
            if target.is_dir():
                pkg = _parse_subproject(target, root, ptype)
                if pkg:
                    packages.append(pkg)

    return packages


def _parse_subproject(pkg_dir: Path, root: Path, ptype: str) -> SubProject | None:
    """解析子项目"""
    if ptype == "node":
        return _parse_node_package(pkg_dir, root)
    elif ptype == "rust":
        return _parse_cargo_package(pkg_dir, root)
    return SubProject(
        name=pkg_dir.name,
        path=str(pkg_dir.relative_to(root)),
        project_type=ptype,
    )


def _parse_node_package(pkg_dir: Path, root: Path | None = None) -> SubProject | None:
    """解析 Node.js 包"""
    pkg_json = pkg_dir / "package.json"
    if not pkg_json.exists():
        return None

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return None

    name = data.get("name", pkg_dir.name)
    version = data.get("version", "")

    # 外部依赖
    deps = list(data.get("dependencies", {}).keys())
    dev_deps = list(data.get("devDependencies", {}).keys())

    # 脚本
    scripts = data.get("scripts", {})

    # 测试
    has_tests = bool(
        (pkg_dir / "tests").is_dir() or
        (pkg_dir / "test").is_dir() or
        (pkg_dir / "__tests__").is_dir() or
        scripts.get("test")
    )

    rel_path = str(pkg_dir.relative_to(root)) if root else str(pkg_dir)

    return SubProject(
        name=name,
        path=rel_path,
        project_type="node",
        version=version,
        dependencies=deps + dev_deps,
        scripts=scripts,
        has_tests=has_tests,
    )


def _parse_cargo_package(pkg_dir: Path, root: Path) -> SubProject:
    """解析 Cargo 包"""
    name = pkg_dir.name
    version = ""

    cargo_toml = pkg_dir / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'name\s*=\s*"([^"]+)"', content)
            if m:
                name = m.group(1)
            m = re.search(r'version\s*=\s*"([^"]+)"', content)
            if m:
                version = m.group(1)
        except OSError:
            pass

    return SubProject(
        name=name,
        path=str(pkg_dir.relative_to(root)),
        project_type="rust",
        version=version,
    )


def _extract_shared_deps_node(data: dict) -> dict[str, str]:
    """提取根 package.json 的共享依赖"""
    shared = {}
    for section in ("dependencies", "devDependencies"):
        for name, version in data.get(section, {}).items():
            shared[name] = str(version)
    return shared


# ─────────────────────────────────────────────
#  依赖图谱
# ─────────────────────────────────────────────

def build_dependency_graph(info: MonorepoInfo) -> list[DependencyEdge]:
    """
    构建子项目间的依赖图谱。

    分析每个子项目的依赖声明，识别对其他子项目的引用。
    """
    edges = []
    pkg_names = {p.name for p in info.packages}

    for pkg in info.packages:
        # 检查外部依赖中是否引用了其他子项目
        for dep in pkg.dependencies:
            if dep in pkg_names:
                edges.append(DependencyEdge(
                    source=pkg.name,
                    target=dep,
                    dep_type="runtime",
                ))
                if dep not in pkg.internal_deps:
                    pkg.internal_deps.append(dep)

    return edges


def topological_sort(info: MonorepoInfo) -> list[str]:
    """拓扑排序 — 确定安装顺序"""
    edges = build_dependency_graph(info)

    # 构建邻接表
    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {}

    for pkg in info.packages:
        graph[pkg.name] = []
        in_degree[pkg.name] = 0

    for edge in edges:
        graph[edge.target].append(edge.source)
        in_degree[edge.source] = in_degree.get(edge.source, 0) + 1

    # Kahn's algorithm
    queue = [name for name, deg in in_degree.items() if deg == 0]
    result = []

    while queue:
        queue.sort()
        node = queue.pop(0)
        result.append(node)

        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 如果有循环依赖，附加剩余节点
    remaining = [name for name in in_degree if name not in result]
    result.extend(remaining)

    return result


# ─────────────────────────────────────────────
#  选择性安装
# ─────────────────────────────────────────────

def plan_selective_install(
    info: MonorepoInfo,
    targets: list[str],
    include_deps: bool = True,
) -> list[SubProject]:
    """
    为选定的子项目生成安装计划。

    自动包含其依赖的子项目。

    Args:
        info: monorepo 信息
        targets: 要安装的子项目名列表
        include_deps: 是否自动包含依赖
    """
    if not include_deps:
        return [p for p in info.packages if p.name in targets]

    # 构建依赖图
    build_dependency_graph(info)

    # 收集所有需要的包（包括依赖）
    needed = set(targets)
    pkg_by_name = {p.name: p for p in info.packages}

    changed = True
    while changed:
        changed = False
        for name in list(needed):
            pkg = pkg_by_name.get(name)
            if pkg:
                for dep in pkg.internal_deps:
                    if dep not in needed:
                        needed.add(dep)
                        changed = True

    # 按拓扑排序
    order = topological_sort(info)
    result = []
    for name in order:
        if name in needed:
            pkg = pkg_by_name.get(name)
            if pkg:
                result.append(pkg)

    return result


def generate_install_commands(
    info: MonorepoInfo,
    targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    生成安装命令。

    Returns:
        [{"package": "名称", "path": "路径", "commands": [...]}]
    """
    if targets:
        packages = plan_selective_install(info, targets)
    else:
        order = topological_sort(info)
        pkg_by_name = {p.name: p for p in info.packages}
        packages = [pkg_by_name[n] for n in order if n in pkg_by_name]

    result = []
    for pkg in packages:
        cmds = _generate_pkg_commands(pkg, info.monorepo_type)
        result.append({
            "package": pkg.name,
            "path": pkg.path,
            "project_type": pkg.project_type,
            "commands": cmds,
        })

    return result


def _generate_pkg_commands(pkg: SubProject, mono_type: str) -> list[str]:
    """为子项目生成安装命令"""
    cmds = [f"cd {pkg.path}"]

    if pkg.project_type == "node":
        if mono_type in ("pnpm", "turborepo"):
            cmds.append(f"pnpm install --filter {pkg.name}")
        elif mono_type == "npm_workspaces":
            cmds.append(f"npm install -w {pkg.path}")
        elif mono_type == "lerna":
            cmds.append(f"npx lerna bootstrap --scope={pkg.name}")
        else:
            cmds.append("npm install")

        if "build" in pkg.scripts:
            cmds.append("npm run build")

    elif pkg.project_type == "python":
        cmds.append("pip install -e .")

    elif pkg.project_type == "rust":
        cmds.append(f"cargo build -p {pkg.name}")

    elif pkg.project_type == "go":
        cmds.append("go build ./...")

    elif pkg.project_type == "java":
        if (Path(pkg.path) / "build.gradle").exists():
            cmds.append("gradle build")
        else:
            cmds.append("mvn install")

    else:
        if (Path(pkg.path) / "Makefile").exists():
            cmds.append("make")

    return cmds


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_monorepo_info(info: MonorepoInfo) -> str:
    """格式化 monorepo 信息"""
    if not info.is_monorepo:
        return "📁 不是 monorepo 项目"

    type_labels = {
        "npm_workspaces": "npm Workspaces",
        "pnpm": "pnpm Workspaces",
        "lerna": "Lerna",
        "turborepo": "Turborepo",
        "nx": "Nx",
        "cargo": "Cargo Workspace",
        "go": "Go Workspace",
        "bazel": "Bazel",
        "pants": "Pants",
        "generic": "通用多项目",
    }

    lines = [
        f"📦 Monorepo 检测结果",
        f"   类型: {type_labels.get(info.monorepo_type, info.monorepo_type)}",
        f"   子项目数: {info.total_packages}",
        "",
        "   子项目列表:",
    ]

    for pkg in info.packages[:20]:
        deps_str = f" (依赖: {', '.join(pkg.internal_deps[:3])})" if pkg.internal_deps else ""
        lines.append(f"     📄 {pkg.name} [{pkg.project_type}] — {pkg.path}{deps_str}")

    if info.total_packages > 20:
        lines.append(f"     ... 还有 {info.total_packages - 20} 个子项目")

    return "\n".join(lines)


def format_install_plan(commands: list[dict]) -> str:
    """格式化安装计划"""
    lines = ["📋 Monorepo 安装计划", ""]

    for i, item in enumerate(commands, 1):
        lines.append(f"  {i}. {item['package']} [{item['project_type']}]")
        lines.append(f"     路径: {item['path']}")
        for cmd in item["commands"]:
            lines.append(f"     $ {cmd}")
        lines.append("")

    return "\n".join(lines)
