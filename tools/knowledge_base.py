"""
knowledge_base.py - 安装知识库
================================

灵感来源：ICE-OEM 的知识库向量检索

把所有成功/失败安装案例存储为「知识」，新安装时通过
关键词匹配找到「相似项目怎么装的」。

知识结构：
  每个安装案例记录：
    - 项目信息：owner/repo, project_types, languages
    - 环境信息：OS, GPU, 已有工具
    - 安装策略：使用的 steps
    - 结果：成功/失败 + 错误信息
    - 标签：便于检索

存储：~/.gitinstall/knowledge.json（纯 JSON，无需向量数据库）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 数据路径 ──
KB_DIR = Path.home() / ".gitinstall"
KB_FILE = KB_DIR / "knowledge.json"


@dataclass
class KnowledgeEntry:
    """一条安装知识"""
    id: str                         # 唯一 ID
    project: str                     # owner/repo
    project_types: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    os_type: str = ""                # darwin, linux, win32
    arch: str = ""                   # x86_64, arm64
    gpu_type: str = ""               # nvidia, apple_silicon, none
    strategy: str = ""               # 使用的安装策略
    steps: list[dict] = field(default_factory=list)  # 安装步骤
    success: bool = False
    error_type: str = ""             # 错误类别
    error_message: str = ""          # 错误信息
    fix_applied: str = ""            # 应用的修复
    duration_sec: float = 0.0
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "project_types": self.project_types,
            "languages": self.languages,
            "os_type": self.os_type,
            "arch": self.arch,
            "gpu_type": self.gpu_type,
            "strategy": self.strategy,
            "steps": self.steps,
            "success": self.success,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "fix_applied": self.fix_applied,
            "duration_sec": self.duration_sec,
            "tags": self.tags,
            "created_at": self.created_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeEntry":
        return cls(
            id=str(d.get("id", "")),
            project=str(d.get("project", "")),
            project_types=d.get("project_types", []),
            languages=d.get("languages", []),
            os_type=str(d.get("os_type", "")),
            arch=str(d.get("arch", "")),
            gpu_type=str(d.get("gpu_type", "")),
            strategy=str(d.get("strategy", "")),
            steps=d.get("steps", []),
            success=bool(d.get("success", False)),
            error_type=str(d.get("error_type", "")),
            error_message=str(d.get("error_message", "")),
            fix_applied=str(d.get("fix_applied", "")),
            duration_sec=float(d.get("duration_sec", 0.0)),
            tags=d.get("tags", []),
            created_at=str(d.get("created_at", "")),
            notes=str(d.get("notes", "")),
        )

    @property
    def keywords(self) -> set[str]:
        """提取关键词用于匹配"""
        words = set()
        words.add(self.project.lower())
        # owner 和 repo 分别加
        parts = self.project.split("/")
        for p in parts:
            words.add(p.lower())
        words.update(t.lower() for t in self.project_types)
        words.update(l.lower() for l in self.languages)
        words.update(t.lower() for t in self.tags)
        if self.os_type:
            words.add(self.os_type.lower())
        if self.gpu_type:
            words.add(self.gpu_type.lower())
        if self.strategy:
            words.add(self.strategy.lower())
        return words


@dataclass
class MatchResult:
    """知识匹配结果"""
    entry: KnowledgeEntry
    score: float = 0.0       # 匹配分数 0-1
    match_reasons: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  Knowledge Base Manager
# ─────────────────────────────────────────────

class KnowledgeBase:
    """安装知识库"""

    def __init__(self, kb_file: Path = None):
        self.kb_file = kb_file or KB_FILE
        self._entries: list[KnowledgeEntry] = []
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load()
        self._loaded = True

    def _load(self):
        if not self.kb_file.exists():
            self._entries = []
            return
        try:
            with open(self.kb_file, encoding="utf-8") as f:
                data = json.load(f)
            self._entries = [KnowledgeEntry.from_dict(d)
                           for d in (data if isinstance(data, list) else [])]
        except (json.JSONDecodeError, OSError):
            self._entries = []

    def _save(self):
        self.kb_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.kb_file, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in self._entries],
                     f, indent=2, ensure_ascii=False)
        try:
            os.chmod(self.kb_file, 0o600)
        except OSError:
            pass

    def record(self, project: str, project_types: list[str] = None,
               languages: list[str] = None, os_type: str = "",
               arch: str = "", gpu_type: str = "",
               strategy: str = "", steps: list[dict] = None,
               success: bool = False, error_type: str = "",
               error_message: str = "", fix_applied: str = "",
               duration_sec: float = 0.0, tags: list[str] = None,
               notes: str = "") -> KnowledgeEntry:
        """记录一次安装知识"""
        self._ensure_loaded()

        entry_id = f"{project}_{int(time.time())}"
        entry = KnowledgeEntry(
            id=entry_id,
            project=project,
            project_types=project_types or [],
            languages=languages or [],
            os_type=os_type, arch=arch, gpu_type=gpu_type,
            strategy=strategy,
            steps=steps or [],
            success=success,
            error_type=error_type,
            error_message=error_message[:500],
            fix_applied=fix_applied,
            duration_sec=duration_sec,
            tags=tags or [],
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            notes=notes,
        )
        self._entries.append(entry)

        # 限制记录数量
        if len(self._entries) > 5000:
            self._entries = self._entries[-5000:]

        self._save()
        return entry

    def search(self, project: str = "", project_types: list[str] = None,
               languages: list[str] = None, os_type: str = "",
               gpu_type: str = "", success_only: bool = False,
               limit: int = 10) -> list[MatchResult]:
        """搜索相似安装案例"""
        self._ensure_loaded()

        # 构建查询关键词
        query_words = set()
        if project:
            query_words.add(project.lower())
            for part in project.split("/"):
                query_words.add(part.lower())
        if project_types:
            query_words.update(t.lower() for t in project_types)
        if languages:
            query_words.update(l.lower() for l in languages)
        if os_type:
            query_words.add(os_type.lower())
        if gpu_type:
            query_words.add(gpu_type.lower())

        if not query_words:
            return []

        results = []
        for entry in self._entries:
            if success_only and not entry.success:
                continue

            entry_kw = entry.keywords
            common = query_words & entry_kw
            if not common:
                continue

            # 计算匹配分数
            score = len(common) / max(len(query_words), 1)
            reasons = []

            # 完全相同项目 → 高分
            if project and entry.project.lower() == project.lower():
                score = min(score + 0.5, 1.0)
                reasons.append("完全相同项目")

            # 相同 OS → 加分
            if os_type and entry.os_type == os_type:
                score = min(score + 0.1, 1.0)
                reasons.append(f"相同 OS ({os_type})")

            # 相同 GPU → 加分
            if gpu_type and entry.gpu_type == gpu_type:
                score = min(score + 0.1, 1.0)
                reasons.append(f"相同 GPU ({gpu_type})")

            # 项目类型匹配
            if project_types and entry.project_types:
                type_overlap = set(t.lower() for t in project_types) & \
                              set(t.lower() for t in entry.project_types)
                if type_overlap:
                    score = min(score + 0.15, 1.0)
                    reasons.append(f"类型匹配 ({', '.join(type_overlap)})")

            results.append(MatchResult(entry=entry, score=score,
                                      match_reasons=reasons))

        # 按分数排序
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get_success_rate(self, project: str = "") -> dict:
        """获取安装成功率统计"""
        self._ensure_loaded()

        entries = self._entries
        if project:
            entries = [e for e in entries
                      if e.project.lower() == project.lower()]

        total = len(entries)
        success = sum(1 for e in entries if e.success)

        # 按策略分组
        strategy_stats: dict[str, dict] = {}
        for e in entries:
            key = e.strategy or "unknown"
            if key not in strategy_stats:
                strategy_stats[key] = {"total": 0, "success": 0}
            strategy_stats[key]["total"] += 1
            if e.success:
                strategy_stats[key]["success"] += 1

        # 常见错误
        errors = Counter(e.error_type for e in entries if e.error_type)

        return {
            "total": total,
            "success": success,
            "rate": success / max(total, 1),
            "strategies": strategy_stats,
            "common_errors": dict(errors.most_common(5)),
        }

    def get_stats(self) -> dict:
        """获取知识库统计"""
        self._ensure_loaded()

        total = len(self._entries)
        success = sum(1 for e in self._entries if e.success)
        projects = len(set(e.project for e in self._entries))
        strategies = Counter(e.strategy for e in self._entries if e.strategy)

        return {
            "total_entries": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate": success / max(total, 1),
            "unique_projects": projects,
            "top_strategies": dict(strategies.most_common(5)),
        }

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._entries)


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_search_results(results: list[MatchResult]) -> str:
    """格式化搜索结果"""
    if not results:
        return "  📭 知识库中没有相似案例"

    lines = [f"  📚 找到 {len(results)} 个相似安装案例：", ""]
    for i, r in enumerate(results[:5]):
        icon = "✅" if r.entry.success else "❌"
        score_bar = "█" * int(r.score * 10) + "░" * (10 - int(r.score * 10))
        lines.append(f"  {i+1}. {icon} {r.entry.project}")
        lines.append(f"     匹配度：{score_bar} {r.score:.0%}")
        if r.match_reasons:
            lines.append(f"     原因：{', '.join(r.match_reasons)}")
        lines.append(f"     策略：{r.entry.strategy}  OS：{r.entry.os_type}"
                    f"  GPU：{r.entry.gpu_type or 'none'}")
        if not r.entry.success and r.entry.error_type:
            lines.append(f"     ❌ 错误：{r.entry.error_type}")
            if r.entry.fix_applied:
                lines.append(f"     💡 修复：{r.entry.fix_applied}")
        lines.append("")

    return "\n".join(lines)


def format_kb_stats(stats: dict) -> str:
    """格式化知识库统计"""
    rate = stats.get("success_rate", 0)
    bar = "█" * int(rate * 20) + "░" * (20 - int(rate * 20))
    lines = [
        "📚 安装知识库統计：",
        f"  总记录：{stats.get('total_entries', 0)}  "
        f"项目数：{stats.get('unique_projects', 0)}",
        f"  成功率：{bar} {rate:.1%}",
        f"  成功 {stats.get('success_count', 0)} / "
        f"失败 {stats.get('failure_count', 0)}",
    ]
    top = stats.get("top_strategies", {})
    if top:
        lines.append(f"  热门策略：{', '.join(f'{k}({v})' for k, v in top.items())}")
    return "\n".join(lines)
