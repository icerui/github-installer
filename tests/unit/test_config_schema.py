"""
Tests for config_schema.py - 配置 Schema 验证
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from config_schema import (
    validate_config, load_and_validate, format_validation_result,
    get_config_value, CONFIG_SCHEMA, ValidationResult,
)


class TestValidateConfig:
    """测试配置验证"""

    def test_empty_config(self):
        result = validate_config({})
        assert result.valid is True
        assert result.config["install_mode"] == "safe"  # 默认值
        assert result.config["telemetry"] is True

    def test_valid_config(self):
        result = validate_config({
            "install_mode": "fast",
            "telemetry": False,
            "web_port": 9090,
        })
        assert result.valid is True
        assert result.config["install_mode"] == "fast"
        assert result.config["telemetry"] is False
        assert result.config["web_port"] == 9090

    def test_invalid_enum(self):
        result = validate_config({"install_mode": "yolo"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].path == "install_mode"

    def test_invalid_type(self):
        result = validate_config({"telemetry": "yes"})
        assert result.valid is False
        assert result.errors[0].path == "telemetry"

    def test_invalid_range_min(self):
        result = validate_config({"web_port": 80})
        assert result.valid is False
        assert "过小" in result.errors[0].message

    def test_invalid_range_max(self):
        result = validate_config({"web_port": 99999})
        assert result.valid is False
        assert "过大" in result.errors[0].message

    def test_unknown_field_warning(self):
        result = validate_config({"unknown_field": "value"})
        assert result.valid is True  # 未知字段不算错误
        assert len(result.warnings) == 1
        assert "unknown_field" in result.warnings[0]

    def test_multiple_errors(self):
        result = validate_config({
            "install_mode": "invalid",
            "web_port": -1,
        })
        assert result.valid is False
        assert len(result.errors) == 2

    def test_int_float_compat(self):
        result = validate_config({"cache_ttl": 3600.0})
        assert result.valid is True
        assert result.config["cache_ttl"] == 3600

    def test_all_valid_enums(self):
        for mode in ["safe", "fast", "strict"]:
            result = validate_config({"install_mode": mode})
            assert result.valid is True

    def test_platform_enum(self):
        for p in ["github", "gitlab", "bitbucket", "gitee", "codeberg"]:
            result = validate_config({"preferred_platform": p})
            assert result.valid is True

    def test_llm_preference_enum(self):
        for llm in ["", "anthropic", "openai", "groq", "ollama", "none"]:
            result = validate_config({"llm_preference": llm})
            assert result.valid is True

    def test_defaults_merged(self):
        result = validate_config({})
        # 所有 schema 字段都应有默认值
        for key in CONFIG_SCHEMA:
            assert key in result.config

    def test_onboard_time_allowed(self):
        result = validate_config({"onboard_time": "2025-01-01T00:00:00Z"})
        assert result.valid is True
        assert len(result.warnings) == 0  # onboard_time 是允许的额外字段


class TestLoadAndValidate:
    def test_no_config_file(self, tmp_path):
        result = load_and_validate(tmp_path / "nonexistent.json")
        assert result.valid is True
        # 应返回全部默认值
        assert result.config["install_mode"] == "safe"

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid json}")
        result = load_and_validate(f)
        assert result.valid is False
        assert "JSON" in result.errors[0].message

    def test_valid_json_file(self, tmp_path):
        import json
        f = tmp_path / "config.json"
        f.write_text(json.dumps({"install_mode": "fast", "web_port": 9090}))
        result = load_and_validate(f)
        assert result.valid is True
        assert result.config["install_mode"] == "fast"


class TestFormatValidationResult:
    def test_valid_format(self):
        result = ValidationResult(valid=True, config={})
        text = format_validation_result(result)
        assert "✅" in text

    def test_invalid_format(self):
        from config_schema import ValidationError
        result = ValidationResult(
            valid=False,
            errors=[ValidationError("test", "broken", "str", 123)],
        )
        text = format_validation_result(result)
        assert "❌" in text
        assert "test" in text


class TestGetConfigValue:
    def test_default_value(self):
        val = get_config_value("install_mode")
        assert val in ("safe", "fast", "strict")

    def test_fallback(self):
        val = get_config_value("nonexistent", fallback="default")
        assert val == "default"


class TestSchemaCompleteness:
    """验证 Schema 定义的完整性"""

    def test_all_have_type(self):
        for key, schema in CONFIG_SCHEMA.items():
            assert "type" in schema, f"{key} 缺少 type"

    def test_all_have_default(self):
        for key, schema in CONFIG_SCHEMA.items():
            assert "default" in schema, f"{key} 缺少 default"

    def test_all_have_description(self):
        for key, schema in CONFIG_SCHEMA.items():
            assert "description" in schema, f"{key} 缺少 description"

    def test_enum_values_match_type(self):
        for key, schema in CONFIG_SCHEMA.items():
            if "enum" in schema:
                for val in schema["enum"]:
                    assert isinstance(val, schema["type"]), \
                        f"{key} 的 enum 值 {val} 类型不匹配"

    def test_default_passes_validation(self):
        defaults = {k: v["default"] for k, v in CONFIG_SCHEMA.items()}
        result = validate_config(defaults)
        assert result.valid is True, f"默认配置验证失败: {result.errors}"
