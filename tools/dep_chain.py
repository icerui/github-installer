"""
dep_chain.py - 依赖链可视化 + Lineage 追踪
=============================================

灵感来源：ICE-cluade-SCompany 的 Task 依赖链 + Failure Lineage

功能：
  1. 构建安装步骤的依赖 DAG（有向无环图）
  2. 检测循环依赖
  3. 失败 Lineage 追踪：安装失败时追溯根因
  4. ASCII 可视化依赖树

例：
  安装 ComfyUI 需要：git → python venv → pip install → pytorch
  如果 pip install 失败 → Lineage 追踪：缺少 gcc → 建议 xcode-select --install

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DepNode:
    """依赖节点"""
    id: str                          # 唯一标识
    name: str                        # 显示名
    node_type: str = "step"          # step, tool, package, system
    status: str = "pending"          # pending, running, completed, failed
    depends_on: list[str] = field(default_factory=list)  # 依赖的节点 ID
    error: str = ""
    duration_sec: float = 0.0


@dataclass
class FailureLineage:
    """失败血统链"""
    root_cause: str               # 根因描述
    chain: list[str] = field(default_factory=list)  # 因果链: [root, ..., leaf]
    suggestion: str = ""          # 修复建议
    confidence: float = 0.0       # 置信度 0-1


@dataclass
class DepChain:
    """依赖链（DAG）"""
    nodes: dict[str, DepNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (from, to)

    def add_node(self, node: DepNode):
        self.nodes[node.id] = node

    def add_edge(self, from_id: str, to_id: str):
        if from_id in self.nodes and to_id in self.nodes:
            self.edges.append((from_id, to_id))
            self.nodes[to_id].depends_on.append(from_id)

    def get_dependents(self, node_id: str) -> list[str]:
        """获取依赖于 node_id 的所有节点"""
        return [to_id for from_id, to_id in self.edges if from_id == node_id]

    def get_dependencies(self, node_id: str) -> list[str]:
        """获取 node_id 依赖的所有节点"""
        return [from_id for from_id, to_id in self.edges if to_id == node_id]

    def topological_sort(self) -> list[str]:
        """拓扑排序（检测循环依赖）"""
        in_degree = defaultdict(int)
        for node_id in self.nodes:
            in_degree[node_id] = 0
        for _, to_id in self.edges:
            in_degree[to_id] += 1

        queue = [n for n in self.nodes if in_degree[n] == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dep in self.get_dependents(node):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        if len(result) != len(self.nodes):
            # 存在循环依赖
            missing = set(self.nodes.keys()) - set(result)
            return result  # 返回能排序的部分
        return result

    def has_cycle(self) -> bool:
        """检测是否有循环依赖"""
        sorted_nodes = self.topological_sort()
        return len(sorted_nodes) < len(self.nodes)

    def to_dict(self) -> dict:
        return {
            "nodes": {k: {
                "id": v.id, "name": v.name, "type": v.node_type,
                "status": v.status, "depends_on": v.depends_on,
                "error": v.error,
            } for k, v in self.nodes.items()},
            "edges": self.edges,
            "has_cycle": self.has_cycle(),
        }


# ─────────────────────────────────────────────
#  从安装计划构建依赖链
# ─────────────────────────────────────────────

# 工具依赖映射：命令 → 需要的系统工具
_TOOL_DEPS = {
    "git clone": ["git"],
    "git pull": ["git"],
    "pip install": ["python", "pip"],
    "pip3 install": ["python3", "pip3"],
    "python -m venv": ["python"],
    "python3 -m venv": ["python3"],
    "npm install": ["node", "npm"],
    "npm ci": ["node", "npm"],
    "npx": ["node", "npx"],
    "cargo build": ["rustc", "cargo"],
    "go build": ["go"],
    "go mod": ["go"],
    "docker": ["docker"],
    "docker compose": ["docker", "docker-compose"],
    "conda": ["conda"],
    "brew": ["brew"],
    "cmake": ["cmake"],
    "make": ["make"],
    "gcc": ["gcc"],
    "g++": ["g++"],
    "curl": ["curl"],
    "wget": ["wget"],
}

# 常见失败根因映射
_FAILURE_PATTERNS = {
    r"command not found: gcc|No such file.*gcc|gcc.*not found": {
        "root_cause": "缺少 C/C++ 编译器 (gcc)",
        "suggestion_darwin": "xcode-select --install",
        "suggestion_linux": "sudo apt-get install build-essential",
        "suggestion": "安装 C/C++ 编译器",
    },
    r"command not found: cmake|cmake.*not found": {
        "root_cause": "缺少 CMake",
        "suggestion_darwin": "brew install cmake",
        "suggestion_linux": "sudo apt-get install cmake",
        "suggestion": "安装 CMake",
    },
    r"No module named|ModuleNotFoundError": {
        "root_cause": "Python 模块缺失",
        "suggestion": "确认 pip install 步骤已成功，或检查 Python 环境",
    },
    r"EACCES|Permission denied": {
        "root_cause": "权限不足",
        "suggestion": "检查目录权限，或使用 --dir 指定安装目录",
    },
    r"Could not resolve host|Network is unreachable|ConnectionError": {
        "root_cause": "网络连接失败",
        "suggestion": "检查网络连接，或设置代理 (HTTPS_PROXY)",
    },
    r"No space left|ENOSPC|disk full": {
        "root_cause": "磁盘空间不足",
        "suggestion": "释放磁盘空间后重试",
    },
    r"CUDA|nvidia|GPU.*not found|torch.*cuda": {
        "root_cause": "GPU/CUDA 驱动问题",
        "suggestion": "检查 NVIDIA 驱动或使用 CPU 模式安装",
    },
    r"SSL.*certificate|CERTIFICATE_VERIFY_FAILED": {
        "root_cause": "SSL 证书验证失败",
        "suggestion_darwin": "安装 certifi: pip install certifi",
        "suggestion": "更新 CA 证书或检查网络代理",
    },
    r"fatal: repository.*not found|404.*Not Found": {
        "root_cause": "仓库不存在或无权限",
        "suggestion": "检查仓库地址是否正确，私有仓库需要 GITHUB_TOKEN",
    },
}


def build_chain_from_plan(plan: dict,
                          env: dict = None) -> DepChain:
    """从安装计划构建依赖链"""
    chain = DepChain()
    steps = plan.get("steps", [])
    env = env or {}

    # 添加步骤节点
    prev_id = None
    for i, step in enumerate(steps):
        node_id = f"step_{i}"
        node = DepNode(
            id=node_id,
            name=step.get("description", f"Step {i+1}"),
            node_type="step",
        )
        chain.add_node(node)

        # 检测该步骤需要的系统工具
        cmd = step.get("command", "")
        for pattern, tools in _TOOL_DEPS.items():
            if pattern in cmd.lower():
                for tool in tools:
                    tool_id = f"tool_{tool}"
                    if tool_id not in chain.nodes:
                        chain.add_node(DepNode(
                            id=tool_id, name=tool,
                            node_type="tool",
                            status="completed",  # 假设工具已有
                        ))
                    chain.add_edge(tool_id, node_id)

        # 步骤间的顺序依赖
        if prev_id:
            chain.add_edge(prev_id, node_id)
        prev_id = node_id

    return chain


def trace_failure_lineage(chain: DepChain, failed_step: int,
                          error_output: str = "",
                          os_type: str = "") -> list[FailureLineage]:
    """追溯失败的血统链"""
    lineages = []

    # 1. 基于错误输出模式匹配
    for pattern, info in _FAILURE_PATTERNS.items():
        if re.search(pattern, error_output, re.IGNORECASE):
            suggestion = info.get(f"suggestion_{os_type}", info["suggestion"])
            lineage = FailureLineage(
                root_cause=info["root_cause"],
                chain=[info["root_cause"], f"Step {failed_step + 1} 失败"],
                suggestion=suggestion,
                confidence=0.8,
            )
            lineages.append(lineage)

    # 2. 基于依赖链追溯
    step_id = f"step_{failed_step}"
    deps = chain.get_dependencies(step_id)
    for dep_id in deps:
        dep_node = chain.nodes.get(dep_id)
        if dep_node and dep_node.status == "failed":
            lineage = FailureLineage(
                root_cause=f"前置依赖 '{dep_node.name}' 失败",
                chain=[dep_node.name, chain.nodes[step_id].name],
                suggestion=f"先修复 '{dep_node.name}' 的问题",
                confidence=0.9,
            )
            lineages.append(lineage)

    # 3. 如果没有匹配到，给通用建议
    if not lineages:
        lineages.append(FailureLineage(
            root_cause="未知原因",
            chain=[f"Step {failed_step + 1} 失败"],
            suggestion="查看完整错误日志或使用 --llm 模式让 AI 分析",
            confidence=0.3,
        ))

    return sorted(lineages, key=lambda l: l.confidence, reverse=True)


# ─────────────────────────────────────────────
#  可视化
# ─────────────────────────────────────────────

def format_dep_chain(chain: DepChain) -> str:
    """ASCII 可视化依赖链"""
    lines = ["🔗 安装依赖链：", ""]

    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
    }

    # 先显示工具节点
    tools = [n for n in chain.nodes.values() if n.node_type == "tool"]
    if tools:
        lines.append("  系统工具：")
        for t in tools:
            icon = status_icons.get(t.status, "?")
            lines.append(f"    {icon} {t.name}")
        lines.append("    │")
        lines.append("    ▼")

    # 显示步骤节点
    steps = sorted(
        [n for n in chain.nodes.values() if n.node_type == "step"],
        key=lambda n: n.id,
    )
    for i, step in enumerate(steps):
        icon = status_icons.get(step.status, "?")
        err = f" → {step.error[:60]}" if step.error else ""
        dur = f" ({step.duration_sec:.1f}s)" if step.duration_sec > 0 else ""
        lines.append(f"  {icon} {step.name}{dur}{err}")
        if i < len(steps) - 1:
            lines.append("    │")
            lines.append("    ▼")

    # 循环依赖警告
    if chain.has_cycle():
        lines.append("")
        lines.append("  ⚠️  警告：检测到循环依赖！")

    return "\n".join(lines)


def format_lineage(lineages: list[FailureLineage]) -> str:
    """格式化失败血统链"""
    if not lineages:
        return "  无法追溯失败原因"

    lines = ["🔍 失败根因分析：", ""]
    for i, l in enumerate(lineages[:3]):  # 最多显示 3 条
        conf = f"[置信度 {l.confidence:.0%}]"
        lines.append(f"  {i+1}. {l.root_cause} {conf}")
        if len(l.chain) > 1:
            chain_str = " → ".join(l.chain)
            lines.append(f"     链路：{chain_str}")
        if l.suggestion:
            lines.append(f"     💡 建议：{l.suggestion}")
        lines.append("")

    return "\n".join(lines)
