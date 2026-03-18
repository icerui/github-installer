#!/usr/bin/env python3
"""gitinstall MCP Server — AI Agent 通过 MCP 协议调用安装引擎
============================================================

零外部依赖。通过 stdio JSON-RPC 2.0 与客户端通信。

启动::

    gitinstall mcp                       # CLI 子命令
    gitinstall-mcp                       # 独立入口（pip install 后可用）
    python -m gitinstall.mcp_server      # 模块运行

Claude Desktop 配置 (~/.claude/claude_desktop_config.json)::

    {
        "mcpServers": {
            "gitinstall": {
                "command": "gitinstall-mcp"
            }
        }
    }

协议: MCP (Model Context Protocol) 2024-11-05
传输: stdio, newline-delimited JSON
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# ── 确保 bare import 可用 ────────────────────────────────
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ── MCP 用 stdout 通信 — 日志全部走 stderr ────────────────
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [MCP] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gitinstall.mcp")

from _sdk import (
    detect as _detect,
    install as _install,
    diagnose as _diagnose,
    fetch as _fetch,
    doctor as _doctor,
    audit as _audit,
    uninstall as _uninstall,
    __version__,
)

PROTOCOL_VERSION = "2024-11-05"

# ── Tool Definitions ─────────────────────────────────────

TOOLS = [
    {
        "name": "install_github_project",
        "description": (
            "Help install a GitHub open-source project on the user's machine. "
            "Handles the ENTIRE process: detects OS/GPU/runtimes → fetches project info → "
            "generates installation plan (30+ languages: Python, Node, Rust, Go, C++, Haskell, Zig...) → "
            "executes safely (dangerous commands blocked) → auto-fixes errors (28 patterns: "
            "missing tools, permission denied, PEP 668, CUDA mismatch, etc.) → retries with "
            "fallback strategies if needed. Works WITHOUT any LLM by default (pure rule engine). "
            "Supports: GitHub URLs, 'owner/repo' format, GitLab/Gitee/Bitbucket URLs. "
            "Example: install_github_project({\"project\": \"comfyanonymous/ComfyUI\"}) "
            "Call this when a user asks you to install, set up, or run a GitHub project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' or full URL (e.g. 'pytorch/pytorch' or 'https://github.com/user/repo')",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Target directory. Default: ~/github-installs/<project>/",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, show the plan without executing. Default: false.",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "diagnose_install_error",
        "description": (
            "Diagnose a software installation or build error and return actionable fix commands. "
            "Covers 28 error patterns including: missing CLI tools (with cross-platform install mapping "
            "for 40+ tools), pip permission/PEP 668/package name typos, npm EACCES/workspace protocol, "
            "Python/Node version mismatches, Rust/Go/CMake missing build deps, Haskell toolchain issues "
            "(ghcup/cabal/stack), Zig SDK compatibility, port conflicts, disk space, git submodule failures. "
            "Returns root cause analysis and ready-to-run fix commands. "
            "Call this when a user shows you an error from pip/npm/cargo/go/cmake/make or any build tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stderr": {
                    "type": "string",
                    "description": "The error output text to diagnose",
                },
                "command": {
                    "type": "string",
                    "description": "The command that produced the error (e.g. 'pip install torch')",
                },
                "stdout": {
                    "type": "string",
                    "description": "Standard output text, if available",
                },
            },
            "required": ["stderr"],
        },
    },
    {
        "name": "detect_environment",
        "description": (
            "Detect the user's development environment. Returns: OS (macOS/Linux/Windows/WSL2), "
            "CPU (x86_64/arm64, Apple Silicon M1-M4), GPU (CUDA version/ROCm/Apple MPS/CPU-only), "
            "RAM and disk space, installed runtimes with versions (Python, Node, Go, Rust, Java, Docker, "
            "Git, FFmpeg...), available package managers (pip, conda, brew, apt, npm, cargo...). "
            "Call this to understand what the user's system can do before suggesting installations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_project_info",
        "description": (
            "Fetch metadata about a GitHub project WITHOUT installing it. "
            "Returns: project name, description, star count, primary language, license, "
            "detected project type (e.g. 'python-ml', 'node-webapp', 'rust-cli'), "
            "clone URL, homepage, dependency files list, and README preview. "
            "Call this to learn about a project before deciding whether to install it."
        ),
        "inputSchema": {
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
        "name": "check_system_health",
        "description": (
            "Run a comprehensive system diagnostic. Checks all development tools, "
            "runtime versions, common configuration issues, PATH problems, and system health. "
            "Returns a checklist with OK/Warning/Error status and specific fix commands. "
            "Call this when installation keeps failing or the user reports environment issues."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "audit_dependencies",
        "description": (
            "Audit a GitHub project's dependencies for known security vulnerabilities (CVEs). "
            "Scans requirements.txt, package.json, Cargo.toml, go.mod etc. "
            "Call this before installing untrusted projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' or full URL",
                },
                "online": {
                    "type": "boolean",
                    "description": "Query online CVE databases for thorough results. Default: false.",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "uninstall_github_project",
        "description": (
            "Safely uninstall a previously installed GitHub project. "
            "Detects and removes: project directory, virtualenvs, Docker containers, "
            "build artifacts, caches. Safety checks prevent deleting system directories. "
            "Shows a cleanup plan before executing. Optionally keeps config files. "
            "Example: uninstall_github_project({\"project\": \"comfyanonymous/ComfyUI\"}) "
            "Call this when a user wants to remove, delete, or uninstall a GitHub project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "GitHub project: 'owner/repo' format",
                },
                "install_dir": {
                    "type": "string",
                    "description": "Directory where the project is installed. Default: ~/github-installs/<project>/",
                },
                "keep_config": {
                    "type": "boolean",
                    "description": "Keep config files (.env, etc.) during uninstall. Default: false.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "If true, execute the uninstall. If false, only show plan. Default: false.",
                },
            },
            "required": ["project"],
        },
    },
]


# ── I/O ──────────────────────────────────────────────────

def _send(msg: dict) -> None:
    """Write one JSON-RPC message to stdout (newline-delimited)."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _notify(level: str, data: str) -> None:
    """Send a log notification to the MCP client."""
    _send({
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": level, "logger": "gitinstall", "data": data},
    })


# ── Progress → MCP Notifications ─────────────────────────

def _progress_to_notification(event: dict) -> None:
    """Convert install on_progress events to MCP log notifications."""
    t = event.get("type", "")
    if t == "detecting":
        _notify("info", "Detecting system environment...")
    elif t == "plan_ready":
        n = len(event.get("steps", []))
        _notify("info", f"Plan ready — {n} steps, confidence={event.get('confidence', '?')}")
    elif t == "step_start":
        _notify("info", f"[{event['step']}/{event['total']}] {event.get('description', event.get('command', ''))}")
    elif t == "step_done":
        _notify("info", f"  ✓ Step {event['step']} done ({event.get('duration', 0):.1f}s)")
    elif t == "step_failed":
        _notify("warning", f"  ✗ Step {event['step']} failed: {event.get('error', '')[:200]}")
    elif t == "step_fixed":
        _notify("info", f"  ↻ Step {event['step']} auto-fixed")
    elif t == "fallback_start":
        _notify("info", f"Trying fallback strategy: {event.get('strategy', '?')}")
    elif t == "install_done":
        status = "succeeded" if event.get("success") else "failed"
        _notify("info", f"Install {status} ({event.get('steps_completed', 0)}/{event.get('steps_total', 0)} steps)")


# ── Tool Execution ───────────────────────────────────────

def _call_tool(name: str, args: dict) -> tuple[list[dict], bool]:
    """Execute a tool. Returns (content_list, is_error)."""
    try:
        if name == "detect_environment":
            result = _detect()

        elif name == "install_github_project":
            result = _install(
                args["project"],
                install_dir=args.get("install_dir"),
                dry_run=args.get("dry_run", False),
                on_progress=_progress_to_notification,
            )

        elif name == "diagnose_install_error":
            result = _diagnose(
                args["stderr"],
                command=args.get("command", ""),
                stdout=args.get("stdout", ""),
            )
            if result is None:
                return [{"type": "text", "text": "No matching error pattern found. The error may be project-specific."}], False

        elif name == "get_project_info":
            result = _fetch(args["project"])

        elif name == "check_system_health":
            result = _doctor()

        elif name == "audit_dependencies":
            result = _audit(
                args["project"],
                online=args.get("online", False),
            )

        elif name == "uninstall_github_project":
            result = _uninstall(
                args["project"],
                install_dir=args.get("install_dir"),
                keep_config=args.get("keep_config", False),
                confirm=args.get("confirm", False),
            )

        else:
            return [{"type": "text", "text": f"Unknown tool: {name}"}], True

        text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        return [{"type": "text", "text": text}], False

    except Exception as e:
        logger.exception(f"Tool '{name}' raised an exception")
        return [{"type": "text", "text": f"Error executing {name}: {e}"}], True


# ── JSON-RPC Dispatch ────────────────────────────────────

def _handle(message: dict) -> dict | None:
    """Process one JSON-RPC message. Returns response dict, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params", {})

    # ── Lifecycle ──

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "logging": {}},
                "serverInfo": {"name": "gitinstall", "version": __version__},
            },
        }

    if method == "notifications/initialized":
        logger.info("Client initialized")
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    # ── Tools ──

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        logger.info(f"→ {name}({json.dumps(arguments, ensure_ascii=False)[:200]})")

        content, is_error = _call_tool(name, arguments)

        result = {"content": content}
        if is_error:
            result["isError"] = True
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # ── Resources / Prompts (empty but respond correctly) ──

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}

    # ── Unknown method ──

    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return None


# ── Main Loop ────────────────────────────────────────────

def serve() -> None:
    """Read JSON-RPC messages from stdin, write responses to stdout."""
    logger.info(f"gitinstall MCP server v{__version__} (protocol {PROTOCOL_VERSION})")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            _send({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            })
            continue

        response = _handle(message)
        if response is not None:
            _send(response)

    logger.info("stdin closed — shutting down")


if __name__ == "__main__":
    serve()
