"""
autopilot.py - 批量安装自动驾驶模式
======================================

灵感来源：ICE-cluade-SCompany 的 Autopilot Revenue 模式

从文件/列表读取多个项目，后台逐个安装：
  gitinstall autopilot projects.txt
  gitinstall autopilot owner1/repo1 owner2/repo2 ...

特性：
  1. 逐个安装：成功继续，失败跳过
  2. 进度追踪：实时显示总进度
  3. 汇总报告：完成后输出成功/失败统计
  4. 可恢复：中断后可从断点继续
  5. 并发控制：可配置同时安装数（默认 1）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from log import get_logger
from i18n import t

logger = get_logger(__name__)


@dataclass
class BatchItem:
    """批量安装项"""
    identifier: str           # owner/repo 或 URL
    status: str = "pending"   # pending, running, completed, failed, skipped
    install_dir: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_sec: float = 0.0
    error: str = ""
    strategy: str = ""


@dataclass
class BatchResult:
    """批量安装结果"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    items: list[BatchItem] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    total_duration_sec: float = 0.0

    @property
    def success_rate(self) -> float:
        done = self.completed + self.failed
        if done == 0:
            return 0.0
        return self.completed / done

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
            "success_rate": self.success_rate,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_sec": self.total_duration_sec,
            "items": [
                {
                    "identifier": it.identifier,
                    "status": it.status,
                    "install_dir": it.install_dir,
                    "duration_sec": it.duration_sec,
                    "error": it.error,
                    "strategy": it.strategy,
                }
                for it in self.items
            ],
        }


# ── 持久化路径 ──
AUTOPILOT_DIR = Path.home() / ".gitinstall" / "autopilot"
AUTOPILOT_STATE = AUTOPILOT_DIR / "state.json"


def parse_project_list(source: str) -> list[str]:
    """解析项目列表

    支持：
      - 单个 owner/repo
      - 空格/逗号分隔多个
      - 文件路径（每行一个或 JSON 数组）
    """
    projects = []

    # 检查是否是文件
    path = Path(source)
    if path.exists() and path.is_file():
        content = path.read_text(encoding="utf-8").strip()
        # 尝试 JSON 数组
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        projects.append(item.strip())
                    elif isinstance(item, dict):
                        projects.append(item.get("project", item.get("repo", "")))
                return [p for p in projects if p]
        except json.JSONDecodeError:
            pass
        # 按行解析
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                # 去除行注释
                line = line.split("#")[0].strip()
                if line:
                    projects.append(line)
        return projects

    # 空格/逗号分隔
    for part in re.split(r'[,\s]+', source):
        part = part.strip()
        if part:
            projects.append(part)

    return projects


def run_autopilot(projects: list[str], install_dir: str = None,
                  llm_force: str = None, dry_run: bool = False,
                  on_progress: callable = None) -> BatchResult:
    """执行批量安装

    Args:
        projects: 项目列表
        install_dir: 基础安装目录
        llm_force: 强制使用的 LLM
        dry_run: 仅规划不执行
        on_progress: 进度回调 (current, total, item)
    """
    result = BatchResult(
        total=len(projects),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    for item_str in projects:
        result.items.append(BatchItem(identifier=item_str))

    # 保存初始状态
    _save_state(result)

    for i, item in enumerate(result.items):
        item.status = "running"
        item.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 进度回调
        if on_progress:
            on_progress(i + 1, result.total, item)

        logger.info("="*60)
        logger.info(t("autopilot.progress", current=i+1, total=result.total, identifier=item.identifier))
        logger.info("="*60)

        start_time = time.time()
        try:
            # 懒加载避免循环导入
            from main import cmd_install

            install_result = cmd_install(
                item.identifier,
                install_dir=install_dir,
                llm_force=llm_force,
                dry_run=dry_run,
            )

            item.duration_sec = time.time() - start_time
            item.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            if install_result.get("success"):
                item.status = "completed"
                item.install_dir = install_result.get("install_dir", "")
                item.strategy = install_result.get("plan_strategy", "")
                result.completed += 1
                logger.info(t("autopilot.success", duration=f"{item.duration_sec:.1f}"))
            else:
                item.status = "failed"
                item.error = install_result.get("error_summary", "未知错误")[:200]
                item.strategy = install_result.get("plan_strategy", "")
                result.failed += 1
                logger.error(t("autopilot.install_failed", error=item.error[:80]))

        except KeyboardInterrupt:
            item.status = "skipped"
            item.duration_sec = time.time() - start_time
            result.skipped += 1
            logger.warning(t("autopilot.user_interrupted"))
            # 剩余标记为跳过
            for remaining in result.items[i+1:]:
                remaining.status = "skipped"
                result.skipped += 1
            break

        except Exception as e:
            item.status = "failed"
            item.error = str(e)[:200]
            item.duration_sec = time.time() - start_time
            result.failed += 1
            logger.error(t("autopilot.exception", error=e))

        # 每步保存状态（支持恢复）
        _save_state(result)

    result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result.total_duration_sec = sum(it.duration_sec for it in result.items)
    _save_state(result)
    return result


def resume_autopilot(llm_force: str = None,
                     install_dir: str = None) -> Optional[BatchResult]:
    """从上次中断处恢复"""
    state = _load_state()
    if not state:
        return None

    # 找到第一个 pending/skipped 的项目
    resume_idx = -1
    for i, item in enumerate(state.items):
        if item.status in ("pending", "skipped", "running"):
            resume_idx = i
            break

    if resume_idx < 0:
        return state  # 所有项目已完成

    # 恢复执行
    pending = [it.identifier for it in state.items[resume_idx:]
               if it.status in ("pending", "skipped", "running")]

    if not pending:
        return state

    new_result = run_autopilot(pending, install_dir=install_dir,
                               llm_force=llm_force)

    # 合并结果
    for i, item in enumerate(state.items[resume_idx:]):
        if i < len(new_result.items):
            state.items[resume_idx + i] = new_result.items[i]

    state.completed = sum(1 for it in state.items if it.status == "completed")
    state.failed = sum(1 for it in state.items if it.status == "failed")
    state.skipped = sum(1 for it in state.items if it.status == "skipped")
    state.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state.total_duration_sec = sum(it.duration_sec for it in state.items)
    _save_state(state)
    return state


def _save_state(result: BatchResult):
    """保存自动驾驶状态"""
    AUTOPILOT_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTOPILOT_STATE, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
    try:
        os.chmod(AUTOPILOT_STATE, 0o600)
    except OSError:
        pass


def _load_state() -> Optional[BatchResult]:
    """加载自动驾驶状态"""
    if not AUTOPILOT_STATE.exists():
        return None
    try:
        with open(AUTOPILOT_STATE, encoding="utf-8") as f:
            data = json.load(f)
        result = BatchResult(
            total=data.get("total", 0),
            completed=data.get("completed", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            total_duration_sec=data.get("total_duration_sec", 0.0),
        )
        for item_data in data.get("items", []):
            result.items.append(BatchItem(
                identifier=item_data.get("identifier", ""),
                status=item_data.get("status", "pending"),
                install_dir=item_data.get("install_dir", ""),
                duration_sec=item_data.get("duration_sec", 0.0),
                error=item_data.get("error", ""),
                strategy=item_data.get("strategy", ""),
            ))
        return result
    except (json.JSONDecodeError, OSError):
        return None


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_batch_result(result: BatchResult) -> str:
    """格式化批量安装结果"""
    lines = [
        "",
        "═" * 60,
        "🚗 自动驾驶安装报告",
        "═" * 60,
        f"  总计：{result.total} 个项目",
        f"  ✅ 成功：{result.completed}",
        f"  ❌ 失败：{result.failed}",
        f"  ⏭️  跳过：{result.skipped}",
        f"  成功率：{result.success_rate:.1%}",
        f"  总耗时：{result.total_duration_sec:.1f}s",
        "",
        "─" * 60,
    ]

    for item in result.items:
        if item.status == "completed":
            lines.append(f"  ✅ {item.identifier} ({item.duration_sec:.1f}s)")
        elif item.status == "failed":
            lines.append(f"  ❌ {item.identifier} ({item.duration_sec:.1f}s)")
            if item.error:
                lines.append(f"     错误：{item.error[:80]}")
        elif item.status == "skipped":
            lines.append(f"  ⏭️  {item.identifier} (跳过)")
        else:
            lines.append(f"  ⏳ {item.identifier} ({item.status})")

    lines.append("═" * 60)
    return "\n".join(lines)
