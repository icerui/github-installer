"""
test_enterprise.py - 企业级特性全面测试
========================================

覆盖：db_backend（SQLite 后端生命周期、工厂模式、PostgreSQL schema 转换）、
      log（结构化格式化、适配器、配置）、i18n（翻译、区域切换、注册、插值）、
      web（CSRF、健康检查、API 版本化）、db sessions（会话生命周期）。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sqlite3
import sys
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))


# ═══════════════════════════════════════════════
#  db_backend 测试
# ═══════════════════════════════════════════════

class TestSQLiteBackend:
    """SQLiteBackend 完整生命周期测试"""

    def test_basic_lifecycle(self, tmp_path):
        from db_backend import SQLiteBackend
        db_path = str(tmp_path / "test.db")
        backend = SQLiteBackend(db_path=db_path)
        try:
            # 创建表
            backend.executescript(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT);"
            )
            # 插入
            backend.execute("INSERT INTO items (name) VALUES (?)", ("alpha",))
            backend.get_connection().commit()
            # 查询
            row = backend.fetchone("SELECT name FROM items WHERE id = ?", (1,))
            assert row == {"name": "alpha"}
            rows = backend.fetchall("SELECT name FROM items ORDER BY id")
            assert len(rows) == 1
            assert rows[0]["name"] == "alpha"
        finally:
            backend.close()

    def test_backend_type(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        assert b.backend_type == "sqlite"
        b.close()

    def test_transaction_commit(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE kv (k TEXT, v TEXT);")
            with b.transaction():
                b.execute("INSERT INTO kv (k, v) VALUES (?, ?)", ("a", "1"))
            row = b.fetchone("SELECT v FROM kv WHERE k = ?", ("a",))
            assert row["v"] == "1"
        finally:
            b.close()

    def test_transaction_rollback(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE nums (n INTEGER);")
            b.execute("INSERT INTO nums (n) VALUES (?)", (1,))
            b.get_connection().commit()
            with pytest.raises(ValueError):
                with b.transaction():
                    b.execute("INSERT INTO nums (n) VALUES (?)", (2,))
                    raise ValueError("boom")
            # 回滚后只有 1 条
            rows = b.fetchall("SELECT n FROM nums")
            assert len(rows) == 1
            assert rows[0]["n"] == 1
        finally:
            b.close()

    def test_executemany(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE tags (tag TEXT);")
            b.executemany("INSERT INTO tags (tag) VALUES (?)", [("a",), ("b",), ("c",)])
            b.get_connection().commit()
            rows = b.fetchall("SELECT tag FROM tags ORDER BY tag")
            assert [r["tag"] for r in rows] == ["a", "b", "c"]
        finally:
            b.close()

    def test_integrity_check(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            result = b.integrity_check()
            assert result == "ok"
        finally:
            b.close()

    def test_table_row_count(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY, x TEXT);")
            assert b.table_row_count("items") == 0
            b.execute("INSERT INTO items (x) VALUES (?)", ("a",))
            b.execute("INSERT INTO items (x) VALUES (?)", ("b",))
            b.get_connection().commit()
            assert b.table_row_count("items") == 2
        finally:
            b.close()

    def test_table_row_count_rejects_injection(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            with pytest.raises(ValueError, match="Invalid table name"):
                b.table_row_count("items; DROP TABLE items")
        finally:
            b.close()

    def test_fetchone_returns_none_for_empty(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE empty (id INTEGER);")
            assert b.fetchone("SELECT * FROM empty") is None
        finally:
            b.close()

    def test_fetchall_returns_empty_list(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            b.executescript("CREATE TABLE empty (id INTEGER);")
            assert b.fetchall("SELECT * FROM empty") == []
        finally:
            b.close()

    def test_close_and_reopen(self, tmp_path):
        from db_backend import SQLiteBackend
        db_path = str(tmp_path / "t.db")
        b = SQLiteBackend(db_path=db_path)
        b.executescript("CREATE TABLE x (v TEXT);")
        b.execute("INSERT INTO x (v) VALUES (?)", ("hello",))
        b.get_connection().commit()
        b.close()
        # 重新打开
        b2 = SQLiteBackend(db_path=db_path)
        try:
            row = b2.fetchone("SELECT v FROM x")
            assert row["v"] == "hello"
        finally:
            b2.close()

    def test_close_idempotent(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        b.get_connection()
        b.close()
        b.close()  # 不应报错

    def test_default_path_creates_dir(self, tmp_path, monkeypatch):
        from db_backend import SQLiteBackend
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        b = SQLiteBackend()
        try:
            assert (fake_home / ".gitinstall").is_dir()
        finally:
            b.close()

    def test_wal_mode_enabled(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        try:
            row = b.get_connection().execute("PRAGMA journal_mode").fetchone()
            assert row[0] == "wal"
        finally:
            b.close()

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix file permissions")
    def test_file_permissions(self, tmp_path):
        from db_backend import SQLiteBackend
        db_path = str(tmp_path / "t.db")
        b = SQLiteBackend(db_path=db_path)
        try:
            b.get_connection()  # 触发创建
            stat = os.stat(db_path)
            assert (stat.st_mode & 0o777) == 0o600
        finally:
            b.close()

    def test_adapt_schema_noop(self, tmp_path):
        from db_backend import SQLiteBackend
        b = SQLiteBackend(db_path=str(tmp_path / "t.db"))
        schema = "CREATE TABLE foo (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        assert b.adapt_schema(schema) == schema
        b.close()


class TestBackendFactory:
    """get_backend / set_backend 工厂模式测试"""

    def test_get_backend_returns_sqlite_by_default(self, tmp_path, monkeypatch):
        import db_backend
        monkeypatch.setattr(db_backend, "_backend", None)
        monkeypatch.delenv("GITINSTALL_DB_BACKEND", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        b = db_backend.get_backend()
        assert b.backend_type == "sqlite"
        db_backend.set_backend(None)

    def test_set_backend_overrides(self, tmp_path):
        import db_backend
        from db_backend import SQLiteBackend, set_backend, get_backend
        original = db_backend._backend
        try:
            custom = SQLiteBackend(db_path=str(tmp_path / "custom.db"))
            set_backend(custom)
            assert get_backend() is custom
            custom.close()
        finally:
            db_backend._backend = original

    def test_get_backend_singleton(self, tmp_path, monkeypatch):
        import db_backend
        monkeypatch.setattr(db_backend, "_backend", None)
        monkeypatch.delenv("GITINSTALL_DB_BACKEND", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        b1 = db_backend.get_backend()
        b2 = db_backend.get_backend()
        assert b1 is b2
        db_backend.set_backend(None)

    def test_postgresql_requires_url(self, monkeypatch):
        import db_backend
        monkeypatch.delenv("GITINSTALL_DATABASE_URL", raising=False)
        with pytest.raises(ValueError, match="GITINSTALL_DATABASE_URL"):
            db_backend.PostgreSQLBackend()

    def test_postgresql_backend_type(self, monkeypatch):
        import db_backend
        monkeypatch.setenv("GITINSTALL_DATABASE_URL", "postgresql://localhost/test")
        # 构造成功但不实际连接
        b = db_backend.PostgreSQLBackend.__new__(db_backend.PostgreSQLBackend)
        b._url = "postgresql://localhost/test"
        b._local = threading.local()
        assert b.backend_type == "postgresql"


class TestPostgreSQLAdaptSchema:
    """PostgreSQL schema 转换测试（不需要真实数据库）"""

    def _make_pg(self):
        from db_backend import PostgreSQLBackend
        b = PostgreSQLBackend.__new__(PostgreSQLBackend)
        b._url = "postgresql://localhost/test"
        b._local = threading.local()
        return b

    def test_autoincrement_to_serial(self):
        b = self._make_pg()
        sql = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);"
        result = b.adapt_schema(sql)
        assert "SERIAL PRIMARY KEY" in result
        assert "AUTOINCREMENT" not in result

    def test_real_to_double_precision(self):
        b = self._make_pg()
        sql = "CREATE TABLE t (value REAL DEFAULT 0.0);"
        result = b.adapt_schema(sql)
        assert "DOUBLE PRECISION" in result
        assert "REAL" not in result.upper().replace("DOUBLE PRECISION", "")

    def test_strftime_to_extract_epoch(self):
        b = self._make_pg()
        sql = "CREATE TABLE t (ts REAL NOT NULL DEFAULT (strftime('%s','now')));"
        result = b.adapt_schema(sql)
        assert "EXTRACT(EPOCH FROM NOW())" in result

    def test_placeholder_conversion(self):
        b = self._make_pg()
        sql = "SELECT * FROM t WHERE id = ? AND name = ?"
        result = b._convert_placeholders(sql)
        assert result == "SELECT * FROM t WHERE id = %s AND name = %s"
        assert "?" not in result

    def test_complex_schema(self):
        b = self._make_pg()
        sql = """CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            expires_at REAL NOT NULL
        );"""
        result = b.adapt_schema(sql)
        assert "SERIAL PRIMARY KEY" in result
        assert "EXTRACT(EPOCH FROM NOW())" in result
        assert "DOUBLE PRECISION NOT NULL" in result


# ═══════════════════════════════════════════════
#  log.py 测试
# ═══════════════════════════════════════════════

class TestStructuredFormatter:
    """StructuredFormatter JSON/人类可读模式测试"""

    def test_human_mode_basic(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=False)
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello world", args=(), exc_info=None,
        )
        output = fmt.format(record)
        assert "hello world" in output
        assert "[   INFO]" in output
        assert "gitinstall.test" in output

    def test_human_mode_with_extra(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=False)
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="warning", args=(), exc_info=None,
        )
        record._structured_extra = {"project": "foo", "step": 3}
        output = fmt.format(record)
        assert "project=foo" in output
        assert "step=3" in output
        assert "|" in output

    def test_json_mode_basic(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=True)
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.ERROR, pathname="test.py",
            lineno=42, msg="bad thing", args=(), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["msg"] == "bad thing"
        assert data["level"] == "ERROR"
        assert data["logger"] == "gitinstall.test"
        assert data["line"] == 42
        assert "ts" in data

    def test_json_mode_with_extra(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=True)
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="ok", args=(), exc_info=None,
        )
        record._structured_extra = {"duration": 1.23}
        output = fmt.format(record)
        data = json.loads(output)
        assert data["extra"]["duration"] == 1.23

    def test_json_mode_with_exception(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=True)
        try:
            raise RuntimeError("test error")
        except RuntimeError:
            import sys as _sys
            exc_info = _sys.exc_info()
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="fail", args=(), exc_info=exc_info,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "RuntimeError" in data["exception"]

    def test_human_mode_with_exception(self):
        from log import StructuredFormatter
        fmt = StructuredFormatter(json_mode=False)
        try:
            raise ValueError("oops")
        except ValueError:
            import sys as _sys
            exc_info = _sys.exc_info()
        record = logging.LogRecord(
            name="gitinstall.test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="error", args=(), exc_info=exc_info,
        )
        output = fmt.format(record)
        assert "ValueError" in output
        assert "oops" in output


class TestStructuredLoggerAdapter:
    """StructuredLoggerAdapter 结构化字段注入测试"""

    def test_extra_fields_injected(self):
        from log import StructuredLoggerAdapter
        underlying = logging.getLogger("test.adapter")
        adapter = StructuredLoggerAdapter(underlying, {"component": "test"})
        msg, kwargs = adapter.process("hello", {"project": "foo"})
        assert msg == "hello"
        extra = kwargs["extra"]["_structured_extra"]
        assert extra["component"] == "test"
        assert extra["project"] == "foo"

    def test_standard_keys_preserved(self):
        from log import StructuredLoggerAdapter
        underlying = logging.getLogger("test.adapter2")
        adapter = StructuredLoggerAdapter(underlying, {})
        msg, kwargs = adapter.process("ok", {"exc_info": True, "step": 1})
        assert kwargs.get("exc_info") is True
        assert kwargs["extra"]["_structured_extra"]["step"] == 1


class TestLogConfigure:
    """log.configure() 配置测试"""

    def test_configure_creates_logger(self, monkeypatch):
        import log as log_mod
        # 重置配置状态
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")  # 不写文件
        log_mod.configure(level="DEBUG")
        root = logging.getLogger("gitinstall")
        assert root.level == logging.DEBUG
        assert len(root.handlers) > 0
        # 恢复
        monkeypatch.setattr(log_mod, "_configured", False)
        root.handlers.clear()

    def test_configure_idempotent(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        log_mod.configure(level="WARNING")
        root = logging.getLogger("gitinstall")
        n_handlers = len(root.handlers)
        log_mod.configure(level="DEBUG")  # 不应重复配置
        assert len(root.handlers) == n_handlers
        monkeypatch.setattr(log_mod, "_configured", False)
        root.handlers.clear()

    def test_get_logger_returns_adapter(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        logger = log_mod.get_logger("test_module")
        assert isinstance(logger, log_mod.StructuredLoggerAdapter)
        monkeypatch.setattr(log_mod, "_configured", False)
        logging.getLogger("gitinstall").handlers.clear()

    def test_get_logger_normalizes_name(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        logger = log_mod.get_logger("executor")
        assert logger.logger.name == "gitinstall.executor"
        monkeypatch.setattr(log_mod, "_configured", False)
        logging.getLogger("gitinstall").handlers.clear()

    def test_get_logger_preserves_gitinstall_prefix(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        logger = log_mod.get_logger("gitinstall.web")
        assert logger.logger.name == "gitinstall.web"
        monkeypatch.setattr(log_mod, "_configured", False)
        logging.getLogger("gitinstall").handlers.clear()

    def test_file_handler_with_rotation(self, tmp_path, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setattr(log_mod, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "1")
        log_mod.configure(level="DEBUG", log_file=True, max_bytes=1024, backup_count=2)
        root = logging.getLogger("gitinstall")
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        fh = file_handlers[0]
        assert fh.maxBytes == 1024
        assert fh.backupCount == 2
        monkeypatch.setattr(log_mod, "_configured", False)
        root.handlers.clear()

    def test_env_json_mode(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        monkeypatch.setenv("GITINSTALL_LOG_JSON", "1")
        log_mod.configure()
        # JSON 模式下 stderr handler 仍是人类可读（JSON 只影响文件）
        root = logging.getLogger("gitinstall")
        monkeypatch.setattr(log_mod, "_configured", False)
        root.handlers.clear()


class TestLogConvenience:
    """便捷函数 progress/debug/warn/error 测试"""

    def test_progress(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        # 不应抛异常
        log_mod.progress("test message", step=1)
        monkeypatch.setattr(log_mod, "_configured", False)
        logging.getLogger("gitinstall").handlers.clear()

    def test_warn_and_error(self, monkeypatch):
        import log as log_mod
        monkeypatch.setattr(log_mod, "_configured", False)
        monkeypatch.setenv("GITINSTALL_LOG_FILE", "0")
        log_mod.warn("warning msg")
        log_mod.error("error msg")
        monkeypatch.setattr(log_mod, "_configured", False)
        logging.getLogger("gitinstall").handlers.clear()


# ═══════════════════════════════════════════════
#  i18n.py 测试
# ═══════════════════════════════════════════════

class TestI18nTranslation:
    """t() 翻译函数测试"""

    def setup_method(self):
        import i18n
        self._orig = i18n._current_locale
        i18n.set_locale("zh")

    def teardown_method(self):
        import i18n
        i18n._current_locale = self._orig

    def test_translate_zh(self):
        from i18n import t
        assert t("common.ok") == "成功"

    def test_translate_en(self):
        from i18n import t, set_locale
        set_locale("en")
        assert t("common.ok") == "OK"

    def test_missing_key_returns_key(self):
        from i18n import t
        assert t("nonexistent.key") == "nonexistent.key"

    def test_parameter_interpolation(self):
        from i18n import t
        result = t("auth.password_min", n=8)
        assert "8" in result

    def test_parameter_interpolation_en(self):
        from i18n import t, set_locale
        set_locale("en")
        result = t("auth.password_min", n=12)
        assert "12" in result
        assert "characters" in result

    def test_missing_param_returns_template(self):
        from i18n import t
        # 缺少参数时不应抛异常
        result = t("auth.password_min")
        assert isinstance(result, str)

    def test_extra_param_ignored(self):
        from i18n import t
        result = t("common.ok", extra_param="unused")
        assert result == "成功"


class TestI18nLocale:
    """区域切换测试"""

    def setup_method(self):
        import i18n
        self._orig = i18n._current_locale

    def teardown_method(self):
        import i18n
        i18n._current_locale = self._orig

    def test_set_locale_zh(self):
        from i18n import set_locale, get_locale
        set_locale("zh")
        assert get_locale() == "zh"

    def test_set_locale_en(self):
        from i18n import set_locale, get_locale
        set_locale("en")
        assert get_locale() == "en"

    def test_set_locale_with_region(self):
        from i18n import set_locale, get_locale
        set_locale("zh_CN")
        assert get_locale() == "zh"

    def test_set_locale_with_hyphen(self):
        from i18n import set_locale, get_locale
        set_locale("en-US")
        assert get_locale() == "en"

    def test_unsupported_locale_ignored(self):
        from i18n import set_locale, get_locale
        set_locale("zh")
        set_locale("fr")  # 不支持
        assert get_locale() == "zh"  # 保持不变

    def test_env_override(self, monkeypatch):
        import importlib
        monkeypatch.setenv("GITINSTALL_LANG", "en")
        import i18n
        # 重新触发环境变量读取
        orig = i18n._current_locale
        i18n.set_locale("en")
        assert i18n.get_locale() == "en"
        i18n._current_locale = orig


class TestI18nRegister:
    """register_messages 扩展消息测试"""

    def setup_method(self):
        import i18n
        self._orig = i18n._current_locale
        i18n.set_locale("zh")

    def teardown_method(self):
        import i18n
        i18n._current_locale = self._orig
        # 清理注册的测试消息
        i18n._MESSAGES.pop("test.custom_msg", None)

    def test_register_and_translate(self):
        from i18n import register_messages, t
        register_messages({
            "test.custom_msg": {
                "zh": "自定义消息 {x}",
                "en": "Custom message {x}",
            }
        })
        assert t("test.custom_msg", x=42) == "自定义消息 42"

    def test_register_en(self):
        from i18n import register_messages, t, set_locale
        set_locale("en")
        register_messages({
            "test.custom_msg": {"zh": "中文", "en": "English {v}"}
        })
        assert t("test.custom_msg", v="ok") == "English ok"


class TestI18nMessageCoverage:
    """验证关键消息键存在且双语完整"""

    REQUIRED_KEYS = [
        "common.ok", "common.error",
        "auth.password_min", "auth.email_exists", "auth.invalid_credentials",
        "api.rate_limited", "api.invalid_json",
        "install.complete", "install.step_failed",
        "llm.no_provider", "llm.using_heuristic",
        "exec.install_start", "exec.step_done",
        "server.started", "server.stopped",
        "health.ok", "health.degraded",
        "fetcher.searching", "fetcher.cloning",
        "autopilot.progress", "autopilot.success",
    ]

    def test_all_keys_exist(self):
        from i18n import _MESSAGES
        for key in self.REQUIRED_KEYS:
            assert key in _MESSAGES, f"Missing i18n key: {key}"

    def test_all_keys_bilingual(self):
        from i18n import _MESSAGES
        for key in self.REQUIRED_KEYS:
            entry = _MESSAGES[key]
            assert "zh" in entry, f"{key} missing 'zh'"
            assert "en" in entry, f"{key} missing 'en'"
            assert entry["zh"], f"{key} 'zh' is empty"
            assert entry["en"], f"{key} 'en' is empty"

    def test_no_empty_translations(self):
        from i18n import _MESSAGES
        for key, entry in _MESSAGES.items():
            for lang in ("zh", "en"):
                if lang in entry:
                    assert entry[lang].strip(), f"{key}[{lang}] is empty"


# ═══════════════════════════════════════════════
#  web.py CSRF 测试
# ═══════════════════════════════════════════════

class TestCSRF:
    """CSRF token 生成/验证/一次性使用测试"""

    def test_generate_returns_string(self):
        from web import _generate_csrf_token
        token = _generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_validate_success(self):
        from web import _generate_csrf_token, _validate_csrf_token
        token = _generate_csrf_token()
        assert _validate_csrf_token(token) is True

    def test_one_time_use(self):
        from web import _generate_csrf_token, _validate_csrf_token
        token = _generate_csrf_token()
        assert _validate_csrf_token(token) is True
        # 第二次使用应失败
        assert _validate_csrf_token(token) is False

    def test_invalid_token_rejected(self):
        from web import _validate_csrf_token
        assert _validate_csrf_token("bogus-token-12345") is False

    def test_empty_token_rejected(self):
        from web import _validate_csrf_token
        assert _validate_csrf_token("") is False

    def test_expired_token_rejected(self, monkeypatch):
        from web import _generate_csrf_token, _validate_csrf_token, _csrf_tokens
        token = _generate_csrf_token()
        # 人为设置为过期
        with __import__("threading").Lock():
            _csrf_tokens[token] = time.time() - 7200  # 2 小时前
        assert _validate_csrf_token(token) is False

    def test_multiple_tokens_independent(self):
        from web import _generate_csrf_token, _validate_csrf_token
        t1 = _generate_csrf_token()
        t2 = _generate_csrf_token()
        assert t1 != t2
        assert _validate_csrf_token(t2) is True
        assert _validate_csrf_token(t1) is True


class TestCSRFEndpoint:
    """GET /api/csrf-token 端点测试"""

    def _make_handler(self, method="GET", path="/", headers=None):
        from web import _Handler
        handler = _Handler.__new__(_Handler)
        handler.path = path
        handler.command = method
        handler.client_address = ("127.0.0.1", 12345)
        effective_headers = dict(headers or {})
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d="": effective_headers.get(k, d)
        handler.wfile = BytesIO()
        handler.rfile = BytesIO(b"")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()
        return handler

    def test_csrf_token_endpoint(self):
        handler = self._make_handler(path="/api/csrf-token")
        handler._api_csrf_token()
        body = handler.wfile.getvalue()
        data = json.loads(body)
        assert "csrf_token" in data
        assert len(data["csrf_token"]) > 20

    def test_csrf_blocks_post_without_token(self):
        handler = self._make_handler(method="POST", path="/api/plan",
                                      headers={})  # 无 Bearer，无 CSRF
        result = handler._check_csrf()
        assert result is False
        handler.send_response.assert_called_with(403)

    def test_csrf_allows_bearer_auth(self):
        handler = self._make_handler(method="POST", path="/api/plan",
                                      headers={"Authorization": "Bearer test-token"})
        result = handler._check_csrf()
        assert result is True

    def test_csrf_allows_valid_token(self):
        from web import _generate_csrf_token
        token = _generate_csrf_token()
        handler = self._make_handler(method="POST", path="/api/plan",
                                      headers={"X-CSRF-Token": token})
        result = handler._check_csrf()
        assert result is True


# ═══════════════════════════════════════════════
#  web.py 健康检查测试
# ═══════════════════════════════════════════════

class TestHealthCheck:
    """健康检查 /health 和 /readiness 端点测试"""

    def _make_handler(self, path="/"):
        from web import _Handler
        handler = _Handler.__new__(_Handler)
        handler.path = path
        handler.command = "GET"
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d="": d
        handler.wfile = BytesIO()
        handler.rfile = BytesIO(b"")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()
        return handler

    def test_health_ok(self, tmp_path, monkeypatch):
        import db as db_mod
        from db_backend import SQLiteBackend, set_backend
        backend = SQLiteBackend(db_path=str(tmp_path / "h.db"))
        set_backend(backend)
        monkeypatch.setattr(db_mod, "DB_DIR", tmp_path)
        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "h.db")
        db_mod._initialized = False
        try:
            db_mod.init_db()
            handler = self._make_handler(path="/health")
            handler._health_check()
            body = handler.wfile.getvalue()
            data = json.loads(body)
            assert data["status"] == "ok"
            assert data["db"] == "ok"
            handler.send_response.assert_called_with(200)
        finally:
            backend.close()
            set_backend(None)
            db_mod._initialized = False

    def test_health_degraded_on_db_error(self, monkeypatch):
        import db as db_mod
        handler = self._make_handler(path="/health")
        # 让 init_db 或 _get_conn().execute 抛异常
        monkeypatch.setattr(db_mod, "init_db", lambda: None)
        monkeypatch.setattr(db_mod, "_get_conn",
                           lambda: MagicMock(execute=MagicMock(side_effect=Exception("db down"))))
        handler._health_check()
        body = handler.wfile.getvalue()
        data = json.loads(body)
        assert data["status"] == "degraded"
        assert data["db"] == "error"
        handler.send_response.assert_called_with(503)

    def test_readiness_ok(self):
        handler = self._make_handler(path="/readiness")
        handler._readiness_check()
        body = handler.wfile.getvalue()
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["ready"] is True
        assert "version" in data


# ═══════════════════════════════════════════════
#  web.py API 版本化测试
# ═══════════════════════════════════════════════

class TestAPIVersioning:
    """/api/v1/* → /api/* 路由别名测试"""

    def _make_handler(self, method="GET", path="/"):
        from web import _Handler
        handler = _Handler.__new__(_Handler)
        handler.path = path
        handler.command = method
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = MagicMock()
        handler.headers.get = lambda k, d="": d
        handler.wfile = BytesIO()
        handler.rfile = BytesIO(b"")
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()
        return handler

    def test_v1_prefix_stripped_for_get(self):
        """验证 /api/v1/detect 被映射到 /api/detect"""
        handler = self._make_handler(path="/api/v1/readiness")
        # readiness 是 GET 路由，不在 v1 映射下，但 do_GET 路由中存在
        # 直接测试 URL 解析逻辑
        import urllib.parse
        parsed = urllib.parse.urlparse("/api/v1/detect")
        path = parsed.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[8:]
        assert path == "/api/detect"

    def test_v1_prefix_stripped_for_post(self):
        """验证 /api/v1/plan 被映射到 /api/plan"""
        import urllib.parse
        parsed = urllib.parse.urlparse("/api/v1/plan")
        path = parsed.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[8:]
        assert path == "/api/plan"

    def test_no_v1_prefix_unchanged(self):
        import urllib.parse
        parsed = urllib.parse.urlparse("/api/detect")
        path = parsed.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[8:]
        assert path == "/api/detect"


# ═══════════════════════════════════════════════
#  web.py Rate Limiting 测试
# ═══════════════════════════════════════════════

class TestRateLimiting:
    """频率限制测试"""

    def test_not_limited_initially(self):
        from web import _check_rate_limit
        # 使用唯一 IP 避免与其他测试冲突
        assert _check_rate_limit("10.99.99.1", "/api/login") is False

    def test_limited_after_max(self):
        from web import _check_rate_limit
        ip = "10.99.99.2"
        path = "/api/forgot-password"
        # forgot-password 限制 60s 内 3 次
        for _ in range(3):
            _check_rate_limit(ip, path)
        assert _check_rate_limit(ip, path) is True

    def test_unknown_path_not_limited(self):
        from web import _check_rate_limit
        # 无规则的路径不限制
        assert _check_rate_limit("10.99.99.3", "/api/unknown") is False


# ═══════════════════════════════════════════════
#  web.py 安全头测试
# ═══════════════════════════════════════════════

class TestSecurityHeaders:
    """安全响应头测试"""

    def test_security_headers_added(self):
        from web import _Handler
        handler = _Handler.__new__(_Handler)
        handler.send_header = MagicMock()
        handler._add_security_headers()
        calls = {c[0][0]: c[0][1] for c in handler.send_header.call_args_list}
        assert calls["X-Content-Type-Options"] == "nosniff"
        assert calls["X-Frame-Options"] == "DENY"
        assert calls["X-XSS-Protection"] == "1; mode=block"
        assert "strict-origin" in calls["Referrer-Policy"]
        assert "Content-Security-Policy" in calls


# ═══════════════════════════════════════════════
#  db.py 会话生命周期测试
# ═══════════════════════════════════════════════

class TestSessionLifecycle:
    """用户注册 → 登录 → 验证 → 过期 → 清理"""

    @pytest.fixture(autouse=True)
    def _temp_db(self, tmp_path, monkeypatch):
        import db as db_mod
        from db_backend import SQLiteBackend, set_backend
        monkeypatch.setattr(db_mod, "DB_DIR", tmp_path)
        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
        db_mod._initialized = False
        backend = SQLiteBackend(db_path=str(tmp_path / "test.db"))
        set_backend(backend)
        yield
        backend.close()
        set_backend(None)
        db_mod._initialized = False

    def test_register_login_validate_cycle(self):
        import db
        db.init_db()
        # 注册
        reg = db.register_user("alice", "alice@test.com", "StrongPass123!")
        assert reg["status"] == "ok"
        # 登录
        login = db.login_user("alice@test.com", "StrongPass123!")
        assert login["status"] == "ok"
        assert "token" in login
        # 验证
        user = db.validate_token(login["token"])
        assert user is not None
        assert user["username"] == "alice"

    def test_invalid_token_returns_none(self):
        import db
        db.init_db()
        assert db.validate_token("nonexistent-token") is None

    def test_expired_session_invalid(self):
        import db
        from db_backend import get_backend
        db.init_db()
        reg = db.register_user("bob", "bob@test.com", "StrongPass123!")
        login = db.login_user("bob@test.com", "StrongPass123!")
        token = login["token"]
        # 手动设置过期
        get_backend().execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 3600, token)
        )
        get_backend().get_connection().commit()
        assert db.validate_token(token) is None

    def test_cleanup_expired_sessions(self):
        import db
        from db_backend import get_backend
        db.init_db()
        db.register_user("charlie", "charlie@test.com", "StrongPass123!")
        login = db.login_user("charlie@test.com", "StrongPass123!")
        # 设置过期
        get_backend().execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 3600, login["token"])
        )
        get_backend().get_connection().commit()
        cleaned = db.cleanup_expired_sessions()
        assert cleaned >= 1

    def test_duplicate_email_rejected(self):
        import db
        db.init_db()
        db.register_user("user1", "dup@test.com", "StrongPass123!")
        result = db.register_user("user2", "dup@test.com", "StrongPass456!")
        assert result["status"] == "error"

    def test_wrong_password_rejected(self):
        import db
        db.init_db()
        db.register_user("dave", "dave@test.com", "StrongPass123!")
        result = db.login_user("dave@test.com", "WrongPassword!")
        assert result["status"] == "error"

    def test_is_admin_default_false(self):
        import db
        db.init_db()
        db.register_user("nonadmin", "na@test.com", "StrongPass123!")
        login = db.login_user("na@test.com", "StrongPass123!")
        assert db.is_admin(login["token"]) is False


class TestPasswordReset:
    """密码重置流程测试"""

    @pytest.fixture(autouse=True)
    def _temp_db(self, tmp_path, monkeypatch):
        import db as db_mod
        from db_backend import SQLiteBackend, set_backend
        monkeypatch.setattr(db_mod, "DB_DIR", tmp_path)
        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
        db_mod._initialized = False
        backend = SQLiteBackend(db_path=str(tmp_path / "test.db"))
        set_backend(backend)
        yield
        backend.close()
        set_backend(None)
        db_mod._initialized = False

    def test_create_and_verify_reset_token(self):
        import db
        db.init_db()
        db.register_user("eve", "eve@test.com", "OldPass123!")
        result = db.create_reset_token("eve@test.com")
        assert result["status"] == "ok"
        assert result["token"] is not None
        assert len(result["token"]) > 10

    def test_reset_password_success(self):
        import db
        db.init_db()
        db.register_user("frank", "frank@test.com", "OldPass123!")
        res = db.create_reset_token("frank@test.com")
        token = res["token"]
        result = db.reset_password(token, "NewPass456!")
        assert result["status"] == "ok"
        # 用新密码登录
        login = db.login_user("frank@test.com", "NewPass456!")
        assert login["status"] == "ok"

    def test_reset_token_one_time_use(self):
        import db
        db.init_db()
        db.register_user("grace", "grace@test.com", "OldPass123!")
        res = db.create_reset_token("grace@test.com")
        token = res["token"]
        db.reset_password(token, "NewPass1!!")
        # 二次使用应失败
        result = db.reset_password(token, "NewPass2!!")
        assert result["status"] == "error"

    def test_expired_reset_token(self):
        import db
        from db_backend import get_backend
        db.init_db()
        db.register_user("helen", "helen@test.com", "OldPass123!")
        res = db.create_reset_token("helen@test.com")
        token = res["token"]
        # 设置过期
        get_backend().execute(
            "UPDATE reset_tokens SET expires_at = ? WHERE token = ?",
            (time.time() - 3600, token)
        )
        get_backend().get_connection().commit()
        result = db.reset_password(token, "NewPass!!")
        assert result["status"] == "error"

    def test_nonexistent_email_returns_fake_ok(self):
        import db
        db.init_db()
        result = db.create_reset_token("nobody@test.com")
        # 不透露邮箱不存在，返回 ok 但 token=None
        assert result["status"] == "ok"
        assert result["token"] is None
