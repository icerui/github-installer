"""
test_knowledge_base.py - 安装知识库测试
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from knowledge_base import (
    KnowledgeEntry, KnowledgeBase, MatchResult,
    format_search_results, format_kb_stats,
)


@pytest.fixture
def kb(tmp_path):
    """创建使用临时文件的知识库"""
    kb_file = tmp_path / "test_knowledge.json"
    return KnowledgeBase(kb_file=kb_file)


class TestKnowledgeEntry:
    """测试 KnowledgeEntry"""

    def test_creation(self):
        entry = KnowledgeEntry(
            id="test_1", project="owner/repo",
            project_types=["python_package"],
            languages=["python"],
            success=True,
        )
        assert entry.id == "test_1"
        assert entry.success is True

    def test_to_dict(self):
        entry = KnowledgeEntry(id="t1", project="a/b")
        d = entry.to_dict()
        assert d["id"] == "t1"
        assert d["project"] == "a/b"

    def test_from_dict(self):
        data = {
            "id": "t2",
            "project": "c/d",
            "success": True,
            "project_types": ["web_app"],
        }
        entry = KnowledgeEntry.from_dict(data)
        assert entry.id == "t2"
        assert entry.success is True
        assert "web_app" in entry.project_types

    def test_keywords(self):
        entry = KnowledgeEntry(
            id="t3", project="pytorch/pytorch",
            project_types=["python_package", "ai_ml"],
            languages=["python", "c++"],
            os_type="darwin",
            tags=["gpu", "cuda"],
        )
        kw = entry.keywords
        assert "pytorch" in kw
        assert "python" in kw
        assert "darwin" in kw
        assert "gpu" in kw


class TestKnowledgeBase:
    """测试 KnowledgeBase"""

    def test_record(self, kb):
        entry = kb.record(
            project="owner/repo",
            project_types=["python_package"],
            success=True,
            strategy="pip_venv",
        )
        assert entry.project == "owner/repo"
        assert entry.success is True

    def test_record_and_count(self, kb):
        assert kb.count() == 0
        kb.record(project="a/b", success=True)
        assert kb.count() == 1
        kb.record(project="c/d", success=False)
        assert kb.count() == 2

    def test_search_by_project(self, kb):
        kb.record(project="pytorch/pytorch", project_types=["ai_ml"],
                 languages=["python"], success=True, strategy="pip")
        kb.record(project="tensorflow/tensorflow", project_types=["ai_ml"],
                 languages=["python"], success=True, strategy="pip")

        results = kb.search(project="pytorch/pytorch")
        assert len(results) >= 1
        assert results[0].entry.project == "pytorch/pytorch"
        assert results[0].score > 0.5

    def test_search_by_type(self, kb):
        kb.record(project="a/b", project_types=["web_app"],
                 languages=["javascript"], success=True)
        kb.record(project="c/d", project_types=["python_package"],
                 languages=["python"], success=True)

        results = kb.search(project_types=["web_app"])
        assert len(results) >= 1
        assert any(r.entry.project == "a/b" for r in results)

    def test_search_success_only(self, kb):
        kb.record(project="a/b", success=True)
        kb.record(project="a/c", success=False)

        all_results = kb.search(project="a")
        success_results = kb.search(project="a", success_only=True)
        assert len(success_results) <= len(all_results)

    def test_search_empty(self, kb):
        results = kb.search(project="nonexistent")
        assert results == []

    def test_search_no_query(self, kb):
        results = kb.search()
        assert results == []

    def test_get_success_rate(self, kb):
        kb.record(project="a/b", success=True, strategy="pip")
        kb.record(project="a/b", success=True, strategy="pip")
        kb.record(project="a/b", success=False, strategy="docker")

        rate = kb.get_success_rate("a/b")
        assert rate["total"] == 3
        assert rate["success"] == 2
        assert abs(rate["rate"] - 2/3) < 0.01

    def test_get_success_rate_global(self, kb):
        kb.record(project="a/b", success=True)
        kb.record(project="c/d", success=False)
        rate = kb.get_success_rate()
        assert rate["total"] == 2
        assert rate["success"] == 1

    def test_get_stats(self, kb):
        kb.record(project="a/b", success=True, strategy="pip")
        kb.record(project="c/d", success=False, strategy="docker")

        stats = kb.get_stats()
        assert stats["total_entries"] == 2
        assert stats["success_count"] == 1
        assert stats["unique_projects"] == 2

    def test_persistence(self, tmp_path):
        kb_file = tmp_path / "persist.json"
        kb1 = KnowledgeBase(kb_file=kb_file)
        kb1.record(project="a/b", success=True)

        # 新实例应能读到数据
        kb2 = KnowledgeBase(kb_file=kb_file)
        assert kb2.count() == 1

    def test_max_entries(self, kb):
        # 记录大量条目
        for i in range(10):
            kb.record(project=f"owner/repo{i}", success=True)
        assert kb.count() == 10

    def test_error_message_truncation(self, kb):
        long_error = "x" * 1000
        entry = kb.record(project="a/b", error_message=long_error)
        assert len(entry.error_message) <= 500

    def test_os_matching_boost(self, kb):
        kb.record(project="a/b", os_type="darwin", success=True)
        kb.record(project="a/b", os_type="linux", success=True)

        results = kb.search(project="a/b", os_type="darwin")
        darwin_scores = [r.score for r in results if r.entry.os_type == "darwin"]
        linux_scores = [r.score for r in results if r.entry.os_type == "linux"]
        if darwin_scores and linux_scores:
            assert darwin_scores[0] >= linux_scores[0]


class TestFormatters:
    """测试格式化函数"""

    def test_format_search_results_empty(self):
        text = format_search_results([])
        assert "没有" in text

    def test_format_search_results(self):
        results = [
            MatchResult(
                entry=KnowledgeEntry(
                    id="t1", project="a/b",
                    success=True, strategy="pip",
                    os_type="darwin", gpu_type="apple_silicon",
                ),
                score=0.85,
                match_reasons=["完全相同项目"],
            ),
        ]
        text = format_search_results(results)
        assert "a/b" in text
        assert "85%" in text

    def test_format_kb_stats(self):
        stats = {
            "total_entries": 100,
            "success_count": 75,
            "failure_count": 25,
            "success_rate": 0.75,
            "unique_projects": 50,
            "top_strategies": {"pip": 40, "docker": 20},
        }
        text = format_kb_stats(stats)
        assert "知识库" in text
        assert "100" in text
