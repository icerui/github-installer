"""
test_feature_flags.py - 功能开关系统测试
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from feature_flags import (
    is_enabled, list_flags, get_all_status, format_flags_table,
    get_flag, FeatureFlag,
)


class TestIsEnabled:
    """测试 is_enabled()"""

    def test_default_enabled_flag(self):
        # telemetry 默认开启
        assert is_enabled("telemetry") is True

    def test_default_disabled_flag(self):
        # event_bus 默认关闭
        assert is_enabled("event_bus") is False

    def test_unknown_flag_returns_false(self):
        assert is_enabled("nonexistent_flag_xyz") is False

    def test_env_override_enable(self, monkeypatch):
        # 默认关闭的 flag 通过环境变量开启
        monkeypatch.setenv("GITINSTALL_EVENT_BUS", "1")
        assert is_enabled("event_bus") is True

    def test_env_override_disable(self, monkeypatch):
        # 默认开启的 flag 通过环境变量关闭
        monkeypatch.setenv("GITINSTALL_TELEMETRY", "0")
        assert is_enabled("telemetry") is False

    def test_env_override_true_string(self, monkeypatch):
        monkeypatch.setenv("GITINSTALL_WATCHDOG", "true")
        assert is_enabled("watchdog") is True

    def test_env_override_false_string(self, monkeypatch):
        monkeypatch.setenv("GITINSTALL_TELEMETRY", "false")
        assert is_enabled("telemetry") is False


class TestListFlags:
    """测试 list_flags()"""

    def test_list_all_flags(self):
        flags = list_flags()
        assert len(flags) >= 10
        names = [f.name for f in flags]
        assert "telemetry" in names
        assert "pre_audit" in names

    def test_list_by_group(self):
        security = list_flags("security")
        names = [f.name for f in security]
        assert "pre_audit" in names
        assert "license_check" in names

    def test_list_nonexistent_group(self):
        result = list_flags("nonexistent_group")
        assert result == []

    def test_flag_info_structure(self):
        flags = list_flags()
        for flag in flags:
            assert isinstance(flag, FeatureFlag)
            assert flag.name
            assert flag.group
            assert flag.description


class TestGetAllStatus:
    """测试 get_all_status()"""

    def test_returns_dict(self):
        status = get_all_status()
        assert isinstance(status, dict)
        assert len(status) > 0

    def test_all_values_are_bool(self):
        status = get_all_status()
        for k, v in status.items():
            assert isinstance(v, bool), f"{k} 应为 bool，实际为 {type(v)}"


class TestFormatFlagsTable:
    """测试 format_flags_table()"""

    def test_returns_string(self):
        table = format_flags_table()
        assert isinstance(table, str)
        assert "功能开关" in table

    def test_contains_flag_names(self):
        table = format_flags_table()
        assert "telemetry" in table
        assert "pre_audit" in table
