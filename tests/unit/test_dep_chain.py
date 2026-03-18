"""
test_dep_chain.py - 依赖链 + Lineage 追踪测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from dep_chain import (
    DepNode, DepChain, FailureLineage,
    build_chain_from_plan, trace_failure_lineage,
    format_dep_chain, format_lineage,
)


class TestDepNode:
    """测试 DepNode"""

    def test_creation(self):
        node = DepNode(id="step_0", name="安装依赖")
        assert node.id == "step_0"
        assert node.status == "pending"
        assert node.node_type == "step"

    def test_with_error(self):
        node = DepNode(id="step_1", name="编译", status="failed", error="gcc not found")
        assert node.status == "failed"
        assert "gcc" in node.error


class TestDepChain:
    """测试 DepChain（DAG）"""

    def test_add_node(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        assert "a" in chain.nodes

    def test_add_edge(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_edge("a", "b")
        assert ("a", "b") in chain.edges
        assert "a" in chain.nodes["b"].depends_on

    def test_get_dependents(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_node(DepNode(id="c", name="C"))
        chain.add_edge("a", "b")
        chain.add_edge("a", "c")
        deps = chain.get_dependents("a")
        assert set(deps) == {"b", "c"}

    def test_get_dependencies(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_edge("a", "b")
        deps = chain.get_dependencies("b")
        assert "a" in deps

    def test_topological_sort_linear(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_node(DepNode(id="c", name="C"))
        chain.add_edge("a", "b")
        chain.add_edge("b", "c")
        order = chain.topological_sort()
        assert order == ["a", "b", "c"]

    def test_no_cycle(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_edge("a", "b")
        assert chain.has_cycle() is False

    def test_detect_cycle(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        chain.add_node(DepNode(id="b", name="B"))
        chain.add_edge("a", "b")
        chain.add_edge("b", "a")
        assert chain.has_cycle() is True

    def test_to_dict(self):
        chain = DepChain()
        chain.add_node(DepNode(id="a", name="A"))
        d = chain.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert "has_cycle" in d

    def test_empty_chain(self):
        chain = DepChain()
        assert chain.topological_sort() == []
        assert chain.has_cycle() is False


class TestBuildChainFromPlan:
    """测试从安装计划构建依赖链"""

    def test_basic_plan(self):
        plan = {
            "steps": [
                {"command": "git clone https://github.com/a/b", "description": "克隆仓库"},
                {"command": "pip install -r requirements.txt", "description": "安装依赖"},
                {"command": "python run.py", "description": "运行"},
            ]
        }
        chain = build_chain_from_plan(plan)
        # 3 个步骤节点
        step_nodes = [n for n in chain.nodes.values() if n.node_type == "step"]
        assert len(step_nodes) == 3
        # 应检测到 git 和 python/pip 工具节点
        tool_nodes = [n for n in chain.nodes.values() if n.node_type == "tool"]
        assert len(tool_nodes) > 0

    def test_empty_plan(self):
        chain = build_chain_from_plan({"steps": []})
        assert len(chain.nodes) == 0

    def test_npm_plan(self):
        plan = {
            "steps": [
                {"command": "npm install", "description": "安装依赖"},
            ]
        }
        chain = build_chain_from_plan(plan)
        tool_names = [n.name for n in chain.nodes.values() if n.node_type == "tool"]
        assert "node" in tool_names or "npm" in tool_names


class TestTraceFailureLineage:
    """测试失败 Lineage 追踪"""

    def test_gcc_not_found(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="compile"))
        lineages = trace_failure_lineage(
            chain, 0, error_output="error: command not found: gcc"
        )
        assert len(lineages) > 0
        assert "gcc" in lineages[0].root_cause.lower() or "编译器" in lineages[0].root_cause

    def test_network_error(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="fetch"))
        lineages = trace_failure_lineage(
            chain, 0, error_output="Could not resolve host: github.com"
        )
        assert len(lineages) > 0
        assert any("网络" in l.root_cause for l in lineages)

    def test_disk_full(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="install"))
        lineages = trace_failure_lineage(
            chain, 0, error_output="No space left on device"
        )
        assert len(lineages) > 0
        assert any("磁盘" in l.root_cause for l in lineages)

    def test_unknown_error(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="unknown"))
        lineages = trace_failure_lineage(
            chain, 0, error_output="something very unusual happened"
        )
        # 应返回至少 1 条通用建议
        assert len(lineages) >= 1
        assert lineages[0].confidence <= 0.5

    def test_dependency_failure_lineage(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="venv", status="failed"))
        chain.add_node(DepNode(id="step_1", name="pip install"))
        chain.add_edge("step_0", "step_1")
        lineages = trace_failure_lineage(chain, 1, error_output="")
        assert any("前置依赖" in l.root_cause for l in lineages)


class TestFormatters:
    """测试格式化函数"""

    def test_format_dep_chain(self):
        chain = DepChain()
        chain.add_node(DepNode(id="step_0", name="克隆", status="completed"))
        chain.add_node(DepNode(id="step_1", name="安装", status="failed", error="err"))
        chain.add_edge("step_0", "step_1")
        text = format_dep_chain(chain)
        assert "依赖链" in text
        assert "克隆" in text
        assert "安装" in text

    def test_format_lineage(self):
        lineages = [
            FailureLineage(
                root_cause="缺少 gcc",
                chain=["gcc 缺失", "编译失败"],
                suggestion="安装 gcc",
                confidence=0.8,
            ),
        ]
        text = format_lineage(lineages)
        assert "gcc" in text
        assert "建议" in text

    def test_format_empty_lineage(self):
        text = format_lineage([])
        assert "无法追溯" in text
