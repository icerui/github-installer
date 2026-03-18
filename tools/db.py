"""
db.py - gitinstall 数据库模块
==============================

数据存储，支持 SQLite（默认）和 PostgreSQL 后端切换。
零外部依赖（SQLite）。
支持：匿名使用统计、用户注册/登录、配额管理、安装历史、密码重置、邮件发送。

数据库位置（SQLite）：~/.gitinstall/data.db
后端选择：环境变量 GITINSTALL_DB_BACKEND = sqlite | postgresql
"""

from __future__ import annotations

import hashlib
import hmac
import html as _html
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import threading
import time
from contextlib import contextmanager
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from log import get_logger
from i18n import t
from db_backend import get_backend, DatabaseBackend

logger = get_logger(__name__)

# ── 旧路径常量（保持向后兼容） ──
DB_DIR = Path.home() / ".gitinstall"
DB_PATH = DB_DIR / "data.db"

# ── 线程安全连接池（通过后端抽象层管理） ──
_init_lock = threading.Lock()
_initialized = False


def _db() -> DatabaseBackend:
    """获取当前数据库后端"""
    return get_backend()


def _get_conn():
    """获取当前线程的数据库连接（向后兼容）"""
    return _db().get_connection()


@contextmanager
def _transaction():
    """事务上下文管理器"""
    with _db().transaction() as conn:
        yield conn


# ─────────────────────────────────────────────
#  Schema 初始化
# ─────────────────────────────────────────────

_SCHEMA = """
-- 使用事件（匿名统计）
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL DEFAULT (strftime('%s','now')),
    event_type  TEXT    NOT NULL,
    project     TEXT,
    os_type     TEXT,
    detail      TEXT,
    user_id     INTEGER,
    ip_hash     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- 用户
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    email       TEXT    NOT NULL UNIQUE,
    pw_hash     TEXT    NOT NULL,
    salt        TEXT    NOT NULL,
    tier        TEXT    NOT NULL DEFAULT 'free',
    is_admin    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
    last_login  REAL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 月度用量
CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    year_month  TEXT    NOT NULL,
    plan_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, year_month),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- 安装计划历史
CREATE TABLE IF NOT EXISTS plans_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL DEFAULT (strftime('%s','now')),
    project     TEXT    NOT NULL,
    strategy    TEXT,
    confidence  TEXT,
    steps_json  TEXT,
    success     INTEGER,
    duration    REAL,
    user_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_plans_project ON plans_history(project);

-- 配置键值对
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Session 独立表（企业级会话管理）
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
    expires_at  REAL    NOT NULL,
    ip_hash     TEXT,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- 密码重置 token 独立表
CREATE TABLE IF NOT EXISTS reset_tokens (
    token       TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    email       TEXT    NOT NULL,
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
    expires_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reset_expires ON reset_tokens(expires_at);

-- 安装智能追踪（数据飞轮核心表）
CREATE TABLE IF NOT EXISTS install_telemetry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL DEFAULT (strftime('%s','now')),
    project         TEXT    NOT NULL,
    strategy        TEXT,
    os_type         TEXT,
    os_version      TEXT,
    arch            TEXT,
    gpu_type        TEXT,
    gpu_name        TEXT,
    vram_gb         REAL,
    cuda_version    TEXT,
    ram_gb          REAL,
    success         INTEGER,
    error_type      TEXT,
    error_message   TEXT,
    duration_sec    REAL,
    steps_total     INTEGER,
    steps_completed INTEGER,
    env_hash        TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_project ON install_telemetry(project);
CREATE INDEX IF NOT EXISTS idx_telemetry_gpu ON install_telemetry(gpu_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_success ON install_telemetry(success);
"""


def init_db():
    """初始化数据库（幂等，可多次调用）"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        backend = _db()
        schema = backend.adapt_schema(_SCHEMA)
        backend.executescript(schema)
        if backend.backend_type == "sqlite":
            backend.get_connection().commit()
        _initialized = True


# ─────────────────────────────────────────────
#  事件记录（匿名统计）
# ─────────────────────────────────────────────

def record_event(
    event_type: str,
    project: str = None,
    os_type: str = None,
    detail: dict = None,
    user_id: int = None,
    ip: str = None,
):
    """
    记录一个使用事件。

    event_type 枚举：
      - plan_generated   生成安装计划
      - install_started  开始安装
      - install_done     安装完成
      - install_failed   安装失败
      - search           搜索项目
      - trending_view    查看热门
      - page_view        访问首页
    """
    init_db()
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else None
    detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO events (event_type, project, os_type, detail, user_id, ip_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, project, os_type, detail_str, user_id, ip_hash),
        )


# ─────────────────────────────────────────────
#  统计查询
# ─────────────────────────────────────────────

def get_stats() -> dict:
    """返回汇总统计信息"""
    init_db()
    conn = _get_conn()

    def _scalar(sql: str, params=()) -> Any:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    total_plans = _scalar("SELECT COUNT(*) FROM events WHERE event_type='plan_generated'")
    total_installs = _scalar("SELECT COUNT(*) FROM events WHERE event_type='install_done'")
    total_users = _scalar("SELECT COUNT(*) FROM users")

    # 最近 7 天活跃
    week_ago = time.time() - 7 * 86400
    active_7d = _scalar(
        "SELECT COUNT(DISTINCT ip_hash) FROM events WHERE ts > ? AND ip_hash IS NOT NULL",
        (week_ago,),
    )

    # 热门项目 TOP 10
    top_projects = [
        {"project": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT project, COUNT(*) as cnt FROM events "
            "WHERE event_type='plan_generated' AND project IS NOT NULL "
            "GROUP BY project ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
    ]

    # OS 分布
    os_dist = [
        {"os": r[0] or "unknown", "count": r[1]}
        for r in conn.execute(
            "SELECT os_type, COUNT(*) as cnt FROM events "
            "WHERE event_type='plan_generated' "
            "GROUP BY os_type ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
    ]

    # 每日趋势（近 30 天）
    month_ago = time.time() - 30 * 86400
    daily_trend = [
        {"date": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') as d, COUNT(*) "
            "FROM events WHERE ts > ? "
            "GROUP BY d ORDER BY d",
            (month_ago,),
        ).fetchall()
    ]

    # 安装成功率
    success = _scalar("SELECT COUNT(*) FROM events WHERE event_type='install_done'")
    failed = _scalar("SELECT COUNT(*) FROM events WHERE event_type='install_failed'")
    success_rate = round(success / max(success + failed, 1) * 100, 1)

    return {
        "total_plans": total_plans,
        "total_installs": total_installs,
        "total_users": total_users,
        "active_7d": active_7d,
        "success_rate": success_rate,
        "top_projects": top_projects,
        "os_distribution": os_dist,
        "daily_trend": daily_trend,
    }


# ─────────────────────────────────────────────
#  用户管理
# ─────────────────────────────────────────────

def _hash_password(password: str, salt: str, iterations: int = 600_000) -> str:
    """PBKDF2 密码哈希"""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations
    ).hex()


def register_user(username: str, email: str, password: str) -> dict:
    """
    注册新用户。
    返回 {"status": "ok", "user_id": ...} 或 {"status": "error", "message": ...}
    """
    init_db()
    username = username.strip()
    email = email.strip().lower()

    if not username or not email or not password:
        return {"status": "error", "message": t("auth.fields_required")}
    if len(password) < 8:
        return {"status": "error", "message": t("auth.password_min", n=8)}

    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)

    try:
        with _transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, email, pw_hash, salt) VALUES (?, ?, ?, ?)",
                (username, email, pw_hash, salt),
            )
            # 跨后端获取新插入 ID
            if _db().backend_type == "postgresql":
                user_id = conn.execute("SELECT currval(pg_get_serial_sequence('users','id'))").fetchone()["currval"]
            else:
                user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"status": "ok", "user_id": user_id}
    except (sqlite3.IntegrityError, Exception) as e:
        # 捕获 SQLite 和 PostgreSQL 的唯一约束冲突
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "integrity" in msg:
            if "email" in msg:
                return {"status": "error", "message": t("auth.email_exists")}
            if "username" in msg:
                return {"status": "error", "message": t("auth.username_taken")}
            return {"status": "error", "message": t("auth.register_failed")}
        raise


def login_user(email: str, password: str) -> dict:
    """
    用户登录。
    返回 {"status": "ok", "user_id": ..., "username": ..., "tier": ..., "token": ...}
    """
    init_db()
    email = email.strip().lower()
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, username, pw_hash, salt, tier FROM users WHERE email = ?",
        (email,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": t("auth.invalid_credentials")}

    pw_hash = _hash_password(password, row["salt"])
    if not hmac.compare_digest(pw_hash, row["pw_hash"]):
        # 兼容旧版 100k 迭代的密码哈希
        pw_hash_legacy = _hash_password(password, row["salt"], iterations=100_000)
        if not hmac.compare_digest(pw_hash_legacy, row["pw_hash"]):
            return {"status": "error", "message": t("auth.invalid_credentials")}
        # 自动升级到 600k 迭代
        new_salt = secrets.token_hex(16)
        new_hash = _hash_password(password, new_salt)
        conn.execute(
            "UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?",
            (new_hash, new_salt, row["id"]),
        )
        conn.commit()
        return {"status": "error", "message": t("auth.invalid_credentials")}

    # 更新最后登录时间
    conn.execute("UPDATE users SET last_login = strftime('%s','now') WHERE id = ?", (row["id"],))
    conn.commit()

    # 生成 session token → 写入 sessions 独立表
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + 7 * 86400  # 7 天过期
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, row["id"], now, expires_at),
    )
    conn.commit()

    return {
        "status": "ok",
        "user_id": row["id"],
        "username": row["username"],
        "tier": row["tier"],
        "token": token,
    }


def validate_token(token: str) -> dict | None:
    """验证 session token，返回 user 信息或 None"""
    if not token:
        return None
    init_db()
    conn = _get_conn()
    now = time.time()

    # 优先查 sessions 独立表
    row = conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if row:
        if now > row["expires_at"]:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        user = conn.execute(
            "SELECT id, username, email, tier, is_admin FROM users WHERE id = ?",
            (row["user_id"],),
        ).fetchone()
        return dict(user) if user else None

    # 向后兼容：查旧 config 表中的 session（自动迁移）
    legacy = conn.execute(
        "SELECT value FROM config WHERE key = ?", (f"session:{token}",)
    ).fetchone()
    if legacy:
        data = json.loads(legacy["value"])
        if now - data["ts"] > 7 * 86400:
            conn.execute("DELETE FROM config WHERE key = ?", (f"session:{token}",))
            conn.commit()
            return None
        # 迁移到新表
        expires_at = data["ts"] + 7 * 86400
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, data["user_id"], data["ts"], expires_at),
            )
            conn.execute("DELETE FROM config WHERE key = ?", (f"session:{token}",))
            conn.commit()
        except Exception:
            pass
        user = conn.execute(
            "SELECT id, username, email, tier, is_admin FROM users WHERE id = ?",
            (data["user_id"],),
        ).fetchone()
        return dict(user) if user else None

    return None


def is_admin(token: str) -> bool:
    """检查 token 对应的用户是否为管理员"""
    user = validate_token(token)
    return bool(user and user.get("is_admin"))


def set_admin(user_id: int, value: bool = True):
    """设置/取消管理员权限"""
    init_db()
    conn = _get_conn()
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if value else 0, user_id))
    conn.commit()


def cleanup_expired_sessions():
    """清理过期的 session 和重置 token"""
    init_db()
    conn = _get_conn()
    now = time.time()
    total_cleaned = 0

    # 清理 sessions 独立表中的过期 session
    cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    total_cleaned += cur.rowcount

    # 清理 reset_tokens 独立表中的过期 token
    cur = conn.execute("DELETE FROM reset_tokens WHERE expires_at < ?", (now,))
    total_cleaned += cur.rowcount

    # 向后兼容：清理旧 config 表中遗留的 session/reset 数据
    rows = conn.execute("SELECT key, value FROM config WHERE key LIKE 'session:%'").fetchall()
    expired = []
    for row in rows:
        try:
            data = json.loads(row["value"])
            if now - data.get("ts", 0) > 7 * 86400:
                expired.append(row["key"])
        except (json.JSONDecodeError, TypeError):
            expired.append(row["key"])
    rows2 = conn.execute("SELECT key, value FROM config WHERE key LIKE 'reset:%'").fetchall()
    for row in rows2:
        try:
            data = json.loads(row["value"])
            if now - data.get("ts", 0) > 1800:
                expired.append(row["key"])
        except (json.JSONDecodeError, TypeError):
            expired.append(row["key"])
    if expired:
        conn.executemany("DELETE FROM config WHERE key = ?", [(k,) for k in expired])
        total_cleaned += len(expired)

    conn.commit()
    return total_cleaned

# ─────────────────────────────────────────────
#  密码重置
# ─────────────────────────────────────────────

def create_reset_token(email: str) -> dict:
    """
    为指定邮箱创建密码重置 token。
    返回 {"status": "ok", "token": ..., "username": ...} 或 {"status": "error", ...}
    """
    init_db()
    email = email.strip().lower()
    conn = _get_conn()
    user = conn.execute(
        "SELECT id, username FROM users WHERE email = ?", (email,)
    ).fetchone()
    if not user:
        # 不透露邮箱是否存在，统一返回 ok
        return {"status": "ok", "token": None, "username": None}

    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + 30 * 60  # 30 分钟有效
    conn.execute(
        "INSERT INTO reset_tokens (token, user_id, email, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, user["id"], email, now, expires_at),
    )
    conn.commit()
    return {"status": "ok", "token": token, "username": user["username"]}


def verify_reset_token(token: str) -> dict | None:
    """验证重置 token，返回 {user_id, email} 或 None（30 分钟有效）"""
    if not token:
        return None
    init_db()
    conn = _get_conn()
    now = time.time()

    # 优先查 reset_tokens 独立表
    row = conn.execute(
        "SELECT user_id, email, expires_at FROM reset_tokens WHERE token = ?", (token,)
    ).fetchone()
    if row:
        if now > row["expires_at"]:
            conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
            conn.commit()
            return None
        return {"user_id": row["user_id"], "email": row["email"]}

    # 向后兼容：查旧 config 表
    legacy = conn.execute(
        "SELECT value FROM config WHERE key = ?", (f"reset:{token}",)
    ).fetchone()
    if legacy:
        data = json.loads(legacy["value"])
        if now - data["ts"] > 30 * 60:
            conn.execute("DELETE FROM config WHERE key = ?", (f"reset:{token}",))
            conn.commit()
            return None
        return {"user_id": data["user_id"], "email": data["email"]}

    return None


def reset_password(token: str, new_password: str) -> dict:
    """
    通过重置 token 修改密码。
    返回 {"status": "ok"} 或 {"status": "error", "message": ...}
    """
    if len(new_password) < 8:
        return {"status": "error", "message": t("auth.password_min", n=8)}

    if not token:
        return {"status": "error", "message": t("auth.reset_expired")}

    init_db()

    # 原子操作：验证 + 删除 token + 更新密码在同一事务中
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(new_password, salt)

    with _transaction() as conn:
        # 优先查 reset_tokens 独立表
        row = conn.execute(
            "SELECT user_id, expires_at FROM reset_tokens WHERE token = ?", (token,)
        ).fetchone()
        if row:
            if time.time() > row["expires_at"]:
                conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
                return {"status": "error", "message": t("auth.reset_expired")}
            conn.execute(
                "UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?",
                (pw_hash, salt, row["user_id"]),
            )
            conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
            return {"status": "ok", "message": t("auth.password_reset_ok")}

        # 向后兼容：查旧 config 表
        legacy = conn.execute(
            "SELECT value FROM config WHERE key = ?", (f"reset:{token}",)
        ).fetchone()
        if not legacy:
            return {"status": "error", "message": t("auth.reset_expired")}

        data = json.loads(legacy["value"])
        if time.time() - data["ts"] > 30 * 60:
            conn.execute("DELETE FROM config WHERE key = ?", (f"reset:{token}",))
            return {"status": "error", "message": t("auth.reset_expired")}

        conn.execute(
            "UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?",
            (pw_hash, salt, data["user_id"]),
        )
        conn.execute("DELETE FROM config WHERE key = ?", (f"reset:{token}",))

    return {"status": "ok", "message": t("auth.password_reset_ok")}

# ─────────────────────────────────────────────
#  配额管理
# ─────────────────────────────────────────────

# 每月免费次数
TIER_LIMITS = {
    "guest": 5,     # 未注册
    "free": 20,     # 注册用户
    "pro": -1,      # 无限制
}


def _current_month() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m")


def check_quota(user_id: int | None = None, ip: str = None) -> dict:
    """
    检查使用配额。
    返回 {"allowed": bool, "used": int, "limit": int, "tier": str}
    """
    init_db()
    conn = _get_conn()
    month = _current_month()

    if user_id:
        user = conn.execute("SELECT tier FROM users WHERE id = ?", (user_id,)).fetchone()
        tier = user["tier"] if user else "guest"
    else:
        tier = "guest"

    limit = TIER_LIMITS.get(tier, 5)

    if limit < 0:  # 无限制
        return {"allowed": True, "used": 0, "limit": -1, "tier": tier}

    if user_id:
        row = conn.execute(
            "SELECT plan_count FROM usage WHERE user_id = ? AND year_month = ?",
            (user_id, month),
        ).fetchone()
        used = row["plan_count"] if row else 0
    else:
        # 匿名用户按 IP 统计
        if not ip:
            return {"allowed": True, "used": 0, "limit": limit, "tier": tier}
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
        month_start = time.time() - 30 * 86400  # 近似
        row = conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE ip_hash = ? AND event_type = 'plan_generated' AND ts > ?",
            (ip_hash, month_start),
        ).fetchone()
        used = row[0] if row else 0

    return {
        "allowed": used < limit,
        "used": used,
        "limit": limit,
        "tier": tier,
    }


def increment_usage(user_id: int):
    """增加已注册用户的月度用量"""
    init_db()
    month = _current_month()
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO usage (user_id, year_month, plan_count) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, year_month) DO UPDATE SET plan_count = plan_count + 1",
            (user_id, month),
        )


# ─────────────────────────────────────────────
#  安装历史
# ─────────────────────────────────────────────

def save_plan_history(
    project: str,
    strategy: str = None,
    confidence: str = None,
    steps: list = None,
    success: bool = None,
    duration: float = None,
    user_id: int = None,
):
    """保存安装计划到历史记录"""
    init_db()
    steps_json = json.dumps(steps, ensure_ascii=False) if steps else None
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO plans_history (project, strategy, confidence, steps_json, success, duration, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project, strategy, confidence, steps_json,
             1 if success else (0 if success is not None else None),
             duration, user_id),
        )


def get_recent_installs(limit: int = 20) -> list[dict]:
    """获取最近的安装记录"""
    init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT project, strategy, confidence, success, duration, ts "
        "FROM plans_history ORDER BY ts DESC LIMIT ?",
        (min(limit, 100),),
    ).fetchall()
    return [
        {
            "project": r["project"],
            "strategy": r["strategy"],
            "confidence": r["confidence"],
            "success": bool(r["success"]) if r["success"] is not None else None,
            "duration": r["duration"],
            "time": r["ts"],
        }
        for r in rows
    ]


# ─────────────────────────────────────────────
#  邮件发送
# ─────────────────────────────────────────────

# 邮件配置通过环境变量：
#   GITINSTALL_SMTP_HOST   (默认 smtp.example.com)
#   GITINSTALL_SMTP_PORT   (默认 465, SSL)
#   GITINSTALL_SMTP_USER   发件邮箱
#   GITINSTALL_SMTP_PASS   授权码/密码
#   GITINSTALL_SMTP_FROM   发件人显示名 (默认 "gitinstall")
#   GITINSTALL_BASE_URL    站点地址 (默认 http://127.0.0.1:8080)

def _get_smtp_config() -> dict | None:
    """获取 SMTP 配置，未配置则返回 None"""
    user = os.environ.get("GITINSTALL_SMTP_USER", "")
    passwd = os.environ.get("GITINSTALL_SMTP_PASS", "")
    if not user or not passwd:
        return None
    return {
        "host": os.environ.get("GITINSTALL_SMTP_HOST", "smtp.example.com"),
        "port": int(os.environ.get("GITINSTALL_SMTP_PORT", "465")),
        "user": user,
        "password": passwd,
        "from_name": os.environ.get("GITINSTALL_SMTP_FROM", "gitinstall"),
    }


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """
    发送 HTML 邮件。
    返回 True 表示已发送，False 表示 SMTP 未配置或发送失败。
    """
    cfg = _get_smtp_config()
    if not cfg:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['user']}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10) as s:
            s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["user"], [to_email], msg.as_string())
        return True
    except Exception:
        return False


def send_welcome_email(to_email: str, username: str) -> bool:
    """注册成功后发送欢迎邮件"""
    base_url = os.environ.get("GITINSTALL_BASE_URL", "http://127.0.0.1:8080")
    safe_user = _html.escape(username)
    safe_email = _html.escape(to_email)
    html = f"""\
<div style="max-width:480px;margin:0 auto;font-family:system-ui,sans-serif;color:#333">
  <h2 style="color:#8b5cf6">{t("email.welcome_greeting")}</h2>
  <p>Hi <strong>{safe_user}</strong>，</p>
  <p>{t("email.register_success")}</p>
  <p>{t("email.account_info")}</p>
  <ul>
    <li>用户名：<strong>{safe_user}</strong></li>
    <li>邮箱：{safe_email}</li>
    <li>{t("email.tier_free")}</li>
  </ul>
  <p>
    <a href="{base_url}" style="display:inline-block;padding:8px 20px;background:#8b5cf6;color:#fff;border-radius:6px;text-decoration:none">
      {t("email.start_using")}
    </a>
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="font-size:12px;color:#999">
    {t("email.forgot_password_hint")}<br>
    {t("email.auto_sent")}
  </p>
</div>"""
    return send_email(to_email, t("email.welcome_subject"), html)


def send_reset_email(to_email: str, username: str, reset_token: str) -> bool:
    """发送密码重置邮件"""
    base_url = os.environ.get("GITINSTALL_BASE_URL", "http://127.0.0.1:8080")
    reset_url = f"{base_url}?reset_token={reset_token}"
    safe_user = _html.escape(username)
    html = f"""\
<div style="max-width:480px;margin:0 auto;font-family:system-ui,sans-serif;color:#333">
  <h2 style="color:#8b5cf6">{t("email.reset_title")}</h2>
  <p>Hi <strong>{safe_user}</strong>，</p>
  <p>{t("email.reset_request")}</p>
  <p>
    <a href="{reset_url}" style="display:inline-block;padding:10px 24px;background:#8b5cf6;color:#fff;border-radius:6px;text-decoration:none;font-weight:600">
      {t("email.reset_button")}
    </a>
  </p>
  <p style="font-size:13px;color:#666">
    {t("email.reset_validity")}
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="font-size:12px;color:#999">
    {t("email.reset_fallback")}<br>
    <span style="word-break:break-all">{reset_url}</span>
  </p>
</div>"""
    return send_email(to_email, t("email.reset_subject"), html)


# ─────────────────────────────────────────────
#  安装智能追踪（数据飞轮）
# ─────────────────────────────────────────────

def record_install_telemetry(
    project: str,
    strategy: str = None,
    gpu_info: dict = None,
    env: dict = None,
    success: bool = None,
    error_type: str = None,
    error_message: str = None,
    duration_sec: float = None,
    steps_total: int = None,
    steps_completed: int = None,
):
    """
    记录安装遥测数据。每次安装尝试都应调用此函数。
    这些数据用于：
      - 安装成功率统计（按项目/OS/GPU 维度）
      - 智能推荐优化
      - 常见错误模式识别
    """
    init_db()
    os_info = (env or {}).get("os", {})
    hw = (env or {}).get("hardware", {})
    gpu = gpu_info or {}
    env_hash = hashlib.sha256(
        json.dumps({"os": os_info.get("type"), "gpu": gpu.get("type"),
                     "arch": os_info.get("arch")}, sort_keys=True).encode()
    ).hexdigest()[:16]

    with _transaction() as conn:
        conn.execute(
            "INSERT INTO install_telemetry "
            "(project, strategy, os_type, os_version, arch, gpu_type, gpu_name, "
            "vram_gb, cuda_version, ram_gb, success, error_type, error_message, "
            "duration_sec, steps_total, steps_completed, env_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project, strategy,
                os_info.get("type"), os_info.get("version"), os_info.get("arch"),
                gpu.get("type"), gpu.get("name"),
                gpu.get("vram_gb"), gpu.get("cuda_version"),
                hw.get("ram_gb"),
                1 if success else (0 if success is not None else None),
                error_type,
                (error_message or "")[:500],  # 限制长度
                duration_sec,
                steps_total, steps_completed, env_hash,
            ),
        )


def get_project_success_rate(project: str) -> dict:
    """
    查询项目的安装成功率（按 GPU 类型分组）。

    Returns:
        {
            "overall": {"total": int, "success": int, "rate": float},
            "by_gpu": {"nvidia": {"total": .., "rate": ..}, ...},
            "by_os": {"macos": {"total": .., "rate": ..}, ...},
            "common_errors": [{"error_type": str, "count": int}, ...],
        }
    """
    init_db()
    conn = _get_conn()
    project = project.lower()

    # 总体成功率
    total = conn.execute(
        "SELECT COUNT(*) FROM install_telemetry WHERE project = ? AND success IS NOT NULL",
        (project,)
    ).fetchone()[0]
    success = conn.execute(
        "SELECT COUNT(*) FROM install_telemetry WHERE project = ? AND success = 1",
        (project,)
    ).fetchone()[0]

    # 按 GPU 类型
    by_gpu = {}
    for row in conn.execute(
        "SELECT gpu_type, COUNT(*) as cnt, SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok "
        "FROM install_telemetry WHERE project = ? AND success IS NOT NULL AND gpu_type IS NOT NULL "
        "GROUP BY gpu_type",
        (project,)
    ).fetchall():
        by_gpu[row[0]] = {"total": row[1], "success": row[2], "rate": round(row[2] / max(row[1], 1) * 100, 1)}

    # 按 OS
    by_os = {}
    for row in conn.execute(
        "SELECT os_type, COUNT(*) as cnt, SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok "
        "FROM install_telemetry WHERE project = ? AND success IS NOT NULL AND os_type IS NOT NULL "
        "GROUP BY os_type",
        (project,)
    ).fetchall():
        by_os[row[0]] = {"total": row[1], "success": row[2], "rate": round(row[2] / max(row[1], 1) * 100, 1)}

    # 常见错误
    common_errors = [
        {"error_type": row[0], "count": row[1]}
        for row in conn.execute(
            "SELECT error_type, COUNT(*) as cnt FROM install_telemetry "
            "WHERE project = ? AND success = 0 AND error_type IS NOT NULL "
            "GROUP BY error_type ORDER BY cnt DESC LIMIT 5",
            (project,)
        ).fetchall()
    ]

    return {
        "overall": {
            "total": total,
            "success": success,
            "rate": round(success / max(total, 1) * 100, 1),
        },
        "by_gpu": by_gpu,
        "by_os": by_os,
        "common_errors": common_errors,
    }
