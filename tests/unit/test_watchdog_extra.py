"""
watchdog.py 额外覆盖 — 超时定时器, 磁盘检查, 格式化告警
"""
import os
import sys
import subprocess
import threading
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))

from watchdog import StepWatchdog, WatchdogConfig, WatchdogAlert


class TestGetTimeoutForCommand:
    """Cover lines 75-88: command-specific timeout logic"""

    def setup_method(self):
        self.wd = StepWatchdog()

    @pytest.mark.parametrize("cmd,expected_attr", [
        ("npm install", "NPM_INSTALL_TIMEOUT"),
        ("npm ci --production", "NPM_INSTALL_TIMEOUT"),
        ("cargo build --release", "PIP_INSTALL_TIMEOUT"),
        ("make -j8", "PIP_INSTALL_TIMEOUT"),
        ("cmake ..", "PIP_INSTALL_TIMEOUT"),
        ("docker build .", "PIP_INSTALL_TIMEOUT"),
        ("conda install pytorch", "PIP_INSTALL_TIMEOUT"),
    ])
    def test_command_timeouts(self, cmd, expected_attr):
        timeout = self.wd.get_timeout_for_command(cmd)
        assert timeout > 0

    def test_default_timeout(self):
        timeout = self.wd.get_timeout_for_command("echo hello")
        assert timeout == self.wd.config.step_timeout


class TestCheckDiskSpace:
    """Cover lines 93-108: disk space checking"""

    def test_disk_ok(self):
        wd = StepWatchdog()
        alert = wd.check_disk_space()
        # On dev machine, disk should have space
        assert alert is None

    def test_disk_low(self):
        wd = StepWatchdog(WatchdogConfig(disk_min_mb=999_999_999))
        alert = wd.check_disk_space()
        assert alert is not None
        assert alert.alert_type == "disk_low"
        assert "磁盘" in alert.message

    def test_disk_invalid_path(self):
        wd = StepWatchdog()
        alert = wd.check_disk_space("/nonexistent/path/xyz")
        # OSError caught, returns None
        assert alert is None


class TestStartTimer:
    """Cover lines 110-143: timer start + timeout callback"""

    def test_timer_disabled(self):
        wd = StepWatchdog(WatchdogConfig(enabled=False))
        proc = MagicMock()
        wd.start_timer(proc, 0, "echo hi")
        assert wd._timer is None

    def test_timer_fires_and_kills(self):
        wd = StepWatchdog(WatchdogConfig(step_timeout=1))
        proc = MagicMock()
        proc.poll.return_value = None  # still running

        alert_received = []
        wd.config.on_alert = lambda a: alert_received.append(a)

        wd.start_timer(proc, 0, "slow command", timeout=1)
        assert wd._timer is not None

        # Wait for timeout to fire
        time.sleep(1.5)

        assert wd.was_killed is True
        assert len(wd.alerts) >= 1
        assert wd.alerts[-1].alert_type == "timeout"
        assert len(alert_received) == 1
        proc.terminate.assert_called()

    def test_cancel_timer(self):
        wd = StepWatchdog()
        proc = MagicMock()
        proc.poll.return_value = None
        wd.start_timer(proc, 0, "echo hi", timeout=60)
        wd.cancel_timer()
        assert wd._timer is None

    def test_timer_process_already_done(self):
        """Process finished before timeout → no kill"""
        wd = StepWatchdog(WatchdogConfig(step_timeout=1))
        proc = MagicMock()
        proc.poll.return_value = 0  # already done

        wd.start_timer(proc, 0, "fast", timeout=1)
        time.sleep(1.5)

        assert wd.was_killed is False
        proc.terminate.assert_not_called()


class TestFormatAlerts:
    """Cover lines 161-169: alert formatting"""

    def test_no_alerts(self):
        wd = StepWatchdog()
        result = wd.format_alerts()
        assert "无告警" in result

    def test_with_alerts(self):
        wd = StepWatchdog()
        wd.alerts.append(WatchdogAlert(
            alert_type="timeout", step_index=0, command="slow cmd",
            message="步骤 1 超时", elapsed_sec=60, threshold_sec=60,
            action_taken="killed",
        ))
        wd.alerts.append(WatchdogAlert(
            alert_type="disk_low", step_index=-1, command="",
            message="磁盘空间不足", action_taken="warned",
        ))
        result = wd.format_alerts()
        assert "2 条" in result
        assert "⏰" in result
        assert "💾" in result
        assert "killed" in result


class TestWasKilled:
    def test_initial_false(self):
        wd = StepWatchdog()
        assert wd.was_killed is False
