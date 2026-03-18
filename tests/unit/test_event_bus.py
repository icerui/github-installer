"""
test_event_bus.py - 事件总线测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from event_bus import (
    EventBus, Event, WebhookNotifier,
    EVT_INSTALL_STARTED, EVT_INSTALL_COMPLETED, EVT_INSTALL_FAILED,
    EVT_STEP_STARTED, EVT_STEP_COMPLETED, EVT_STEP_FAILED,
    EVT_AUDIT_WARNING, EVT_WATCHDOG_ALERT,
)


class TestEvent:
    """测试 Event 数据类"""

    def test_creation(self):
        evt = Event(event_type=EVT_INSTALL_STARTED, project="a/b")
        assert evt.event_type == EVT_INSTALL_STARTED
        assert evt.project == "a/b"
        assert evt.timestamp  # 自动填充

    def test_to_dict(self):
        evt = Event(event_type=EVT_INSTALL_COMPLETED, project="a/b",
                   data={"duration": 10.5})
        d = evt.to_dict()
        assert d["event_type"] == EVT_INSTALL_COMPLETED
        assert d["project"] == "a/b"
        assert d["data"]["duration"] == 10.5
        assert d["source"] == "gitinstall"

    def test_custom_timestamp(self):
        evt = Event(event_type="test", timestamp="2025-01-01T00:00:00Z")
        assert evt.timestamp == "2025-01-01T00:00:00Z"


class TestEventBus:
    """测试 EventBus"""

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe(EVT_INSTALL_STARTED, lambda e: received.append(e))
        bus.publish(Event(event_type=EVT_INSTALL_STARTED, project="test"))
        assert len(received) == 1
        assert received[0].project == "test"

    def test_subscribe_all(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e))
        bus.emit(EVT_INSTALL_STARTED, project="a")
        bus.emit(EVT_INSTALL_COMPLETED, project="b")
        assert len(received) == 2

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        cb = lambda e: received.append(e)
        bus.subscribe(EVT_INSTALL_STARTED, cb)
        bus.unsubscribe(EVT_INSTALL_STARTED, cb)
        bus.emit(EVT_INSTALL_STARTED)
        assert len(received) == 0

    def test_emit_convenience(self):
        bus = EventBus()
        received = []
        bus.subscribe(EVT_STEP_FAILED, lambda e: received.append(e))
        bus.emit(EVT_STEP_FAILED, project="a/b", step=3, error="timeout")
        assert len(received) == 1
        assert received[0].data["step"] == 3
        assert received[0].data["error"] == "timeout"

    def test_history(self):
        bus = EventBus()
        bus.emit("a")
        bus.emit("b")
        bus.emit("c")
        history = bus.get_history()
        assert len(history) == 3

    def test_history_filter(self):
        bus = EventBus()
        bus.emit("x", project="1")
        bus.emit("y", project="2")
        bus.emit("x", project="3")
        history = bus.get_history(event_type="x")
        assert len(history) == 2

    def test_history_limit(self):
        bus = EventBus()
        for i in range(100):
            bus.emit("test", project=str(i))
        history = bus.get_history(limit=10)
        assert len(history) == 10

    def test_clear_history(self):
        bus = EventBus()
        bus.emit("test")
        bus.clear_history()
        assert len(bus.get_history()) == 0

    def test_max_history(self):
        bus = EventBus()
        bus._max_history = 5
        for i in range(10):
            bus.emit("test")
        assert len(bus.get_history(limit=100)) == 5

    def test_listener_exception_doesnt_break(self):
        bus = EventBus()
        received = []
        bus.subscribe("test", lambda e: (_ for _ in ()).throw(ValueError("boom")))
        bus.subscribe("test", lambda e: received.append(e))
        bus.emit("test")
        # 第二个监听器不受第一个异常影响
        assert len(received) == 1


class TestWebhookNotifier:
    """测试 WebhookNotifier"""

    def test_disabled_by_default(self):
        wh = WebhookNotifier(url="", secret="")
        assert wh.enabled is False

    def test_enabled_with_url(self):
        wh = WebhookNotifier(url="https://hooks.example.com/test", secret="s")
        assert wh.enabled is True

    def test_format_slack_message(self):
        wh = WebhookNotifier()
        evt = Event(event_type=EVT_INSTALL_COMPLETED, project="a/b")
        msg = wh.format_slack_message(evt)
        assert "text" in msg
        assert "blocks" in msg
        assert "a/b" in msg["text"]
        assert "✅" in msg["text"]

    def test_format_slack_failed(self):
        wh = WebhookNotifier()
        evt = Event(event_type=EVT_INSTALL_FAILED, project="x/y")
        msg = wh.format_slack_message(evt)
        assert "❌" in msg["text"]

    def test_notify_disabled_noop(self):
        wh = WebhookNotifier(url="", secret="")
        # 不应抛出异常
        evt = Event(event_type="test")
        wh.notify(evt)
