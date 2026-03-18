# gitinstall

<p align="center">
  <strong>MCP server & CLI — helps you easily install GitHub projects</strong><br/>
  <em>让你轻松安装 GitHub 项目</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT License"></a>
  <a href="https://pypi.org/project/gitinstall"><img src="https://img.shields.io/pypi/v/gitinstall?style=for-the-badge" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/MCP-2024--11--05-blueviolet?style=for-the-badge" alt="MCP Protocol">
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen?style=for-the-badge" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-green?style=for-the-badge" alt="Platforms">
</p>

<p align="center">
  <b>Zero external dependencies</b> · Pure Python stdlib · Works without any LLM
</p>

---

## The Problem

AI agents can write code, explain errors, and suggest commands — but they **can't actually install software** on your machine. When you say "help me set up ComfyUI", the AI gives you a wall of commands and hopes for the best.

**gitinstall** bridges this gap. It's an MCP server that gives any AI agent (Claude, Cursor, Copilot, etc.) the ability to:

1. **Detect** your system (OS, GPU, runtimes, package managers)
2. **Plan** the installation with intelligent multi-strategy planning
3. **Execute** safely with dangerous command blocking
4. **Auto-fix** errors (PEP 668, CUDA mismatch, missing tools, and more)
5. **Retry** with fallback strategies if the first approach fails

---

## Quick Start

### For Claude Desktop

```bash
pip install gitinstall
```

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gitinstall": {
      "command": "gitinstall-mcp"
    }
  }
}
```

Restart Claude Desktop. Now you can say:

> "Help me install ComfyUI on this machine"

Claude will call gitinstall's tools to detect your system, generate a plan, and execute it — with real-time progress updates and automatic error recovery.

### For Cursor

Add to Cursor's MCP settings:

```json
{
  "mcpServers": {
    "gitinstall": {
      "command": "gitinstall-mcp"
    }
  }
}
```

### For VS Code (Copilot)

Add to your VS Code `settings.json`:

```json
{
  "mcp": {
    "servers": {
      "gitinstall": {
        "command": "gitinstall-mcp"
      }
    }
  }
}
```

### For any MCP client

```bash
gitinstall-mcp    # Starts MCP server on stdio (JSON-RPC 2.0)
```

---

## MCP Tools

gitinstall exposes 7 tools via MCP:

| Tool | What it does |
|------|-------------|
| `install_github_project` | End-to-end installation: detect → plan → execute → auto-fix → fallback retry |
| `diagnose_install_error` | Diagnose any build/install error and return fix commands |
| `detect_environment` | Report OS, CPU, GPU, runtimes, package managers, disk space |
| `get_project_info` | Fetch GitHub project metadata without installing |
| `check_system_health` | Full system diagnostic with fix suggestions |
| `audit_dependencies` | Scan project dependencies for known CVEs |
| `uninstall_github_project` | Safely uninstall a project: removes files, venvs, Docker, caches |

### Example: `install_github_project`

The AI calls:
```json
{
  "name": "install_github_project",
  "arguments": {
    "project": "comfyanonymous/ComfyUI"
  }
}
```

gitinstall handles everything:
- Detects macOS Apple Silicon M3 + Python 3.12 + brew
- Generates 3-step plan (clone → install PyTorch MPS → install deps)
- Executes each step with safety checks
- If `pip install` fails with PEP 668, auto-fixes with `python -m venv`
- Returns success status + launch command

### Example: `diagnose_install_error`

Pass any build error to the AI, and gitinstall will identify the root cause and return fix commands automatically. Supports PEP 668, CUDA version mismatches, missing system libraries, and many more common patterns.

---

## What makes it different

### No LLM required

The core engine is a **rule-based system**, not an AI wrapper:
- Extensive library of known projects with hand-crafted installation recipes
- Wide language coverage with intelligent type detection
- Comprehensive error-fix rules covering real-world installation failures
- LLM is optional — only used when the rule engine isn't confident

### Zero dependencies

```bash
$ pip show gitinstall | grep Requires
Requires:
```

Pure Python standard library. No `requests`, no `click`, no `rich`. One `pip install` and it works. No dependency conflicts, ever.

### Safety built-in

- **Dangerous command patterns blocked** (including encoding bypass attempts)
- **SSRF protection** on URL fetching
- **No auto-sudo** — prompts explicitly when root needed
- **Smart failure detection** — stops retrying when the error is unfixable

---

## Also works as a CLI

gitinstall works standalone too, no AI agent needed:

```bash
gitinstall comfyanonymous/ComfyUI     # Install a project
gitinstall uninstall comfyanonymous/ComfyUI --confirm  # Safely uninstall
gitinstall detect                      # Check your system
gitinstall doctor                      # System diagnostic
gitinstall audit pytorch/pytorch       # Security audit
gitinstall plan neovim/neovim          # Preview install plan
```

---

## Supported Languages & Platforms

**Languages**: Python, Node.js, TypeScript, Rust, Go, C, C++, Java, Kotlin, Scala, Swift, Ruby, PHP, Haskell, Zig, Elixir, Erlang, and many more

**Platforms**: macOS (Intel + Apple Silicon), Ubuntu/Debian, Fedora/RHEL, Arch Linux, Windows 10/11, WSL2

**GPU**: NVIDIA CUDA, AMD ROCm, Apple MPS, CPU fallback

---

## How It Works

```
User → AI Agent → MCP → gitinstall
                         ├── detect   → OS / GPU / runtimes / package managers
                         ├── plan     → intelligent multi-strategy planner
                         ├── execute  → safe subprocess execution
                         ├── fix      → error pattern matching → auto-retry
                         └── fallback → alternative install strategies
```

The planner uses a multi-tier strategy that combines project-specific knowledge, language-aware templates, and README analysis to generate optimal installation plans for a wide range of projects.

---

## Development

```bash
git clone https://github.com/icerui/github-installer.git
cd github-installer

# Run tests
python -m pytest tests/unit/ -q

# Run locally
python tools/mcp_server.py  # MCP server on stdio
python tools/main.py detect # CLI mode
```

---

## License

MIT © icerui
