"""tests/unit/test_coverage_web.py — web.py 覆盖率补全测试

覆盖目标：web.py 130 行未覆盖 → <60 行
"""
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))


# ── 辅助：构建 mock handler ──────────────────────

def _make_handler(method="GET", path="/", body=b"", headers=None, client_ip="127.0.0.1"):
    """创建模拟的 _Handler 实例（绕过 BaseHTTPRequestHandler.__init__）"""
    from web import _Handler

    handler = object.__new__(_Handler)
    handler.path = path
    handler.command = method
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.client_address = (client_ip, 54321)

    # wfile — 用 BytesIO 接收响应
    handler.wfile = BytesIO()

    # rfile — 提供 body 读取
    handler.rfile = BytesIO(body)

    # headers
    from http.client import HTTPMessage
    msg = HTTPMessage()
    if headers:
        for k, v in headers.items():
            msg[k] = v
    if body:
        msg["Content-Length"] = str(len(body))
    handler.headers = msg

    # 标记 headers 未发送
    handler._headers_buffer = []
    handler.responses = {}

    return handler


def _json_response(handler) -> dict:
    """从 wfile 提取 JSON 响应体"""
    raw = handler.wfile.getvalue()
    # 找到 \r\n\r\n 后的 body
    idx = raw.find(b"\r\n\r\n")
    if idx >= 0:
        body = raw[idx + 4:]
    else:
        body = raw
    return json.loads(body.decode("utf-8"))


# ═══════════════════════════════════════════════
#  1. _cache_plan / _pop_plan
# ═══════════════════════════════════════════════

class TestPlanCache:
    def test_cache_plan_and_pop(self):
        from web import _cache_plan, _pop_plan, _plan_cache
        _plan_cache.clear()
        _cache_plan("test-id-1", {"steps": [{"command": "echo hi"}]})
        result = _pop_plan("test-id-1")
        assert result is not None
        assert result["steps"][0]["command"] == "echo hi"
        # pop 后应该为空
        assert _pop_plan("test-id-1") is None

    def test_cache_plan_expiry(self):
        from web import _cache_plan, _pop_plan, _plan_cache, _PLAN_TTL
        _plan_cache.clear()
        _cache_plan("expired-1", {"steps": []})
        # 手动设回timestamp 使其过期
        _plan_cache["expired-1"] = (time.time() - _PLAN_TTL - 10, {"steps": []})
        assert _pop_plan("expired-1") is None

    def test_cache_plan_eviction(self):
        from web import _cache_plan, _plan_cache
        _plan_cache.clear()
        for i in range(25):
            _cache_plan(f"id-{i}", {"i": i})
        assert len(_plan_cache) <= 21  # max 20 + 1 at a time


# ═══════════════════════════════════════════════
#  2. Handler — _json / _read_body / log_message
# ═══════════════════════════════════════════════

class TestHandlerHelpers:
    def test_json_response(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._json({"status": "ok"}, 200)
        handler.send_response.assert_called_once_with(200)
        written = handler.wfile.getvalue()
        assert b'"status": "ok"' in written

    def test_read_body(self):
        body = b'{"key": "value"}'
        handler = _make_handler(body=body, headers={"Content-Length": str(len(body))})
        result = handler._read_body()
        assert json.loads(result) == {"key": "value"}

    def test_read_body_too_large(self):
        handler = _make_handler(headers={"Content-Length": "999999999"})
        with pytest.raises(ValueError, match="请求体过大"):
            handler._read_body()

    def test_log_message_suppressed(self):
        handler = _make_handler()
        # 调用 log_message 不应抛异常
        handler.log_message("test %s", "arg")


# ═══════════════════════════════════════════════
#  3. API 端点测试
# ═══════════════════════════════════════════════

class TestApiStats:
    @patch("web._db")
    def test_stats_success(self, mock_db):
        mock_db.is_admin.return_value = True
        mock_db.get_stats.return_value = {"total_installs": 42}
        mock_db.get_recent_installs.return_value = []

        handler = _make_handler(headers={"Authorization": "Bearer admin-token"})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._api_stats()

        written = handler.wfile.getvalue()
        data = json.loads(written)
        assert data["status"] == "ok"
        assert data["total_installs"] == 42

    @patch("web._db")
    def test_stats_error(self, mock_db):
        mock_db.is_admin.return_value = True
        mock_db.get_stats.side_effect = RuntimeError("db error")

        handler = _make_handler(headers={"Authorization": "Bearer admin-token"})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._api_stats()

        written = handler.wfile.getvalue()
        data = json.loads(written)
        assert data["status"] == "error"


class TestApiAdminSet:
    @patch("web._db")
    def test_admin_set_via_secret(self, mock_db):
        body = json.dumps({"user_id": "user-123", "admin_secret": "s3cret"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        with patch.dict(os.environ, {"GITINSTALL_ADMIN_SECRET": "s3cret"}):
            handler._api_admin_set()

        mock_db.set_admin.assert_called_once_with("user-123", True)
        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"


class TestApiFlags:
    def test_flags_endpoint(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        with patch("web.get_all_status", return_value={"flag1": True}) if False else \
             patch.dict(sys.modules, {}):
            handler._api_flags()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert "flags" in data


class TestApiRegistry:
    def test_registry_endpoint(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        mock_registry = MagicMock()
        mock_registry.list_all.return_value = ["a", "b"]
        mock_info = MagicMock()
        mock_info.info.name = "test_installer"
        mock_registry.list_available.return_value = [mock_info]
        mock_registry.to_dict.return_value = {}

        with patch("web.InstallerRegistry", return_value=mock_registry) if False else \
             patch.dict(sys.modules, {}):
            handler._api_registry()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"


class TestApiEvents:
    def test_events_endpoint(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        mock_bus = MagicMock()
        mock_event = MagicMock()
        mock_event.to_dict.return_value = {"type": "install", "project": "test"}
        mock_bus.get_history.return_value = [mock_event]

        with patch("event_bus.get_event_bus", return_value=mock_bus):
            handler._api_events()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert len(data["events"]) == 1


class TestApiKbStats:
    def test_kb_stats_endpoint(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        mock_kb = MagicMock()
        mock_kb.get_stats.return_value = {"total": 10, "success_rate": 0.9}

        with patch("knowledge_base.KnowledgeBase", return_value=mock_kb):
            handler._api_kb_stats()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert data["total"] == 10


class TestApiLogin:
    @patch("web._db")
    def test_login_success(self, mock_db):
        mock_db.login_user.return_value = {"status": "ok", "token": "abc123"}
        body = json.dumps({"email": "user@test.com", "password": "pass123"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._api_login()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        handler.send_response.assert_called_with(200)

    @patch("web._db")
    def test_login_failure(self, mock_db):
        mock_db.login_user.return_value = {"status": "error", "message": "密码错误"}
        body = json.dumps({"email": "user@test.com", "password": "wrong"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._api_login()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "error"
        handler.send_response.assert_called_with(401)


class TestApiResetPassword:
    @patch("web._db")
    def test_reset_password_success(self, mock_db):
        mock_db.reset_password.return_value = {"status": "ok"}
        body = json.dumps({"token": "reset-tok", "password": "newPass123!"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._api_reset_password()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"


class TestApiAudit:
    def test_audit_no_deps(self):
        handler = _make_handler(path="/api/audit?project=owner/repo")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        mock_info = MagicMock()
        mock_info.dependency_files = {}

        with patch("web.fetch_project", return_value=mock_info) if False else \
             patch.dict(sys.modules, {}):
            # 直接 mock _api_audit 中的导入
            with patch("fetcher.fetch_project", return_value=mock_info):
                handler._api_audit()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert data.get("results") == [] or "message" in data


class TestApiLicense:
    def test_license_not_declared(self):
        handler = _make_handler(path="/api/license?project=owner/repo")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        with patch("license_check.fetch_license_from_github", return_value=(None, None)):
            handler._api_license()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert data.get("risk") == "warning"


class TestApiUninstall:
    @patch("web._db")
    def test_uninstall_dry_run(self, mock_db):
        body = json.dumps({"project": "owner/repo", "confirm": False}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.install_dir = "/tmp/test"
        mock_tracker.get_project.return_value = mock_proj
        mock_plan = MagicMock()
        mock_uninstall_dict = {"dirs": [], "files": []}

        with patch("auto_update.InstallTracker", return_value=mock_tracker), \
             patch("uninstaller.plan_uninstall", return_value=mock_plan), \
             patch("uninstaller.uninstall_to_dict", return_value=mock_uninstall_dict), \
             patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")):
            handler._api_uninstall()

        data = json.loads(handler.wfile.getvalue())
        assert data["status"] == "ok"
        assert data["action"] == "dry_run"


# ═══════════════════════════════════════════════
#  4. _serve_ui event recording
# ═══════════════════════════════════════════════

class TestServeUI:
    @patch("web._db")
    def test_serve_ui_records_event(self, mock_db):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()

        html_path = Path(__file__).resolve().parent.parent.parent / "tools" / "web_ui.html"
        if html_path.exists():
            handler._serve_ui()
            mock_db.record_event.assert_called_once()
        else:
            # web_ui.html 不存在时应回退到 send_error
            handler.send_error = MagicMock()
            handler._serve_ui()
            handler.send_error.assert_called()


# ═══════════════════════════════════════════════
#  5. do_GET / do_POST routing
# ═══════════════════════════════════════════════

class TestRouting:
    def test_do_get_install_rate_limited(self):
        """rate-limited install endpoint"""
        handler = _make_handler(path="/api/install?project=test/repo")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._rate_limited = MagicMock(return_value=True)

        handler.do_GET()
        handler._rate_limited.assert_called()

    def test_do_get_search_rate_limited(self):
        """rate-limited search endpoint"""
        handler = _make_handler(path="/api/search?q=test")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._add_security_headers = MagicMock()
        handler._rate_limited = MagicMock(return_value=True)

        handler.do_GET()
        handler._rate_limited.assert_called()


# ═══════════════════════════════════════════════
#  6. start_server (smoke test)
# ═══════════════════════════════════════════════

class TestStartServer:
    @patch("web._ThreadedServer")
    @patch("web._db")
    def test_start_server_port_fallback(self, mock_db, mock_server_cls):
        """端口被占用时应尝试下一个"""
        from web import start_server

        call_count = [0]
        def side_effect(addr, handler):
            call_count[0] += 1
            if call_count[0] < 3:
                raise OSError("port in use")
            mock_srv = MagicMock()
            mock_srv.serve_forever.side_effect = KeyboardInterrupt()
            return mock_srv

        mock_server_cls.side_effect = side_effect

        with patch("builtins.print"):
            start_server(port=8080, open_browser=False)

        assert call_count[0] == 3  # 前两次失败，第三次成功
