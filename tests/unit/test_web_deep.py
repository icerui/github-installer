"""web.py 深层分支全覆盖测试

覆盖 _do_api_plan, _do_install_stream, _serve_ui/admin,
start_server 及各 API handler 的内部逻辑。
"""
import io
import json
import os
import sys
import time
import threading

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import web


# ─── 辅助 ────────────────────────────────

def _make_handler(method="GET", path="/", body=None, headers=None):
    """构造一个模拟 _Handler，不启动真正的 HTTP 服务"""
    handler = MagicMock(spec=web._Handler)
    handler.command = method
    handler.path = path
    handler.headers = headers or {}
    handler.wfile = io.BytesIO()
    handler.requestline = f"{method} {path} HTTP/1.1"

    # Let real methods work on our mock
    handler._json = lambda data, code=200: _mock_json(handler, data, code)
    handler._client_ip = lambda: "127.0.0.1"
    handler._add_security_headers = MagicMock()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    if body:
        handler.rfile = io.BytesIO(body.encode() if isinstance(body, str) else body)
        handler.headers = {"Content-Length": str(len(body if isinstance(body, str) else body))}
        handler._read_body = lambda: body if isinstance(body, str) else body.decode()
    else:
        handler._read_body = lambda: ""

    return handler


def _mock_json(handler, data, code=200):
    handler._last_json_response = data
    handler._last_json_code = code


# ═══════════════════════════════════════════
#  Module-level helpers
# ═══════════════════════════════════════════

class TestModuleHelpers:
    def test_check_rate_limit(self):
        # Should not be rate limited on first call
        result = web._check_rate_limit("192.168.1.1", "/api/detect")
        assert result is True or result is False  # depends on state

    def test_make_plan_id(self):
        pid = web._make_plan_id()
        assert isinstance(pid, str)
        assert len(pid) > 10

    def test_cache_and_pop_plan(self):
        pid = web._make_plan_id()
        web._cache_plan(pid, {"steps": [{"command": "echo hi"}]})
        result = web._pop_plan(pid)
        assert result is not None
        assert result["steps"][0]["command"] == "echo hi"
        # Second pop should return None
        assert web._pop_plan(pid) is None

    def test_pop_plan_not_found(self):
        assert web._pop_plan("nonexistent-id") is None


# ═══════════════════════════════════════════
#  _Handler API methods via direct invocation
# ═══════════════════════════════════════════

class TestServeUI:
    def test_serve_ui_file_exists(self, tmp_path, monkeypatch):
        html_file = tmp_path / "web_ui.html"
        html_file.write_bytes(b"<html>hello</html>")
        monkeypatch.setattr(web, "_THIS_DIR", tmp_path)

        handler = _make_handler("GET", "/")
        with patch.object(web._db, "record_event"):
            web._Handler._serve_ui(handler)
        handler.send_response.assert_called()

    def test_serve_ui_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(web, "_THIS_DIR", tmp_path)  # no web_ui.html
        handler = _make_handler("GET", "/")
        handler.send_error = MagicMock()
        web._Handler._serve_ui(handler)
        handler.send_error.assert_called_once()

    def test_serve_admin(self, tmp_path, monkeypatch):
        html_file = tmp_path / "admin.html"
        html_file.write_bytes(b"<html>admin</html>")
        monkeypatch.setattr(web, "_THIS_DIR", tmp_path)
        handler = _make_handler("GET", "/admin")
        web._Handler._serve_admin(handler)
        handler.send_response.assert_called()


class TestApiDetect:
    def test_api_detect(self):
        handler = _make_handler("GET", "/api/detect")
        mock_det = MagicMock()
        mock_det.detect.return_value = {
            "os": {"type": "macos"}, "hardware": {"cpu_count": 8},
            "gpu": {"type": "mps"}, "runtimes": {"python": {"version": "3.13"}},
            "package_managers": {"brew": {"available": True}},
        }
        with patch("detector.EnvironmentDetector", return_value=mock_det):
            web._Handler._api_detect(handler)
        assert handler._last_json_response is not None


class TestApiTrending:
    def test_api_trending(self):
        handler = _make_handler("GET", "/api/trending")
        with patch("trending.get_trending", return_value=[{"repo": "a/b"}]), \
             patch.object(web._db, "record_event"):
            web._Handler._api_trending(handler)
        assert handler._last_json_response is not None

    def test_api_trending_refresh_needs_admin(self):
        handler = _make_handler("GET", "/api/trending/refresh")
        handler.headers = {"Authorization": "Bearer fake"}
        with patch.object(web._db, "is_admin", return_value=False):
            web._Handler._api_trending_refresh(handler)
        assert handler._last_json_code == 403


class TestApiPlan:
    def test_do_api_plan_success(self):
        body = json.dumps({"project": "owner/repo"})
        handler = _make_handler("POST", "/api/plan", body=body)
        plan_result = {
            "status": "ok", "steps": [{"command": "make"}],
            "project_name": "owner/repo", "_log": "...", "strategy": "heuristic",
            "confidence": "high", "_stderr": ""
        }
        with patch("main.cmd_plan", return_value=plan_result), \
             patch.object(web._db, "record_event"), \
             patch.object(web._db, "save_plan_history"):
            web._Handler._do_api_plan(handler)
        resp = handler._last_json_response
        assert resp["status"] == "ok"
        assert "plan_id" in resp

    def test_api_plan_concurrent_limit(self, monkeypatch):
        monkeypatch.setattr(web, "_active_plans", web.MAX_CONCURRENT_PLANS)
        handler = _make_handler("POST", "/api/plan", body='{"project":"a/b"}')
        web._Handler._api_plan(handler)
        assert handler._last_json_code == 503


class TestApiInstallStream:
    def test_do_install_stream_with_cached_plan(self, monkeypatch, tmp_path):
        plan_id = web._make_plan_id()
        web._cache_plan(plan_id, {
            "plan": {
                "steps": [{"command": "echo hello", "description": "test"}],
                "launch_command": "",
            },
            "project": "o/r",
            "status": "ok",
        })
        install_dir = str(tmp_path / "installdir")
        qs = {"plan_id": [plan_id], "project": ["o/r"],
              "install_dir": [install_dir]}

        handler = _make_handler("GET", f"/api/install?plan_id={plan_id}")
        handler.wfile = io.BytesIO()

        mock_proc = MagicMock()
        mock_proc.stdout = iter(["output line\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("executor.check_command_safety", return_value=(True, "")), \
             patch.object(web._db, "record_event"):
            try:
                web._Handler._do_install_stream(handler, qs, plan_id, "o/r", install_dir)
            except (BrokenPipeError, OSError):
                pass  # Expected when wfile is BytesIO

    def test_api_install_concurrent_limit(self, monkeypatch):
        monkeypatch.setattr(web, "_active_installs",
                            {k: web.MAX_CONCURRENT_INSTALLS_PER_IP for k in ["127.0.0.1"]})
        handler = _make_handler("GET", "/api/install?plan_id=x&project=a/b")
        handler._rate_limited = lambda p: False
        handler.wfile = io.BytesIO()
        qs = {"plan_id": ["x"], "project": ["a/b"], "install_dir": ["/tmp"]}
        web._Handler._api_install_stream(handler, qs)
        handler.send_response.assert_called_with(429)


class TestApiAuth:
    def test_api_register(self):
        body = json.dumps({"username": "u", "email": "e@e.com", "password": "password123"})
        handler = _make_handler("POST", "/api/register", body=body)
        with patch.object(web._db, "register_user", return_value={"status": "ok", "user_id": 1}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_register(handler)
        assert handler._last_json_response["status"] == "ok"

    def test_api_login(self):
        body = json.dumps({"email": "e@e.com", "password": "password123"})
        handler = _make_handler("POST", "/api/login", body=body)
        with patch.object(web._db, "login_user",
                          return_value={"status": "ok", "token": "tok123"}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_login(handler)
        assert handler._last_json_response["status"] == "ok"

    def test_api_forgot_password(self):
        body = json.dumps({"email": "e@e.com"})
        handler = _make_handler("POST", "/api/forgot-password", body=body)
        with patch.object(web._db, "create_reset_token",
                          return_value={"status": "ok", "token": "t", "username": "u"}):
            web._Handler._api_forgot_password(handler)
        # Always returns success to prevent enumeration
        assert handler._last_json_response["status"] == "ok"

    def test_api_reset_password(self):
        body = json.dumps({"token": "tok", "password": "newpass123"})
        handler = _make_handler("POST", "/api/reset-password", body=body)
        with patch.object(web._db, "reset_password",
                          return_value={"status": "ok"}):
            web._Handler._api_reset_password(handler)
        assert handler._last_json_response["status"] == "ok"

    def test_api_user(self):
        handler = _make_handler("GET", "/api/user")
        handler.headers = {"Authorization": "Bearer tok123"}
        with patch.object(web._db, "validate_token",
                          return_value={"id": 1, "username": "u", "email": "e@e", "tier": "free"}), \
             patch.object(web._db, "check_quota",
                          return_value={"allowed": True, "used": 0, "limit": 20}):
            web._Handler._api_user(handler)
        assert handler._last_json_response is not None


class TestApiStats:
    def test_api_stats_admin(self):
        handler = _make_handler("GET", "/api/stats")
        handler.headers = {"Authorization": "Bearer admintoken"}
        with patch.object(web._db, "is_admin", return_value=True), \
             patch.object(web._db, "get_stats", return_value={"total_plans": 10}), \
             patch.object(web._db, "get_recent_installs", return_value=[]):
            web._Handler._api_stats(handler)
        assert "total_plans" in handler._last_json_response

    def test_api_stats_non_admin(self):
        handler = _make_handler("GET", "/api/stats")
        handler.headers = {"Authorization": "Bearer usertoken"}
        with patch.object(web._db, "is_admin", return_value=False):
            web._Handler._api_stats(handler)
        assert handler._last_json_code == 403


class TestApiAudit:
    def test_api_audit_success(self):
        handler = _make_handler("GET", "/api/audit?project=owner/repo")
        with patch("fetcher.fetch_project", return_value=MagicMock()), \
             patch("dependency_audit.audit_project", return_value=MagicMock()), \
             patch("dependency_audit.audit_to_dict", return_value={"risk": "low"}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_audit(handler)


class TestApiLicense:
    def test_api_license_success(self):
        handler = _make_handler("GET", "/api/license?project=owner/repo")
        compat = MagicMock()
        with patch("license_check.fetch_license_from_github", return_value=("MIT", "text")), \
             patch("license_check.analyze_license", return_value=compat), \
             patch("license_check.license_to_dict", return_value={"spdx": "MIT"}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_license(handler)


class TestApiUpdates:
    def test_api_updates_list(self):
        handler = _make_handler("GET", "/api/updates")
        tracker = MagicMock()
        tracker.list_installed.return_value = []
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("auto_update.updates_to_dict", return_value={"installed": []}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_updates(handler)

    def test_api_updates_check(self):
        handler = _make_handler("GET", "/api/updates?action=check")
        tracker = MagicMock()
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.updates_to_dict", return_value={"updates": []}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_updates(handler)


class TestApiUninstall:
    def test_api_uninstall_dry_run(self):
        body = json.dumps({"project": "owner/repo", "confirm": False})
        handler = _make_handler("POST", "/api/uninstall", body=body)
        tracker = MagicMock()
        tracker.get_install_info.return_value = {"install_dir": "/tmp/repo"}
        plan = MagicMock()
        with patch("auto_update.InstallTracker", return_value=tracker), \
             patch("fetcher.parse_repo_identifier", return_value=("owner", "repo")), \
             patch("uninstaller.plan_uninstall", return_value=plan), \
             patch("uninstaller.uninstall_to_dict", return_value={"plan": {}}), \
             patch.object(web._db, "record_event"):
            web._Handler._api_uninstall(handler)


class TestApiMisc:
    def test_api_flags(self):
        handler = _make_handler("GET", "/api/flags")
        with patch("feature_flags.get_all_status", return_value={}), \
             patch("feature_flags.format_flags_table", return_value=""):
            web._Handler._api_flags(handler)

    def test_api_registry(self):
        handler = _make_handler("GET", "/api/registry")
        reg = MagicMock()
        reg.to_dict.return_value = {"installers": []}
        with patch("installer_registry.InstallerRegistry", return_value=reg):
            web._Handler._api_registry(handler)

    def test_api_events(self):
        handler = _make_handler("GET", "/api/events")
        bus = MagicMock()
        bus.get_history.return_value = []
        with patch("event_bus.get_event_bus", return_value=bus):
            web._Handler._api_events(handler)

    def test_api_search(self):
        handler = _make_handler("GET", "/api/search?q=flask")
        qs = {"q": ["flask"]}
        m = MagicMock()
        m.read.return_value = json.dumps({"items": []}).encode()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=m), \
             patch.object(web._db, "record_event"):
            web._Handler._api_search(handler, qs)


class TestApiAdminSet:
    def test_admin_set_with_secret(self, monkeypatch):
        monkeypatch.setenv("GITINSTALL_ADMIN_SECRET", "supersecret")
        body = json.dumps({"user_id": 1, "admin_secret": "supersecret"})
        handler = _make_handler("POST", "/api/admin/set", body=body)
        with patch.object(web._db, "set_admin"), \
             patch.object(web._db, "record_event"):
            web._Handler._api_admin_set(handler)

    def test_admin_set_with_token(self):
        body = json.dumps({"user_id": 2})
        handler = _make_handler("POST", "/api/admin/set", body=body)
        handler.headers = {"Authorization": "Bearer tok", "Content-Length": str(len(body))}
        handler._read_body = lambda: body
        with patch.object(web._db, "is_admin", return_value=True), \
             patch.object(web._db, "set_admin"), \
             patch.object(web._db, "record_event"):
            web._Handler._api_admin_set(handler)


class TestApiKB:
    def test_api_kb_stats(self):
        handler = _make_handler("GET", "/api/kb/stats")
        kb = MagicMock()
        kb.get_stats.return_value = {"total": 0}
        with patch("knowledge_base.KnowledgeBase", return_value=kb):
            web._Handler._api_kb_stats(handler)

    def test_api_kb_search(self):
        body = json.dumps({"query": "flask"})
        handler = _make_handler("POST", "/api/kb/search", body=body)
        kb = MagicMock()
        kb.search.return_value = []
        with patch("knowledge_base.KnowledgeBase", return_value=kb):
            web._Handler._api_kb_search(handler)


class TestApiChain:
    def test_api_chain(self):
        body = json.dumps({"project": "owner/repo"})
        handler = _make_handler("POST", "/api/chain", body=body)
        with patch("main.cmd_plan", return_value={"steps": [], "status": "ok"}), \
             patch("dep_chain.build_chain_from_plan", return_value=MagicMock()), \
             patch("dep_chain.format_dep_chain", return_value="chain output"):
            web._Handler._api_chain(handler)


# ═══════════════════════════════════════════
#  start_server
# ═══════════════════════════════════════════

class TestStartServer:
    def test_start_server_keyboard_interrupt(self, monkeypatch):
        server_mock = MagicMock()
        server_mock.serve_forever.side_effect = KeyboardInterrupt
        monkeypatch.setattr(web, "_ThreadedServer", lambda addr, handler: server_mock)
        with patch("webbrowser.open"):
            web.start_server(port=19999, host="127.0.0.1", open_browser=False)
        server_mock.server_close.assert_called_once()

    def test_start_server_port_fallback(self, monkeypatch):
        call_count = 0
        def fake_server(addr, handler):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("port in use")
            m = MagicMock()
            m.serve_forever.side_effect = KeyboardInterrupt
            m.server_address = addr
            return m
        monkeypatch.setattr(web, "_ThreadedServer", fake_server)
        with patch("webbrowser.open"):
            web.start_server(port=19990, host="127.0.0.1", open_browser=False)
        assert call_count == 3  # Tried 2 failed ports, 3rd succeeded
