"""
event_bus.py - 安装事件总线 + Webhook 通知
============================================

灵感来源：ICE-OEM 的告警派发队列

安装过程中发布事件，支持：
  1. 事件监听器：内部模块订阅事件
  2. Webhook 推送：安装完成/失败时推送到外部（Slack/Discord/钉钉/企微）
  3. 事件历史：记录所有事件供回溯

事件类型：
  install.started, install.step_started, install.step_completed,
  install.step_failed, install.completed, install.failed,
  audit.warning, license.issue, watchdog.alert

配置：
  GITINSTALL_WEBHOOK_URL=https://hooks.slack.com/services/xxx
  GITINSTALL_WEBHOOK_SECRET=xxx

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional


# ── 事件类型常量 ──
EVT_INSTALL_STARTED = "install.started"
EVT_STEP_STARTED = "install.step_started"
EVT_STEP_COMPLETED = "install.step_completed"
EVT_STEP_FAILED = "install.step_failed"
EVT_INSTALL_COMPLETED = "install.completed"
EVT_INSTALL_FAILED = "install.failed"
EVT_AUDIT_WARNING = "audit.warning"
EVT_LICENSE_ISSUE = "license.issue"
EVT_WATCHDOG_ALERT = "watchdog.alert"
EVT_CHECKPOINT_SAVED = "checkpoint.saved"
EVT_RESUME_STARTED = "resume.started"


@dataclass
class Event:
    """安装事件"""
    event_type: str
    timestamp: str = ""
    project: str = ""
    data: dict = field(default_factory=dict)
    source: str = "gitinstall"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "project": self.project,
            "data": self.data,
            "source": self.source,
        }


# ── 事件监听器类型 ──
EventListener = Callable[[Event], None]


class EventBus:
    """事件总线"""

    def __init__(self):
        self._listeners: dict[str, list[EventListener]] = {}
        self._history: list[Event] = []
        self._lock = threading.Lock()
        self._max_history = 1000

    def subscribe(self, event_type: str, listener: EventListener):
        """订阅事件"""
        with self._lock:
            self._listeners.setdefault(event_type, []).append(listener)

    def subscribe_all(self, listener: EventListener):
        """订阅所有事件"""
        self.subscribe("*", listener)

    def unsubscribe(self, event_type: str, listener: EventListener):
        """取消订阅"""
        with self._lock:
            listeners = self._listeners.get(event_type, [])
            if listener in listeners:
                listeners.remove(listener)

    def publish(self, event: Event):
        """发布事件"""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            # 通知特定类型的监听器
            specific = list(self._listeners.get(event.event_type, []))
            # 通知通配符监听器
            wildcard = list(self._listeners.get("*", []))

        # 在锁外执行回调
        for listener in specific + wildcard:
            try:
                listener(event)
            except Exception:
                pass

    def emit(self, event_type: str, project: str = "", **data):
        """便捷发布事件"""
        self.publish(Event(event_type=event_type, project=project, data=data))

    def get_history(self, event_type: str = None,
                    limit: int = 50) -> list[Event]:
        """获取事件历史"""
        with self._lock:
            events = list(self._history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-limit:]

    def clear_history(self):
        """清空历史"""
        with self._lock:
            self._history.clear()


# ─────────────────────────────────────────────
#  Webhook 推送器
# ─────────────────────────────────────────────

class WebhookNotifier:
    """Webhook 通知器"""

    def __init__(self, url: str = None, secret: str = None):
        self.url = url or os.environ.get("GITINSTALL_WEBHOOK_URL", "")
        self.secret = secret or os.environ.get("GITINSTALL_WEBHOOK_SECRET", "")
        self._send_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def notify(self, event: Event):
        """发送 Webhook 通知（异步）"""
        if not self.enabled:
            return
        thread = threading.Thread(target=self._send, args=(event,), daemon=True)
        thread.start()

    def _send(self, event: Event):
        """实际发送 Webhook"""
        with self._send_lock:
            try:
                payload = json.dumps(event.to_dict(), ensure_ascii=False).encode()
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "gitinstall-webhook/1.0",
                }

                # HMAC 签名（防篡改）
                if self.secret:
                    sig = hmac.new(
                        self.secret.encode(), payload, hashlib.sha256
                    ).hexdigest()
                    headers["X-Gitinstall-Signature"] = f"sha256={sig}"

                req = urllib.request.Request(
                    self.url, data=payload, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except (urllib.error.URLError, OSError):
                pass  # Webhook 失败不影响安装流程

    def format_slack_message(self, event: Event) -> dict:
        """格式化为 Slack Block 消息"""
        icons = {
            EVT_INSTALL_COMPLETED: "✅",
            EVT_INSTALL_FAILED: "❌",
            EVT_AUDIT_WARNING: "🚨",
            EVT_WATCHDOG_ALERT: "🐕",
        }
        icon = icons.get(event.event_type, "📦")

        return {
            "text": f"{icon} [{event.event_type}] {event.project}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{icon} {event.event_type}*\n"
                            f"项目：`{event.project}`\n"
                            f"时间：{event.timestamp}"
                        ),
                    },
                }
            ],
        }


# ─────────────────────────────────────────────
#  全局实例
# ─────────────────────────────────────────────

_bus: Optional[EventBus] = None
_webhook: Optional[WebhookNotifier] = None


def get_event_bus() -> EventBus:
    """获取全局事件总线"""
    global _bus
    if _bus is None:
        _bus = EventBus()
        # 自动注册 Webhook 监听器
        wh = get_webhook_notifier()
        if wh.enabled:
            # 只推送关键事件
            for evt in (EVT_INSTALL_COMPLETED, EVT_INSTALL_FAILED,
                        EVT_AUDIT_WARNING, EVT_WATCHDOG_ALERT):
                _bus.subscribe(evt, wh.notify)
    return _bus


def get_webhook_notifier() -> WebhookNotifier:
    """获取全局 Webhook 通知器"""
    global _webhook
    if _webhook is None:
        _webhook = WebhookNotifier()
    return _webhook


def emit(event_type: str, project: str = "", **data):
    """便捷发布事件（全局）"""
    get_event_bus().emit(event_type, project=project, **data)
