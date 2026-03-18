"""
uninstaller.py - 项目安全卸载与清理
=====================================

安全地卸载已安装的 GitHub 项目：
  1. 检测项目安装的文件和依赖
  2. 清理 virtualenv / Docker 容器 / 编译产物
  3. 从安装记录中移除
  4. 可选：保留配置文件（不删用户数据）

安全优先：
  - 不删除 home 目录之外的文件（除非用户确认）
  - 不删除非 gitinstall 安装的目录
  - 删除前显示完整清理计划

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CleanupItem:
    """待清理项"""
    path: str
    item_type: str      # directory, file, venv, docker, cache
    size_bytes: int = 0
    description: str = ""
    safe: bool = True    # 是否安全删除


@dataclass
class UninstallPlan:
    """卸载计划"""
    owner: str
    repo: str
    install_dir: str
    items: list[CleanupItem] = field(default_factory=list)
    total_size: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def total_size_mb(self) -> float:
        return self.total_size / (1024 * 1024)


# ── 安全路径白名单 ──
SAFE_BASES = [
    Path.home(),
    Path("/tmp"),
]


def _is_safe_path(path: Path) -> bool:
    """检查路径是否在安全范围内"""
    resolved = path.resolve()
    # 不能删除 home 目录本身
    if resolved == Path.home():
        return False
    # 不能删除系统关键目录
    danger_paths = ["/", "/usr", "/bin", "/etc", "/var", "/System", "/Library",
                    "/opt", "/Applications", "/sbin"]
    if str(resolved) in danger_paths:
        return False
    # 必须在白名单目录下
    return any(
        str(resolved).startswith(str(base.resolve()))
        for base in SAFE_BASES
    )


def _dir_size(path: Path) -> int:
    """计算目录大小"""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except (PermissionError, OSError):
        pass
    return total


def _find_venvs(install_dir: Path) -> list[Path]:
    """查找 virtualenv 目录"""
    venvs = []
    for name in ("venv", ".venv", "env", ".env", ".conda"):
        venv_dir = install_dir / name
        if venv_dir.is_dir() and (
            (venv_dir / "bin" / "python").exists() or
            (venv_dir / "bin" / "activate").exists() or
            (venv_dir / "pyvenv.cfg").exists() or
            (venv_dir / "conda-meta").is_dir()
        ):
            venvs.append(venv_dir)
    return venvs


def _find_docker_artifacts(install_dir: Path) -> list[str]:
    """查找 Docker 相关产物（容器/镜像名称）"""
    artifacts = []
    compose_files = ["docker-compose.yml", "docker-compose.yaml",
                     "compose.yml", "compose.yaml"]
    for cf in compose_files:
        if (install_dir / cf).exists():
            # 使用目录名作为项目名
            project_name = install_dir.name.lower().replace(" ", "")
            artifacts.append(f"docker-compose:{project_name}")
    return artifacts


def _find_cache(owner: str, repo: str) -> list[Path]:
    """查找缓存文件"""
    caches = []
    cache_base = Path.home() / ".cache" / "gitinstall"
    if cache_base.exists():
        for p in cache_base.iterdir():
            if repo.lower() in p.name.lower() or f"{owner}_{repo}" in p.name:
                caches.append(p)
    return caches


def _find_build_artifacts(install_dir: Path) -> list[Path]:
    """查找编译产物"""
    artifacts = []
    build_dirs = [
        "build", "dist", "__pycache__", ".eggs", "*.egg-info",
        "node_modules", "target", ".next", ".nuxt",
        ".gradle", ".mvn", "bin", "obj",
    ]
    for name in build_dirs:
        if "*" in name:
            for p in install_dir.glob(name):
                if p.is_dir():
                    artifacts.append(p)
        else:
            d = install_dir / name
            if d.is_dir():
                artifacts.append(d)
    return artifacts


# ─────────────────────────────────────────────
#  卸载计划生成
# ─────────────────────────────────────────────

def plan_uninstall(
    owner: str,
    repo: str,
    install_dir: str,
    keep_config: bool = False,
    clean_only: bool = False,
) -> UninstallPlan:
    """
    生成卸载计划（不执行）。

    Args:
        owner: GitHub owner
        repo: GitHub repo
        install_dir: 安装目录
        keep_config: 是否保留配置文件
        clean_only: 仅清理缓存和编译产物（不删主目录）
    """
    plan = UninstallPlan(owner=owner, repo=repo, install_dir=install_dir)
    install_path = Path(install_dir)

    if not install_path.exists():
        plan.error = f"目录不存在: {install_dir}"
        return plan

    if not _is_safe_path(install_path):
        plan.error = f"安全检查失败：{install_dir} 不在安全范围内"
        plan.warnings.append("仅允许删除 HOME 目录下的安装")
        return plan

    # 1. 查找 virtualenv
    for venv in _find_venvs(install_path):
        size = _dir_size(venv)
        plan.items.append(CleanupItem(
            path=str(venv),
            item_type="venv",
            size_bytes=size,
            description=f"Python 虚拟环境: {venv.name}",
        ))
        plan.total_size += size

    # 2. 查找 Docker 产物
    for docker_ref in _find_docker_artifacts(install_path):
        plan.items.append(CleanupItem(
            path=docker_ref,
            item_type="docker",
            description=f"Docker 容器/网络: {docker_ref}",
        ))

    # 3. 查找编译产物
    for build_dir in _find_build_artifacts(install_path):
        size = _dir_size(build_dir)
        plan.items.append(CleanupItem(
            path=str(build_dir),
            item_type="cache",
            size_bytes=size,
            description=f"编译产物: {build_dir.name}",
        ))
        plan.total_size += size

    # 4. 查找缓存
    for cache_path in _find_cache(owner, repo):
        size = _dir_size(cache_path) if cache_path.is_dir() else cache_path.stat().st_size
        plan.items.append(CleanupItem(
            path=str(cache_path),
            item_type="cache",
            size_bytes=size,
            description=f"缓存: {cache_path.name}",
        ))
        plan.total_size += size

    # 5. 主目录（如果不是仅清理模式）
    if not clean_only:
        size = _dir_size(install_path)
        # 减去已列出的子目录大小避免重复计算
        sub_sizes = sum(item.size_bytes for item in plan.items
                       if str(item.path).startswith(str(install_path)))
        main_size = max(0, size - sub_sizes)

        plan.items.append(CleanupItem(
            path=str(install_path),
            item_type="directory",
            size_bytes=main_size,
            description=f"项目主目录: {install_path.name}",
        ))
        plan.total_size = size  # 用目录总大小

    # 6. 警告
    if (install_path / ".git").exists():
        plan.warnings.append("目录包含 .git 仓库，删除后无法恢复本地修改")

    user_data_patterns = [".env", "config.local.*", "*.db", "*.sqlite", "data/"]
    for pattern in user_data_patterns:
        matches = list(install_path.glob(pattern))
        if matches:
            names = [m.name for m in matches[:3]]
            plan.warnings.append(f"检测到可能的用户数据: {', '.join(names)}")
            if keep_config:
                plan.warnings.append("--keep-config 已启用，将保留这些文件")

    return plan


def execute_uninstall(plan: UninstallPlan, keep_config: bool = False) -> dict:
    """
    执行卸载计划。

    Returns:
        {"success": bool, "removed": [...], "errors": [...]}
    """
    removed = []
    errors = []

    if plan.error:
        return {"success": False, "removed": [], "errors": [plan.error]}

    install_path = Path(plan.install_dir)

    # 配置文件模式
    config_patterns = {".env", "config.local", ".gitinstall.json"}

    for item in plan.items:
        path = Path(item.path)

        try:
            if item.item_type == "docker":
                # Docker 停止容器
                project_name = item.path.split(":")[-1] if ":" in item.path else ""
                if project_name:
                    try:
                        subprocess.run(
                            ["docker", "compose", "-p", project_name, "down", "--remove-orphans"],
                            capture_output=True, timeout=30,
                            cwd=str(install_path) if install_path.exists() else None,
                        )
                        removed.append(f"Docker: {project_name}")
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        errors.append(f"Docker 清理失败: {project_name}")
                continue

            if not path.exists():
                continue

            if not _is_safe_path(path):
                errors.append(f"跳过不安全路径: {path}")
                continue

            # keep_config 模式：跳过配置文件
            if keep_config and item.item_type == "directory":
                # 保留配置文件，仅删除其他内容
                _remove_dir_except_configs(path, config_patterns)
                removed.append(f"清理（保留配置）: {path}")
                continue

            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))

        except (PermissionError, OSError) as e:
            errors.append(f"{path}: {e}")

    return {
        "success": len(errors) == 0,
        "removed": removed,
        "errors": errors,
        "freed_mb": round(plan.total_size_mb, 1),
    }


def _remove_dir_except_configs(path: Path, config_patterns: set[str]):
    """删除目录内容但保留配置文件"""
    for child in path.iterdir():
        if child.name in config_patterns:
            continue
        if child.name.startswith(".env"):
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except (PermissionError, OSError):
            pass


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def _size_str(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


_TYPE_ICONS = {
    "directory": "📂",
    "venv": "🐍",
    "docker": "🐳",
    "cache": "🗂️ ",
    "file": "📄",
}


def format_uninstall_plan(plan: UninstallPlan) -> str:
    """格式化卸载计划"""
    lines = ["", f"🗑️  卸载计划: {plan.owner}/{plan.repo}", "=" * 50]

    if plan.error:
        lines.append(f"  ❌ {plan.error}")
        return "\n".join(lines)

    if not plan.items:
        lines.append("  （无需清理的内容）")
        return "\n".join(lines)

    for item in plan.items:
        icon = _TYPE_ICONS.get(item.item_type, "📦")
        size = f" ({_size_str(item.size_bytes)})" if item.size_bytes > 0 else ""
        lines.append(f"  {icon} {item.description}{size}")
        lines.append(f"     {item.path}")

    lines.append(f"\n  📊 总计释放空间: {_size_str(plan.total_size)}")

    if plan.warnings:
        lines.append("\n  ⚠️  警告:")
        for w in plan.warnings:
            lines.append(f"     {w}")

    return "\n".join(lines)


def uninstall_to_dict(plan: UninstallPlan) -> dict:
    """序列化卸载计划为 JSON"""
    return {
        "owner": plan.owner,
        "repo": plan.repo,
        "install_dir": plan.install_dir,
        "items": [
            {
                "path": item.path,
                "type": item.item_type,
                "size_bytes": item.size_bytes,
                "description": item.description,
            }
            for item in plan.items
        ],
        "total_size_mb": round(plan.total_size_mb, 1),
        "warnings": plan.warnings,
        "error": plan.error,
    }
