"""tests/unit/test_coverage_final.py — 最终覆盖率冲刺

目标: error_fixer.py 剩余分支 + web.py 异常分支 + db.py 函数
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


# ═══════════════════════════════════════════════
#  1. error_fixer.py — 剩余分支覆盖
# ═══════════════════════════════════════════════

class TestErrorFixerBranches:
    """覆盖 error_fixer.py 中未测试的具体修复规则分支"""

    def test_install_pkg_cmd_linux_apt(self):
        """Linux + apt → sudo apt-get install"""
        from error_fixer import _install_pkg_cmd
        with patch("error_fixer._is_macos", return_value=False), \
             patch("error_fixer._is_linux", return_value=True), \
             patch("error_fixer._has_apt", return_value=True):
            result = _install_pkg_cmd("openssl", "openssl", "libssl-dev")
        assert "sudo apt-get install" in result
        assert "libssl-dev" in result

    def test_fix_npm_workspace_protocol(self):
        """npm workspace: 协议 → pnpm"""
        from error_fixer import _fix_npm_workspace_protocol
        result = _fix_npm_workspace_protocol(
            "npm install", "EUNSUPPORTEDPROTOCOL workspace:", "")
        assert result is not None
        assert "pnpm" in result.fix_commands[0]

    def test_fix_npm_eexist(self):
        """npm EEXIST 缓存冲突"""
        from error_fixer import _fix_npm_eexist
        result = _fix_npm_eexist("npm install foo", "EEXIST file conflict", "")
        assert result is not None
        assert "npm cache clean" in " ".join(result.fix_commands)

    def test_fix_rust_openssl(self):
        """Rust 编译 → OpenSSL not found"""
        from error_fixer import _fix_rust_compile_error
        with patch("error_fixer._is_macos", return_value=True), \
             patch("error_fixer._has_brew", return_value=True):
            result = _fix_rust_compile_error(
                "cargo build", "Could not find directory of OpenSSL", "")
        assert result is not None
        assert "openssl" in " ".join(result.fix_commands).lower()

    def test_fix_go_no_root_module(self):
        """Go → 无 go.mod"""
        from error_fixer import _fix_go_no_root_module
        result = _fix_go_no_root_module(
            "go build ./...", "go.mod file not found in current directory", "")
        assert result is not None
        assert "find" in result.fix_commands[0]

    def test_fix_build_essentials_linux(self):
        """Linux → build-essential"""
        from error_fixer import _fix_build_essentials
        with patch("error_fixer._is_macos", return_value=False), \
             patch("error_fixer._is_linux", return_value=True), \
             patch("error_fixer._has_apt", return_value=True):
            result = _fix_build_essentials(
                "make all", "make: not found", "")
        assert result is not None
        assert "build-essential" in " ".join(result.fix_commands)

    def test_fix_gradle_gradlew_not_found(self):
        """gradlew 不存在"""
        from error_fixer import _fix_gradle_error
        result = _fix_gradle_error(
            "./gradlew build", "./gradlew: No such file or directory", "")
        assert result is not None

    def test_fix_gradle_mvnw_not_found(self):
        """mvnw 不存在"""
        from error_fixer import _fix_gradle_error
        result = _fix_gradle_error(
            "./mvnw clean install", "./mvnw: Permission denied", "")
        assert result is not None

    def test_fix_gradle_daemon_error(self):
        """Gradle daemon error"""
        from error_fixer import _fix_gradle_error
        result = _fix_gradle_error(
            "gradle build", "DaemonCommandExecution failed", "")
        assert result is not None

    def test_fix_gradle_java_version(self):
        """Java 版本不兼容"""
        from error_fixer import _fix_gradle_error
        result = _fix_gradle_error(
            "gradle build", "Unsupported class file major version 65", "")
        assert result is not None

    def test_fix_gradle_partial_success(self):
        """部分模块构建成功"""
        from error_fixer import _fix_gradle_error
        result = _fix_gradle_error(
            "gradle build", "FAILURE: Build failed", "BUILD SUCCESSFUL in 30s")
        assert result is not None

    def test_fix_haskell_ghc_not_found(self):
        """cabal + ghc 未找到"""
        from error_fixer import _fix_haskell_toolchain
        result = _fix_haskell_toolchain(
            "cabal build all", "The program 'ghc' could not be found", "")
        assert result is not None
        assert "ghcup" in " ".join(result.fix_commands)

    def test_fix_haskell_stack_extra_deps(self):
        """Stack yi/vty extra-deps"""
        from error_fixer import _fix_haskell_stack_extra_deps
        stderr = (
            "Error: While constructing the build plan, the following exceptions were encountered:\n"
            "In the dependencies for yi-frontend-vty:\n"
            "    vty-crossplatform needed, but the stack configuration has no specified version\n"
            "    vty-unix needed X but not found in snapshot\n"
        )
        result = _fix_haskell_stack_extra_deps("stack build", stderr, "")
        assert result is not None

    def test_fix_haskell_system_libraries_linux(self):
        """Linux 系统库缺失"""
        from error_fixer import _fix_haskell_system_libraries
        with patch("error_fixer._is_macos", return_value=False), \
             patch("error_fixer._is_linux", return_value=True), \
             patch("error_fixer._has_apt", return_value=True):
            result = _fix_haskell_system_libraries(
                "cabal build", "Missing (or bad) C header file: pcre.h not found", "")
        assert result is not None

    def test_fix_npm_no_package_json(self):
        """npm install 无 package.json"""
        from error_fixer import _fix_npm_no_package_json
        result = _fix_npm_no_package_json(
            "npm install", "ENOENT: no such file or directory, open '/a/package.json'", "")
        assert result is not None
        assert result.outcome == "trusted_failure"

    def test_diagnose_rule_exception(self):
        """某规则抛异常 → 静默跳过"""
        from error_fixer import diagnose, ERROR_FIX_RULES

        # 构造一个会抛异常的规则
        def bad_rule(cmd, stderr, stdout):
            raise RuntimeError("boom")

        original_rules = list(ERROR_FIX_RULES)
        try:
            ERROR_FIX_RULES.insert(0, bad_rule)
            # diagnose 应该跳过坏规则，继续执行
            result = diagnose("echo hello", "normal output", "")
            # 不应该抛异常
        finally:
            ERROR_FIX_RULES[:] = original_rules


# ═══════════════════════════════════════════════
#  2. web.py — 异常分支和 API 补充
# ═══════════════════════════════════════════════

def _make_handler():
    """构造 mock _Handler 实例"""
    from web import _Handler
    h = object.__new__(_Handler)
    h.wfile = BytesIO()
    h.rfile = BytesIO(b"")
    h.requestline = "GET / HTTP/1.1"
    h.client_address = ("127.0.0.1", 12345)
    h.request_version = "HTTP/1.1"
    h.command = "GET"
    from http.client import HTTPMessage
    h.headers = HTTPMessage()
    h.headers["Content-Type"] = "application/json"
    h.headers["Authorization"] = "Bearer test_token"
    h._headers_buffer = []
    h.path = "/"
    return h


class TestWebRateLimit:
    def test_rate_limit_exceeded(self):
        """频率限制超过 → 返回 True"""
        from web import _check_rate_limit, _rate_limits
        _rate_limits.clear()
        key = ("rate_test_ip", "/api/login")
        for _ in range(10):
            _check_rate_limit("rate_test_ip", "/api/login")
        result = _check_rate_limit("rate_test_ip", "/api/login")
        assert result is True
        _rate_limits.clear()


class TestWebApiExceptions:
    """覆盖 web.py 各 API 端点的异常处理分支"""

    def test_api_search_exception(self):
        """搜索异常 → 502"""
        h = _make_handler()
        with patch("web.urllib.request.urlopen", side_effect=Exception("timeout")):
            h._api_search({"q": ["test"]})
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_plan_concurrent_limit(self):
        """并发计划限制 → 503"""
        import web
        h = _make_handler()
        h.rfile = BytesIO(json.dumps({"project": "a/b"}).encode())
        h.headers["Content-Length"] = "20"
        old = web._active_plans
        try:
            web._active_plans = web.MAX_CONCURRENT_PLANS
            h._api_plan()
            out = h.wfile.getvalue()
            assert b"503" in out or b"\xe7\xb3\xbb\xe7\xbb\x9f\xe7\xb9\x81\xe5\xbf\x99" in out
        finally:
            web._active_plans = old

    def test_api_stats_non_admin(self):
        """非管理员请求 stats → 403"""
        h = _make_handler()
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            h._api_stats()
        out = h.wfile.getvalue()
        assert b"403" in out or b"error" in out

    def test_api_trending_refresh_non_admin(self):
        """非管理员刷新 trending → 403"""
        h = _make_handler()
        with patch("web._db") as mock_db:
            mock_db.is_admin.return_value = False
            h._api_trending_refresh()
        out = h.wfile.getvalue()
        assert b"403" in out or b"\xe7\xae\xa1\xe7\x90\x86\xe5\x91\x98" in out

    def test_api_login_success(self):
        """登录成功 → 200"""
        h = _make_handler()
        body = json.dumps({"email": "a@b.com", "password": "pass1234"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("web._db") as mock_db:
            mock_db.login_user.return_value = {"status": "ok", "token": "abc"}
            h._api_login()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_register_success(self):
        """注册成功 → 发欢迎邮件"""
        h = _make_handler()
        body = json.dumps({"username": "tester", "email": "t@e.st", "password": "12345678"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("web._db") as mock_db:
            mock_db.register_user.return_value = {"status": "ok", "user_id": 1}
            mock_db.send_welcome_email = MagicMock()
            h._api_register()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_user_valid_token(self):
        """有效 token → 返回用户信息"""
        h = _make_handler()
        with patch("web._db") as mock_db:
            mock_db.validate_token.return_value = {
                "id": 1, "username": "foo", "tier": "free"
            }
            mock_db.check_quota.return_value = {"remaining": 10, "limit": 100}
            h._api_user()
        out = h.wfile.getvalue()
        assert b"foo" in out

    def test_api_forgot_password_send(self):
        """发送重置邮件"""
        h = _make_handler()
        body = json.dumps({"email": "a@b.com"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("web._db") as mock_db:
            mock_db.create_reset_token.return_value = {
                "status": "ok", "token": "rst123", "username": "foo"
            }
            mock_db.send_reset_email = MagicMock()
            h._api_forgot_password()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_reset_password_success(self):
        """重置密码成功"""
        h = _make_handler()
        body = json.dumps({"token": "rst", "password": "newpass123"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("web._db") as mock_db:
            mock_db.reset_password.return_value = {"status": "ok"}
            h._api_reset_password()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_updates_check(self):
        """更新检查"""
        h = _make_handler()
        h.path = "/api/updates?action=check"
        with patch("auto_update.InstallTracker") as mock_tracker_cls, \
             patch("auto_update.check_all_updates") as mock_check, \
             patch("auto_update.updates_to_dict") as mock_to_dict:
            mock_check.return_value = []
            mock_to_dict.return_value = {"updates": []}
            with patch("web._db"):
                h._api_updates()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_updates_exception(self):
        """更新异常 → 502"""
        h = _make_handler()
        h.path = "/api/updates"
        with patch.dict("sys.modules", {"auto_update": None}):
            h._api_updates()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_audit_success(self):
        """审计成功"""
        h = _make_handler()
        h.path = "/api/audit?project=test/repo"
        with patch("fetcher.fetch_project") as mock_fetch, \
             patch("dependency_audit.audit_project") as mock_audit, \
             patch("dependency_audit.audit_to_dict") as mock_to_dict, \
             patch("web._db"):
            info = MagicMock()
            info.dependency_files = {"requirements.txt": "flask"}
            mock_fetch.return_value = info
            mock_audit.return_value = MagicMock()
            mock_to_dict.return_value = {"vulnerabilities": []}
            h._api_audit()
        out = h.wfile.getvalue()
        assert b"ok" in out

    def test_api_flags_exception(self):
        """flags 异常 → 502"""
        h = _make_handler()
        with patch.dict("sys.modules", {"feature_flags": None}):
            h._api_flags()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_registry_exception(self):
        """registry 异常 → 502"""
        h = _make_handler()
        with patch("installer_registry.InstallerRegistry", side_effect=RuntimeError("boom")):
            h._api_registry()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_events_exception(self):
        """events 异常 → 502"""
        h = _make_handler()
        with patch("event_bus.get_event_bus", side_effect=RuntimeError("boom")):
            h._api_events()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_kb_search_exception(self):
        """kb search 异常 → 502"""
        h = _make_handler()
        body = json.dumps({"query": "test"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("knowledge_base.KnowledgeBase", side_effect=RuntimeError("boom")):
            h._api_kb_search()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_chain_exception(self):
        """chain 异常 → 502"""
        h = _make_handler()
        body = json.dumps({"project": "a/b"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch("main.cmd_plan", side_effect=RuntimeError("fail")):
            h._api_chain()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out

    def test_api_uninstall_exception(self):
        """卸载异常 → 502"""
        h = _make_handler()
        body = json.dumps({"project": "test/repo"}).encode()
        h.rfile = BytesIO(body)
        h.headers["Content-Length"] = str(len(body))
        with patch.dict("sys.modules", {"auto_update": None}):
            h._api_uninstall()
        out = h.wfile.getvalue()
        assert b"502" in out or b"error" in out


class TestWebInstallStream:
    """install stream 分支覆盖"""

    def test_install_expired_plan_no_project(self):
        """过期 plan + 无 project → 错误"""
        h = _make_handler()
        with patch("web._pop_plan", return_value=None):
            h._do_install_stream({}, "nonexistent", "", "")
        out = h.wfile.getvalue()
        assert b"error" in out.lower() or b"\xe8\xae\xa1\xe5\x88\x92" in out

    def test_install_empty_steps(self):
        """空步骤列表"""
        h = _make_handler()
        with patch("web._pop_plan", return_value={"plan": {"steps": []}, "status": "ok"}):
            h._do_install_stream({}, "cached_id", "test/repo", "")
        out = h.wfile.getvalue()
        assert b"step_error" in out or b"\xe6\x97\xa0\xe5\xae\x89\xe8\xa3\x85" in out

    def test_install_empty_command_skip(self):
        """步骤中空命令 → 跳过"""
        h = _make_handler()
        plan = {
            "status": "ok",
            "plan": {"steps": [
                {"command": "", "description": "no-op"},
                {"command": "echo done", "description": "echo"},
            ]}
        }
        with patch("web._pop_plan", return_value=plan), \
             patch("subprocess.Popen") as mock_popen, \
             patch("web._db"):
            mock_proc = MagicMock()
            mock_proc.stdout.__iter__ = MagicMock(return_value=iter([b"done\n"]))
            mock_proc.wait.return_value = None
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            h._do_install_stream({}, "cached_id", "test/repo", "")
        out = h.wfile.getvalue()
        assert b"step_done" in out


# ═══════════════════════════════════════════════
#  3. db.py — 使用内存数据库覆盖
# ═══════════════════════════════════════════════

class TestDbIntegration:
    """使用真实 sqlite3 操作但重定向到内存"""

    def test_init_db_double_call(self):
        """double-init guard"""
        import db
        old_initialized = db._initialized
        try:
            db._initialized = True
            db.init_db()  # 第二次调用应短路
        finally:
            db._initialized = old_initialized

    def test_send_email_no_smtp(self):
        """SMTP 未配置 → False"""
        from db import send_email
        with patch("db._get_smtp_config", return_value=None):
            result = send_email("a@b.com", "test", "<p>hello</p>")
        assert result is False

    def test_chmod_oserror_silent(self):
        """chmod 失败 → 静默（通过后端抽象层）"""
        from db_backend import SQLiteBackend
        import tempfile, os
        # 使用临时文件测试 SQLite 后端的 chmod 容错
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            backend = SQLiteBackend(db_path=db_path)
            with patch("os.chmod", side_effect=OSError("permission denied")):
                conn = backend.get_connection()
            assert conn is not None
            backend.close()
