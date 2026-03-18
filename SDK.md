# gitinstall SDK 嵌入指南

> 本文件随开源发布，帮助开发者将 gitinstall 引擎嵌入到自己的项目中。

---

## 快速开始

```bash
pip install gitinstall
```

```python
import gitinstall

# 检测当前系统环境
env = gitinstall.detect()
print(env["os"]["type"])      # "macos"
print(env["gpu"]["type"])     # "apple_mps"

# 生成安装方案
plan = gitinstall.plan("comfyanonymous/ComfyUI", env=env)
print(plan["confidence"])     # "high"

# 执行安装
result = gitinstall.install(plan)
print(result["success"])      # True
print(result["install_dir"])  # "/Users/x/ComfyUI"
```

---

## 核心 API

### `gitinstall.detect() → dict`

检测当前系统环境。返回 OS、GPU、已安装的运行时和包管理器等信息。

```python
env = gitinstall.detect()

# 返回结构（关键字段）：
{
    "os": {"type": "macos", "version": "15.7", "arch": "arm64"},
    "hardware": {"cpu_count": 32, "ram_gb": 512.0},
    "gpu": {"type": "apple_mps", "name": "Apple Neural Engine + GPU"},
    "runtimes": {"python": {"available": True, "version": "3.13.2"}, ...},
    "package_managers": {"brew": {"available": True, "version": "5.0"}, ...},
    "disk": {"free_gb": 1200, "total_gb": 3000},
    "network": {"github": True, "pypi": True}
}
```

### `gitinstall.plan(identifier, *, env=None, llm=None, local=False) → dict`

为指定项目生成安装方案。

```python
plan = gitinstall.plan("pytorch/pytorch")

# 参数：
#   identifier — "owner/repo" 或 GitHub URL
#   env        — detect() 的返回值（可选，省略则自动检测）
#   llm        — 指定 LLM："ollama"/"openai"/"none"（可选，自动选择）
#   local      — True 时用 git clone 分析，避免 API 限额

# 返回结构：
{
    "status": "ok",
    "plan": {
        "steps": [
            {"command": "git clone ...", "description": "克隆仓库"},
            {"command": "pip install ...", "description": "安装依赖"}
        ],
        "launch_command": "python main.py",
        "confidence": "high",       # high/medium/low
        "strategy": "known_project" # 使用了哪种策略
    },
    "project": "pytorch/pytorch",
    "confidence": "high"
}
```

### `gitinstall.install(plan_or_identifier, *, ...) → dict`

执行安装。接受 plan() 返回的 dict 或直接传项目标识。

```python
# 方式 1：直接传标识符（自动 plan + install）
result = gitinstall.install("comfyanonymous/ComfyUI")

# 方式 2：先 plan 再 install（可以在中间检查/修改方案）
plan = gitinstall.plan("comfyanonymous/ComfyUI")
# ... 检查 plan["steps"] 是否合理 ...
result = gitinstall.install(plan)

# 方式 3：指定安装目录
result = gitinstall.install("comfyanonymous/ComfyUI", install_dir="~/AI")

# 方式 4：只看计划不执行
result = gitinstall.install("comfyanonymous/ComfyUI", dry_run=True)

# 返回结构：
{
    "status": "ok",
    "success": True,
    "project": "comfyanonymous/ComfyUI",
    "install_dir": "/Users/x/ComfyUI",
    "launch_command": "python main.py --listen",
    "steps_completed": 3,
    "steps_total": 3,
    "plan_strategy": "known_project"
}
```

### `gitinstall.diagnose(stderr, command="", stdout="") → dict | None`

诊断安装报错，返回修复建议。

```python
fix = gitinstall.diagnose(
    stderr="error: externally-managed-environment",
    command="pip install pandas"
)

if fix:
    print(fix["root_cause"])     # "PEP 668: 系统 Python 禁止直接 pip install"
    print(fix["fix_commands"])   # ["pip install --break-system-packages pandas"]
    print(fix["confidence"])     # "high"
```

---

## 进度回调

嵌入到 UI 应用时，用 `on_progress` 监控安装进度：

```python
def on_progress(event):
    t = event["type"]
    if t == "plan_ready":
        print(f"方案就绪，共 {len(event['steps'])} 步")
    elif t == "step_start":
        print(f"[{event['step']}/{event['total']}] {event['description']}")
    elif t == "step_done":
        print(f"  ✓ 完成 ({event['duration']:.1f}s)")
    elif t == "step_failed":
        print(f"  ✗ 失败: {event['error'][:100]}")
    elif t == "step_fixed":
        print(f"  ↻ 已自动修复")
    elif t == "install_done":
        print(f"{'成功' if event['success'] else '失败'}")

result = gitinstall.install("comfyanonymous/ComfyUI", on_progress=on_progress)
```

### 回调事件类型

| type | 触发时机 | 关键字段 |
|------|---------|---------|
| `detecting` | 开始检测环境 | message |
| `plan_ready` | 方案生成完毕 | steps, project, confidence |
| `preflight` | 预检发现缺失工具 | missing_tools, install_commands |
| `step_start` | 步骤开始执行 | step, total, command, description |
| `step_done` | 步骤执行成功 | step, total, duration |
| `step_failed` | 步骤执行失败 | step, total, error, command |
| `step_fixed` | 步骤报错已自动修复 | step, total, fix |
| `step_blocked` | 步骤被安全过滤拦截 | step, total, reason |
| `fallback_start` | 开始回退策略 | strategy, tier |
| `install_done` | 安装流程结束 | success, install_dir, steps_completed |

---

## 嵌入场景示例

### 1. AI Agent（MCP Server）

```python
# mcp_server.py — 让 Claude 能调用 gitinstall
import gitinstall

async def tool_detect(params):
    return gitinstall.detect()

async def tool_plan(params):
    return gitinstall.plan(params["project"])

async def tool_install(params):
    return gitinstall.install(params["project"])
```

### 2. IDE 插件

```python
# vscode_extension_backend.py
import gitinstall

def ensure_project_deps(project_path: str):
    """IDE 检测到项目缺少依赖时自动安装。"""
    env = gitinstall.detect()
    plan = gitinstall.plan(project_path, env=env, local=True)

    if plan["confidence"] == "high":
        result = gitinstall.install(plan, on_progress=update_status_bar)
        return result["success"]
    return False
```

### 3. CI/CD 流水线

```python
# ci_setup.py — GitHub Actions / GitLab CI 环境配置
import gitinstall

def setup_ci_environment(repos: list[str]):
    """批量安装 CI 依赖。"""
    env = gitinstall.detect()
    results = []

    for repo in repos:
        plan = gitinstall.plan(repo, env=env, llm="none")  # CI 不用 LLM
        result = gitinstall.install(plan)
        results.append({"repo": repo, "success": result["success"]})

    failed = [r for r in results if not r["success"]]
    if failed:
        raise RuntimeError(f"{len(failed)} 个项目安装失败")
```

### 4. 开源项目安装脚本

```python
# install_my_project.py — 替代 setup.sh
import gitinstall

# 检测环境，确保依赖就绪
env = gitinstall.detect()

if not env["runtimes"].get("python"):
    fix = gitinstall.diagnose(
        stderr="command not found: python3",
        command="python3 --version"
    )
    if fix:
        print(f"请先安装 Python：{fix['fix_commands'][0]}")

# 安装项目本身
result = gitinstall.install("my-org/my-project", env=env)
```

### 5. 教育平台

```python
# classroom.py — 一键配置学生编程环境
import gitinstall

COURSE_TOOLS = ["jupyter/notebook", "pandas-dev/pandas", "matplotlib/matplotlib"]

def setup_student_env(student_id: str):
    env = gitinstall.detect()

    for tool in COURSE_TOOLS:
        result = gitinstall.install(tool, on_progress=lambda e:
            send_ws_update(student_id, e)
        )
        if not result["success"]:
            alert_teacher(student_id, tool, result["error_summary"])
```

---

## 特性

| 特性 | 说明 |
|------|------|
| **零外部依赖** | 纯 Python 标准库，不会干扰宿主项目的依赖 |
| **跨平台** | macOS / Linux / Windows / WSL / ARM64 |
| **线程安全** | 所有核心函数可并发调用 |
| **AI 可选** | 有 LLM（9 种支持） 更智能，无 LLM 也能覆盖 90% 项目 |
| **离线可用** | 引擎本身不联网（安装过程需要网络下载） |
| **安全过滤** | 20+ 危险命令模式自动拦截 |
| **自动修复** | 25+ 常见安装报错自动诊断修复 |
| **进度回调** | on_progress 回调支持 UI 集成 |

---

## 本地 LLM 配置

gitinstall 内置 9 级 LLM 降级策略，自动选择最优可用模型。**无任何 LLM 也能运行**（规则模式覆盖 90% 项目），有 LLM 则更智能。

### 降级优先级

| 优先级 | 提供商 | 配置方式 | 说明 |
|--------|--------|----------|------|
| 1 | Anthropic Claude | `ANTHROPIC_API_KEY` | 质量最高 |
| 2 | OpenAI GPT-4o | `OPENAI_API_KEY` | |
| 3 | OpenRouter | `OPENROUTER_API_KEY` | 灵活切换模型 |
| 4 | Google Gemini | `GEMINI_API_KEY` | 免费额度大 |
| 5 | Groq | `GROQ_API_KEY` | 速度最快 |
| 6 | DeepSeek | `DEEPSEEK_API_KEY` | 性价比高 |
| 7 | **LM Studio** | 本地运行即可（localhost:1234） | **零配置** |
| 8 | **Ollama** | 本地运行即可（localhost:11434） | **零配置，推荐** |
| 9 | 无 LLM 规则模式 | 永远可用 | 60+ 已知项目精确匹配 |

### Ollama（推荐本地方案）

```bash
# 1. 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh    # Linux
brew install ollama                               # macOS

# 2. 拉取推荐的小模型（~1GB，普通笔记本即可）
ollama pull qwen2.5:1.5b

# 3. 启动 Ollama（通常安装后自动启动）
ollama serve

# 4. gitinstall 自动检测 — 无需任何配置
gitinstall plan comfyanonymous/ComfyUI
# → 自动使用 Ollama (qwen2.5:1.5b)
```

gitinstall 会自动检测 Ollama 已安装的模型，优先选取小模型：

| 模型 | 大小 | 说明 |
|------|------|------|
| `qwen2.5:1.5b` | ~1GB | **默认推荐**，中英双语 |
| `deepseek-r1:1.5b` | ~1GB | 有推理链 |
| `qwen2.5-coder:1.5b` | ~1GB | 代码理解强 |
| `qwen2.5:3b` | ~2GB | 质量更好，推荐有独显 |
| `llama3.2:3b` | ~2GB | Meta 官方 |

### LM Studio

```bash
# 1. 下载 LM Studio: https://lmstudio.ai
# 2. 在 LM Studio 中下载并加载任意模型
# 3. 打开 LM Studio 的 Local Server（默认端口 1234）
# 4. gitinstall 自动检测
gitinstall plan comfyanonymous/ComfyUI
# → 自动使用 LM Studio
```

### 在 SDK 中指定 LLM

```python
import gitinstall

# 自动选择（推荐）
plan = gitinstall.plan("owner/repo")

# 强制使用 Ollama
plan = gitinstall.plan("owner/repo", llm="ollama")

# 强制使用 LM Studio
plan = gitinstall.plan("owner/repo", llm="lmstudio")

# 强制不用 LLM（纯规则模式）
plan = gitinstall.plan("owner/repo", llm="none")

# 自定义模型名称
import os
os.environ["GITINSTALL_LLM_MODEL"] = "deepseek-r1:1.5b"
plan = gitinstall.plan("owner/repo", llm="ollama")
```

### 在 MCP 中指定 LLM

当 AI 调用 `plan` 工具时，可通过 `llm` 参数指定：

```json
{"name": "plan", "arguments": {"project": "comfyanonymous/ComfyUI", "llm": "ollama"}}
```

---

## MCP Server（AI Agent 集成）

gitinstall 内置 MCP (Model Context Protocol) 服务器，让 Claude Desktop、Cursor、VS Code Copilot、Windsurf、Cline 等 AI 工具直接调用安装引擎。

### 启动

```bash
gitinstall mcp    # 作为 MCP 子进程运行（stdio 模式）
```

### 可用工具

MCP 客户端连接后可使用 7 个工具：

| 工具 | 类型 | 用途 |
|------|------|------|
| `detect` | 只读 | 检测系统环境（OS/GPU/运行时/包管理器） |
| `plan` | 只读 | 为 GitHub 项目生成安装方案 |
| `install` | 执行 | 安装项目（带安全过滤 + 自动修复 + 进度通知） |
| `diagnose` | 只读 | 诊断安装报错并给出修复建议 |
| `fetch` | 只读 | 获取 GitHub 项目元数据 |
| `doctor` | 只读 | 系统健康检查 |
| `audit` | 只读 | 依赖安全审计 |

---

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）
或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
    "mcpServers": {
        "gitinstall": {
            "command": "gitinstall",
            "args": ["mcp"]
        }
    }
}
```

重启 Claude Desktop 即可。

---

### Cursor

`Settings → Features → MCP Servers → Add new MCP Server`：

- Name: `gitinstall`
- Type: `command`
- Command: `gitinstall mcp`

或编辑 `~/.cursor/mcp.json`：

```json
{
    "mcpServers": {
        "gitinstall": {
            "command": "gitinstall",
            "args": ["mcp"]
        }
    }
}
```

Cursor 的 Agent 模式下，输入 "安装 ComfyUI" 即会自动调用 gitinstall 工具。

---

### VS Code Copilot (GitHub Copilot)

在项目根目录创建 `.vscode/mcp.json`：

```json
{
    "servers": {
        "gitinstall": {
            "type": "stdio",
            "command": "gitinstall",
            "args": ["mcp"]
        }
    }
}
```

或在 VS Code `settings.json` 中全局配置：

```json
{
    "mcp": {
        "servers": {
            "gitinstall": {
                "type": "stdio",
                "command": "gitinstall",
                "args": ["mcp"]
            }
        }
    }
}
```

在 Copilot Chat 中使用 Agent 模式（`@workspace`），即可调用 gitinstall 工具。

---

### Windsurf (Codeium)

编辑 `~/.codeium/windsurf/mcp_config.json`：

```json
{
    "mcpServers": {
        "gitinstall": {
            "command": "gitinstall",
            "args": ["mcp"]
        }
    }
}
```

---

### Cline (VS Code 扩展)

`Cline Settings → MCP Servers → Configure`，添加：

```json
{
    "mcpServers": {
        "gitinstall": {
            "command": "gitinstall",
            "args": ["mcp"]
        }
    }
}
```

---

### Continue.dev

编辑 `~/.continue/config.yaml`：

```yaml
mcpServers:
  - name: gitinstall
    command: gitinstall
    args:
      - mcp
```

---

### 通用配置说明

**所有工具的配置本质相同** — 因为 gitinstall MCP 使用标准 stdio 传输：

```
command: gitinstall
args:    ["mcp"]
```

如果用 Python 绝对路径更可靠（避免 PATH 问题）：

```json
{
    "command": "python",
    "args": ["-m", "gitinstall.mcp_server"]
}
```

如果用 `uvx` 免安装运行：

```json
{
    "command": "uvx",
    "args": ["gitinstall", "mcp"]
}
```

### 对话示例

```
用户：帮我安装 ComfyUI

AI：  我来检查一下你的系统环境。
      [调用 detect] → macOS arm64, Apple MPS, Python 3.13
      
      让我为 ComfyUI 生成安装方案。
      [调用 plan("comfyanonymous/ComfyUI")] → 6 步, 置信度 high
      
      安装方案如下：
      1. git clone ComfyUI
      2. 创建虚拟环境
      3. 安装 PyTorch (MPS 版)
      4. 安装依赖
      5. 下载模型
      6. 启动
      
      确认执行吗？

用户：执行吧

AI：  [调用 install("comfyanonymous/ComfyUI")]
      ✓ 安装成功！启动命令: python main.py --listen
```

---

## 通用 AI 模型集成（Function Calling / Tool Use）

> **核心理念：不只是被几个大厂产品调用，而是被所有模型嵌入。**
>
> 未来每个企业、每个开发者都可能训练自己的模型（综合型或垂直型）。
> gitinstall 提供标准化的工具定义，让任何模型都能调用安装引擎的能力。

### 架构

```
┌─────────────────────────────────────────────────────────────┐
│                   任意 AI 模型 / Agent                        │
│                                                             │
│  OpenAI GPT · Claude · Gemini · Ollama · vLLM · LM Studio  │
│  自训练模型 · 垂直行业模型 · 企业私有模型 · 开源微调模型        │
└───────────────────────┬─────────────────────────────────────┘
                        │ Function Calling / Tool Use
                        ▼
┌───────────────────────────────────────────┐
│        gitinstall.call_tool(name, args)   │  ← 通用分发器
│                                           │
│  openai_tools     → OpenAI 格式 Schema    │
│  anthropic_tools  → Claude 格式 Schema    │
│  gemini_tools     → Gemini 格式 Schema    │
│  json_schemas     → 纯 JSON Schema       │
└───────────────────────┬───────────────────┘
                        │
                        ▼
              gitinstall 安装引擎
        detect / plan / install / diagnose
```

### 获取工具定义

```python
import gitinstall

# OpenAI 格式（兼容 Ollama / vLLM / LM Studio / Azure / Groq / DeepSeek / Together AI）
gitinstall.openai_tools

# Anthropic 格式（Claude API / AWS Bedrock）
gitinstall.anthropic_tools

# Google Gemini 格式（Gemini API / Vertex AI）
gitinstall.gemini_tools

# 纯 JSON Schema（任何框架）
gitinstall.json_schemas
```

或通过 CLI：

```bash
gitinstall schema --format openai      # 默认，兼容性最广
gitinstall schema --format anthropic
gitinstall schema --format gemini
gitinstall schema --format json_schema
```

### 通用工具调用

```python
import gitinstall

# 不管模型返回什么格式的 tool_call，最终都调用这个：
result = gitinstall.call_tool("detect", {})
result = gitinstall.call_tool("plan", {"project": "comfyanonymous/ComfyUI"})
result = gitinstall.call_tool("install", {"project": "comfyanonymous/ComfyUI"})
result = gitinstall.call_tool("diagnose", {"stderr": "No module named torch"})
```

---

### 示例 1：Ollama 本地模型 + Function Calling

```python
"""任何在 Ollama 上运行的模型，都可以调用 gitinstall。"""
import json, requests
import gitinstall

response = requests.post("http://localhost:11434/api/chat", json={
    "model": "qwen2.5:7b",  # 或你自训练的模型
    "messages": [
        {"role": "user", "content": "帮我安装 ComfyUI"}
    ],
    "tools": gitinstall.openai_tools,  # ← 传入工具定义
})

data = response.json()
if tool_calls := data["message"].get("tool_calls"):
    for call in tool_calls:
        name = call["function"]["name"]
        args = call["function"]["arguments"]
        result = gitinstall.call_tool(name, args)  # ← 执行
        print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 示例 2：OpenAI / Azure / DeepSeek / 任意 OpenAI 兼容 API

```python
"""所有 OpenAI 兼容 API 用同一套代码。"""
import json
from openai import OpenAI
import gitinstall

# 换 base_url 就能切换到任何 OpenAI 兼容服务
client = OpenAI(base_url="https://api.openai.com/v1")  # 或 DeepSeek / Azure / 自建

response = client.chat.completions.create(
    model="gpt-4o",  # 或 deepseek-chat / 你的自训练模型
    messages=[{"role": "user", "content": "帮我在这台机器上安装 Stable Diffusion WebUI"}],
    tools=gitinstall.openai_tools,
)

if response.choices[0].message.tool_calls:
    for call in response.choices[0].message.tool_calls:
        result = gitinstall.call_tool(
            call.function.name,
            json.loads(call.function.arguments),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 示例 3：Claude API

```python
import anthropic, json
import gitinstall

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    tools=gitinstall.anthropic_tools,  # ← Anthropic 格式
    messages=[{"role": "user", "content": "检查一下我的开发环境有什么问题"}],
)

for block in response.content:
    if block.type == "tool_use":
        result = gitinstall.call_tool(block.name, block.input)
        print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 示例 4：vLLM 自部署模型

```python
"""企业自训练的模型，通过 vLLM 部署后调用 gitinstall。"""
from openai import OpenAI
import gitinstall, json

# vLLM 提供 OpenAI 兼容 API
client = OpenAI(base_url="http://your-vllm-server:8000/v1", api_key="dummy")

response = client.chat.completions.create(
    model="your-finetuned-model",  # 你自训练的垂直模型
    messages=[{"role": "user", "content": "部署 FastAPI 项目到这台服务器"}],
    tools=gitinstall.openai_tools,
)

for call in (response.choices[0].message.tool_calls or []):
    result = gitinstall.call_tool(call.function.name, json.loads(call.function.arguments))
```

### 示例 5：LM Studio 本地模型

```python
"""LM Studio 桌面端本地模型调用 gitinstall。"""
from openai import OpenAI
import gitinstall, json

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

response = client.chat.completions.create(
    model="lmstudio-community/qwen2.5-7b",  # LM Studio 中加载的模型
    messages=[{"role": "user", "content": "安装 PyTorch 到这台机器"}],
    tools=gitinstall.openai_tools,
)

for call in (response.choices[0].message.tool_calls or []):
    result = gitinstall.call_tool(call.function.name, json.loads(call.function.arguments))
```

### 示例 6：Google Gemini

```python
import google.generativeai as genai
import gitinstall

model = genai.GenerativeModel("gemini-2.0-flash", tools=gitinstall.gemini_tools)
chat = model.start_chat()
response = chat.send_message("帮我检测系统环境")

for part in response.parts:
    if fn := part.function_call:
        result = gitinstall.call_tool(fn.name, dict(fn.args))
```

### 示例 7：自训练 / 垂直行业模型

```python
"""
场景：一家 DevOps 公司训练了自己的运维模型，
希望模型能帮用户自动安装开源软件。

只需要两步：
1. 训练时在 system prompt 告诉模型有哪些工具可用
2. 推理时用 gitinstall.call_tool 执行
"""
import gitinstall, json

# 1. 训练 / 微调时：把工具定义加入训练数据
SYSTEM_PROMPT = f"""你是一个 DevOps 助手。你有以下工具可以调用：

{json.dumps(gitinstall.json_schemas, indent=2)}

当用户需要安装软件时，调用对应工具。输出 JSON 格式的工具调用。
"""

# 2. 推理时：解析模型输出的工具调用
def handle_model_output(model_output: str):
    # 假设模型输出了 {"tool": "plan", "arguments": {"project": "..."}}
    call = json.loads(model_output)
    return gitinstall.call_tool(call["tool"], call["arguments"])
```

---

## 版本

```python
import gitinstall
print(gitinstall.__version__)  # "1.1.0"
```
