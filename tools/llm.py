"""
llm.py - 多 LLM 适配器
=====================================

15 级降级策略，永远不失败：
  1. Anthropic Claude     (ANTHROPIC_API_KEY)
  2. OpenAI GPT-4o        (OPENAI_API_KEY)
  3. OpenRouter           (OPENROUTER_API_KEY)
  4. Google Gemini        (GEMINI_API_KEY)
  5. Groq Llama 3.3       (GROQ_API_KEY)
  6. DeepSeek             (DEEPSEEK_API_KEY)
  7. 通义千问 Qwen        (DASHSCOPE_API_KEY)
  8. 智谱 GLM             (ZHIPU_API_KEY)
  9. 月之暗面 Kimi         (MOONSHOT_API_KEY)
  10. 百川智能             (BAICHUAN_API_KEY)
  11. 零一万物 Yi          (YI_API_KEY)
  12. 阶跃星辰 Step        (STEPFUN_API_KEY)
  13. LM Studio           (localhost:1234，本地运行即可)
  14. Ollama              (localhost:11434，本地运行即可)
  15. 无 LLM 规则模式      (永远可用，无需任何配置)

面向大众原则（最低配置要求）：
  - 本地模型推荐 1.5B~3B（普通笔记本即可运行）
  - 默认推荐：qwen2.5:1.5b（中英双语，~1GB 显存/内存）
  - 环境变量 GITINSTALL_LLM_MODEL 可自定义模型名称
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request

from log import get_logger
from i18n import t

logger = get_logger(__name__)
from abc import ABC, abstractmethod
from typing import Optional

# LLM 请求超时（秒）：超过此时间自动降级到规则模式
# 可通过环境变量 GITINSTALL_LLM_TIMEOUT 覆盖
LLM_TIMEOUT = int(os.getenv("GITINSTALL_LLM_TIMEOUT", "30"))


# ─────────────────────────────────────────────
#  抽象基类
# ─────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """所有 LLM Provider 的统一接口"""

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """发送对话请求，返回 AI 回复文本"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称，用于日志和用户提示"""

    def is_available(self) -> bool:
        """检查 Provider 是否可用（子类可覆写）"""
        return True


# ─────────────────────────────────────────────
#  通用 OpenAI 兼容 Provider
#  支持：OpenAI / OpenRouter / Groq / DeepSeek /
#         Gemini / LM Studio / Ollama / 任何 OpenAI 兼容 API
# ─────────────────────────────────────────────

class OpenAICompatibleProvider(BaseLLMProvider):
    """
    适配所有 OpenAI Chat Completions 兼容接口。
    只用 Python 标准库 urllib，无需安装任何第三方包。
    """

    def __init__(self, api_key: str, base_url: str, model: str, provider_name: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._name = provider_name

    @property
    def name(self) -> str:
        return self._name

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,   # 安装任务要确定性，不要随机性
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "gitinstall/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self._name} API 错误 {e.code}: {body[:300]}") from e
        except (socket.timeout, TimeoutError):
            raise RuntimeError(f"{self._name} 请求超时（{LLM_TIMEOUT}秒），自动降级到规则模式")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise RuntimeError(f"{self._name} 请求超时（{LLM_TIMEOUT}秒），自动降级到规则模式")
            raise RuntimeError(f"{self._name} 连接失败: {e.reason}") from e


# ─────────────────────────────────────────────
#  Anthropic 原生 Provider（非 OpenAI 兼容格式）
# ─────────────────────────────────────────────

class AnthropicProvider(BaseLLMProvider):
    """Anthropic Messages API 原生接口"""

    DEFAULT_MODEL = "claude-opus-4-5"

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL

    @property
    def name(self) -> str:
        return f"Anthropic {self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": "gitinstall/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["content"][0]["text"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API 错误 {e.code}: {body[:300]}") from e
        except (socket.timeout, TimeoutError):
            raise RuntimeError(f"Anthropic 请求超时（{LLM_TIMEOUT}秒），自动降级到规则模式")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise RuntimeError(f"Anthropic 请求超时（{LLM_TIMEOUT}秒），自动降级到规则模式")
            raise RuntimeError(f"Anthropic 连接失败: {e.reason}") from e


# ─────────────────────────────────────────────
#  无 LLM 规则模式（永远可用）
# ─────────────────────────────────────────────

class HeuristicProvider(BaseLLMProvider):
    """
    无需任何 API Key 或本地模型。
    通过正则表达式和规则库解析 README，提取安装命令。
    
    覆盖 90% 的主流开源项目（pip/npm/docker/brew/cargo 类型）。
    """

    @property
    def name(self) -> str:
        return "规则引擎（无 LLM）"

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """解析 prompt 中的 README 内容，提取安装步骤"""
        return self._extract_install_plan(user)

    def _extract_install_plan(self, content: str) -> str:
        """从 README 文本中提取安装命令"""
        steps = []

        # 提取所有代码块
        code_blocks = re.findall(
            r'```(?:bash|shell|sh|zsh|powershell|cmd|console|text)?\n(.*?)```',
            content,
            re.DOTALL | re.IGNORECASE,
        )

        # 按优先级排序的安装命令模式
        patterns = [
            # Git 克隆（几乎所有项目的第一步）
            (r'git\s+clone\s+(?:--depth[= ]\S+\s+)?(?:https?://|git@)\S+', "git_clone"),
            # Python 安装
            (r'pip(?:3)?\s+install[^\n]+', "pip"),
            (r'pip(?:3)?\s+install\s+-r\s+requirements[^\n]*\.txt', "pip_req"),
            (r'conda\s+(?:install|env\s+create)[^\n]+', "conda"),
            (r'python(?:3)?\s+setup\.py\s+install', "setup_py"),
            (r'python(?:3)?\s+-m\s+pip\s+install[^\n]+', "pip_m"),
            # Node.js
            (r'npm\s+install[^\n]*', "npm"),
            (r'pnpm\s+install[^\n]*', "pnpm"),
            (r'yarn(?:\s+install)?[^\n]*', "yarn"),
            # 系统包管理器
            (r'brew\s+install[^\n]+', "brew"),
            (r'apt(?:-get)?\s+install[^\n]+', "apt"),
            (r'dnf\s+install[^\n]+', "dnf"),
            (r'pacman\s+-S[^\n]+', "pacman"),
            (r'winget\s+install[^\n]+', "winget"),
            (r'choco\s+install[^\n]+', "choco"),
            # 其他语言
            (r'cargo\s+install[^\n]+', "cargo"),
            (r'go\s+install[^\n]+', "go"),
            # Docker
            (r'docker\s+(?:pull|run)[^\n]+', "docker"),
            (r'docker-compose\s+up[^\n]*', "docker_compose"),
            # 安装脚本
            (r'curl\s+[^\n]+\s*\|[^\n]+(?:bash|sh)', "curl_pipe"),
            (r'bash\s+(?:install|setup)\.sh[^\n]*', "bash_script"),
            (r'make(?:\s+install)?[^\n]*', "make"),
        ]

        seen = set()
        for block in code_blocks:
            for pattern, kind in patterns:
                for match in re.finditer(pattern, block, re.IGNORECASE):
                    cmd = match.group(0).strip()
                    # 安全过滤
                    if self._is_dangerous(cmd):
                        continue
                    if cmd not in seen:
                        seen.add(cmd)
                        steps.append({
                            "command": cmd,
                            "type": kind,
                            "description": self._describe(kind),
                        })

        if not steps:
            return json.dumps({
                "mode": "heuristic",
                "status": "insufficient_data",
                "steps": [],
                "message": (
                    "规则模式未能从 README 提取到安装命令。\n"
                    "建议：配置任意 LLM（哪怕是免费的 Groq）以获得更好效果，\n"
                    "或手动查阅项目 README。"
                ),
            }, ensure_ascii=False, indent=2)

        return json.dumps({
            "mode": "heuristic",
            "status": "ok",
            "steps": steps,
            "warning": "规则模式，建议人工确认步骤后再执行",
        }, ensure_ascii=False, indent=2)

    @staticmethod
    def _is_dangerous(cmd: str) -> bool:
        """过滤危险命令"""
        dangerous = [
            r'rm\s+-rf\s+/',
            r'rm\s+-rf\s+~',
            r':\(\)\{',          # fork bomb
            r'format\s+[cC]:',
            r'mkfs\.',
            r'dd\s+if=',
            r'wget[^\n]+\|\s*(?:sudo\s+)?(?:bash|sh)',
        ]
        return any(re.search(p, cmd, re.IGNORECASE) for p in dangerous)

    @staticmethod
    def _describe(kind: str) -> str:
        descriptions = {
            "git_clone": "克隆代码仓库",
            "pip": "安装 Python 包",
            "pip_req": "安装 Python 依赖（requirements.txt）",
            "pip_m": "安装 Python 包（pip module 方式）",
            "conda": "安装 Conda 包",
            "setup_py": "编译安装 Python 包",
            "npm": "安装 Node.js 包",
            "pnpm": "安装 Node.js 包（pnpm）",
            "yarn": "安装 Node.js 包（yarn）",
            "brew": "通过 Homebrew 安装（macOS）",
            "apt": "通过 apt 安装（Debian/Ubuntu）",
            "dnf": "通过 dnf 安装（Fedora/RHEL）",
            "pacman": "通过 pacman 安装（Arch Linux）",
            "winget": "通过 winget 安装（Windows）",
            "choco": "通过 Chocolatey 安装（Windows）",
            "cargo": "通过 Cargo 安装（Rust）",
            "go": "通过 go install 安装",
            "docker": "通过 Docker 运行",
            "docker_compose": "通过 Docker Compose 启动",
            "curl_pipe": "下载并执行安装脚本",
            "bash_script": "执行安装脚本",
            "make": "编译安装",
        }
        return descriptions.get(kind, "执行安装命令")


# ─────────────────────────────────────────────
#  自动检测 + 工厂函数
# ─────────────────────────────────────────────

def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """检查本地端口是否在监听"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


# 用户可通过环境变量指定本地模型，方便普通用户自定义
# 例：export GITINSTALL_LLM_MODEL=qwen2.5:1.5b
_DEFAULT_SMALL_MODEL = "qwen2.5:1.5b"    # ~1GB，普通笔记本可跑
_DEFAULT_MEDIUM_MODEL = "qwen2.5:3b"    # ~2GB，推荐有独显用户

# 优先推荐的小模型列表（按质量/大小权衡排序）
# 如果 Ollama 里安装了这些模型之一，优先使用最小的
_PREFERRED_SMALL_MODELS = [
    "qwen2.5:1.5b",          # 1.5B，中英双语最佳（推荐大众用户）
    "deepseek-r1:1.5b",      # 1.5B，有推理链
    "smollm2:1.7b",          # 1.7B，英文为主
    "qwen2.5-coder:1.5b",    # 1.5B，代码理解强（已有 base 版本）
    "gemma3:1b",             # 1B，超小
    "qwen2.5:3b",            # 3B，质量更好
    "llama3.2:3b",           # 3B，Meta 官方
    "llama3.2:1b",           # 1B，Meta 官方最小
]


def _get_local_model(base_url: str, endpoint: str = "/v1/models",
                     fallback: str = _DEFAULT_SMALL_MODEL) -> str:
    """
    获取本地运行的模型名称。

    优先级：
      1. 环境变量 GITINSTALL_LLM_MODEL（用户显式指定）
      2. 本地已安装的小模型（_PREFERRED_SMALL_MODELS 顺序）
      3. 本地第一个可用模型
      4. fallback 默认值
    """
    # 用户显式指定模型（最高优先级）
    user_model = os.getenv("GITINSTALL_LLM_MODEL", "").strip()
    if user_model:
        return user_model

    try:
        req = urllib.request.Request(
            f"{base_url}{endpoint}",
            headers={"Authorization": "Bearer local"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = data.get("data", [])
            if not models:
                return fallback
            installed_ids = [m.get("id", "") for m in models]
            # 优先选用已知小模型
            for preferred in _PREFERRED_SMALL_MODELS:
                for installed in installed_ids:
                    if preferred in installed or installed.startswith(preferred.split(":")[0] + ":1"):
                        return installed
            # 否则返回第一个
            return installed_ids[0]
    except Exception:
        pass
    return fallback


def create_provider(force: Optional[str] = None) -> BaseLLMProvider:
    """
    自动检测并创建最优可用的 LLM Provider。
    
    Args:
        force: 强制指定 Provider，可选值：
               "anthropic" | "openai" | "openrouter" | "gemini" |
               "groq" | "deepseek" | "lmstudio" | "ollama" | "none"
               None 表示自动检测
               
    Returns:
        可用的 LLM Provider 实例，永远不会返回 None
    """

    # ── 强制指定模式 ──
    if force == "none":
        logger.info(t("llm.using_heuristic"))
        return HeuristicProvider()

    if force == "lmstudio":
        model = _get_local_model("http://localhost:1234", fallback=_DEFAULT_SMALL_MODEL)
        logger.info(t("llm.using_with_model", name="LM Studio", model=model))
        return OpenAICompatibleProvider("lm-studio", "http://localhost:1234/v1", model, "LM Studio")

    if force == "ollama":
        model = _get_local_model("http://localhost:11434", fallback=_DEFAULT_SMALL_MODEL)
        logger.info(t("llm.using_with_model", name="Ollama", model=model))
        logger.info(t("llm.ollama_hint", model=model))
        return OpenAICompatibleProvider("ollama", "http://localhost:11434/v1", model, f"Ollama ({model})")

    # ── 自动检测（按质量/成本优先级排序）──
    # 格式：(环境变量名, Provider 构造函数)
    cloud_providers = [
        (
            "ANTHROPIC_API_KEY",
            lambda k: AnthropicProvider(k),
        ),
        (
            "OPENAI_API_KEY",
            lambda k: OpenAICompatibleProvider(k, "https://api.openai.com/v1", "gpt-4o", "OpenAI GPT-4o"),
        ),
        (
            "OPENROUTER_API_KEY",
            lambda k: OpenAICompatibleProvider(k, "https://openrouter.ai/api/v1", "anthropic/claude-opus-4-5", "OpenRouter"),
        ),
        (
            "GEMINI_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k,
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "gemini-2.0-flash",
                "Google Gemini",
            ),
        ),
        (
            "GROQ_API_KEY",
            lambda k: OpenAICompatibleProvider(k, "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "Groq Llama"),
        ),
        (
            "DEEPSEEK_API_KEY",
            lambda k: OpenAICompatibleProvider(k, "https://api.deepseek.com/v1", "deepseek-chat", "DeepSeek"),
        ),
        # ── 中国 LLM 提供商 ──
        (
            "DASHSCOPE_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen-plus", "通义千问 Qwen",
            ),
        ),
        (
            "ZHIPU_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://open.bigmodel.cn/api/paas/v4",
                "glm-4-flash", "智谱 GLM",
            ),
        ),
        (
            "MOONSHOT_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://api.moonshot.cn/v1",
                "moonshot-v1-8k", "月之暗面 Kimi",
            ),
        ),
        (
            "BAICHUAN_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://api.baichuan-ai.com/v1",
                "Baichuan4", "百川智能",
            ),
        ),
        (
            "YI_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://api.lingyiwanwu.com/v1",
                "yi-lightning", "零一万物 Yi",
            ),
        ),
        (
            "STEPFUN_API_KEY",
            lambda k: OpenAICompatibleProvider(
                k, "https://api.stepfun.com/v1",
                "step-2-16k", "阶跃星辰 Step",
            ),
        ),
    ]

    # 检查环境变量，支持 force 指定特定 Provider
    for env_var, factory in cloud_providers:
        provider_name = env_var.replace("_API_KEY", "").lower()
        if force and force != provider_name:
            continue
        key = os.getenv(env_var, "").strip()
        if key:
            provider = factory(key)
            logger.info(t("llm.using_named", name=provider.name))
            return provider

    # 检查本地服务
    if not force or force == "lmstudio":
        if _is_port_open("localhost", 1234):
            model = _get_local_model("http://localhost:1234", fallback="local-model")
            logger.info(t("llm.detected_local", name="LM Studio", model=model))
            return OpenAICompatibleProvider("lm-studio", "http://localhost:1234/v1", model, "LM Studio")

    if not force or force == "ollama":
        if _is_port_open("localhost", 11434):
            model = _get_local_model("http://localhost:11434", fallback=_DEFAULT_SMALL_MODEL)
            logger.info(t("llm.detected_local", name="Ollama", model=model))
            return OpenAICompatibleProvider("ollama", "http://localhost:11434/v1", model, f"Ollama ({model})")

    # 最终兜底：规则模式
    logger.warning(t("llm.no_provider"))
    logger.info(t("llm.hint_ollama"))
    logger.info(t("llm.hint_groq"))
    return HeuristicProvider()


# ─────────────────────────────────────────────
#  标准化 System Prompt（供各模块复用）
# ─────────────────────────────────────────────

# System Prompt 有两个版本：
# - INSTALL_SYSTEM_PROMPT       完整版（大模型/云端 API 使用）
# - INSTALL_SYSTEM_PROMPT_SMALL 精简版（1.5B~3B 本地小模型使用，避免 context 超限）

INSTALL_SYSTEM_PROMPT = """\
你是开源软件安装专家。根据用户提供的项目信息和环境，输出安装步骤（纯 JSON，无代码块）。

JSON 格式：
{"project_name":"名","steps":[{"id":1,"description":"说明","command":"命令"}],"launch_command":"启动命令","notes":"注意"}

规则：Python项目用venv；Apple Silicon用pip install torch（无--index-url）；NVIDIA CUDA12用--index-url .../cu121；只输出JSON。
"""

# 精简版 prompt，适配 1.5B~3B 本地小模型
# 关键：越短越好，指令越清楚越好，JSON schema 越简单越好
INSTALL_SYSTEM_PROMPT_SMALL = """\
Output JSON only. No explanation. Format:
{"steps":[{"description":"step","command":"shell cmd"}],"launch_command":"start cmd"}
Rules: use venv for Python; Apple MPS: pip install torch (no --index-url); CUDA12: add --index-url https://download.pytorch.org/whl/cu121
"""

ERROR_FIX_SYSTEM_PROMPT = """\
你是一个开源软件安装报错修复专家。
用户在安装 GitHub 项目时遇到了报错，请分析报错原因并提供修复方案。

输出 JSON 格式：
{
  "root_cause": "报错根本原因（一句话）",
  "fix_commands": ["修复命令1", "修复命令2"],
  "explanation": "详细解释",
  "prevention": "如何避免再次出现"
}
"""
