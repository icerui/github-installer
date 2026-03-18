"""
test_watchdog.py - 安装看门狗测试
"""

import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from watchdog import (
    StepWatchdog, WatchdogAlert, WatchdogConfig,
    create_watchdog,
)


class TestWatchdogConfig:

    def test_default_values(self):
        cfg = WatchdogConfig()
        assert cfg.enabled is True
        assert cfg.disk_min_mb > 0
        assert cfg.step_timeout > 0


class TestStepWatchdog:

    def test_timeout_for_git_clone(self):
        wd = StepWatchdog()
        timeout = wd.get_timeout_for_command("git clone https://github.com/a/b")
        assert timeout == 300

    def test_timeout_for_pip_install(self):
        wd = StepWatchdog()
        timeout = wd.get_timeout_for_command("pip install -r requirements.txt")
        assert timeout == 900

    def test_timeout_for_npm(self):
        wd = StepWatchdog()
        timeout = wd.get_timeout_for_command("npm install")
        assert timeout == 600

    def test_timeout_for_cargo(self):
        wd = StepWatchdog()
        timeout = wd.get_timeout_for_command("cargo build --release")
        assert timeout == 900

    def test_timeout_for_unknown_command(self):
        wd = StepWatchdog()
        timeout = wd.get_timeout_for_command("some-random-command")
        assert timeout == 600  # default step_timeout

    def test_disk_space_check_ok(self):
        wd = StepWatchdog(WatchdogConfig(disk_min_mb=1))
        alert = wd.check_disk_space()
        assert alert is None  # should have at least 1MB

    def test_disk_space_check_low(self):
        wd = StepWatchdog(WatchdogConfig(disk_min_mb=999999999))
        alert = wd.check_disk_space()
        assert alert is not None
        assert alert.alert_type == "disk_low"
        assert "磁盘空间" in alert.message

    @pytest.mark.skipif(platform.system() == "Windows", reason="sleep command not available on Windows")
    def test_start_and_cancel_timer(self):
        wd = StepWatchdog()
        proc = subprocess.Popen(["sleep", "60"])
        try:
            wd.start_timer(proc, 0, "sleep 60", timeout=10)
            assert wd._timer is not None
            wd.cancel_timer()
            assert not wd.was_killed
        finally:
            proc.kill()
            proc.wait()

    def test_was_killed_default_false(self):
        wd = StepWatchdog()
        assert wd.was_killed is False

    def test_format_alerts_empty(self):
        wd = StepWatchdog()
        text = wd.format_alerts()
        assert "无告警" in text

    def test_create_watchdog_enabled(self):
        wd = create_watchdog(enabled=True)
        assert isinstance(wd, StepWatchdog)
        assert wd.config.enabled is True

    def test_create_watchdog_disabled(self):
        wd = create_watchdog(enabled=False)
        assert isinstance(wd, StepWatchdog)
        assert wd.config.enabled is False


class TestWatchdogAlert:

    def test_alert_creation(self):
        alert = WatchdogAlert(
            alert_type="timeout", step_index=0,
            command="pip install x", message="超时了",
            action_taken="killed",
        )
        assert alert.alert_type == "timeout"
        assert alert.step_index == 0
        assert alert.command == "pip install x"
