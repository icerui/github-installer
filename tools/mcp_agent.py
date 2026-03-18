"""
mcp_agent.py — MCP 生态 + AI Agent 增强引擎
=============================================

目标市场：MCP 生态 + AI Agent（新兴，★★★★☆）

功能：
  1. Agent-Friendly API — 面向 AI Agent 的高层 API
  2. 多步计划执行（Multi-step Plan Executor）
  3. 会话上下文管理（Conversation Context）
  4. Tool Schema 生成（OpenAI / Anthropic / MCP 格式）
  5. Agent 协作协议（Agent-to-Agent）
  6. 流式结果（Streaming Results）
  7. Agent Memory — 安装经验记忆
  8. 自然语言意图解析（Intent Parser）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, Optional


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class AgentAction:
    """Agent 可执行的原子操作"""
    action_id: str = ""
    name: str = ""              # detect | fetch | plan | install | audit | doctor | ...
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    status: str = "pending"     # pending | running | success | failed | skipped
    error: str = ""
    duration_ms: int = 0


@dataclass
class AgentPlan:
    """Agent 执行计划 — 多步骤"""
    plan_id: str = ""
    intent: str = ""            # 用户原始意图
    actions: list[AgentAction] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    status: str = "pending"     # pending | executing | completed | failed


@dataclass
class AgentSession:
    """Agent 会话 — 跨多轮对话保持上下文"""
    session_id: str = ""
    created_at: str = ""
    last_active: str = ""
    history: list[dict] = field(default_factory=list)  # 对话历史
    environment: dict[str, Any] = field(default_factory=dict)  # 缓存的环境信息
    installed: list[str] = field(default_factory=list)  # 已安装的项目
    preferences: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMemory:
    """Agent 经验记忆"""
    memory_id: str = ""
    project: str = ""
    outcome: str = ""           # success | failed
    environment_hash: str = ""
    steps_taken: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    resolution: str = ""
    timestamp: str = ""


# ─────────────────────────────────────────────
#  自然语言意图解析
# ─────────────────────────────────────────────

@dataclass
class ParsedIntent:
    """解析后的用户意图"""
    action: str = ""            # install | info | audit | diagnose | recommend | compare | ...
    targets: list[str] = field(default_factory=list)  # 目标项目/模型
    constraints: dict[str, Any] = field(default_factory=dict)  # 约束条件
    confidence: float = 0.0
    raw_input: str = ""


# 意图模式库 — 覆盖中英文
_INTENT_PATTERNS: list[tuple[str, str, float]] = [
    # (正则, action, 基础置信度)
    (r'(?:install|安装|装|setup|部署|deploy)\s+(.+)', "install", 0.9),
    (r'(?:uninstall|卸载|删除|remove)\s+(.+)', "uninstall", 0.9),
    (r'(?:audit|审计|检查安全|scan)\s+(.+)', "audit", 0.85),
    (r'(?:diagnose|诊断|排错|debug|fix)\s*(.+)?', "diagnose", 0.8),
    (r'(?:detect|检测环境|环境|system|系统)', "detect", 0.85),
    (r'(?:doctor|体检|health)', "doctor", 0.85),
    (r'(?:plan|计划|规划)\s+(.+)', "plan", 0.8),
    (r'(?:fetch|获取|info|信息)\s+(.+)', "fetch", 0.8),
    (r'(?:recommend|推荐|建议)\s*(.+)?', "recommend", 0.75),
    (r'(?:compare|对比|比较)\s+(.+)\s+(?:and|和|vs|与)\s+(.+)', "compare", 0.85),
    (r'(?:update|更新|升级)\s*(.+)?', "update", 0.8),
    (r'(?:license|许可证|协议)\s+(.+)', "license", 0.85),
    (r'(?:sbom|物料清单)\s+(.+)', "sbom", 0.85),
    (r'(?:paper|论文|arxiv)\s+(.+)', "paper", 0.85),
    (r'(?:learn|学习|教程|tutorial)\s*(.+)?', "learn", 0.75),
    (r'(?:vram|显存|内存|memory)\s+(.+)', "vram", 0.8),
    (r'(?:gpu|显卡)\s*(.+)?', "gpu_info", 0.8),
    (r'(?:classroom|课堂|教室)\s*(.+)?', "classroom", 0.8),
    (r'(?:badge|徽章|按钮)\s+(.+)', "badge", 0.8),
    (r'(?:ci|cicd|pipeline)\s+(.+)', "cicd", 0.8),
]


def parse_intent(text: str) -> ParsedIntent:
    """
    解析自然语言意图。

    支持：
      - "安装 pytorch/pytorch"
      - "install langchain with GPU support"
      - "这个项目安全吗 huggingface/transformers"
      - "推荐一个 NLP 框架"
      - "对比 vllm 和 tgi"

    Returns:
        ParsedIntent
    """
    text = text.strip()
    if not text:
        return ParsedIntent(raw_input=text)

    best_match = None
    best_confidence = 0.0

    for pattern, action, base_conf in _INTENT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            # 提取目标
            targets = []
            for g in m.groups():
                if g:
                    targets.extend(_extract_targets(g.strip()))

            # 提取约束
            constraints = _extract_constraints(text)

            conf = base_conf
            if targets:
                conf += 0.05

            if conf > best_confidence:
                best_confidence = conf
                best_match = ParsedIntent(
                    action=action,
                    targets=targets,
                    constraints=constraints,
                    confidence=conf,
                    raw_input=text,
                )

    if best_match:
        return best_match

    # 如果没有明确意图，尝试识别项目名
    targets = _extract_targets(text)
    if targets:
        return ParsedIntent(
            action="install",
            targets=targets,
            confidence=0.5,
            raw_input=text,
        )

    return ParsedIntent(action="unknown", raw_input=text, confidence=0.0)


def _extract_targets(text: str) -> list[str]:
    """提取目标项目/模型名"""
    targets = []

    # GitHub owner/repo 格式
    for m in re.finditer(r'(?:https?://github\.com/)?([\w.-]+/[\w.-]+)', text):
        targets.append(m.group(1))

    # 如果没有 owner/repo，尝试识别知名项目
    if not targets:
        known_aliases = {
            "pytorch": "pytorch/pytorch",
            "tensorflow": "tensorflow/tensorflow",
            "react": "facebook/react",
            "vue": "vuejs/core",
            "langchain": "langchain-ai/langchain",
            "transformers": "huggingface/transformers",
            "vllm": "vllm-project/vllm",
            "llama.cpp": "ggml-org/llama.cpp",
            "llamacpp": "ggml-org/llama.cpp",
            "whisper": "openai/whisper",
            "yolo": "ultralytics/ultralytics",
            "fastapi": "fastapi/fastapi",
            "flask": "pallets/flask",
            "django": "django/django",
            "next.js": "vercel/next.js",
            "nextjs": "vercel/next.js",
            "deno": "denoland/deno",
            "rust": "rust-lang/rust",
            "go": "golang/go",
            "node": "nodejs/node",
            "numpy": "numpy/numpy",
            "pandas": "pandas-dev/pandas",
            "scikit-learn": "scikit-learn/scikit-learn",
            "sklearn": "scikit-learn/scikit-learn",
            "stable-diffusion": "CompVis/stable-diffusion",
            "ollama": "ollama/ollama",
            "comfyui": "comfyanonymous/ComfyUI",
        }
        text_lower = text.lower()
        for alias, repo in known_aliases.items():
            if alias in text_lower:
                targets.append(repo)
                break

    return targets


def _extract_constraints(text: str) -> dict[str, Any]:
    """提取约束条件"""
    constraints = {}
    text_lower = text.lower()

    if any(w in text_lower for w in ("gpu", "cuda", "显卡")):
        constraints["gpu"] = True
    if any(w in text_lower for w in ("cpu only", "无gpu", "无显卡", "no gpu")):
        constraints["gpu"] = False
    if any(w in text_lower for w in ("docker", "容器")):
        constraints["docker"] = True
    if any(w in text_lower for w in ("本地", "local", "离线", "offline")):
        constraints["local"] = True
    if any(w in text_lower for w in ("安全", "secure", "安全模式")):
        constraints["security_first"] = True

    # VRAM 约束
    m = re.search(r'(\d+)\s*(?:gb|g)\s*(?:vram|显存|内存)', text_lower)
    if m:
        constraints["vram_gb"] = int(m.group(1))

    return constraints


# ─────────────────────────────────────────────
#  Agent 执行器
# ─────────────────────────────────────────────

class AgentExecutor:
    """
    AI Agent 高层执行引擎。

    将自然语言意图转化为结构化的多步骤执行计划，
    并执行每一步，支持流式输出和错误恢复。
    """

    def __init__(self, session: AgentSession | None = None):
        if session:
            self.session = session
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            sid = hashlib.sha256(now.encode()).hexdigest()[:12]
            self.session = AgentSession(
                session_id=sid,
                created_at=now,
                last_active=now,
            )

    def process(self, user_input: str) -> AgentPlan:
        """
        处理用户输入，生成并执行计划。

        这是 Agent 的主入口。传入自然语言，返回执行结果。
        """
        intent = parse_intent(user_input)
        plan = self._create_plan(intent)
        self._execute_plan(plan)

        # 记录到会话历史
        self.session.history.append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        self.session.history.append({
            "role": "assistant",
            "plan_id": plan.plan_id,
            "status": plan.status,
            "actions": len(plan.actions),
        })
        self.session.last_active = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return plan

    def _create_plan(self, intent: ParsedIntent) -> AgentPlan:
        """从意图创建执行计划"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        plan_id = hashlib.sha256(f"{now}-{intent.action}".encode()).hexdigest()[:10]

        plan = AgentPlan(
            plan_id=plan_id,
            intent=intent.raw_input,
            created_at=now,
            context={"parsed_intent": intent.action, "targets": intent.targets},
        )

        actions = _INTENT_TO_ACTIONS.get(intent.action, _default_actions)
        plan.actions = actions(intent, self.session)
        return plan

    def _execute_plan(self, plan: AgentPlan) -> None:
        """执行计划中的每一步"""
        plan.status = "executing"

        for action in plan.actions:
            if action.status == "skipped":
                continue

            action.status = "running"
            start = time.monotonic()

            try:
                result = self._execute_action(action)
                action.result = result
                action.status = "success"

                # 缓存环境信息
                if action.name == "detect":
                    self.session.environment = result
                elif action.name == "install" and result.get("success"):
                    target = action.params.get("identifier", "")
                    if target and target not in self.session.installed:
                        self.session.installed.append(target)

            except Exception as e:
                action.status = "failed"
                action.error = str(e)

            action.duration_ms = int((time.monotonic() - start) * 1000)

        # 整体状态
        if all(a.status in ("success", "skipped") for a in plan.actions):
            plan.status = "completed"
        elif any(a.status == "failed" for a in plan.actions):
            plan.status = "failed"
        else:
            plan.status = "completed"

    def _execute_action(self, action: AgentAction) -> dict:
        """执行单个 action"""
        from _sdk import detect, plan, install, diagnose, fetch, doctor, audit

        name = action.name
        params = action.params

        if name == "detect":
            return detect()
        elif name == "fetch":
            return fetch(params.get("identifier", ""))
        elif name == "plan":
            return plan(params.get("identifier", ""))
        elif name == "install":
            return install(
                params.get("identifier", ""),
                install_dir=params.get("install_dir"),
            )
        elif name == "audit":
            return audit(params.get("identifier", ""))
        elif name == "doctor":
            return doctor()
        elif name == "diagnose":
            return diagnose(params.get("identifier", ""))
        elif name == "noop":
            return {"message": params.get("message", "OK")}
        else:
            return {"error": f"Unknown action: {name}"}

    def stream_process(self, user_input: str) -> Generator[dict, None, None]:
        """
        流式处理 — 适合长时间任务的实时反馈。

        Yields:
            {"type": "intent", "data": {...}}
            {"type": "action_start", "data": {...}}
            {"type": "action_complete", "data": {...}}
            {"type": "plan_complete", "data": {...}}
        """
        intent = parse_intent(user_input)
        yield {"type": "intent", "data": {
            "action": intent.action,
            "targets": intent.targets,
            "confidence": intent.confidence,
        }}

        plan = self._create_plan(intent)
        plan.status = "executing"

        for action in plan.actions:
            if action.status == "skipped":
                continue

            yield {"type": "action_start", "data": {
                "action_id": action.action_id,
                "name": action.name,
                "params": action.params,
            }}

            action.status = "running"
            start = time.monotonic()

            try:
                result = self._execute_action(action)
                action.result = result
                action.status = "success"
            except Exception as e:
                action.status = "failed"
                action.error = str(e)

            action.duration_ms = int((time.monotonic() - start) * 1000)

            yield {"type": "action_complete", "data": {
                "action_id": action.action_id,
                "name": action.name,
                "status": action.status,
                "duration_ms": action.duration_ms,
                "error": action.error,
            }}

        plan.status = "completed" if all(
            a.status in ("success", "skipped") for a in plan.actions
        ) else "failed"

        yield {"type": "plan_complete", "data": {
            "plan_id": plan.plan_id,
            "status": plan.status,
            "total_actions": len(plan.actions),
        }}


# ─────────────────────────────────────────────
#  意图→Actions 映射
# ─────────────────────────────────────────────

def _make_action(name: str, params: dict | None = None, aid: str = "") -> AgentAction:
    """创建 action"""
    return AgentAction(
        action_id=aid or hashlib.sha256(f"{name}-{time.time()}".encode()).hexdigest()[:8],
        name=name,
        params=params or {},
    )


def _install_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    """安装流程: detect → plan → install"""
    actions = []

    # 如果会话中没有缓存环境信息，先检测
    if not session.environment:
        actions.append(_make_action("detect"))

    for target in intent.targets:
        actions.append(_make_action("plan", {"identifier": target}))
        actions.append(_make_action("install", {"identifier": target}))

    return actions or [_make_action("noop", {"message": "请指定要安装的项目"})]


def _audit_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    """审计流程"""
    actions = []
    for target in intent.targets:
        actions.append(_make_action("audit", {"identifier": target}))
    return actions or [_make_action("noop", {"message": "请指定要审计的项目"})]


def _diagnose_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    """诊断流程: doctor + diagnose"""
    actions = [_make_action("doctor")]
    for target in intent.targets:
        actions.append(_make_action("diagnose", {"identifier": target}))
    return actions


def _detect_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    return [_make_action("detect")]


def _doctor_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    return [_make_action("doctor")]


def _fetch_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    actions = []
    for target in intent.targets:
        actions.append(_make_action("fetch", {"identifier": target}))
    return actions or [_make_action("noop", {"message": "请指定项目"})]


def _plan_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    actions = []
    if not session.environment:
        actions.append(_make_action("detect"))
    for target in intent.targets:
        actions.append(_make_action("plan", {"identifier": target}))
    return actions or [_make_action("noop", {"message": "请指定项目"})]


def _default_actions(intent: ParsedIntent, session: AgentSession) -> list[AgentAction]:
    return [_make_action("noop", {"message": f"未识别的操作: {intent.action}"})]


_INTENT_TO_ACTIONS: dict[str, Callable] = {
    "install": _install_actions,
    "uninstall": _default_actions,  # TODO: 对接 uninstaller.py
    "audit": _audit_actions,
    "diagnose": _diagnose_actions,
    "detect": _detect_actions,
    "doctor": _doctor_actions,
    "plan": _plan_actions,
    "fetch": _fetch_actions,
    "recommend": _default_actions,
    "compare": _default_actions,
    "update": _default_actions,
    "license": _audit_actions,
    "sbom": _audit_actions,
}


# ─────────────────────────────────────────────
#  Tool Schema 生成（OpenAI / Anthropic / MCP）
# ─────────────────────────────────────────────

def generate_tool_schemas(format: str = "openai") -> list[dict]:
    """
    生成适配不同 AI 平台的 tool schema。

    Args:
        format: "openai" | "anthropic" | "mcp"

    Returns:
        Tool schema 列表
    """
    base_tools = [
        {
            "name": "gitinstall_detect",
            "description": "检测当前系统环境（OS、CPU、GPU、运行时、包管理器）",
            "parameters": {},
        },
        {
            "name": "gitinstall_install",
            "description": "轻松安装 GitHub 项目。支持 owner/repo 格式或完整 URL",
            "parameters": {
                "identifier": {"type": "string", "description": "GitHub 项目标识（owner/repo 或 URL）", "required": True},
                "install_dir": {"type": "string", "description": "安装目录（可选）"},
            },
        },
        {
            "name": "gitinstall_plan",
            "description": "为 GitHub 项目生成安装计划，不执行安装",
            "parameters": {
                "identifier": {"type": "string", "description": "GitHub 项目标识", "required": True},
            },
        },
        {
            "name": "gitinstall_audit",
            "description": "安全审计 — 检查依赖漏洞、许可证风险、typosquatting",
            "parameters": {
                "identifier": {"type": "string", "description": "GitHub 项目标识", "required": True},
                "online": {"type": "boolean", "description": "是否查询在线 CVE 数据库"},
            },
        },
        {
            "name": "gitinstall_doctor",
            "description": "系统健康检查 — Python、Git、包管理器、GPU、磁盘空间",
            "parameters": {},
        },
        {
            "name": "gitinstall_fetch",
            "description": "获取 GitHub 项目元数据（README、依赖、项目类型）",
            "parameters": {
                "identifier": {"type": "string", "description": "GitHub 项目标识", "required": True},
            },
        },
        {
            "name": "gitinstall_vram",
            "description": "评估 AI 模型的 VRAM 需求和最佳量化方案",
            "parameters": {
                "model_id": {"type": "string", "description": "HuggingFace 模型 ID", "required": True},
                "vram_gb": {"type": "number", "description": "可用 VRAM (GB)"},
            },
        },
        {
            "name": "gitinstall_paper",
            "description": "从 arXiv 论文 ID 查找代码仓库并安装",
            "parameters": {
                "paper_id": {"type": "string", "description": "arXiv ID (如 2301.13688)", "required": True},
            },
        },
        {
            "name": "gitinstall_classroom",
            "description": "创建课堂环境 — 批量配置学生开发环境",
            "parameters": {
                "name": {"type": "string", "description": "课堂名称", "required": True},
                "projects": {"type": "array", "items": {"type": "string"}, "description": "项目列表", "required": True},
            },
        },
        {
            "name": "gitinstall_natural",
            "description": "自然语言安装 — 用中文或英文描述你想做什么，自动理解执行",
            "parameters": {
                "text": {"type": "string", "description": "自然语言描述", "required": True},
            },
        },
    ]

    if format == "openai":
        return _to_openai_format(base_tools)
    elif format == "anthropic":
        return _to_anthropic_format(base_tools)
    elif format == "mcp":
        return _to_mcp_format(base_tools)
    return base_tools


def _to_openai_format(tools: list[dict]) -> list[dict]:
    """转换为 OpenAI function calling 格式"""
    result = []
    for t in tools:
        props = {}
        required = []
        for pname, pinfo in t.get("parameters", {}).items():
            props[pname] = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("items"):
                props[pname]["items"] = pinfo["items"]
            if pinfo.get("required"):
                required.append(pname)

        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return result


def _to_anthropic_format(tools: list[dict]) -> list[dict]:
    """转换为 Anthropic tool_use 格式"""
    result = []
    for t in tools:
        props = {}
        required = []
        for pname, pinfo in t.get("parameters", {}).items():
            props[pname] = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required"):
                required.append(pname)

        result.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    return result


def _to_mcp_format(tools: list[dict]) -> list[dict]:
    """转换为 MCP 协议格式"""
    result = []
    for t in tools:
        props = {}
        required = []
        for pname, pinfo in t.get("parameters", {}).items():
            props[pname] = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required"):
                required.append(pname)

        result.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    return result


# ─────────────────────────────────────────────
#  Agent 经验记忆
# ─────────────────────────────────────────────

_MEMORY_DIR = os.path.expanduser("~/.gitinstall/agent_memory")


def remember_outcome(
    project: str,
    outcome: str,
    steps: list[str] | None = None,
    errors: list[str] | None = None,
    resolution: str = "",
    env_info: dict | None = None,
) -> AgentMemory:
    """记录安装结果到经验库"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    env_hash = hashlib.sha256(
        json.dumps(env_info or {}, sort_keys=True).encode()
    ).hexdigest()[:12]

    mem = AgentMemory(
        memory_id=hashlib.sha256(f"{project}-{now}".encode()).hexdigest()[:10],
        project=project,
        outcome=outcome,
        environment_hash=env_hash,
        steps_taken=steps or [],
        errors_encountered=errors or [],
        resolution=resolution,
        timestamp=now,
    )

    os.makedirs(_MEMORY_DIR, exist_ok=True)
    path = os.path.join(_MEMORY_DIR, f"{mem.memory_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "memory_id": mem.memory_id,
            "project": mem.project,
            "outcome": mem.outcome,
            "environment_hash": mem.environment_hash,
            "steps_taken": mem.steps_taken,
            "errors_encountered": mem.errors_encountered,
            "resolution": mem.resolution,
            "timestamp": mem.timestamp,
        }, f, indent=2, ensure_ascii=False)

    return mem


def recall_experience(project: str) -> list[AgentMemory]:
    """查找某项目的安装经验"""
    if not os.path.isdir(_MEMORY_DIR):
        return []

    results = []
    for fname in os.listdir(_MEMORY_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_MEMORY_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("project", "") == project:
                results.append(AgentMemory(**data))
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    results.sort(key=lambda x: x.timestamp, reverse=True)
    return results


# ─────────────────────────────────────────────
#  Agent-to-Agent 协作协议
# ─────────────────────────────────────────────

def create_agent_handoff(
    from_agent: str,
    to_agent: str,
    task: str,
    context: dict[str, Any] | None = None,
) -> dict:
    """
    创建 Agent 间的任务交接消息。

    用于多 Agent 编排场景（如 AutoGen、CrewAI）。
    """
    return {
        "protocol": "gitinstall-agent-handoff/1.0",
        "from": from_agent,
        "to": to_agent,
        "task": task,
        "context": context or {},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capabilities": [
            "detect", "fetch", "plan", "install", "audit",
            "doctor", "vram_estimate", "paper_install", "classroom",
        ],
    }


def format_plan_result(plan: AgentPlan) -> str:
    """格式化计划执行结果"""
    status_icons = {"completed": "✅", "failed": "❌", "pending": "⏳", "executing": "🔄"}

    lines = [
        f"{status_icons.get(plan.status, '?')} 执行计划 [{plan.plan_id}]",
        f"   意图: {plan.intent}",
        f"   状态: {plan.status}",
        "",
    ]

    for a in plan.actions:
        icon = {"success": "✅", "failed": "❌", "skipped": "⏭️", "pending": "○", "running": "🔄"}.get(a.status, "?")
        duration = f" ({a.duration_ms}ms)" if a.duration_ms else ""
        lines.append(f"   {icon} {a.name}{duration}")
        if a.error:
            lines.append(f"      ⚠️ {a.error}")

    return "\n".join(lines)
