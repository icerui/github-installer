"""
auto_update.py - 已安装项目自动更新追踪
==========================================

追踪已安装的 GitHub 项目，检测新版本：
  1. 记录每个项目的安装版本（commit SHA / tag / release）
  2. 检查 GitHub 是否有新 commit / release
  3. 显示更新摘要（新增功能 / 修复 / breaking changes）
  4. 一键更新已安装项目

数据存储：~/.gitinstall/installed.json

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 数据路径 ──
DATA_DIR = Path.home() / ".gitinstall"
INSTALLED_FILE = DATA_DIR / "installed.json"


@dataclass
class InstalledProject:
    """已安装项目记录"""
    owner: str
    repo: str
    install_dir: str
    installed_at: str            # ISO timestamp
    installed_commit: str = ""    # commit SHA
    installed_tag: str = ""       # tag / release version
    installed_branch: str = "main"
    last_check: str = ""          # 最后检查时间
    auto_update: bool = False     # 是否自动更新
    notes: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "install_dir": self.install_dir,
            "installed_at": self.installed_at,
            "installed_commit": self.installed_commit,
            "installed_tag": self.installed_tag,
            "installed_branch": self.installed_branch,
            "last_check": self.last_check,
            "auto_update": self.auto_update,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InstalledProject":
        return cls(
            owner=str(d.get("owner", "")),
            repo=str(d.get("repo", "")),
            install_dir=str(d.get("install_dir", "")),
            installed_at=str(d.get("installed_at", "")),
            installed_commit=str(d.get("installed_commit", "")),
            installed_tag=str(d.get("installed_tag", "")),
            installed_branch=str(d.get("installed_branch", "main")),
            last_check=str(d.get("last_check", "")),
            auto_update=bool(d.get("auto_update", False)),
            notes=str(d.get("notes", "")),
        )


@dataclass
class UpdateInfo:
    """更新信息"""
    owner: str
    repo: str
    has_update: bool = False
    current_commit: str = ""
    latest_commit: str = ""
    current_tag: str = ""
    latest_tag: str = ""
    commits_behind: int = 0
    latest_release_name: str = ""
    latest_release_body: str = ""
    latest_release_date: str = ""
    error: str = ""


# ─────────────────────────────────────────────
#  安装记录管理
# ─────────────────────────────────────────────

class InstallTracker:
    """管理已安装项目记录"""

    def __init__(self, data_file: Path = None):
        self.data_file = data_file or INSTALLED_FILE

    def _ensure_dir(self):
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self.data_file.exists():
            return []
        try:
            with open(self.data_file, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, records: list[dict]):
        self._ensure_dir()
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        # 安全权限
        try:
            os.chmod(self.data_file, 0o600)
        except OSError:
            pass

    def record_install(
        self,
        owner: str,
        repo: str,
        install_dir: str,
        commit: str = "",
        tag: str = "",
        branch: str = "main",
    ) -> InstalledProject:
        """记录一次安装"""
        records = self._load()

        # 检查是否已存在
        full_name = f"{owner}/{repo}".lower()
        records = [r for r in records if f"{r.get('owner','')}/{r.get('repo','')}".lower() != full_name]

        project = InstalledProject(
            owner=owner,
            repo=repo,
            install_dir=install_dir,
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            installed_commit=commit,
            installed_tag=tag,
            installed_branch=branch,
        )
        records.append(project.to_dict())
        self._save(records)
        return project

    def list_installed(self) -> list[InstalledProject]:
        """列出所有已安装项目"""
        return [InstalledProject.from_dict(r) for r in self._load()]

    def get_project(self, owner: str, repo: str) -> Optional[InstalledProject]:
        """获取特定项目的安装记录"""
        full_name = f"{owner}/{repo}".lower()
        for r in self._load():
            if f"{r.get('owner','')}/{r.get('repo','')}".lower() == full_name:
                return InstalledProject.from_dict(r)
        return None

    def remove_project(self, owner: str, repo: str) -> bool:
        """删除安装记录"""
        records = self._load()
        full_name = f"{owner}/{repo}".lower()
        new_records = [r for r in records if f"{r.get('owner','')}/{r.get('repo','')}".lower() != full_name]
        if len(new_records) == len(records):
            return False
        self._save(new_records)
        return True

    def update_check_time(self, owner: str, repo: str):
        """更新最后检查时间"""
        records = self._load()
        full_name = f"{owner}/{repo}".lower()
        for r in records:
            if f"{r.get('owner','')}/{r.get('repo','')}".lower() == full_name:
                r["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                break
        self._save(records)

    def set_auto_update(self, owner: str, repo: str, enabled: bool) -> bool:
        """设置自动更新开关"""
        records = self._load()
        full_name = f"{owner}/{repo}".lower()
        found = False
        for r in records:
            if f"{r.get('owner','')}/{r.get('repo','')}".lower() == full_name:
                r["auto_update"] = enabled
                found = True
                break
        if found:
            self._save(records)
        return found


# ─────────────────────────────────────────────
#  GitHub API 更新检查
# ─────────────────────────────────────────────

def _github_headers() -> dict:
    headers = {"User-Agent": "gitinstall/1.0", "Accept": "application/json"}
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def check_for_update(project: InstalledProject) -> UpdateInfo:
    """
    检查项目是否有新版本。

    比较：
      1. 最新 commit vs 安装时的 commit
      2. 最新 release / tag vs 安装时的 tag
    """
    info = UpdateInfo(
        owner=project.owner,
        repo=project.repo,
        current_commit=project.installed_commit,
        current_tag=project.installed_tag,
    )

    headers = _github_headers()

    # 检查最新 commit
    try:
        branch = project.installed_branch or "main"
        url = f"https://api.github.com/repos/{project.owner}/{project.repo}/commits/{branch}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            info.latest_commit = data.get("sha", "")[:12]
            if info.current_commit and info.latest_commit:
                if not info.latest_commit.startswith(info.current_commit[:7]):
                    info.has_update = True
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        info.error = "无法连接 GitHub API"
        return info

    # 检查最新 release
    try:
        url = f"https://api.github.com/repos/{project.owner}/{project.repo}/releases/latest"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            info.latest_tag = data.get("tag_name", "")
            info.latest_release_name = data.get("name", "")
            info.latest_release_body = data.get("body", "")[:500]
            info.latest_release_date = data.get("published_at", "")

            if info.current_tag and info.latest_tag:
                if info.current_tag != info.latest_tag:
                    info.has_update = True
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404 = 没有 release，正常
            info.error = f"API 错误: {e.code}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    # 对比 commits behind（如果有安装 commit）
    if project.installed_commit and info.latest_commit:
        try:
            url = (
                f"https://api.github.com/repos/{project.owner}/{project.repo}"
                f"/compare/{project.installed_commit}...{info.latest_commit}"
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                info.commits_behind = data.get("ahead_by", 0)
                if info.commits_behind > 0:
                    info.has_update = True
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass

    return info


def check_all_updates(tracker: InstallTracker = None) -> list[UpdateInfo]:
    """检查所有已安装项目的更新"""
    tracker = tracker or InstallTracker()
    results = []
    for project in tracker.list_installed():
        info = check_for_update(project)
        tracker.update_check_time(project.owner, project.repo)
        results.append(info)
    return results


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_installed_list(projects: list[InstalledProject]) -> str:
    """格式化已安装项目列表"""
    if not projects:
        return "  （未记录任何安装项目）\n  安装项目后会自动记录到此列表"

    lines = ["", "📦 已安装项目", "=" * 50]
    for p in projects:
        auto = "🔄" if p.auto_update else "  "
        tag = f" @ {p.installed_tag}" if p.installed_tag else ""
        commit = f" [{p.installed_commit[:7]}]" if p.installed_commit else ""
        lines.append(f"  {auto} {p.full_name}{tag}{commit}")
        lines.append(f"     📂 {p.install_dir}")
        lines.append(f"     ⏰ 安装于 {p.installed_at[:10]}")
        if p.last_check:
            lines.append(f"     🔍 最后检查 {p.last_check[:10]}")
    lines.append(f"\n  共 {len(projects)} 个项目")
    return "\n".join(lines)


def format_update_results(updates: list[UpdateInfo]) -> str:
    """格式化更新检查结果"""
    if not updates:
        return "  （没有可检查的项目）"

    lines = ["", "🔄 更新检查结果", "=" * 50]

    available = [u for u in updates if u.has_update]
    up_to_date = [u for u in updates if not u.has_update and not u.error]
    errors = [u for u in updates if u.error]

    if available:
        lines.append(f"\n📢 有 {len(available)} 个项目可更新:")
        for u in available:
            lines.append(f"  🆕 {u.owner}/{u.repo}")
            if u.commits_behind:
                lines.append(f"     落后 {u.commits_behind} 个 commit")
            if u.latest_tag and u.current_tag:
                lines.append(f"     {u.current_tag} → {u.latest_tag}")
            if u.latest_release_name:
                lines.append(f"     📋 {u.latest_release_name}")
            if u.latest_release_body:
                # 只显示前 3 行
                body_lines = u.latest_release_body.strip().splitlines()[:3]
                for bl in body_lines:
                    lines.append(f"     {bl.strip()}")

    if up_to_date:
        lines.append(f"\n✅ {len(up_to_date)} 个项目已是最新:")
        for u in up_to_date:
            lines.append(f"  ✅ {u.owner}/{u.repo}")

    if errors:
        lines.append(f"\n⚠️  {len(errors)} 个项目检查失败:")
        for u in errors:
            lines.append(f"  ❌ {u.owner}/{u.repo}: {u.error}")

    return "\n".join(lines)


def updates_to_dict(updates: list[UpdateInfo]) -> dict:
    """序列化更新结果为 JSON"""
    return {
        "updates": [
            {
                "owner": u.owner,
                "repo": u.repo,
                "has_update": u.has_update,
                "current_commit": u.current_commit,
                "latest_commit": u.latest_commit,
                "current_tag": u.current_tag,
                "latest_tag": u.latest_tag,
                "commits_behind": u.commits_behind,
                "latest_release_name": u.latest_release_name,
                "error": u.error,
            }
            for u in updates
        ],
        "total": len(updates),
        "available": sum(1 for u in updates if u.has_update),
    }
