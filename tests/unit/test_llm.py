"""
test_llm.py - 多 LLM 适配器测试
=================================
"""

from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from llm import (
    BaseLLMProvider,
    OpenAICompatibleProvider,
    AnthropicProvider,
    HeuristicProvider,
    _is_port_open,
    _get_local_model,
    create_provider,
    INSTALL_SYSTEM_PROMPT,
    INSTALL_SYSTEM_PROMPT_SMALL,
    ERROR_FIX_SYSTEM_PROMPT,
)


# ─────────────────────────────────────────────
#  BaseLLMProvider 接口
# ─────────────────────────────────────────────

class TestBaseLLMProvider:
    def test_abstract_methods(self):
        with pytest.raises(TypeError):
            BaseLLMProvider()

    def test_is_available_default(self):
        class DummyProvider(BaseLLMProvider):
            def complete(self, system, user, max_tokens=2048):
                return ""
            @property
            def name(self):
                return "dummy"
        p = DummyProvider()
        assert p.is_available() is True


# ─────────────────────────────────────────────
#  OpenAICompatibleProvider
# ─────────────────────────────────────────────

class TestOpenAICompatibleProvider:
    def test_name(self):
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        assert p.name == "TestProvider"

    def test_complete_success(self):
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "hello"}}]
        }).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm.urllib.request.urlopen", return_value=mock_resp):
            result = p.complete("sys", "usr")
            assert result == "hello"

    def test_complete_http_error(self):
        import urllib.error
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        err = urllib.error.HTTPError(
            "http://localhost/v1/chat/completions", 401, "Unauthorized",
            {}, BytesIO(b"invalid key")
        )
        with patch("llm.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="API 错误 401"):
                p.complete("sys", "usr")

    def test_complete_timeout(self):
        import socket
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        with patch("llm.urllib.request.urlopen", side_effect=socket.timeout):
            with pytest.raises(RuntimeError, match="超时"):
                p.complete("sys", "usr")

    def test_complete_url_error(self):
        import urllib.error
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        err = urllib.error.URLError("connection refused")
        with patch("llm.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="连接失败"):
                p.complete("sys", "usr")

    def test_complete_url_error_timeout(self):
        import socket
        import urllib.error
        p = OpenAICompatibleProvider("key", "http://localhost/v1", "model", "TestProvider")
        err = urllib.error.URLError(socket.timeout("timed out"))
        with patch("llm.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="超时"):
                p.complete("sys", "usr")

    def test_trailingslash_stripped(self):
        p = OpenAICompatibleProvider("key", "http://localhost/v1/", "model", "TestProvider")
        assert p.base_url == "http://localhost/v1"


# ─────────────────────────────────────────────
#  AnthropicProvider
# ─────────────────────────────────────────────

class TestAnthropicProvider:
    def test_name(self):
        p = AnthropicProvider("key")
        assert "Anthropic" in p.name

    def test_default_model(self):
        p = AnthropicProvider("key")
        assert p.model == AnthropicProvider.DEFAULT_MODEL

    def test_custom_model(self):
        p = AnthropicProvider("key", model="claude-3-haiku-20240307")
        assert p.model == "claude-3-haiku-20240307"

    def test_complete_success(self):
        p = AnthropicProvider("key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [{"text": "hello from claude"}]
        }).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("llm.urllib.request.urlopen", return_value=mock_resp):
            result = p.complete("sys", "usr")
            assert result == "hello from claude"

    def test_complete_http_error(self):
        import urllib.error
        p = AnthropicProvider("key")
        err = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 429, "Too Many",
            {}, BytesIO(b"rate limited")
        )
        with patch("llm.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="Anthropic API 错误 429"):
                p.complete("sys", "usr")

    def test_complete_timeout(self):
        import socket
        p = AnthropicProvider("key")
        with patch("llm.urllib.request.urlopen", side_effect=socket.timeout):
            with pytest.raises(RuntimeError, match="Anthropic 请求超时"):
                p.complete("sys", "usr")


# ─────────────────────────────────────────────
#  HeuristicProvider（规则引擎）
# ─────────────────────────────────────────────

class TestHeuristicProvider:
    def test_name(self):
        p = HeuristicProvider()
        assert "规则" in p.name

    def test_extract_pip_install(self):
        p = HeuristicProvider()
        content = "# Install\n```bash\npip install mypackage\n```"
        result = json.loads(p.complete("sys", content))
        assert result["status"] == "ok"
        cmds = [s["command"] for s in result["steps"]]
        assert any("pip install mypackage" in c for c in cmds)

    def test_extract_git_clone(self):
        p = HeuristicProvider()
        content = "```bash\ngit clone https://github.com/user/repo.git\n```"
        result = json.loads(p.complete("sys", content))
        assert result["status"] == "ok"
        cmds = [s["command"] for s in result["steps"]]
        assert any("git clone" in c for c in cmds)

    def test_extract_npm_install(self):
        p = HeuristicProvider()
        content = "```sh\nnpm install express\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert any("npm install" in c for c in cmds)

    def test_extract_docker(self):
        p = HeuristicProvider()
        content = "```bash\ndocker pull nginx\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert any("docker pull" in c for c in cmds)

    def test_no_install_found(self):
        p = HeuristicProvider()
        content = "This is a readme with no install commands."
        result = json.loads(p.complete("sys", content))
        assert result["status"] == "insufficient_data"
        assert result["steps"] == []

    def test_dangerous_command_filtered(self):
        p = HeuristicProvider()
        content = "```bash\nrm -rf /\npip install safe\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert not any("rm -rf /" in c for c in cmds)
        assert any("pip install safe" in c for c in cmds)

    def test_is_dangerous(self):
        assert HeuristicProvider._is_dangerous("rm -rf /") is True
        assert HeuristicProvider._is_dangerous("rm -rf ~/") is True
        assert HeuristicProvider._is_dangerous("pip install flask") is False
        assert HeuristicProvider._is_dangerous(":(){ :|:& };:") is True
        assert HeuristicProvider._is_dangerous("format C:") is True

    def test_describe_known(self):
        assert "Python" in HeuristicProvider._describe("pip")
        assert "Node" in HeuristicProvider._describe("npm")
        assert "Docker" in HeuristicProvider._describe("docker")

    def test_describe_unknown(self):
        desc = HeuristicProvider._describe("unknown_type")
        assert isinstance(desc, str)

    def test_multiple_code_blocks(self):
        p = HeuristicProvider()
        content = """
```bash
pip install torch
```

Then:

```bash
pip install transformers
```
"""
        result = json.loads(p.complete("sys", content))
        assert len(result["steps"]) == 2

    def test_dedup_commands(self):
        p = HeuristicProvider()
        content = """
```bash
pip install torch
pip install torch
```
"""
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert cmds.count("pip install torch") == 1

    def test_brew_install(self):
        p = HeuristicProvider()
        content = "```bash\nbrew install ffmpeg\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert any("brew install" in c for c in cmds)

    def test_cargo_install(self):
        p = HeuristicProvider()
        content = "```bash\ncargo install ripgrep\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert any("cargo install" in c for c in cmds)

    def test_make_install(self):
        p = HeuristicProvider()
        content = "```bash\nmake install\n```"
        result = json.loads(p.complete("sys", content))
        cmds = [s["command"] for s in result["steps"]]
        assert any("make install" in c for c in cmds)


# ─────────────────────────────────────────────
#  端口检测
# ─────────────────────────────────────────────

class TestIsPortOpen:
    def test_closed_port(self):
        assert _is_port_open("localhost", 19999, timeout=0.1) is False

    def test_open_port_mock(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("llm.socket.create_connection", return_value=mock_conn):
            assert _is_port_open("localhost", 1234) is True

    def test_timeout(self):
        import socket
        with patch("llm.socket.create_connection", side_effect=socket.timeout):
            assert _is_port_open("localhost", 1234) is False

    def test_connection_refused(self):
        with patch("llm.socket.create_connection", side_effect=ConnectionRefusedError):
            assert _is_port_open("localhost", 1234) is False


# ─────────────────────────────────────────────
#  本地模型检测
# ─────────────────────────────────────────────

class TestGetLocalModel:
    def test_env_override(self):
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": "custom-model"}):
            result = _get_local_model("http://localhost:1234")
            assert result == "custom-model"

    def test_preferred_model_selected(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": [
                {"id": "mistral:7b"},
                {"id": "qwen2.5:1.5b"},
            ]
        }).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.dict(os.environ, {}, clear=False), \
             patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": ""}), \
             patch("llm.urllib.request.urlopen", return_value=mock_resp):
            result = _get_local_model("http://localhost:1234")
            assert "qwen2.5" in result

    def test_first_model_fallback(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "custom-exotic-model"}]
        }).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": ""}), \
             patch("llm.urllib.request.urlopen", return_value=mock_resp):
            result = _get_local_model("http://localhost:1234")
            assert result == "custom-exotic-model"

    def test_no_models_available(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": []}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": ""}), \
             patch("llm.urllib.request.urlopen", return_value=mock_resp):
            result = _get_local_model("http://localhost:1234")
            assert result == "qwen2.5:1.5b"  # default fallback

    def test_connection_failure(self):
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": ""}), \
             patch("llm.urllib.request.urlopen", side_effect=Exception("fail")):
            result = _get_local_model("http://localhost:1234")
            assert result == "qwen2.5:1.5b"  # default fallback


# ─────────────────────────────────────────────
#  create_provider 工厂
# ─────────────────────────────────────────────

class TestCreateProvider:
    def test_force_none(self):
        p = create_provider(force="none")
        assert isinstance(p, HeuristicProvider)

    def test_force_lmstudio(self):
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": "test-model"}):
            p = create_provider(force="lmstudio")
            assert isinstance(p, OpenAICompatibleProvider)
            assert "LM Studio" in p.name

    def test_force_ollama(self):
        with patch.dict(os.environ, {"GITINSTALL_LLM_MODEL": "test-model"}):
            p = create_provider(force="ollama")
            assert isinstance(p, OpenAICompatibleProvider)
            assert "Ollama" in p.name

    def test_auto_anthropic(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
            p = create_provider()
            assert isinstance(p, AnthropicProvider)

    def test_auto_openai(self):
        env = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "sk-openai-test",
            "OPENROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GROQ_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("llm._is_port_open", return_value=False):
            p = create_provider()
            assert isinstance(p, OpenAICompatibleProvider)
            assert "OpenAI" in p.name

    def test_auto_fallback_to_heuristic(self):
        env = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GROQ_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("llm._is_port_open", return_value=False):
            p = create_provider()
            assert isinstance(p, HeuristicProvider)

    def test_auto_detect_lmstudio(self):
        env = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GROQ_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "GITINSTALL_LLM_MODEL": "test-model",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("llm._is_port_open", side_effect=lambda h, p, **kw: p == 1234):
            p = create_provider()
            assert isinstance(p, OpenAICompatibleProvider)
            assert "LM Studio" in p.name

    def test_auto_detect_ollama(self):
        env = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GROQ_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "GITINSTALL_LLM_MODEL": "test-model",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("llm._is_port_open", side_effect=lambda h, p, **kw: p == 11434):
            p = create_provider()
            assert isinstance(p, OpenAICompatibleProvider)
            assert "Ollama" in p.name


# ─────────────────────────────────────────────
#  Prompt 常量
# ─────────────────────────────────────────────

class TestPrompts:
    def test_install_prompt_exists(self):
        assert len(INSTALL_SYSTEM_PROMPT) > 50
        assert "JSON" in INSTALL_SYSTEM_PROMPT

    def test_small_prompt_exists(self):
        assert len(INSTALL_SYSTEM_PROMPT_SMALL) > 20
        assert "JSON" in INSTALL_SYSTEM_PROMPT_SMALL

    def test_error_fix_prompt_exists(self):
        assert len(ERROR_FIX_SYSTEM_PROMPT) > 50
        assert "JSON" in ERROR_FIX_SYSTEM_PROMPT
