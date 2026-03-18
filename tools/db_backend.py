"""
db_backend.py - 数据库抽象后端
=====================================

支持 SQLite（默认）→ PostgreSQL 等后端无缝切换。
环境变量 GITINSTALL_DB_BACKEND 控制后端类型：
  - "sqlite"     (默认) 使用本地文件 ~/.gitinstall/data.db
  - "postgresql"  连接 GITINSTALL_DATABASE_URL

面向大众原则：零配置即可用 SQLite，生产部署时切换 PostgreSQL。
零外部依赖：SQLite 使用 stdlib，PostgreSQL 需安装 psycopg2。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────
#  抽象后端接口
# ─────────────────────────────────────────────

class DatabaseBackend(ABC):
    """数据库后端抽象接口。

    所有操作通过 execute/executemany/executescript 统一调用。
    使用 '?' 占位符（SQLite 风格），PostgreSQL 后端自动转换为 '%s'。
    """

    @abstractmethod
    def get_connection(self) -> Any:
        """获取当前线程的数据库连接"""

    @abstractmethod
    @contextmanager
    def transaction(self):
        """事务上下文管理器，yield connection"""

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> Any:
        """执行单条 SQL，返回 cursor"""

    @abstractmethod
    def executemany(self, sql: str, params_list: list[tuple]) -> Any:
        """批量执行 SQL"""

    @abstractmethod
    def executescript(self, sql: str) -> None:
        """执行多条 SQL 脚本（DDL 初始化用）"""

    @abstractmethod
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """执行查询返回单行（dict 或 None）"""

    @abstractmethod
    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行查询返回所有行（list[dict]）"""

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""

    @abstractmethod
    def integrity_check(self) -> str:
        """数据库完整性检查，返回 'ok' 或错误描述"""

    @abstractmethod
    def table_row_count(self, table: str) -> int:
        """返回指定表的行数（仅诊断用）"""

    @property
    @abstractmethod
    def backend_type(self) -> str:
        """返回后端类型标识，如 'sqlite' 或 'postgresql'"""

    def adapt_schema(self, sqlite_schema: str) -> str:
        """将 SQLite 风格 schema 转换为当前后端语法。
        默认原样返回（SQLite 无需转换）。"""
        return sqlite_schema


# ─────────────────────────────────────────────
#  SQLite 后端实现
# ─────────────────────────────────────────────

class SQLiteBackend(DatabaseBackend):
    """线程安全 SQLite 后端，每线程一个连接。"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_dir = Path.home() / ".gitinstall"
            db_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(db_dir, 0o700)
            except OSError:
                pass
            self._db_path = str(db_dir / "data.db")
        else:
            self._db_path = db_path
        self._local = threading.local()

    @property
    def backend_type(self) -> str:
        return "sqlite"

    def get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                if os.path.exists(self._db_path):
                    os.chmod(self._db_path, 0o600)
            except OSError:
                pass
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> Any:
        return self.get_connection().execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> Any:
        return self.get_connection().executemany(sql, params_list)

    def executescript(self, sql: str) -> None:
        self.get_connection().executescript(sql)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        row = self.get_connection().execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self.get_connection().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def integrity_check(self) -> str:
        try:
            row = self.get_connection().execute("PRAGMA integrity_check").fetchone()
            return row[0] if row else "unknown"
        except Exception as e:
            return str(e)

    def table_row_count(self, table: str) -> int:
        # 防注入：仅允许字母、数字、下划线
        import re
        if not re.match(r'^[a-zA-Z_]\w*$', table):
            raise ValueError(f"Invalid table name: {table}")
        row = self.get_connection().execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()
        return row[0] if row else 0


# ─────────────────────────────────────────────
#  PostgreSQL 后端实现（占位，需要 psycopg2）
# ─────────────────────────────────────────────

class _PGCursorProxy:
    """包装 psycopg2 cursor，使 fetchone 返回 dict（兼容 sqlite3.Row）"""

    def __init__(self, cursor):
        self._cur = cursor

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def __iter__(self):
        return iter(self._cur)


class _PGConnectionProxy:
    """包装 psycopg2 connection，提供 sqlite3 兼容 API。

    使 db.py 中的 conn.execute() / conn.executemany() / conn.commit()
    无需修改即可同时支持 SQLite 和 PostgreSQL。
    """

    def __init__(self, raw_conn, extras_module):
        self._conn = raw_conn
        self._extras = extras_module

    def execute(self, sql: str, params: tuple = ()) -> _PGCursorProxy:
        sql = _pg_adapt_sql(sql)
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.execute(sql, params)
        return _PGCursorProxy(cur)

    def executemany(self, sql: str, params_list) -> _PGCursorProxy:
        sql = _pg_adapt_sql(sql)
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.executemany(sql, params_list)
        return _PGCursorProxy(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def closed(self):
        return self._conn.closed


def _pg_adapt_sql(sql: str) -> str:
    """将 SQLite 风格 SQL 实时转为 PostgreSQL 兼容语法"""
    # strftime('%s','now') → EXTRACT(EPOCH FROM NOW())  (先转，避免 % 转义干扰)
    sql = re.sub(
        r"strftime\('%s',\s*'now'\)",
        "EXTRACT(EPOCH FROM NOW())",
        sql,
    )
    # date(ts, 'unixepoch', 'localtime') → TO_CHAR(TO_TIMESTAMP(ts), 'YYYY-MM-DD')
    sql = re.sub(
        r"date\((\w+),\s*'unixepoch',\s*'localtime'\)",
        r"TO_CHAR(TO_TIMESTAMP(\1), 'YYYY-MM-DD')",
        sql,
    )
    # 转义已有的 % 为 %%（防止 LIKE 'session:%' 被误解析）
    sql = sql.replace("%", "%%")
    # ? → %s 占位符
    sql = sql.replace("?", "%s")
    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    has_or_ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, re.IGNORECASE))
    sql = re.sub(
        r"INSERT\s+OR\s+IGNORE\s+INTO",
        "INSERT INTO",
        sql, flags=re.IGNORECASE,
    )
    if has_or_ignore:
        # 在 VALUES(...) 后追加 ON CONFLICT DO NOTHING
        sql = re.sub(r"(\)\s*)$", r"\1 ON CONFLICT DO NOTHING", sql.rstrip())
        if "ON CONFLICT DO NOTHING" not in sql:
            sql = sql.rstrip(";").rstrip() + " ON CONFLICT DO NOTHING"
    return sql


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL 后端。需要安装 psycopg2-binary。

    使用 GITINSTALL_DATABASE_URL 环境变量配置连接字符串，例如：
      postgresql://user:pass@localhost:5432/gitinstall

    get_connection() 返回 _PGConnectionProxy，提供 sqlite3 兼容 API，
    使 db.py 无需修改即可运行在 PostgreSQL 上。
    """

    def __init__(self, database_url: Optional[str] = None):
        self._url = database_url or os.getenv("GITINSTALL_DATABASE_URL", "")
        if not self._url:
            raise ValueError(
                "PostgreSQL backend requires GITINSTALL_DATABASE_URL environment variable"
            )
        self._local = threading.local()

    @property
    def backend_type(self) -> str:
        return "postgresql"

    def _import_psycopg2(self):
        try:
            import psycopg2
            import psycopg2.extras
            return psycopg2, psycopg2.extras
        except ImportError:
            raise ImportError(
                "PostgreSQL backend requires psycopg2. "
                "Install with: pip install psycopg2-binary"
            )

    def _raw_connection(self):
        """获取原始 psycopg2 connection（内部用）"""
        raw = getattr(self._local, "raw_conn", None)
        if raw is None or raw.closed:
            psycopg2, _ = self._import_psycopg2()
            raw = psycopg2.connect(self._url)
            raw.autocommit = False
            self._local.raw_conn = raw
        return raw

    def get_connection(self) -> _PGConnectionProxy:
        """返回 SQLite 兼容的连接代理"""
        proxy = getattr(self._local, "conn_proxy", None)
        if proxy is None or proxy.closed:
            _, extras = self._import_psycopg2()
            raw = self._raw_connection()
            proxy = _PGConnectionProxy(raw, extras)
            self._local.conn_proxy = proxy
        return proxy

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _convert_placeholders(self, sql: str) -> str:
        """将 SQLite 的 ? 占位符转换为 PostgreSQL 的 %s"""
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple = ()) -> Any:
        proxy = self.get_connection()
        return proxy.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> Any:
        proxy = self.get_connection()
        return proxy.executemany(sql, params_list)

    def executescript(self, sql: str) -> None:
        adapted = self.adapt_schema(sql)
        raw = self._raw_connection()
        cur = raw.cursor()
        cur.execute(adapted)
        raw.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        proxy = self.get_connection()
        result = proxy.execute(sql, params)
        return result.fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        proxy = self.get_connection()
        result = proxy.execute(sql, params)
        return result.fetchall()

    def close(self) -> None:
        raw = getattr(self._local, "raw_conn", None)
        if raw:
            raw.close()
            self._local.raw_conn = None
            self._local.conn_proxy = None

    def integrity_check(self) -> str:
        try:
            self.execute("SELECT 1")
            return "ok"
        except Exception as e:
            return str(e)

    def table_row_count(self, table: str) -> int:
        import re
        if not re.match(r'^[a-zA-Z_]\w*$', table):
            raise ValueError(f"Invalid table name: {table}")
        row = self.fetchone(f"SELECT COUNT(*) as cnt FROM {table}")
        return row["cnt"] if row else 0

    def adapt_schema(self, sqlite_schema: str) -> str:
        """将 SQLite schema SQL 转为 PostgreSQL 兼容语法"""
        import re
        sql = sqlite_schema
        # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
        sql = re.sub(
            r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            'SERIAL PRIMARY KEY',
            sql, flags=re.IGNORECASE,
        )
        # REAL DEFAULT (strftime('%s','now')) → DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
        sql = re.sub(
            r"REAL\s+NOT\s+NULL\s+DEFAULT\s+\(strftime\('%s','now'\)\)",
            "DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())",
            sql, flags=re.IGNORECASE,
        )
        # REAL → DOUBLE PRECISION (remaining)
        sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql, flags=re.IGNORECASE)
        # INTEGER → INTEGER (compatible, no change needed)
        return sql


# ─────────────────────────────────────────────
#  后端工厂
# ─────────────────────────────────────────────

_backend: Optional[DatabaseBackend] = None
_backend_lock = threading.Lock()


def get_backend() -> DatabaseBackend:
    """获取当前数据库后端（单例）。

    通过环境变量 GITINSTALL_DB_BACKEND 选择：
      - "sqlite"      (默认) 本地 SQLite
      - "postgresql"  PostgreSQL（需要 psycopg2 + GITINSTALL_DATABASE_URL）
    """
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        backend_type = os.getenv("GITINSTALL_DB_BACKEND", "sqlite").lower()
        if backend_type == "postgresql":
            _backend = PostgreSQLBackend()
        else:
            _backend = SQLiteBackend()
        return _backend


def set_backend(backend: DatabaseBackend) -> None:
    """替换数据库后端（测试用）"""
    global _backend
    _backend = backend
