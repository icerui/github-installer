"""
test_web_handlers.py - Web API 处理器覆盖率突破
=================================================

突破性算法：mock self 模式 — 所有 _api_* 方法共享同一测试模板

核心发现：HTTP handler 的本质是 (request → module_call → json_response)
         mock 掉 self 的 HTTP 管道方法，只测业务逻辑。

覆盖 web.py 中 ~295 行未覆盖代码
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest


@pytest.fixture(scope="module")
def handler_cls():
    from web import _Handler
    return _Handler


def make_handler(handler_cls, path="/", body=None, headers=None):
    """创建 mock handler — 不实例化真实 HTTPServer"""
    h = MagicMock()
    h.path = path
    h.headers = headers or {}
    h.client_address = ("127.0.0.1", 12345)
    h._client_ip.return_value = "127.0.0.1"
    if body is not None:
        h._read_body.return_value = json.dumps(body) if isinstance(body, dict) else body
    return h


# ========================================================
#  模式 1: GET handlers — 无 body，直接调用
# ========================================================

class TestGETHandlers:
    """所有 GET API 处理器：调用模块 → self._json(result)"""

    def test_api_detect(self, handler_cls):
        h = make_handler(handler_cls)
        mock_env = {"os": {"type": "macOS", "arch": "arm64"},
                     "gpu": {"type": "apple_silicon"},
                     "runtimes": {"python": {"available": True, "version": "3.13"}}}
        with patch("detector.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = mock_env
            handler_cls._api_detect(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"
        assert "env" in data

    def test_api_trending(self, handler_cls):
        h = make_handler(handler_cls)
        with patch("trending.get_trending", return_value=[{"repo": "a/b", "stars": 100}]):
            handler_cls._api_trending(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"
        assert isinstance(data["projects"], list)

    def test_api_trending_refresh_no_auth(self, handler_cls):
        h = make_handler(handler_cls, headers={"Authorization": ""})
        h.headers = {"Authorization": ""}
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            handler_cls._api_trending_refresh(h)
        # 无管理员权限应返回 403
        h._json.assert_called_once()
        call_args = h._json.call_args
        assert call_args[0][0]["status"] == "error"

    def test_api_trending_refresh_admin(self, handler_cls):
        h = make_handler(handler_cls)
        h.headers = MagicMock()
        h.headers.get.return_value = "Bearer admin-token"
        with patch("web._db") as mock_db, \
             patch("trending.get_trending", return_value=[]):
            mock_db.is_admin.return_value = True
            handler_cls._api_trending_refresh(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"
        assert data.get("refreshed") is True

    def test_api_stats(self, handler_cls):
        h = make_handler(handler_cls)
        with patch("web._db") as mock_db:
            mock_db.get_stats.return_value = {"total_installs": 10, "total_plans": 5}
            handler_cls._api_stats(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_user_no_token(self, handler_cls):
        h = make_handler(handler_cls)
        h.headers = MagicMock()
        h.headers.get.return_value = ""
        handler_cls._api_user(h)
        h._json.assert_called_once()

    def test_api_flags(self, handler_cls):
        h = make_handler(handler_cls)
        handler_cls._api_flags(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"
        assert "flags" in data

    def test_api_registry(self, handler_cls):
        h = make_handler(handler_cls)
        mock_reg = MagicMock()
        mock_inst = MagicMock()
        mock_inst.info.name = "pip"
        mock_reg.list_all.return_value = [mock_inst]
        mock_reg.list_available.return_value = [mock_inst]
        mock_reg.to_dict.return_value = {}
        with patch("installer_registry.InstallerRegistry", return_value=mock_reg):
            handler_cls._api_registry(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_events(self, handler_cls):
        h = make_handler(handler_cls)
        mock_bus = MagicMock()
        mock_bus.get_history.return_value = []
        with patch("event_bus.get_event_bus", return_value=mock_bus):
            handler_cls._api_events(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"
        assert data["events"] == []

    def test_api_kb_stats(self, handler_cls):
        h = make_handler(handler_cls)
        mock_kb = MagicMock()
        mock_kb.get_stats.return_value = {"total": 0, "success_count": 0}
        with patch("knowledge_base.KnowledgeBase", return_value=mock_kb):
            handler_cls._api_kb_stats(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_audit_missing_project(self, handler_cls):
        h = make_handler(handler_cls, path="/api/audit")
        handler_cls._api_audit(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_audit_bad_format(self, handler_cls):
        h = make_handler(handler_cls, path="/api/audit?project=../../../etc/passwd")
        handler_cls._api_audit(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_audit_success(self, handler_cls):
        h = make_handler(handler_cls, path="/api/audit?project=owner/repo")
        mock_info = MagicMock()
        mock_info.dependency_files = {"req.txt": "flask"}
        with patch("fetcher.fetch_project", return_value=mock_info), \
             patch("dependency_audit.audit_project", return_value=[]), \
             patch("dependency_audit.audit_to_dict", return_value={"results": []}), \
             patch("web._db"):
            handler_cls._api_audit(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_license_missing_project(self, handler_cls):
        h = make_handler(handler_cls, path="/api/license")
        handler_cls._api_license(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_license_success(self, handler_cls):
        h = make_handler(handler_cls, path="/api/license?project=owner/repo")
        with patch("license_check.fetch_license_from_github", return_value=("MIT", "MIT text")), \
             patch("license_check.analyze_license", return_value=MagicMock()), \
             patch("license_check.license_to_dict", return_value={"spdx": "MIT"}), \
             patch("web._db"):
            handler_cls._api_license(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_updates_list(self, handler_cls):
        h = make_handler(handler_cls, path="/api/updates?action=list")
        mock_tracker = MagicMock()
        mock_proj = MagicMock()
        mock_proj.to_dict.return_value = {"owner": "a", "repo": "b"}
        mock_tracker.list_installed.return_value = [mock_proj]
        with patch("auto_update.InstallTracker", return_value=mock_tracker), \
             patch("auto_update.check_all_updates", return_value=[]), \
             patch("auto_update.updates_to_dict", return_value={"updates": []}), \
             patch("web._db"):
            handler_cls._api_updates(h)
        h._json.assert_called_once()

    def test_api_search_empty(self, handler_cls):
        h = make_handler(handler_cls)
        handler_cls._api_search(h, {})
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"


# ========================================================
#  模式 2: POST handlers — 有 body，需要 _read_body
# ========================================================

class TestPOSTHandlers:
    """所有 POST API 处理器：读 body → 调模块 → json 响应"""

    def test_api_plan_invalid_json(self, handler_cls):
        h = make_handler(handler_cls, body="not json")
        h._read_body.return_value = "not json"
        handler_cls._do_api_plan(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_plan_empty_project(self, handler_cls):
        h = make_handler(handler_cls, body={"project": ""})
        handler_cls._do_api_plan(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_register(self, handler_cls):
        h = make_handler(handler_cls, body={"username": "test", "email": "test@test.com", "password": "Str0ng!Pass"})
        with patch("web._db") as mock_db:
            mock_db.register_user.return_value = ({"status": "ok", "token": "abc"}, 200)
        handler_cls._api_register(h)
        h._json.assert_called_once()

    def test_api_register_invalid_json(self, handler_cls):
        h = make_handler(handler_cls, body="bad json")
        h._read_body.return_value = "bad json"
        handler_cls._api_register(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_login(self, handler_cls):
        h = make_handler(handler_cls, body={"email": "test@test.com", "password": "pass"})
        with patch("web._db") as mock_db:
            mock_db.login_user.return_value = {"status": "ok", "token": "abc"}
            handler_cls._api_login(h)
        h._json.assert_called_once()

    def test_api_login_invalid_json(self, handler_cls):
        h = make_handler(handler_cls, body="bad")
        h._read_body.return_value = "bad"
        handler_cls._api_login(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_forgot_password(self, handler_cls):
        h = make_handler(handler_cls, body={"email": "test@test.com"})
        with patch("web._db") as mock_db:
            mock_db.create_reset_token.return_value = {"token": None, "username": None}
            handler_cls._api_forgot_password(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"  # 无论邮箱是否存在都返回 ok

    def test_api_forgot_password_no_email(self, handler_cls):
        h = make_handler(handler_cls, body={"email": ""})
        handler_cls._api_forgot_password(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_reset_password(self, handler_cls):
        h = make_handler(handler_cls, body={"token": "abc", "password": "NewStr0ng!"})
        with patch("web._db") as mock_db:
            mock_db.reset_password.return_value = {"status": "ok"}
            handler_cls._api_reset_password(h)
        h._json.assert_called_once()

    def test_api_reset_password_missing_params(self, handler_cls):
        h = make_handler(handler_cls, body={"token": "", "password": ""})
        handler_cls._api_reset_password(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_uninstall_missing_project(self, handler_cls):
        h = make_handler(handler_cls, body={"project": ""})
        handler_cls._api_uninstall(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_uninstall_bad_format(self, handler_cls):
        h = make_handler(handler_cls, body={"project": "../../etc"})
        handler_cls._api_uninstall(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_kb_search_success(self, handler_cls):
        h = make_handler(handler_cls, body={"query": "pytorch"})
        mock_kb = MagicMock()
        mock_kb.search.return_value = []
        with patch("knowledge_base.KnowledgeBase", return_value=mock_kb):
            handler_cls._api_kb_search(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_kb_search_empty_query(self, handler_cls):
        h = make_handler(handler_cls, body={"query": ""})
        handler_cls._api_kb_search(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_kb_search_invalid_json(self, handler_cls):
        h = make_handler(handler_cls)
        h._read_body.return_value = "not json"
        handler_cls._api_kb_search(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_chain_success(self, handler_cls):
        h = make_handler(handler_cls, body={"project": "owner/repo"})
        mock_plan = {"status": "ok", "plan": {"steps": [{"command": "pip install .", "description": "install"}]}}
        mock_chain = MagicMock()
        mock_chain.to_dict.return_value = {"nodes": []}
        mock_chain.has_cycle.return_value = False
        with patch("main.cmd_plan", return_value=mock_plan), \
             patch("dep_chain.build_chain_from_plan", return_value=mock_chain):
            handler_cls._api_chain(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "ok"

    def test_api_chain_missing_project(self, handler_cls):
        h = make_handler(handler_cls, body={"project": ""})
        handler_cls._api_chain(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_chain_invalid_json(self, handler_cls):
        h = make_handler(handler_cls)
        h._read_body.return_value = "not json"
        handler_cls._api_chain(h)
        h._json.assert_called_once()
        data = h._json.call_args[0][0]
        assert data["status"] == "error"

    def test_api_admin_set(self, handler_cls):
        h = make_handler(handler_cls, body={"key": "test_key", "value": "test_val"})
        h.headers = MagicMock()
        h.headers.get.return_value = "Bearer admin-token"
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = True
            mock_db.set_admin_config.return_value = True
            handler_cls._api_admin_set(h)
        h._json.assert_called_once()


# ========================================================
#  模式 3: 路由分发 — do_GET / do_POST
# ========================================================

class TestRouteDispatch:

    def test_do_GET_404(self, handler_cls):
        h = make_handler(handler_cls, path="/nonexistent")
        handler_cls.do_GET(h)
        h.send_error.assert_called_with(404)

    def test_do_POST_404(self, handler_cls):
        h = make_handler(handler_cls, path="/nonexistent")
        h._rate_limited.return_value = False
        handler_cls.do_POST(h)
        h.send_error.assert_called_with(404)

    def test_do_POST_rate_limited(self, handler_cls):
        h = make_handler(handler_cls, path="/api/plan")
        h._rate_limited.return_value = True
        handler_cls.do_POST(h)
        # 被限流时不应该调用 handler
        h._json.assert_not_called()


# ========================================================
#  模式 4: 工具方法
# ========================================================

class TestHandlerUtils:

    def test_rate_limit_check(self):
        from web import _check_rate_limit, _rate_limits
        _rate_limits.clear()
        # 不超限
        assert _check_rate_limit("1.2.3.4", "/api/plan") is False
        # 填满限额
        for _ in range(15):
            _check_rate_limit("1.2.3.4", "/api/plan")
        # 超限
        assert _check_rate_limit("1.2.3.4", "/api/plan") is True

    def test_rate_limit_no_rule(self):
        from web import _check_rate_limit
        assert _check_rate_limit("1.2.3.4", "/unknown") is False

    def test_make_plan_id(self):
        from web import _make_plan_id
        pid = _make_plan_id()
        assert isinstance(pid, str)
        assert len(pid) > 20

    def test_plan_cache(self):
        from web import _cache_plan, _pop_plan, _make_plan_id
        pid = _make_plan_id()
        _cache_plan(pid, {"plan": {"steps": []}})
        result = _pop_plan(pid)
        assert result is not None
        # 取出后应该清空
        assert _pop_plan(pid) is None
