"""
test_db.py - db.py 数据库模块全面测试
=======================================

覆盖：Schema 初始化、事件记录、用户注册/登录、Token 验证、
      配额管理、安装历史、密码重置、会话清理、安装遥测。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── 让 db 模块使用临时数据库 ──
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    """每个测试使用独立的临时数据库"""
    import db as db_mod
    from db_backend import SQLiteBackend, set_backend
    monkeypatch.setattr(db_mod, "DB_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    # 重置初始化状态
    db_mod._initialized = False
    # 使用临时目录的 SQLite 后端
    backend = SQLiteBackend(db_path=str(tmp_path / "test.db"))
    set_backend(backend)
    yield
    # 清理
    backend.close()
    set_backend(None)
    db_mod._initialized = False


# ─────────────────────────────────────────────
#  Schema 初始化
# ─────────────────────────────────────────────

class TestInitDB:
    def test_init_creates_tables(self):
        import db
        db.init_db()
        conn = db._get_conn()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "events" in tables
        assert "users" in tables
        assert "usage" in tables
        assert "plans_history" in tables
        assert "config" in tables
        assert "install_telemetry" in tables

    def test_init_idempotent(self):
        import db
        db.init_db()
        db.init_db()  # 再次调用不应报错
        conn = db._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    def test_wal_mode(self):
        import db
        db.init_db()
        conn = db._get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


# ─────────────────────────────────────────────
#  事件记录
# ─────────────────────────────────────────────

class TestEvents:
    def test_record_event_basic(self):
        import db
        db.record_event("plan_generated", project="ollama/ollama", os_type="macos")
        conn = db._get_conn()
        row = conn.execute("SELECT * FROM events WHERE event_type='plan_generated'").fetchone()
        assert row is not None
        assert row["project"] == "ollama/ollama"
        assert row["os_type"] == "macos"

    def test_record_event_with_ip(self):
        import db
        db.record_event("page_view", ip="127.0.0.1")
        conn = db._get_conn()
        row = conn.execute("SELECT ip_hash FROM events").fetchone()
        expected_hash = hashlib.sha256(b"127.0.0.1").hexdigest()[:16]
        assert row["ip_hash"] == expected_hash

    def test_record_event_with_detail(self):
        import db
        db.record_event("install_done", detail={"steps": 5, "duration": 30.0})
        conn = db._get_conn()
        row = conn.execute("SELECT detail FROM events").fetchone()
        data = json.loads(row["detail"])
        assert data["steps"] == 5

    def test_record_event_no_ip(self):
        import db
        db.record_event("search", project="test/repo")
        conn = db._get_conn()
        row = conn.execute("SELECT ip_hash FROM events").fetchone()
        assert row["ip_hash"] is None

    def test_record_event_with_user(self):
        import db
        db.record_event("trending_view", user_id=42)
        conn = db._get_conn()
        row = conn.execute("SELECT user_id FROM events").fetchone()
        assert row["user_id"] == 42


# ─────────────────────────────────────────────
#  统计查询
# ─────────────────────────────────────────────

class TestStats:
    def test_get_stats_empty(self):
        import db
        stats = db.get_stats()
        assert stats["total_plans"] == 0
        assert stats["total_installs"] == 0
        assert stats["total_users"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["top_projects"] == []

    def test_get_stats_with_data(self):
        import db
        db.record_event("plan_generated", project="ollama/ollama", os_type="macos")
        db.record_event("plan_generated", project="ollama/ollama")
        db.record_event("plan_generated", project="comfyanonymous/comfyui")
        db.record_event("install_done", project="ollama/ollama")
        stats = db.get_stats()
        assert stats["total_plans"] == 3
        assert stats["total_installs"] == 1
        assert len(stats["top_projects"]) >= 1

    def test_success_rate_calculation(self):
        import db
        db.record_event("install_done")
        db.record_event("install_done")
        db.record_event("install_failed")
        stats = db.get_stats()
        assert stats["success_rate"] == pytest.approx(66.7, abs=0.1)


# ─────────────────────────────────────────────
#  用户管理
# ─────────────────────────────────────────────

class TestUserManagement:
    def test_register_success(self):
        import db
        result = db.register_user("testuser", "test@example.com", "password123")
        assert result["status"] == "ok"
        assert "user_id" in result

    def test_register_duplicate_email(self):
        import db
        db.register_user("user1", "test@example.com", "password123")
        result = db.register_user("user2", "test@example.com", "password456")
        assert result["status"] == "error"
        assert "邮箱" in result["message"]

    def test_register_duplicate_username(self):
        import db
        db.register_user("testuser", "test1@example.com", "password123")
        result = db.register_user("testuser", "test2@example.com", "password456")
        assert result["status"] == "error"
        assert "用户名" in result["message"]

    def test_register_empty_fields(self):
        import db
        result = db.register_user("", "test@example.com", "password123")
        assert result["status"] == "error"

    def test_register_short_password(self):
        import db
        result = db.register_user("user", "test@example.com", "short")
        assert result["status"] == "error"
        assert "8" in result["message"]

    def test_login_success(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        result = db.login_user("test@example.com", "password123")
        assert result["status"] == "ok"
        assert result["username"] == "testuser"
        assert "token" in result

    def test_login_wrong_password(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        result = db.login_user("test@example.com", "wrongpassword")
        assert result["status"] == "error"

    def test_login_nonexistent_email(self):
        import db
        result = db.login_user("nobody@example.com", "password123")
        assert result["status"] == "error"

    def test_login_case_insensitive_email(self):
        import db
        db.register_user("testuser", "Test@Example.com", "password123")
        result = db.login_user("test@example.com", "password123")
        assert result["status"] == "ok"

    def test_login_updates_last_login(self):
        import db
        reg = db.register_user("testuser", "test@example.com", "password123")
        db.login_user("test@example.com", "password123")
        conn = db._get_conn()
        row = conn.execute("SELECT last_login FROM users WHERE id=?", (reg["user_id"],)).fetchone()
        assert row["last_login"] is not None


# ─────────────────────────────────────────────
#  Token 验证
# ─────────────────────────────────────────────

class TestTokenValidation:
    def test_validate_valid_token(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        login = db.login_user("test@example.com", "password123")
        user = db.validate_token(login["token"])
        assert user is not None
        assert user["username"] == "testuser"

    def test_validate_invalid_token(self):
        import db
        db.init_db()
        assert db.validate_token("invalid-token-12345") is None

    def test_validate_empty_token(self):
        import db
        assert db.validate_token("") is None
        assert db.validate_token(None) is None

    def test_token_expiry(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        login = db.login_user("test@example.com", "password123")
        token = login["token"]
        # 把 session 过期时间改为过去（模拟过期）
        conn = db._get_conn()
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 86400, token),
        )
        conn.commit()
        assert db.validate_token(token) is None


# ─────────────────────────────────────────────
#  管理员
# ─────────────────────────────────────────────

class TestAdmin:
    def test_set_admin(self):
        import db
        reg = db.register_user("admin", "admin@example.com", "password123")
        db.set_admin(reg["user_id"], True)
        login = db.login_user("admin@example.com", "password123")
        assert db.is_admin(login["token"]) is True

    def test_non_admin(self):
        import db
        db.register_user("user", "user@example.com", "password123")
        login = db.login_user("user@example.com", "password123")
        assert db.is_admin(login["token"]) is False

    def test_is_admin_invalid_token(self):
        import db
        db.init_db()
        assert db.is_admin("bogus") is False


# ─────────────────────────────────────────────
#  会话清理
# ─────────────────────────────────────────────

class TestSessionCleanup:
    def test_cleanup_expired_sessions(self):
        import db
        db.init_db()
        conn = db._get_conn()
        # 插入一个过期 session（8天前）
        old_data = json.dumps({"user_id": 1, "ts": time.time() - 8 * 86400})
        conn.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("session:old", old_data))
        # 插入一个有效 session
        new_data = json.dumps({"user_id": 1, "ts": time.time()})
        conn.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("session:new", new_data))
        conn.commit()
        cleaned = db.cleanup_expired_sessions()
        assert cleaned >= 1
        # 有效 session 应保留
        row = conn.execute("SELECT * FROM config WHERE key='session:new'").fetchone()
        assert row is not None

    def test_cleanup_expired_reset_tokens(self):
        import db
        db.init_db()
        conn = db._get_conn()
        old_data = json.dumps({"user_id": 1, "ts": time.time() - 3600})  # 1小时前
        conn.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("reset:old", old_data))
        conn.commit()
        cleaned = db.cleanup_expired_sessions()
        assert cleaned >= 1

    def test_cleanup_no_expired(self):
        import db
        db.init_db()
        cleaned = db.cleanup_expired_sessions()
        assert cleaned == 0


# ─────────────────────────────────────────────
#  密码重置
# ─────────────────────────────────────────────

class TestPasswordReset:
    def test_create_reset_token(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        result = db.create_reset_token("test@example.com")
        assert result["status"] == "ok"
        assert result["token"] is not None
        assert result["username"] == "testuser"

    def test_create_reset_token_nonexistent(self):
        import db
        db.init_db()
        result = db.create_reset_token("nobody@example.com")
        assert result["status"] == "ok"
        assert result["token"] is None  # 不透露是否存在

    def test_verify_reset_token(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        reset = db.create_reset_token("test@example.com")
        data = db.verify_reset_token(reset["token"])
        assert data is not None
        assert data["email"] == "test@example.com"

    def test_verify_expired_reset_token(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        reset = db.create_reset_token("test@example.com")
        # 修改 reset_tokens 表中的过期时间为过去
        conn = db._get_conn()
        conn.execute(
            "UPDATE reset_tokens SET expires_at = ? WHERE token = ?",
            (time.time() - 60, reset["token"]),
        )
        conn.commit()
        assert db.verify_reset_token(reset["token"]) is None

    def test_verify_empty_token(self):
        import db
        assert db.verify_reset_token("") is None
        assert db.verify_reset_token(None) is None

    def test_reset_password_success(self):
        import db
        db.register_user("testuser", "test@example.com", "password123")
        reset = db.create_reset_token("test@example.com")
        result = db.reset_password(reset["token"], "newpassword123")
        assert result["status"] == "ok"
        # 新密码应该能登录
        login = db.login_user("test@example.com", "newpassword123")
        assert login["status"] == "ok"

    def test_reset_password_short(self):
        import db
        result = db.reset_password("token", "short")
        assert result["status"] == "error"

    def test_reset_password_empty_token(self):
        import db
        result = db.reset_password("", "newpassword123")
        assert result["status"] == "error"

    def test_reset_password_invalid_token(self):
        import db
        db.init_db()
        result = db.reset_password("fake-token", "newpassword123")
        assert result["status"] == "error"


# ─────────────────────────────────────────────
#  配额管理
# ─────────────────────────────────────────────

class TestQuota:
    def test_check_quota_guest(self):
        import db
        result = db.check_quota()
        assert result["tier"] == "guest"
        assert result["limit"] == 5

    def test_check_quota_free_user(self):
        import db
        reg = db.register_user("testuser", "test@example.com", "password123")
        result = db.check_quota(user_id=reg["user_id"])
        assert result["tier"] == "free"
        assert result["limit"] == 20
        assert result["used"] == 0

    def test_increment_usage(self):
        import db
        reg = db.register_user("testuser", "test@example.com", "password123")
        db.increment_usage(reg["user_id"])
        db.increment_usage(reg["user_id"])
        result = db.check_quota(user_id=reg["user_id"])
        assert result["used"] == 2

    def test_guest_ip_quota(self):
        import db
        # 记录若干事件
        for _ in range(3):
            db.record_event("plan_generated", ip="10.0.0.1")
        result = db.check_quota(ip="10.0.0.1")
        assert result["used"] == 3

    def test_guest_no_ip(self):
        import db
        result = db.check_quota()
        assert result["allowed"] is True


# ─────────────────────────────────────────────
#  安装历史
# ─────────────────────────────────────────────

class TestInstallHistory:
    def test_save_plan_history(self):
        import db
        db.save_plan_history(
            project="ollama/ollama",
            strategy="known_project",
            confidence="high",
            steps=[{"command": "brew install ollama", "description": "安装"}],
            success=True,
            duration=5.0,
        )
        records = db.get_recent_installs(limit=1)
        assert len(records) == 1
        assert records[0]["project"] == "ollama/ollama"
        assert records[0]["success"] is True

    def test_get_recent_installs_limit(self):
        import db
        for i in range(5):
            db.save_plan_history(project=f"test/project{i}", success=True)
        records = db.get_recent_installs(limit=3)
        assert len(records) == 3

    def test_get_recent_installs_empty(self):
        import db
        db.init_db()
        records = db.get_recent_installs()
        assert records == []

    def test_limit_cap(self):
        import db
        # limit 超过 100 应被截断
        records = db.get_recent_installs(limit=200)
        assert isinstance(records, list)


# ─────────────────────────────────────────────
#  安装遥测（数据飞轮）
# ─────────────────────────────────────────────

class TestInstallTelemetry:
    def test_record_telemetry_basic(self):
        import db
        db.record_install_telemetry(
            project="ollama/ollama",
            strategy="known_project",
            success=True,
            steps_total=3,
            steps_completed=3,
        )
        conn = db._get_conn()
        row = conn.execute("SELECT * FROM install_telemetry").fetchone()
        assert row is not None
        assert row["project"] == "ollama/ollama"
        assert row["success"] == 1

    def test_record_telemetry_with_gpu(self):
        import db
        db.record_install_telemetry(
            project="comfyanonymous/comfyui",
            gpu_info={"type": "nvidia", "name": "RTX 4090", "vram_gb": 24, "cuda_version": "12.4"},
            env={"os": {"type": "linux", "version": "22.04", "arch": "x86_64"}, "hardware": {"ram_gb": 64}},
            success=True,
        )
        conn = db._get_conn()
        row = conn.execute("SELECT * FROM install_telemetry").fetchone()
        assert row["gpu_type"] == "nvidia"
        assert row["gpu_name"] == "RTX 4090"
        assert row["vram_gb"] == 24
        assert row["os_type"] == "linux"

    def test_record_telemetry_failure(self):
        import db
        db.record_install_telemetry(
            project="test/project",
            success=False,
            error_type="step_failed",
            error_message="Command not found: cmake",
        )
        conn = db._get_conn()
        row = conn.execute("SELECT * FROM install_telemetry").fetchone()
        assert row["success"] == 0
        assert row["error_type"] == "step_failed"

    def test_error_message_truncated(self):
        import db
        long_msg = "x" * 1000
        db.record_install_telemetry(
            project="test/project",
            success=False,
            error_message=long_msg,
        )
        conn = db._get_conn()
        row = conn.execute("SELECT error_message FROM install_telemetry").fetchone()
        assert len(row["error_message"]) == 500

    def test_get_project_success_rate_empty(self):
        import db
        result = db.get_project_success_rate("test/project")
        assert result["overall"]["total"] == 0
        assert result["overall"]["rate"] == 0.0

    def test_get_project_success_rate_with_data(self):
        import db
        for _ in range(7):
            db.record_install_telemetry(
                project="ollama/ollama", success=True,
                gpu_info={"type": "apple_mps"},
                env={"os": {"type": "macos"}, "hardware": {}},
            )
        for _ in range(3):
            db.record_install_telemetry(
                project="ollama/ollama", success=False,
                gpu_info={"type": "nvidia"},
                env={"os": {"type": "linux"}, "hardware": {}},
            )
        result = db.get_project_success_rate("ollama/ollama")
        assert result["overall"]["total"] == 10
        assert result["overall"]["rate"] == 70.0
        assert "apple_mps" in result["by_gpu"]
        assert "macos" in result["by_os"]


# ─────────────────────────────────────────────
#  密码哈希
# ─────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_deterministic(self):
        import db
        h1 = db._hash_password("test", "salt123")
        h2 = db._hash_password("test", "salt123")
        assert h1 == h2

    def test_hash_different_salts(self):
        import db
        h1 = db._hash_password("test", "salt1")
        h2 = db._hash_password("test", "salt2")
        assert h1 != h2

    def test_hash_format(self):
        import db
        h = db._hash_password("password", "salt")
        # Should be hex string
        assert all(c in "0123456789abcdef" for c in h)


# ─────────────────────────────────────────────
#  邮件（mock）
# ─────────────────────────────────────────────

class TestEmail:
    def test_get_smtp_config_missing(self):
        import db
        with patch.dict(os.environ, {}, clear=True):
            result = db._get_smtp_config()
            assert result is None

    def test_get_smtp_config_present(self):
        import db
        with patch.dict(os.environ, {
            "GITINSTALL_SMTP_USER": "user@test.com",
            "GITINSTALL_SMTP_PASS": "secret",
        }):
            result = db._get_smtp_config()
            assert result is not None
            assert result["user"] == "user@test.com"

    def test_send_email_no_config(self):
        import db
        with patch.dict(os.environ, {}, clear=True):
            result = db.send_email("test@example.com", "Subject", "<p>Body</p>")
            assert result is False

    def test_send_welcome_email_no_config(self):
        import db
        with patch.dict(os.environ, {}, clear=True):
            result = db.send_welcome_email("test@example.com", "testuser")
            assert result is False

    def test_send_reset_email_no_config(self):
        import db
        with patch.dict(os.environ, {}, clear=True):
            result = db.send_reset_email("test@example.com", "testuser", "token123")
            assert result is False


# ─────────────────────────────────────────────
#  事务
# ─────────────────────────────────────────────

class TestTransaction:
    def test_transaction_commit(self):
        import db
        db.init_db()
        with db._transaction() as conn:
            conn.execute("INSERT INTO config (key, value) VALUES ('test', 'value')")
        conn = db._get_conn()
        row = conn.execute("SELECT value FROM config WHERE key='test'").fetchone()
        assert row["value"] == "value"

    def test_transaction_rollback(self):
        import db
        db.init_db()
        try:
            with db._transaction() as conn:
                conn.execute("INSERT INTO config (key, value) VALUES ('test', 'value')")
                raise ValueError("deliberate error")
        except ValueError:
            pass
        conn = db._get_conn()
        row = conn.execute("SELECT value FROM config WHERE key='test'").fetchone()
        assert row is None


# ─────────────────────────────────────────────
#  Tier 限制
# ─────────────────────────────────────────────

class TestTierLimits:
    def test_tier_limits_defined(self):
        import db
        assert db.TIER_LIMITS["guest"] == 5
        assert db.TIER_LIMITS["free"] == 20
        assert db.TIER_LIMITS["pro"] == -1

    def test_pro_unlimited(self):
        import db
        reg = db.register_user("pro", "pro@example.com", "password123")
        conn = db._get_conn()
        conn.execute("UPDATE users SET tier='pro' WHERE id=?", (reg["user_id"],))
        conn.commit()
        result = db.check_quota(user_id=reg["user_id"])
        assert result["allowed"] is True
        assert result["limit"] == -1
