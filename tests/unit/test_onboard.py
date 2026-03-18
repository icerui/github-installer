"""
test_onboard.py - 交互式引导向导覆盖率突破
=============================================

突破性算法：mock input() + 预定义回答序列 → 一次覆盖整个交互流程

onboard.py 164 行未覆盖（12% → 目标 90%+）
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from onboard import _input_with_default, _yes_no, _step_header, run_onboard


# ── 保留原有功能测试（load_config, is_first_run）──

class TestLoadConfig:
    def test_no_config_file(self):
        with patch.object(Path, "exists", return_value=False):
            from onboard import load_config
            config = load_config()
            assert isinstance(config, dict)

    def test_valid_config(self, tmp_path):
        from onboard import load_config
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "version": "1.0", "onboard_completed": True, "install_mode": "safe",
        }))
        with patch("onboard.CONFIG_FILE", config_file):
            config = load_config()
            assert config["version"] == "1.0"

    def test_invalid_json(self, tmp_path):
        from onboard import load_config
        config_file = tmp_path / "bad.json"
        config_file.write_text("{bad json")
        with patch("onboard.CONFIG_FILE", config_file):
            config = load_config()
            assert config == {}


class TestIsFirstRun:
    def test_first_run_no_config(self):
        from onboard import is_first_run
        with patch("onboard.load_config", return_value={}):
            assert is_first_run() is True

    def test_not_first_run(self):
        from onboard import is_first_run
        with patch("onboard.load_config", return_value={"onboard_completed": True}):
            assert is_first_run() is False

    def test_first_run_incomplete(self):
        from onboard import is_first_run
        with patch("onboard.load_config", return_value={"onboard_completed": False}):
            assert is_first_run() is True


# ── 新增：交互式函数 + 完整向导覆盖 ──

class TestInputWithDefault:
    """_input_with_default: 带默认值的输入"""

    def test_with_user_input(self):
        with patch("builtins.input", return_value="custom"):
            assert _input_with_default("prompt", "default") == "custom"

    def test_empty_input_uses_default(self):
        with patch("builtins.input", return_value=""):
            assert _input_with_default("prompt", "default") == "default"

    def test_no_default(self):
        with patch("builtins.input", return_value="val"):
            assert _input_with_default("prompt") == "val"

    def test_eof_uses_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _input_with_default("prompt", "fallback") == "fallback"

    def test_keyboard_interrupt_uses_default(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert _input_with_default("prompt", "fallback") == "fallback"


class TestYesNo:
    """_yes_no: 是/否确认"""

    def test_yes(self):
        with patch("builtins.input", return_value="y"):
            assert _yes_no("continue?") is True

    def test_no(self):
        with patch("builtins.input", return_value="n"):
            assert _yes_no("continue?") is False

    def test_empty_default_true(self):
        with patch("builtins.input", return_value=""):
            assert _yes_no("continue?", default=True) is True

    def test_empty_default_false(self):
        with patch("builtins.input", return_value=""):
            assert _yes_no("continue?", default=False) is False

    def test_chinese_yes(self):
        with patch("builtins.input", return_value="是"):
            assert _yes_no("continue?") is True

    def test_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _yes_no("continue?", default=False) is False


class TestStepHeader:
    """_step_header: 步骤标题打印"""

    def test_prints_header(self, capsys):
        _step_header(1, 5, "测试步骤")
        captured = capsys.readouterr().out
        assert "步骤 1/5" in captured
        assert "测试步骤" in captured


class TestRunOnboard:
    """
    run_onboard: 完整交互式向导

    突破点：用 input_sequence 预定义所有用户输入，
    一次测试覆盖整个 170+ 行的向导流程。
    """

    @pytest.fixture(autouse=True)
    def _setup_temp_config(self, tmp_path, monkeypatch):
        """重定向配置目录到临时目录"""
        self.config_dir = tmp_path / ".gitinstall"
        self.config_file = self.config_dir / "config.json"
        monkeypatch.setattr("onboard.CONFIG_DIR", self.config_dir)
        monkeypatch.setattr("onboard.CONFIG_FILE", self.config_file)

    def test_full_wizard_skip_all(self):
        """跳过所有可选步骤的快速通道"""
        input_answers = iter([
            "n",    # 配置 GITHUB_TOKEN? No
            "n",    # 配置 LLM? No
            "",     # 安装目录 (default)
            "1",    # 安装模式: 安全
            "y",    # 启用遥测
            "n",    # 初始化 Skills? No
            "n",    # 运行 Doctor? No
        ])

        mock_env = {
            "os": {"type": "macOS", "version": "15.0", "arch": "arm64", "chip": "Apple M3"},
            "gpu": {"name": "Apple M3 GPU", "type": "apple_silicon"},
            "runtimes": {"python": {"available": True, "version": "3.13"}},
        }

        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()

        assert self.config_file.exists()
        config = json.loads(self.config_file.read_text())
        assert config["onboard_completed"] is True
        assert config["install_mode"] == "safe"
        assert config["telemetry"] is True

    def test_full_wizard_configure_everything(self):
        """配置所有选项"""
        input_answers = iter([
            "y",                    # GITHUB_TOKEN
            "ghp_testtoken12345",   # Token 值
            "y",                    # LLM
            "3",                    # Groq
            "gsk_testkey",          # API Key
            "/tmp/test_projects",   # 安装目录
            "2",                    # 快速模式
            "n",                    # 遥测关闭
            "y",                    # 初始化 Skills
            "n",                    # Doctor
        ])

        mock_env = {
            "os": {"type": "macOS", "version": "15.0", "arch": "arm64"},
            "gpu": {"type": "apple_silicon"},
            "runtimes": {},
        }
        mock_skills_mgr = MagicMock()
        mock_skills_mgr.list_skills.return_value = [MagicMock(), MagicMock()]

        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet, \
             patch("skills.ensure_builtin_skills"), \
             patch("skills.SkillManager", return_value=mock_skills_mgr):
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()

        config = json.loads(self.config_file.read_text())
        assert config["github_token"] == "ghp_testtoken12345"
        assert "GROQ_API_KEY" in config.get("llm_key", {})
        assert config["install_mode"] == "fast"
        assert config["telemetry"] is False
        assert config.get("skills_initialized") is True

    def test_wizard_ollama_choice(self):
        """Ollama 本地模型"""
        input_answers = iter([
            "n", "y", "6", "", "3", "y", "n", "n",
        ])
        mock_env = {"os": {"type": "Linux"}, "gpu": {"type": "nvidia"}, "runtimes": {}}
        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()

        config = json.loads(self.config_file.read_text())
        assert config.get("llm_preference") == "ollama"
        assert config["install_mode"] == "strict"

    def test_wizard_skip_llm(self):
        """LLM 选择 7 (跳过)"""
        input_answers = iter(["n", "y", "7", "", "", "y", "n", "n"])
        mock_env = {"os": {"type": "macOS"}, "gpu": {}, "runtimes": {}}
        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()
        config = json.loads(self.config_file.read_text())
        assert "llm_key" not in config

    def test_wizard_with_doctor(self):
        """运行 Doctor 诊断"""
        input_answers = iter(["n", "n", "", "1", "y", "n", "y"])
        mock_env = {"os": {"type": "macOS"}, "gpu": {}, "runtimes": {}}
        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet, \
             patch("doctor.run_doctor", return_value=MagicMock()), \
             patch("doctor.format_doctor_report", return_value="All OK"):
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()
        assert self.config_file.exists()

    def test_wizard_env_detection_error(self):
        """环境检测失败也能继续"""
        input_answers = iter(["n", "n", "", "1", "y", "n", "n"])
        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector", side_effect=Exception("fail")):
            run_onboard()
        assert self.config_file.exists()

    @pytest.mark.parametrize("choice,env_var", [
        ("1", "ANTHROPIC_API_KEY"),
        ("2", "OPENAI_API_KEY"),
        ("4", "DEEPSEEK_API_KEY"),
        ("5", "GEMINI_API_KEY"),
    ])
    def test_wizard_llm_providers(self, choice, env_var):
        """遍历所有 LLM provider 选项"""
        input_answers = iter([
            "n", "y", choice, "test_key_value",
            "", "1", "y", "n", "n",
        ])
        mock_env = {"os": {"type": "macOS"}, "gpu": {}, "runtimes": {}}
        with patch("builtins.input", lambda _: next(input_answers)), \
             patch("detector.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = mock_env
            run_onboard()
        config = json.loads(self.config_file.read_text())
        assert env_var in config.get("llm_key", {}), f"Expected {env_var} for choice {choice}"
