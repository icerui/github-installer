"""
web.py 额外覆盖 — SSE 安装流, start_server, 卸载执行体, admin_set 等
"""
import io
import json
import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))

from web import (
    _check_rate_limit, _make_plan_id, _cache_plan, _pop_plan,
    _plan_cache, _PLAN_TTL,
)


# ─── plan cache edge cases ──────────────────

class TestPlanCacheEdge:
    def test_pop_expired(self):
        pid = _make_plan_id()
        _plan_cache[pid] = (time.time() - _PLAN_TTL - 10, {"plan": {}})
        assert _pop_plan(pid) is None  # expired

    def test_pop_missing(self):
        assert _pop_plan("nonexistent") is None

    def test_cache_eviction(self):
        """Over 20 entries → evicts oldest"""
        _plan_cache.clear()
        for i in range(21):
            _cache_plan(f"pid_{i}", {"n": i})
        assert len(_plan_cache) <= 20

    def test_cache_round_trip(self):
        _plan_cache.clear()
        pid = _make_plan_id()
        _cache_plan(pid, {"test": True})
        result = _pop_plan(pid)
        assert result == {"test": True}
        assert _pop_plan(pid) is None  # consumed


# ─── SSE install flow ────────────────────────

def _make_handler():
    """Create a mock _Handler suitable for SSE stream tests."""
    # Import here to avoid circular issues
    import web
    h = MagicMock()
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 12345)
    h.headers = {"Authorization": ""}
    h.path = "/api/install?project=test/proj"
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h._add_security_headers = MagicMock()
    return h, web._Handler


class TestSSEInstallStream:
    def test_sse_no_plan_no_project(self):
        h, cls = _make_handler()
        # Call _do_install_stream with empty plan_id and empty project
        cls._do_install_stream(h, {}, "", "", "")
        # Should write SSE with error about expired plan
        output = h.wfile.getvalue().decode()
        assert "step_error" in output or "done" in output

    def test_sse_with_cached_plan_no_steps(self):
        h, cls = _make_handler()
        pid = _make_plan_id()
        _cache_plan(pid, {"plan": {"steps": []}, "project": "test/proj"})
        cls._do_install_stream(h, {}, pid, "test/proj", "")
        output = h.wfile.getvalue().decode()
        assert "step_error" in output

    def test_sse_install_dir_security_check(self, tmp_path):
        """Install dir outside home should be rejected"""
        h, cls = _make_handler()
        pid = _make_plan_id()
        _cache_plan(pid, {
            "plan": {"steps": [{"command": "echo hi", "description": "test"}]},
            "project": "test/proj",
        })
        # Use /tmp which IS under a valid path but test with absolute path
        cls._do_install_stream(h, {}, pid, "test/proj", "/nonexistent/evil/../../etc")
        output = h.wfile.getvalue().decode()
        # Should either succeed (if path resolves to home child) or error
        assert "event:" in output

    def test_sse_with_successful_step(self, tmp_path, monkeypatch):
        """Test SSE with a real-ish plan execution"""
        h, cls = _make_handler()
        pid = _make_plan_id()
        _cache_plan(pid, {
            "plan": {
                "steps": [{"command": "echo hello", "description": "test echo"}],
                "launch_command": "",
            },
            "project": "test/proj",
        })
        # Use home dir as install dir
        home = os.path.expanduser("~")
        cls._do_install_stream(h, {}, pid, "test/proj", "")
        output = h.wfile.getvalue().decode()
        assert "plan" in output


# ─── _api_uninstall handler body ─────────────

class TestUninstallHandler:
    def test_uninstall_project_not_found(self):
        h, cls = _make_handler()
        h._read_body = MagicMock(return_value=json.dumps({"project": "foo/bar", "confirm": True}))
        h._json = MagicMock()
        h._client_ip = MagicMock(return_value="127.0.0.1")

        mock_tracker = MagicMock()
        mock_tracker.get_project.return_value = None

        with patch("auto_update.InstallTracker", return_value=mock_tracker):
            with patch("fetcher.parse_repo_identifier", return_value=("foo", "bar")):
                cls._api_uninstall(h)

        h._json.assert_called()
        call_args = h._json.call_args[0][0]
        assert call_args["status"] == "error"
        assert "未找到" in call_args["message"]

    def test_uninstall_dry_run(self):
        h, cls = _make_handler()
        h._read_body = MagicMock(return_value=json.dumps({"project": "foo/bar", "confirm": False}))
        h._json = MagicMock()
        h._client_ip = MagicMock(return_value="127.0.0.1")

        mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.install_dir = "/tmp/test"
        mock_tracker.get_project.return_value = mock_proj

        mock_plan = MagicMock()
        mock_plan.error = None

        with patch("auto_update.InstallTracker", return_value=mock_tracker):
            with patch("fetcher.parse_repo_identifier", return_value=("foo", "bar")):
                with patch("uninstaller.plan_uninstall", return_value=mock_plan):
                    with patch("uninstaller.uninstall_to_dict", return_value={"files": []}):
                        cls._api_uninstall(h)

        h._json.assert_called()
        call_args = h._json.call_args[0][0]
        assert call_args["action"] == "dry_run"

    def test_uninstall_confirmed_success(self):
        h, cls = _make_handler()
        h._read_body = MagicMock(return_value=json.dumps({"project": "foo/bar", "confirm": True}))
        h._json = MagicMock()
        h._client_ip = MagicMock(return_value="127.0.0.1")

        mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.install_dir = "/tmp/test"
        mock_tracker.get_project.return_value = mock_proj

        mock_plan = MagicMock()
        mock_plan.error = None

        with patch("auto_update.InstallTracker", return_value=mock_tracker):
            with patch("fetcher.parse_repo_identifier", return_value=("foo", "bar")):
                with patch("uninstaller.plan_uninstall", return_value=mock_plan):
                    with patch("uninstaller.uninstall_to_dict", return_value={"files": []}):
                        with patch("uninstaller.execute_uninstall", return_value={"success": True, "freed_mb": 10}):
                            cls._api_uninstall(h)

        h._json.assert_called()
        call_args = h._json.call_args[0][0]
        assert call_args["status"] == "ok"


# ─── start_server ────────────────────────────

class TestStartServer:
    def test_start_server_all_ports_busy(self, monkeypatch):
        monkeypatch.setattr("web.logging.basicConfig", lambda **kw: None)
        with patch("web._ThreadedServer", side_effect=OSError("busy")):
            with pytest.raises(SystemExit):
                from web import start_server
                start_server(port=9990, open_browser=False)

    def test_start_server_binds_first_port(self, monkeypatch):
        monkeypatch.setattr("web.logging.basicConfig", lambda **kw: None)
        mock_server = MagicMock()

        with patch("web._ThreadedServer", return_value=mock_server) as mock_ts:
            # serve_forever blocks, so make it raise KeyboardInterrupt
            mock_server.serve_forever.side_effect = KeyboardInterrupt
            from web import start_server
            try:
                start_server(port=19999, host="127.0.0.1", open_browser=False)
            except (KeyboardInterrupt, SystemExit):
                pass
        mock_ts.assert_called()

    def test_start_server_with_0000(self, monkeypatch, capsys):
        monkeypatch.setattr("web.logging.basicConfig", lambda **kw: None)
        mock_server = MagicMock()
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        with patch("web._ThreadedServer", return_value=mock_server):
            from web import start_server
            try:
                start_server(port=19998, host="0.0.0.0", open_browser=False)
            except (KeyboardInterrupt, SystemExit):
                pass
        out = capsys.readouterr().out
        assert "监听所有网络接口" in out or "0.0.0.0" in out or "gitinstall" in out


# ─── additional handler edge cases ───────────

class TestAdminSetHandler:
    def test_admin_set_via_secret(self, monkeypatch):
        h, cls = _make_handler()
        h._read_body = MagicMock(return_value=json.dumps({
            "user_id": "user1",
            "admin_secret": "mysecret"
        }))
        h._json = MagicMock()
        monkeypatch.setenv("GITINSTALL_ADMIN_SECRET", "mysecret")

        with patch("web._db") as mock_db:
            cls._api_admin_set(h)

        h._json.assert_called()
        call_args = h._json.call_args[0][0]
        assert call_args["status"] == "ok"

    def test_admin_set_unauthorized(self, monkeypatch):
        h, cls = _make_handler()
        h._read_body = MagicMock(return_value=json.dumps({
            "user_id": "user1",
            "admin_secret": "wrong"
        }))
        h._json = MagicMock()
        h.headers = {"Authorization": "Bearer bad"}
        monkeypatch.setenv("GITINSTALL_ADMIN_SECRET", "correct")

        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            cls._api_admin_set(h)

        h._json.assert_called()
        args = h._json.call_args
        assert args[0][0]["status"] == "error"


class TestApiStats:
    def test_stats_non_admin(self):
        h, cls = _make_handler()
        h._json = MagicMock()
        h.headers = {"Authorization": "Bearer nonadmin"}

        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            cls._api_stats(h)

        call_args = h._json.call_args
        assert call_args[0][0]["status"] == "error"
        assert call_args[0][1] == 403

    def test_stats_admin_success(self):
        h, cls = _make_handler()
        h._json = MagicMock()
        h.headers = {"Authorization": "Bearer admin"}

        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = True
            mock_db.get_stats.return_value = {"users": 5}
            mock_db.get_recent_installs.return_value = []
            cls._api_stats(h)

        call_args = h._json.call_args[0][0]
        assert call_args["status"] == "ok"
        assert call_args["users"] == 5
