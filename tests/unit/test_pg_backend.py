"""
test_pg_backend.py - PostgreSQL 后端真实集成测试
=================================================

要求：本地 PostgreSQL 运行中 + psycopg2-binary 已安装。
数据库：gitinstall_test（每个测试自动清空所有表）。

运行：
    python -m pytest tests/unit/test_pg_backend.py -x -q --tb=short
    
跳过条件：无 psycopg2 或 PG 不可达时自动 skip。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

# ── 连接字符串（本地无密码 peer 认证） ──
_PG_URL = os.getenv(
    "GITINSTALL_DATABASE_URL",
    "postgresql://localhost/gitinstall_test"
)

# ── 可用性检测 ──
def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_URL)
        conn.close()
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available (need local PG + psycopg2 + gitinstall_test db)"
)


@pytest.fixture(autouse=True)
def _clean_pg():
    """每个测试前清空所有表"""
    from db_backend import PostgreSQLBackend
    b = PostgreSQLBackend(database_url=_PG_URL)
    try:
        raw = b._raw_connection()
        cur = raw.cursor()
        # 获取所有用户表
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
        """)
        tables = [row[0] for row in cur.fetchall()]
        for t in tables:
            cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        raw.commit()
    finally:
        b.close()
    yield


# ═══════════════════════════════════════════════
#  PostgreSQLBackend 基础生命周期
# ═══════════════════════════════════════════════

class TestPGLifecycle:
    """PostgreSQL 后端完整 CRUD 生命周期"""

    def _make(self):
        from db_backend import PostgreSQLBackend
        return PostgreSQLBackend(database_url=_PG_URL)

    def test_backend_type(self):
        b = self._make()
        assert b.backend_type == "postgresql"
        b.close()

    def test_create_table_and_insert(self):
        b = self._make()
        try:
            b.executescript(
                "CREATE TABLE items (id SERIAL PRIMARY KEY, name TEXT NOT NULL);"
            )
            b.execute("INSERT INTO items (name) VALUES (?)", ("alpha",))
            b.get_connection().commit()
            row = b.fetchone("SELECT name FROM items WHERE id = ?", (1,))
            assert row is not None
            assert row["name"] == "alpha"
        finally:
            b.close()

    def test_fetchall(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE tags (id SERIAL PRIMARY KEY, tag TEXT);")
            b.execute("INSERT INTO tags (tag) VALUES (?)", ("a",))
            b.execute("INSERT INTO tags (tag) VALUES (?)", ("b",))
            b.execute("INSERT INTO tags (tag) VALUES (?)", ("c",))
            b.get_connection().commit()
            rows = b.fetchall("SELECT tag FROM tags ORDER BY tag")
            assert [r["tag"] for r in rows] == ["a", "b", "c"]
        finally:
            b.close()

    def test_fetchone_empty_returns_none(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE empty (id SERIAL PRIMARY KEY);")
            assert b.fetchone("SELECT * FROM empty") is None
        finally:
            b.close()

    def test_fetchall_empty_returns_list(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE empty (id SERIAL PRIMARY KEY);")
            assert b.fetchall("SELECT * FROM empty") == []
        finally:
            b.close()

    def test_executemany(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE nums (n INTEGER);")
            b.executemany("INSERT INTO nums (n) VALUES (?)", [(1,), (2,), (3,)])
            b.get_connection().commit()
            rows = b.fetchall("SELECT n FROM nums ORDER BY n")
            assert [r["n"] for r in rows] == [1, 2, 3]
        finally:
            b.close()


class TestPGTransaction:
    """事务 commit / rollback"""

    def _make(self):
        from db_backend import PostgreSQLBackend
        return PostgreSQLBackend(database_url=_PG_URL)

    def test_transaction_commit(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE kv (k TEXT, v TEXT);")
            with b.transaction():
                b.execute("INSERT INTO kv (k, v) VALUES (?, ?)", ("x", "1"))
            row = b.fetchone("SELECT v FROM kv WHERE k = ?", ("x",))
            assert row["v"] == "1"
        finally:
            b.close()

    def test_transaction_rollback(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE nums (n INTEGER);")
            b.execute("INSERT INTO nums (n) VALUES (?)", (1,))
            b.get_connection().commit()
            with pytest.raises(ValueError):
                with b.transaction():
                    b.execute("INSERT INTO nums (n) VALUES (?)", (2,))
                    raise ValueError("boom")
            rows = b.fetchall("SELECT n FROM nums")
            assert len(rows) == 1
            assert rows[0]["n"] == 1
        finally:
            b.close()


class TestPGIntegrityAndDiag:
    """完整性检查和诊断"""

    def _make(self):
        from db_backend import PostgreSQLBackend
        return PostgreSQLBackend(database_url=_PG_URL)

    def test_integrity_check_ok(self):
        b = self._make()
        try:
            assert b.integrity_check() == "ok"
        finally:
            b.close()

    def test_table_row_count(self):
        b = self._make()
        try:
            b.executescript("CREATE TABLE items (x TEXT);")
            assert b.table_row_count("items") == 0
            b.execute("INSERT INTO items (x) VALUES (?)", ("a",))
            b.execute("INSERT INTO items (x) VALUES (?)", ("b",))
            b.get_connection().commit()
            assert b.table_row_count("items") == 2
        finally:
            b.close()

    def test_table_row_count_rejects_injection(self):
        b = self._make()
        try:
            with pytest.raises(ValueError, match="Invalid table name"):
                b.table_row_count("items; DROP TABLE items")
        finally:
            b.close()

    def test_close_idempotent(self):
        b = self._make()
        b.get_connection()
        b.close()
        b.close()  # 不应报错


class TestPGPlaceholderConversion:
    """占位符 ? → %s 转换"""

    def _make(self):
        from db_backend import PostgreSQLBackend
        return PostgreSQLBackend(database_url=_PG_URL)

    def test_single_placeholder(self):
        b = self._make()
        assert b._convert_placeholders("SELECT * FROM t WHERE id = ?") == \
               "SELECT * FROM t WHERE id = %s"

    def test_multiple_placeholders(self):
        b = self._make()
        sql = "INSERT INTO t (a, b, c) VALUES (?, ?, ?)"
        assert b._convert_placeholders(sql) == \
               "INSERT INTO t (a, b, c) VALUES (%s, %s, %s)"

    def test_no_placeholders(self):
        b = self._make()
        sql = "SELECT 1"
        assert b._convert_placeholders(sql) == sql


class TestPGAdaptSchema:
    """adapt_schema 真实执行（转换 + CREATE TABLE）"""

    def _make(self):
        from db_backend import PostgreSQLBackend
        return PostgreSQLBackend(database_url=_PG_URL)

    def test_adapt_and_create_events_table(self):
        b = self._make()
        try:
            sqlite_ddl = """
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL DEFAULT (strftime('%s','now')),
                event_type  TEXT    NOT NULL,
                project     TEXT
            );
            """
            adapted = b.adapt_schema(sqlite_ddl)
            assert "SERIAL PRIMARY KEY" in adapted
            assert "EXTRACT(EPOCH FROM NOW())" in adapted
            assert "DOUBLE PRECISION" in adapted
            # 实际执行
            b.executescript(sqlite_ddl)  # executescript 内部调用 adapt_schema
            b.execute(
                "INSERT INTO events (event_type, project) VALUES (?, ?)",
                ("install", "comfyui")
            )
            b.get_connection().commit()
            row = b.fetchone("SELECT event_type, project FROM events WHERE id = ?", (1,))
            assert row["event_type"] == "install"
            assert row["project"] == "comfyui"
        finally:
            b.close()

    def test_adapt_sessions_table(self):
        """完整 sessions 表 schema 转换并写入"""
        b = self._make()
        try:
            # 先建 users 表（sessions 有外键依赖）
            b.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id          SERIAL PRIMARY KEY,
                    username    TEXT NOT NULL UNIQUE,
                    email       TEXT NOT NULL UNIQUE,
                    pw_hash     TEXT NOT NULL,
                    salt        TEXT NOT NULL,
                    tier        TEXT NOT NULL DEFAULT 'free',
                    is_admin    INTEGER NOT NULL DEFAULT 0,
                    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                    last_login  DOUBLE PRECISION
                );
            """)
            sqlite_sessions = """
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT    PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
                expires_at  REAL    NOT NULL,
                ip_hash     TEXT,
                user_agent  TEXT
            );
            """
            b.executescript(sqlite_sessions)
            # 插入测试用户
            b.execute(
                "INSERT INTO users (username, email, pw_hash, salt) VALUES (?, ?, ?, ?)",
                ("testuser", "test@test.com", "hash123", "salt123")
            )
            b.get_connection().commit()
            # 插入 session
            now = time.time()
            b.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                ("tok-abc", 1, now + 3600)
            )
            b.get_connection().commit()
            row = b.fetchone("SELECT token, user_id FROM sessions WHERE token = ?", ("tok-abc",))
            assert row["token"] == "tok-abc"
            assert row["user_id"] == 1
        finally:
            b.close()

    def test_full_schema_adapt(self):
        """完整 db._SCHEMA 转换后可执行"""
        import db as db_mod
        from db_backend import PostgreSQLBackend
        b = PostgreSQLBackend(database_url=_PG_URL)
        try:
            adapted = b.adapt_schema(db_mod._SCHEMA)
            # 验证转换正确
            assert "AUTOINCREMENT" not in adapted
            assert "SERIAL PRIMARY KEY" in adapted
            # 实际执行全部 schema
            b.executescript(db_mod._SCHEMA)
            # 写入一条事件验证
            b.execute(
                "INSERT INTO events (event_type, project) VALUES (?, ?)",
                ("test", "pg-test")
            )
            b.get_connection().commit()
            count = b.table_row_count("events")
            assert count == 1
        finally:
            b.close()


# ═══════════════════════════════════════════════
#  工厂 + 环境变量集成
# ═══════════════════════════════════════════════

class TestPGFactory:
    """get_backend 通过环境变量选择 PostgreSQL"""

    def test_factory_selects_pg(self, monkeypatch):
        import db_backend
        monkeypatch.setattr(db_backend, "_backend", None)
        monkeypatch.setenv("GITINSTALL_DB_BACKEND", "postgresql")
        monkeypatch.setenv("GITINSTALL_DATABASE_URL", _PG_URL)
        b = db_backend.get_backend()
        try:
            assert b.backend_type == "postgresql"
            assert b.integrity_check() == "ok"
        finally:
            b.close()
            db_backend.set_backend(None)


# ═══════════════════════════════════════════════
#  db.py 全流程走 PG 后端
# ═══════════════════════════════════════════════

class TestDBOverPG:
    """db.py 业务逻辑通过 PostgreSQL 后端执行"""

    @pytest.fixture(autouse=True)
    def _pg_backend(self):
        import db as db_mod
        from db_backend import PostgreSQLBackend, set_backend
        b = PostgreSQLBackend(database_url=_PG_URL)
        set_backend(b)
        db_mod._initialized = False
        yield
        b.close()
        set_backend(None)
        db_mod._initialized = False

    def test_init_db(self):
        import db
        db.init_db()
        from db_backend import get_backend
        b = get_backend()
        # 验证核心表存在
        row = b.fetchone(
            "SELECT COUNT(*) as cnt FROM pg_tables WHERE schemaname='public' AND tablename='events'"
        )
        assert row["cnt"] == 1

    def test_record_event(self):
        import db
        db.init_db()
        db.record_event("test_pg", project="pg-proj")
        from db_backend import get_backend
        count = get_backend().table_row_count("events")
        assert count >= 1

    def test_register_login_validate(self):
        import db
        db.init_db()
        reg = db.register_user("pguser", "pg@test.com", "StrongPass123!")
        assert reg["status"] == "ok"
        login = db.login_user("pg@test.com", "StrongPass123!")
        assert login["status"] == "ok"
        user = db.validate_token(login["token"])
        assert user is not None
        assert user["username"] == "pguser"

    def test_password_reset_flow(self):
        import db
        db.init_db()
        db.register_user("resetpg", "resetpg@test.com", "OldPass123!")
        res = db.create_reset_token("resetpg@test.com")
        assert res["token"] is not None
        result = db.reset_password(res["token"], "NewPass456!")
        assert result["status"] == "ok"
        login = db.login_user("resetpg@test.com", "NewPass456!")
        assert login["status"] == "ok"

    def test_session_lifecycle(self):
        import db
        from db_backend import get_backend
        db.init_db()
        db.register_user("sessuser", "sess@test.com", "StrongPass123!")
        login = db.login_user("sess@test.com", "StrongPass123!")
        token = login["token"]
        # 有效
        assert db.validate_token(token) is not None
        # 手动过期
        get_backend().execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 3600, token)
        )
        get_backend().get_connection().commit()
        # 过期后不可用（validate_token 会自动删除过期 session）
        assert db.validate_token(token) is None
        # 清理（可能为 0 因为 validate_token 已清除了该 session）
        cleaned = db.cleanup_expired_sessions()
        assert cleaned >= 0
