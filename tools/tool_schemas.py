"""
gitinstall Tool Schemas — 让任意 AI 模型调用安装引擎
====================================================

提供多种格式的工具定义，使任何支持 Function Calling / Tool Use 的模型
（无论是 OpenAI、自训练模型、垂直模型、还是本地 Ollama 模型）
都能调用 gitinstall 的能力。

用法::

    # 获取 OpenAI 格式 (也兼容 Ollama / vLLM / LM Studio / 任意 OpenAI 兼容 API)
    from gitinstall.tool_schemas import openai_tools

    # 获取原始 JSON Schema 格式 (框架无关)
    from gitinstall.tool_schemas import json_schemas

    # 获取全部工具名列表
    from gitinstall.tool_schemas import tool_names

    # 执行工具调用（通用分发器）
    from gitinstall.tool_schemas import call_tool
    result = call_tool("detect", {})
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ── 原始工具定义（格式无关的真相源）──────────────

_TOOLS = [
    {
        "name": "detect",
        "description": (
            "Detect the current system environment. Returns OS type, CPU architecture, "
            "GPU (CUDA/ROCm/MPS/CPU), installed runtimes (Python, Node.js, Docker, Rust, Go, FFmpeg), "
            "package managers (pip, conda, brew, apt, npm, cargo), disk space, and network connectivity."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "plan",
        "description": (
            "Generate a step-by-step installation plan for a GitHub project. "
            "Analyzes the project's tech stack, dependencies, and the current environment "
            "to produce an optimal sequence of shell commands. Returns steps with commands, "
            "launch command, confidence level (high/medium/low), and strategy used. "
            "The plan is NOT executed — use the 'install' tool to execute it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project identifier: 'owner/repo' or full URL",
                },
                "llm": {
                    "type": "string",
                    "description": "LLM provider for analysis. Auto-selects if omitted.",
                    "enum": ["anthropic", "openai", "openrouter", "gemini", "groq",
                             "deepseek", "lmstudio", "ollama", "none"],
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "install",
        "description": (
            "Execute the installation of a GitHub project on this system. "
            "Runs shell commands with safety filtering (blocks dangerous commands), "
            "automatic error recovery (25+ patterns), and multi-strategy fallback. "
            "Returns success status, install directory, and launch command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' or full URL",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Target directory for installation.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, show plan only without executing.",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "diagnose",
        "description": (
            "Diagnose a software installation error and suggest fixes. "
            "Covers 25+ error patterns: dependency conflicts, permission issues, "
            "missing tools, version mismatches, PEP 668, CUDA/GPU issues, and more."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stderr": {
                    "type": "string",
                    "description": "Error output text to diagnose",
                },
                "command": {
                    "type": "string",
                    "description": "The command that produced the error.",
                },
                "stdout": {
                    "type": "string",
                    "description": "Standard output text.",
                },
            },
            "required": ["stderr"],
        },
    },
    {
        "name": "fetch",
        "description": (
            "Fetch metadata about a GitHub project: name, description, stars, "
            "language, license, project type, clone URL, dependency files, README preview."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' or full URL",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "doctor",
        "description": (
            "Run a comprehensive system diagnostic. Checks tool installations, "
            "runtime versions, configuration issues, and system health."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "audit",
        "description": (
            "Audit a GitHub project's dependencies for security vulnerabilities (CVEs)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' or full URL",
                },
                "online": {
                    "type": "boolean",
                    "description": "Query online CVE databases for thorough results.",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "uninstall",
        "description": (
            "Safely uninstall a previously installed GitHub project. "
            "Removes project directory, virtualenvs, Docker containers, build artifacts, caches. "
            "Shows cleanup plan before executing. Call with confirm=true to actually delete."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' format",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Directory where the project is installed.",
                },
                "keep_config": {
                    "type": "boolean",
                    "description": "Keep config files during uninstall.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "If true, execute uninstall. If false, only show plan.",
                },
            },
            "required": ["project"],
        },
    },
]

# ── 工具名列表 ──────────────────────────────

tool_names: list[str] = [t["name"] for t in _TOOLS]


# ── OpenAI Function Calling 格式 ────────────
# 兼容: OpenAI / Ollama / vLLM / LM Studio / Azure OpenAI / Groq /
#        DeepSeek / Together AI / Fireworks / Mistral / 任何 OpenAI 兼容 API

openai_tools: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        },
    }
    for t in _TOOLS
]


# ── Anthropic Tool Use 格式 ─────────────────
# 兼容: Claude API / AWS Bedrock Claude

anthropic_tools: list[dict] = [
    {
        "name": t["name"],
        "description": t["description"],
        "input_schema": t["parameters"],
    }
    for t in _TOOLS
]


# ── Google Gemini 格式 ──────────────────────
# 兼容: Gemini API / Vertex AI

gemini_tools: list[dict] = [
    {
        "function_declarations": [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
            for t in _TOOLS
        ]
    }
]


# ── 纯 JSON Schema 格式（框架无关）────────────
# 任何框架/模型都可以消费

json_schemas: list[dict] = [
    {
        "name": t["name"],
        "description": t["description"],
        "parameters": t["parameters"],
    }
    for t in _TOOLS
]


# ── 通用工具调用分发器 ──────────────────────

def call_tool(name: str, arguments: dict) -> dict | None:
    """
    执行 gitinstall 工具调用。

    所有 AI 框架集成都可以用这一个函数来分发工具调用：

        result = call_tool("detect", {})
        result = call_tool("plan", {"project": "comfyanonymous/ComfyUI"})
        result = call_tool("diagnose", {"stderr": "error: ..."})

    Args:
        name: 工具名 (detect/plan/install/diagnose/fetch/doctor/audit)
        arguments: 工具参数 dict

    Returns:
        工具执行结果 dict，或 None（diagnose 无匹配时）
    """
    from _sdk import detect, plan, install, diagnose, fetch, doctor, audit, uninstall

    if name == "detect":
        return detect()

    if name == "plan":
        return plan(
            arguments["project"],
            llm=arguments.get("llm"),
        )

    if name == "install":
        return install(
            arguments["project"],
            install_dir=arguments.get("install_dir"),
            dry_run=arguments.get("dry_run", False),
        )

    if name == "diagnose":
        return diagnose(
            arguments["stderr"],
            command=arguments.get("command", ""),
            stdout=arguments.get("stdout", ""),
        )

    if name == "fetch":
        return fetch(arguments["project"])

    if name == "doctor":
        return doctor()

    if name == "audit":
        return audit(
            arguments["project"],
            online=arguments.get("online", False),
        )

    if name == "uninstall":
        return uninstall(
            arguments["project"],
            install_dir=arguments.get("install_dir"),
            keep_config=arguments.get("keep_config", False),
            confirm=arguments.get("confirm", False),
        )

    raise ValueError(f"Unknown tool: {name}. Available: {tool_names}")


# ── 输出为 JSON ──────────────────────────────

def to_json(format: str = "openai") -> str:
    """
    输出指定格式的工具定义 JSON。

    Args:
        format: "openai" | "anthropic" | "gemini" | "json_schema"
    """
    schemas = {
        "openai": openai_tools,
        "anthropic": anthropic_tools,
        "gemini": gemini_tools,
        "json_schema": json_schemas,
    }
    data = schemas.get(format, openai_tools)
    return json.dumps(data, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    fmt = sys.argv[1] if len(sys.argv) > 1 else "openai"
    print(to_json(fmt))
