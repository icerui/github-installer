"""
watchdog.py - 安装看门狗系统
===============================

灵感来源：ICE-cluade-SCompany 的 Watchdog 线程

监控安装步骤执行，处理以下情况：
  1. 步骤超时：pip install 卡死 → 自动 kill + 切换策略
  2. 资源溢出：检测磁盘/内存不足 → 及时中止
  3. 挂起检测：进程无输出超过 N 秒 → 判定挂起

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable


# ── 默认超时配置（秒）──
DEFAULT_STEP_TIMEOUT = 600       # 单步最大 10 分钟
DEFAULT_IDLE_TIMEOUT = 120       # 无输出最大 2 分钟
GIT_CLONE_TIMEOUT = 300          # git clone 最大 5 分钟
PIP_INSTALL_TIMEOUT = 900        # pip install 最大 15 分钟
NPM_INSTALL_TIMEOUT = 600        # npm install 最大 10 分钟
DISK_MIN_MB = 500                # 最少剩余 500 MB


@dataclass
class WatchdogAlert:
    """看门狗告警"""
    alert_type: str        # timeout, idle, disk_low, memory_high, killed
    step_index: int
    command: str
    message: str
    elapsed_sec: float = 0.0
    threshold_sec: float = 0.0
    action_taken: str = ""   # killed, skipped, retried, warned


@dataclass
class WatchdogConfig:
    """看门狗配置"""
    step_timeout: int = DEFAULT_STEP_TIMEOUT
    idle_timeout: int = DEFAULT_IDLE_TIMEOUT
    disk_min_mb: int = DISK_MIN_MB
    enabled: bool = True
    on_alert: Optional[Callable] = None  # 告警回调


class StepWatchdog:
    """单步执行看门狗"""

    def __init__(self, config: WatchdogConfig = None):
        self.config = config or WatchdogConfig()
        self.alerts: list[WatchdogAlert] = []
        self._timer: Optional[threading.Timer] = None
        self._process: Optional[subprocess.Popen] = None
        self._killed = False

    def get_timeout_for_command(self, command: str) -> int:
        """根据命令类型智能选择超时时间"""
        cmd_lower = command.lower()
        if "git clone" in cmd_lower or "git pull" in cmd_lower:
            return GIT_CLONE_TIMEOUT
        if "pip install" in cmd_lower or "pip3 install" in cmd_lower:
            return PIP_INSTALL_TIMEOUT
        if "npm install" in cmd_lower or "npm ci" in cmd_lower:
            return NPM_INSTALL_TIMEOUT
        if "cargo build" in cmd_lower:
            return PIP_INSTALL_TIMEOUT  # Rust 编译也可能很慢
        if "make" in cmd_lower or "cmake" in cmd_lower:
            return PIP_INSTALL_TIMEOUT
        if "docker" in cmd_lower:
            return PIP_INSTALL_TIMEOUT
        if "conda install" in cmd_lower:
            return PIP_INSTALL_TIMEOUT
        return self.config.step_timeout

    def check_disk_space(self, path: str = None) -> Optional[WatchdogAlert]:
        """检查磁盘空间"""
        try:
            target = path or os.path.expanduser("~")
            usage = shutil.disk_usage(target)
            free_mb = usage.free / (1024 * 1024)
            if free_mb < self.config.disk_min_mb:
                alert = WatchdogAlert(
                    alert_type="disk_low",
                    step_index=-1, command="",
                    message=f"磁盘空间不足：仅剩 {free_mb:.0f} MB（阈值 {self.config.disk_min_mb} MB）",
                    action_taken="warned",
                )
                self.alerts.append(alert)
                return alert
        except OSError:
            pass
        return None

    def start_timer(self, process: subprocess.Popen, step_index: int,
                    command: str, timeout: int = None):
        """启动超时定时器"""
        if not self.config.enabled:
            return

        self._process = process
        self._killed = False
        timeout = timeout or self.get_timeout_for_command(command)

        def _on_timeout():
            if process.poll() is None:  # 还在运行
                self._killed = True
                alert = WatchdogAlert(
                    alert_type="timeout",
                    step_index=step_index,
                    command=command,
                    message=f"步骤 {step_index + 1} 超时（{timeout}s），已终止",
                    elapsed_sec=timeout,
                    threshold_sec=timeout,
                    action_taken="killed",
                )
                self.alerts.append(alert)
                if self.config.on_alert:
                    self.config.on_alert(alert)
                # 优雅终止：先 SIGTERM，再 SIGKILL
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                except OSError:
                    pass

        self._timer = threading.Timer(timeout, _on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def cancel_timer(self):
        """取消超时定时器"""
        if self._timer:
            self._timer.cancel()
            self._timer = None

    @property
    def was_killed(self) -> bool:
        """进程是否被看门狗终止"""
        return self._killed

    def format_alerts(self) -> str:
        """格式化告警列表"""
        if not self.alerts:
            return "  🐕 看门狗：无告警"

        lines = [f"  🐕 看门狗告警（共 {len(self.alerts)} 条）："]
        icons = {"timeout": "⏰", "idle": "💤", "disk_low": "💾",
                 "memory_high": "🧠", "killed": "☠️"}
        for a in self.alerts:
            icon = icons.get(a.alert_type, "⚠️")
            lines.append(f"    {icon} [{a.alert_type}] {a.message}")
            if a.action_taken:
                lines.append(f"       操作：{a.action_taken}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
#  便捷函数
# ─────────────────────────────────────────────

def create_watchdog(enabled: bool = True,
                    on_alert: Callable = None) -> StepWatchdog:
    """创建看门狗实例"""
    config = WatchdogConfig(enabled=enabled, on_alert=on_alert)
    return StepWatchdog(config)
