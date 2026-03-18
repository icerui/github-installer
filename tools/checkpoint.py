"""
checkpoint.py - 安装断点恢复系统
==================================

灵感来源：PersonalBrain 的 status.json 断点恢复机制

安装过程中每完成一步写入进度文件，遇到网络中断/崩溃后：
  gitinstall resume [project]
可以从上次断点继续安装，无需从头重来。

数据存储：~/.gitinstall/checkpoints/<owner>__<repo>.json

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 数据路径 ──
CHECKPOINT_DIR = Path.home() / ".gitinstall" / "checkpoints"


@dataclass
class StepCheckpoint:
    """单步检查点"""
    index: int
    command: str
    description: str
    status: str = "pending"     # pending, running, completed, failed, skipped
    started_at: str = ""
    completed_at: str = ""
    exit_code: int = -1
    error: str = ""
    duration_sec: float = 0.0


@dataclass
class InstallCheckpoint:
    """完整安装检查点"""
    project: str                 # owner/repo
    owner: str = ""
    repo: str = ""
    install_dir: str = ""
    llm_used: str = ""
    strategy: str = ""
    created_at: str = ""
    updated_at: str = ""
    status: str = "in_progress"  # in_progress, completed, failed, abandoned
    current_step: int = 0
    total_steps: int = 0
    steps: list[StepCheckpoint] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    env_snapshot: dict = field(default_factory=dict)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    @property
    def progress_pct(self) -> float:
        if not self.total_steps:
            return 0.0
        return (self.completed_steps / self.total_steps) * 100

    @property
    def checkpoint_file(self) -> Path:
        safe = f"{self.owner}__{self.repo}".replace("/", "__")
        return CHECKPOINT_DIR / f"{safe}.json"

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "owner": self.owner,
            "repo": self.repo,
            "install_dir": self.install_dir,
            "llm_used": self.llm_used,
            "strategy": self.strategy,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "steps": [
                {
                    "index": s.index,
                    "command": s.command,
                    "description": s.description,
                    "status": s.status,
                    "started_at": s.started_at,
                    "completed_at": s.completed_at,
                    "exit_code": s.exit_code,
                    "error": s.error,
                    "duration_sec": s.duration_sec,
                }
                for s in self.steps
            ],
            "plan": self.plan,
            "env_snapshot": self.env_snapshot,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InstallCheckpoint":
        cp = cls(
            project=d.get("project", ""),
            owner=d.get("owner", ""),
            repo=d.get("repo", ""),
            install_dir=d.get("install_dir", ""),
            llm_used=d.get("llm_used", ""),
            strategy=d.get("strategy", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            status=d.get("status", "in_progress"),
            current_step=d.get("current_step", 0),
            total_steps=d.get("total_steps", 0),
            plan=d.get("plan", {}),
            env_snapshot=d.get("env_snapshot", {}),
        )
        for s in d.get("steps", []):
            cp.steps.append(StepCheckpoint(
                index=s.get("index", 0),
                command=s.get("command", ""),
                description=s.get("description", ""),
                status=s.get("status", "pending"),
                started_at=s.get("started_at", ""),
                completed_at=s.get("completed_at", ""),
                exit_code=s.get("exit_code", -1),
                error=s.get("error", ""),
                duration_sec=s.get("duration_sec", 0.0),
            ))
        return cp


# ─────────────────────────────────────────────
#  Checkpoint Manager
# ─────────────────────────────────────────────

class CheckpointManager:
    """管理安装断点"""

    def __init__(self):
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    def create(self, owner: str, repo: str, plan: dict,
               install_dir: str = "", llm_used: str = "",
               env_snapshot: dict = None) -> InstallCheckpoint:
        """创建新的安装检查点"""
        steps_data = plan.get("steps", [])
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        cp = InstallCheckpoint(
            project=f"{owner}/{repo}",
            owner=owner, repo=repo,
            install_dir=install_dir,
            llm_used=llm_used,
            strategy=plan.get("strategy", ""),
            created_at=now, updated_at=now,
            status="in_progress",
            current_step=0,
            total_steps=len(steps_data),
            plan=plan,
            env_snapshot=env_snapshot or {},
        )

        for i, step in enumerate(steps_data):
            cp.steps.append(StepCheckpoint(
                index=i,
                command=step.get("command", ""),
                description=step.get("description", ""),
            ))

        self._save(cp)
        return cp

    def mark_step_running(self, cp: InstallCheckpoint, step_idx: int):
        """标记步骤开始执行"""
        if step_idx < len(cp.steps):
            cp.steps[step_idx].status = "running"
            cp.steps[step_idx].started_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cp.current_step = step_idx
            self._save(cp)

    def mark_step_completed(self, cp: InstallCheckpoint, step_idx: int,
                            exit_code: int = 0, duration_sec: float = 0.0):
        """标记步骤完成"""
        if step_idx < len(cp.steps):
            s = cp.steps[step_idx]
            s.status = "completed"
            s.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            s.exit_code = exit_code
            s.duration_sec = duration_sec
            cp.current_step = step_idx + 1
            self._save(cp)

    def mark_step_failed(self, cp: InstallCheckpoint, step_idx: int,
                         exit_code: int = 1, error: str = "",
                         duration_sec: float = 0.0):
        """标记步骤失败"""
        if step_idx < len(cp.steps):
            s = cp.steps[step_idx]
            s.status = "failed"
            s.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            s.exit_code = exit_code
            s.error = error
            s.duration_sec = duration_sec
            cp.status = "failed"
            self._save(cp)

    def mark_completed(self, cp: InstallCheckpoint):
        """标记整个安装完成"""
        cp.status = "completed"
        cp.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save(cp)

    def mark_abandoned(self, cp: InstallCheckpoint):
        """标记安装放弃"""
        cp.status = "abandoned"
        cp.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save(cp)

    def get_checkpoint(self, owner: str, repo: str) -> Optional[InstallCheckpoint]:
        """获取项目的检查点"""
        safe = f"{owner}__{repo}"
        path = CHECKPOINT_DIR / f"{safe}.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return InstallCheckpoint.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def get_resumable(self) -> list[InstallCheckpoint]:
        """获取所有可恢复的安装"""
        results = []
        if not CHECKPOINT_DIR.exists():
            return results
        for f in CHECKPOINT_DIR.glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                cp = InstallCheckpoint.from_dict(data)
                if cp.status in ("in_progress", "failed"):
                    results.append(cp)
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(results, key=lambda c: c.updated_at, reverse=True)

    def remove_checkpoint(self, owner: str, repo: str) -> bool:
        """删除检查点"""
        safe = f"{owner}__{repo}"
        path = CHECKPOINT_DIR / f"{safe}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def get_resume_step(self, cp: InstallCheckpoint) -> int:
        """计算应该从哪一步恢复"""
        for i, s in enumerate(cp.steps):
            if s.status in ("pending", "failed", "running"):
                return i
        return len(cp.steps)  # 全部完成

    def _save(self, cp: InstallCheckpoint):
        """保存检查点到文件"""
        cp.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = cp.checkpoint_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cp.to_dict(), f, indent=2, ensure_ascii=False)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_checkpoint_list(checkpoints: list[InstallCheckpoint]) -> str:
    """格式化可恢复安装列表"""
    if not checkpoints:
        return "  📭 没有可恢复的安装任务"

    lines = [f"  📋 发现 {len(checkpoints)} 个可恢复的安装：\n"]
    for cp in checkpoints:
        icon = "⏸️ " if cp.status == "in_progress" else "❌"
        lines.append(f"  {icon} {cp.project}")
        lines.append(f"     进度：{cp.completed_steps}/{cp.total_steps} 步"
                     f"（{cp.progress_pct:.0f}%）")
        lines.append(f"     策略：{cp.strategy}  LLM：{cp.llm_used}")
        lines.append(f"     更新：{cp.updated_at}")
        # 显示失败点
        failed = [s for s in cp.steps if s.status == "failed"]
        if failed:
            lines.append(f"     失败：Step {failed[0].index + 1} - {failed[0].description}")
            if failed[0].error:
                lines.append(f"     错误：{failed[0].error[:80]}")
        lines.append("")

    return "\n".join(lines)


def format_resume_plan(cp: InstallCheckpoint, resume_step: int) -> str:
    """格式化恢复计划"""
    lines = [
        f"\n🔄 恢复安装：{cp.project}",
        f"   从第 {resume_step + 1}/{cp.total_steps} 步继续",
        f"   已完成：{cp.completed_steps} 步",
        "   ─" * 25,
    ]
    for i, s in enumerate(cp.steps):
        if s.status == "completed":
            lines.append(f"   ✅ {i+1}. {s.description} ({s.duration_sec:.1f}s)")
        elif i == resume_step:
            lines.append(f"   ▶️  {i+1}. {s.description}  ← 从这里继续")
        else:
            lines.append(f"   ⏳ {i+1}. {s.description}")
    lines.append("   ─" * 25)
    return "\n".join(lines)
