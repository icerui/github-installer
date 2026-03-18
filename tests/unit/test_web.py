"""
test_web.py - Web UI 服务器测试
================================
"""

from __future__ import annotations

import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from web import (
    _check_rate_limit,
    _make_plan_id,
    _cache_plan,
    _pop_plan,
    _plan_cache,
    _rate_limits,
    _RATE_RULES,
    MAX_BODY_SIZE,
    MAX_CONCURRENT_INSTALLS_PER_IP,
    MAX_CONCURRENT_PLANS,
    _Handler,
    _ThreadedServer,
)


# ─────────────────────────────────────────────
#  Rate Limiting
# ─────────────────────────────────────────────

class TestRateLimit:
    def setup_method(self):
        _rate_limits.clear()

    def test_no_rule_not_limited(self):
        assert _check_rate_limit("1.2.3.4", "/api/unknown") is False

    def test_under_limit(self):
        for _ in range(9):
            assert _check_rate_limit("1.2.3.4", "/api/login") is False

    def test_over_limit(self):
        for _ in range(10):
            _check_rate_limit("1.2.3.4", "/api/login")
        assert _check_rate_limit("1.2.3.4", "/api/login") is True

    def test_different_ips_independent(self):
        for _ in range(10):
            _check_rate_limit("1.2.3.4", "/api/login")
        # Different IP should not be limited
        assert _check_rate_limit("5.6.7.8", "/api/login") is False

    def test_register_limit(self):
        for _ in range(5):
            _check_rate_limit("1.2.3.4", "/api/register")
        assert _check_rate_limit("1.2.3.4", "/api/register") is True


# ─────────────────────────────────────────────
#  Plan Cache
# ─────────────────────────────────────────────

class TestPlanCache:
    def setup_method(self):
        _plan_cache.clear()

    def test_make_plan_id(self):
        pid = _make_plan_id()
        assert isinstance(pid, str)
        assert len(pid) > 20  # 256-bit token

    def test_cache_and_pop(self):
        pid = _make_plan_id()
        result = {"status": "ok", "plan": {"steps": []}}
        _cache_plan(pid, result)
        popped = _pop_plan(pid)
        assert popped == result
        # Second pop should return None (consumed)
        assert _pop_plan(pid) is None

    def test_expired_plan(self):
        pid = _make_plan_id()
        _plan_cache[pid] = (time.time() - 600, {"status": "ok"})  # 10 min ago
        assert _pop_plan(pid) is None

    def test_cache_eviction(self):
        for i in range(25):
            _cache_plan(f"plan_{i}", {"i": i})
        assert len(_plan_cache) <= 21  # max 20 + 1 being added

    def test_unique_ids(self):
        ids = {_make_plan_id() for _ in range(100)}
        assert len(ids) == 100


# ─────────────────────────────────────────────
#  Helper: 创建模拟 Handler
# ─────────────────────────────────────────────

def _make_handler(method="GET", path="/", body=b"", headers=None):
    """Create a mock _Handler for testing without real socket."""
    handler = _Handler.__new__(_Handler)
    handler.path = path
    handler.command = method
    handler.client_address = ("127.0.0.1", 12345)
    # Auto-inject Bearer token for POST requests to bypass CSRF checks in tests
    effective_headers = dict(headers or {})
    if method == "POST" and "Authorization" not in effective_headers:
        effective_headers["Authorization"] = "Bearer test-token"
    handler.headers = MagicMock()
    handler.headers.get = lambda k, d="": effective_headers.get(k, d)
    handler.wfile = BytesIO()
    handler.rfile = BytesIO(body)

    # Mock response methods
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()

    return handler


# ─────────────────────────────────────────────
#  Handler 工具方法
# ─────────────────────────────────────────────

class TestHandlerUtils:
    def test_json_response(self):
        handler = _make_handler()
        handler._json({"status": "ok"})
        handler.send_response.assert_called_with(200)
        written = handler.wfile.getvalue()
        data = json.loads(written)
        assert data["status"] == "ok"

    def test_json_error_response(self):
        handler = _make_handler()
        handler._json({"status": "error"}, 400)
        handler.send_response.assert_called_with(400)

    def test_security_headers(self):
        handler = _make_handler()
        handler._add_security_headers()
        header_names = [call[0][0] for call in handler.send_header.call_args_list]
        assert "X-Content-Type-Options" in header_names
        assert "X-Frame-Options" in header_names
        assert "X-XSS-Protection" in header_names

    def test_client_ip_direct(self):
        handler = _make_handler()
        assert handler._client_ip() == "127.0.0.1"

    def test_client_ip_forwarded(self):
        handler = _make_handler(headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"})
        ip = handler._client_ip()
        assert ip == "10.0.0.1"

    def test_read_body(self):
        body = b'{"key": "value"}'
        handler = _make_handler(body=body, headers={"Content-Length": str(len(body))})
        result = handler._read_body()
        assert json.loads(result)["key"] == "value"

    def test_read_body_too_large(self):
        handler = _make_handler(headers={"Content-Length": str(MAX_BODY_SIZE + 1)})
        with pytest.raises(ValueError, match="请求体过大"):
            handler._read_body()

    def test_read_body_empty(self):
        handler = _make_handler(headers={"Content-Length": "0"})
        result = handler._read_body()
        assert result == ""


# ─────────────────────────────────────────────
#  API 端点 (mocked)
# ─────────────────────────────────────────────

class TestAPIDetect:
    def test_detect(self):
        handler = _make_handler(path="/api/detect")
        fake_env = {
            "os": {"type": "macos", "arch": "arm64"},
            "gpu": {"type": "apple_mps"},
            "runtimes": {"python": {"version": "3.13"}, "node": None},
        }
        with patch("detector.EnvironmentDetector") as mock:
            mock.return_value.detect.return_value = fake_env
            handler._api_detect()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert written["env"]["os"] == "macos"
            assert written["env"]["gpu"] == "apple_mps"


class TestAPIPlan:
    def setup_method(self):
        _plan_cache.clear()

    def test_plan_empty_project(self):
        body = json.dumps({"project": ""}).encode()
        handler = _make_handler(method="POST", path="/api/plan", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._do_api_plan()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"

    def test_plan_invalid_json(self):
        body = b"not json"
        handler = _make_handler(method="POST", path="/api/plan", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._do_api_plan()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"
        assert "JSON" in written["message"]

    def test_plan_success(self):
        body = json.dumps({"project": "user/repo"}).encode()
        handler = _make_handler(method="POST", path="/api/plan", body=body,
                                headers={"Content-Length": str(len(body))})
        fake_result = {
            "status": "ok",
            "plan": {"steps": [{"command": "pip install x", "description": "Install"}]},
            "project": "user/repo",
            "confidence": "high",
            "strategy": "known_project",
        }
        with patch("main.cmd_plan", return_value=fake_result), \
             patch("web._db") as mock_db:
            mock_db.record_event = MagicMock()
            mock_db.save_plan_history = MagicMock()
            handler._do_api_plan()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert "plan_id" in written
            # Internal fields stripped
            assert "strategy" not in written
            assert "confidence" not in written


class TestAPIUser:
    def test_user_anonymous(self):
        handler = _make_handler(path="/api/user", headers={"Authorization": ""})
        with patch("web._db") as mock_db:
            mock_db.validate_token.return_value = None
            mock_db.check_quota.return_value = {"remaining": 10}
            handler._api_user()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert "user" not in written

    def test_user_authenticated(self):
        handler = _make_handler(path="/api/user",
                                headers={"Authorization": "Bearer test-token"})
        with patch("web._db") as mock_db:
            mock_db.validate_token.return_value = {"id": 1, "username": "test", "tier": "free"}
            mock_db.check_quota.return_value = {"remaining": 50}
            handler._api_user()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert written["user"]["username"] == "test"


class TestAPIRegister:
    def test_register_success(self):
        body = json.dumps({"username": "test", "email": "test@example.com", "password": "pass123"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        with patch("web._db") as mock_db:
            mock_db.register_user.return_value = {"status": "ok", "user_id": 1}
            handler._api_register()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"

    def test_register_invalid_json(self):
        body = b"not json"
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._api_register()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"


class TestAPILogin:
    def test_login_success(self):
        body = json.dumps({"email": "test@example.com", "password": "pass123"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        with patch("web._db") as mock_db:
            mock_db.login_user.return_value = {"status": "ok", "token": "abc123"}
            handler._api_login()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"


class TestAPIForgotPassword:
    def test_forgot_password(self):
        body = json.dumps({"email": "test@example.com"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        with patch("web._db") as mock_db:
            mock_db.create_reset_token.return_value = {"token": "reset-tok", "username": "test"}
            handler._api_forgot_password()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"

    def test_forgot_password_empty_email(self):
        body = json.dumps({"email": ""}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._api_forgot_password()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"


class TestAPIResetPassword:
    def test_reset_password(self):
        body = json.dumps({"token": "tok", "password": "newpass"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        with patch("web._db") as mock_db:
            mock_db.reset_password.return_value = {"status": "ok"}
            handler._api_reset_password()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"

    def test_reset_password_missing_params(self):
        body = json.dumps({"token": "", "password": ""}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._api_reset_password()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"


class TestAPIAdminSet:
    def test_admin_set_with_secret(self):
        body = json.dumps({"user_id": 1, "admin_secret": "mysecret"}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        with patch.dict(os.environ, {"GITINSTALL_ADMIN_SECRET": "mysecret"}), \
             patch("web._db") as mock_db:
            mock_db.set_admin = MagicMock()
            handler._api_admin_set()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"

    def test_admin_set_unauthorized(self):
        body = json.dumps({"user_id": 1}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body)),
                                          "Authorization": ""})
        with patch.dict(os.environ, {"GITINSTALL_ADMIN_SECRET": ""}, clear=False), \
             patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            handler._api_admin_set()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "error"

    def test_admin_set_missing_user_id(self):
        body = json.dumps({}).encode()
        handler = _make_handler(method="POST", body=body,
                                headers={"Content-Length": str(len(body))})
        handler._api_admin_set()
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"


class TestAPIStats:
    def test_stats_admin(self):
        handler = _make_handler(path="/api/stats",
                                headers={"Authorization": "Bearer admin-tok"})
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = True
            mock_db.get_stats.return_value = {"total_installs": 42}
            mock_db.get_recent_installs.return_value = []
            handler._api_stats()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert written["total_installs"] == 42

    def test_stats_unauthorized(self):
        handler = _make_handler(path="/api/stats",
                                headers={"Authorization": ""})
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            handler._api_stats()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "error"


# ─────────────────────────────────────────────
#  路由
# ─────────────────────────────────────────────

class TestRouting:
    def test_get_routes(self):
        handler = _make_handler(path="/api/detect")
        with patch.object(handler, "_api_detect") as mock:
            handler.do_GET()
            mock.assert_called_once()

    def test_post_plan_route(self):
        handler = _make_handler(method="POST", path="/api/plan")
        with patch.object(handler, "_api_plan") as mock, \
             patch.object(handler, "_rate_limited", return_value=False):
            handler.do_POST()
            mock.assert_called_once()

    def test_get_404(self):
        handler = _make_handler(path="/api/nonexistent")
        handler.do_GET()
        handler.send_error.assert_called_with(404)

    def test_post_404(self):
        handler = _make_handler(method="POST", path="/api/nonexistent")
        with patch.object(handler, "_rate_limited", return_value=False):
            handler.do_POST()
            handler.send_error.assert_called_with(404)


# ─────────────────────────────────────────────
#  ThreadedServer
# ─────────────────────────────────────────────

class TestThreadedServer:
    def test_daemon_threads(self):
        assert _ThreadedServer.daemon_threads is True
        assert _ThreadedServer.allow_reuse_address is True


# ─────────────────────────────────────────────
#  Rate Rules 配置
# ─────────────────────────────────────────────

class TestRateRules:
    def test_critical_endpoints_have_rules(self):
        assert "/api/login" in _RATE_RULES
        assert "/api/register" in _RATE_RULES
        assert "/api/forgot-password" in _RATE_RULES
        assert "/api/plan" in _RATE_RULES
        assert "/api/install" in _RATE_RULES

    def test_register_stricter_than_login(self):
        _, reg_max = _RATE_RULES["/api/register"]
        _, login_max = _RATE_RULES["/api/login"]
        assert reg_max < login_max


# ─────────────────────────────────────────────
#  Serve UI / Admin
# ─────────────────────────────────────────────

class TestServeUI:
    def test_serve_ui(self, tmp_path):
        html = b"<html><body>Hello</body></html>"
        html_path = tmp_path / "web_ui.html"
        html_path.write_bytes(html)
        handler = _make_handler(path="/")
        with patch("web._THIS_DIR", tmp_path), \
             patch("web._db") as mock_db:
            mock_db.record_event = MagicMock()
            handler._serve_ui()
            handler.send_response.assert_called_with(200)
            written = handler.wfile.getvalue()
            assert b"Hello" in written

    def test_serve_ui_missing(self, tmp_path):
        handler = _make_handler(path="/")
        with patch("web._THIS_DIR", tmp_path):
            handler._serve_ui()
            handler.send_error.assert_called()

    def test_serve_admin(self, tmp_path):
        html = b"<html>Admin</html>"
        (tmp_path / "admin.html").write_bytes(html)
        handler = _make_handler(path="/admin")
        with patch("web._THIS_DIR", tmp_path):
            handler._serve_admin()
            handler.send_response.assert_called_with(200)

    def test_serve_admin_missing(self, tmp_path):
        handler = _make_handler(path="/admin")
        with patch("web._THIS_DIR", tmp_path):
            handler._serve_admin()
            handler.send_error.assert_called()


# ─────────────────────────────────────────────
#  API: Trending
# ─────────────────────────────────────────────

class TestAPITrending:
    def test_trending(self):
        handler = _make_handler(path="/api/trending")
        with patch("web._db") as mock_db, \
             patch("trending.get_trending", return_value=[{"repo": "a/b"}]):
            mock_db.record_event = MagicMock()
            handler._api_trending()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert len(written["projects"]) == 1

    def test_trending_refresh_admin(self):
        handler = _make_handler(path="/api/trending/refresh",
                                headers={"Authorization": "Bearer admin-tok"})
        with patch("web._db") as mock_db, \
             patch("trending.get_trending", return_value=[]):
            mock_db.is_admin.return_value = True
            handler._api_trending_refresh()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert written["refreshed"] is True

    def test_trending_refresh_unauthorized(self):
        handler = _make_handler(path="/api/trending/refresh",
                                headers={"Authorization": ""})
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            handler._api_trending_refresh()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "error"


# ─────────────────────────────────────────────
#  API: Search
# ─────────────────────────────────────────────

class TestAPISearch:
    def test_search_empty_query(self):
        handler = _make_handler(path="/api/search?q=")
        handler._api_search({"q": [""]})
        written = json.loads(handler.wfile.getvalue())
        assert written["status"] == "error"

    def test_search_success(self):
        handler = _make_handler(path="/api/search?q=flask")
        search_data = {
            "items": [
                {"full_name": "pallets/flask", "name": "flask",
                 "description": "A micro framework", "stargazers_count": 60000,
                 "language": "Python"}
            ],
            "total_count": 1,
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(search_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("web.urllib.request.urlopen", return_value=mock_resp), \
             patch("web._db") as mock_db:
            mock_db.record_event = MagicMock()
            handler._api_search({"q": ["flask"]})
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "ok"
            assert written["results"][0]["repo"] == "pallets/flask"

    def test_search_failure(self):
        handler = _make_handler(path="/api/search?q=test")
        with patch("web.urllib.request.urlopen", side_effect=Exception("network")):
            handler._api_search({"q": ["test"]})
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "error"


# ─────────────────────────────────────────────
#  API: Install Stream (validation only)
# ─────────────────────────────────────────────

class TestAPIInstallStream:
    def test_install_missing_params(self):
        handler = _make_handler(path="/api/install")
        handler._api_install_stream({"plan_id": [""], "project": [""]})
        handler.send_error.assert_called_with(400, "Missing plan_id or project")

    def test_install_concurrent_limit(self):
        from web import _active_installs, _active_installs_lock
        handler = _make_handler(path="/api/install")
        ip = handler._client_ip()
        with _active_installs_lock:
            _active_installs[ip] = MAX_CONCURRENT_INSTALLS_PER_IP
        try:
            handler._api_install_stream({"plan_id": ["pid"], "project": ["p"]})
            handler.send_response.assert_called_with(429)
        finally:
            with _active_installs_lock:
                _active_installs[ip] = 0

    def test_install_expired_plan_no_project(self):
        handler = _make_handler(path="/api/install")
        _plan_cache.clear()
        handler._do_install_stream({}, "nonexistent-plan", "", "")
        written = handler.wfile.getvalue().decode()
        assert "过期" in written

    def test_install_empty_steps(self):
        handler = _make_handler(path="/api/install")
        pid = _make_plan_id()
        _cache_plan(pid, {"status": "ok", "plan": {"steps": []}, "project": "test"})
        handler._do_install_stream({}, pid, "test", "")
        written = handler.wfile.getvalue().decode()
        assert "无安装步骤" in written or "未能生成" in written


# ─────────────────────────────────────────────
#  Concurrent Plan Limit
# ─────────────────────────────────────────────

class TestConcurrentPlanLimit:
    def test_plan_concurrency_limit(self):
        import web
        orig = web._active_plans
        web._active_plans = MAX_CONCURRENT_PLANS
        handler = _make_handler(method="POST", path="/api/plan")
        try:
            handler._api_plan()
            written = json.loads(handler.wfile.getvalue())
            assert written["status"] == "error"
            assert "繁忙" in written["message"]
        finally:
            web._active_plans = orig


# ─────────────────────────────────────────────
#  Rate Limiting HTTP Response
# ─────────────────────────────────────────────

class TestRateLimitedResponse:
    def setup_method(self):
        _rate_limits.clear()

    def test_rate_limited_returns_429(self):
        # Exhaust login rate limit
        for _ in range(10):
            _check_rate_limit("10.0.0.1", "/api/login")
        handler = _make_handler(method="POST", path="/api/login")
        handler.client_address = ("10.0.0.1", 12345)
        result = handler._rate_limited("/api/login")
        assert result is True
        handler.send_response.assert_called_with(429)

    def test_not_rate_limited(self):
        handler = _make_handler(method="POST", path="/api/login")
        result = handler._rate_limited("/api/login")
        assert result is False


# ─────────────────────────────────────────────
#  do_GET / do_POST integration
# ─────────────────────────────────────────────

class TestDoGetPost:
    def setup_method(self):
        _rate_limits.clear()

    def test_do_get_search_rate_limited(self):
        # Exhaust /api/search rate limit
        for _ in range(30):
            _check_rate_limit("127.0.0.1", "/api/search")
        handler = _make_handler(path="/api/search?q=test")
        handler.do_GET()
        handler.send_response.assert_called_with(429)

    def test_do_get_install_with_params(self):
        handler = _make_handler(path="/api/install?plan_id=x&project=y")
        with patch.object(handler, "_api_install_stream") as mock:
            handler.do_GET()
            mock.assert_called_once()

    def test_do_post_register(self):
        handler = _make_handler(method="POST", path="/api/register")
        with patch.object(handler, "_api_register") as mock, \
             patch.object(handler, "_rate_limited", return_value=False):
            handler.do_POST()
            mock.assert_called_once()

    def test_do_post_login(self):
        handler = _make_handler(method="POST", path="/api/login")
        with patch.object(handler, "_api_login") as mock, \
             patch.object(handler, "_rate_limited", return_value=False):
            handler.do_POST()
            mock.assert_called_once()


# ─────────────────────────────────────────────
#  Install Stream: security checks
# ─────────────────────────────────────────────

class TestInstallStreamSecurity:
    def test_install_dir_outside_home_blocked(self):
        handler = _make_handler(path="/api/install")
        pid = _make_plan_id()
        _cache_plan(pid, {
            "status": "ok",
            "plan": {"steps": [{"command": "echo hi", "description": "test"}]},
            "project": "test",
        })
        handler._do_install_stream({}, pid, "test", "/tmp/evil")
        written = handler.wfile.getvalue().decode()
        assert "安全限制" in written or "不合法" in written

    def test_install_regenerates_plan_if_expired(self):
        handler = _make_handler(path="/api/install")
        _plan_cache.clear()
        fake_plan = {
            "status": "ok",
            "plan": {"steps": [{"command": "echo hi", "description": "test"}]},
            "project": "test",
        }
        with patch("main.cmd_plan", return_value=fake_plan), \
             patch("executor.check_command_safety", return_value=(True, "")), \
             patch("web.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter(["output line\n"])
            mock_proc.wait.return_value = None
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            handler._do_install_stream({}, "", "user/repo", "")
            written = handler.wfile.getvalue().decode()
            assert "重新生成" in written or "done" in written
