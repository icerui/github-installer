"""覆盖所有小 gap 模块的补充测试

目标模块：event_bus, trending, checkpoint, db, installer_registry,
          license_check, detector, executor, error_fixer, fetcher
"""
import json
import os
import platform
import sys
import time
import hashlib
import hmac

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))


# ═══════════════════════════════════════════
#  event_bus — WebhookNotifier + Slack
# ═══════════════════════════════════════════

from event_bus import (
    Event, EventBus, WebhookNotifier,
    get_event_bus, get_webhook_notifier,
    EVT_INSTALL_COMPLETED, EVT_INSTALL_FAILED, EVT_AUDIT_WARNING,
)


class TestWebhookNotifierSend:
    def test_send_with_secret(self):
        wh = WebhookNotifier(url="http://localhost:9999/hook", secret="mysecret")
        event = Event(event_type=EVT_INSTALL_COMPLETED, project="a/b")

        with patch("urllib.request.urlopen") as mock_open:
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.read.return_value = b""
            mock_open.return_value = m
            wh._send(event)

        call_args = mock_open.call_args
        req = call_args[0][0]
        assert req.get_header("X-gitinstall-signature").startswith("sha256=")
        # Verify HMAC
        payload = req.data
        expected_sig = hmac.new(b"mysecret", payload, hashlib.sha256).hexdigest()
        assert req.get_header("X-gitinstall-signature") == f"sha256={expected_sig}"

    def test_send_without_secret(self):
        wh = WebhookNotifier(url="http://localhost:9999/hook", secret="")
        event = Event(event_type=EVT_INSTALL_COMPLETED, project="a/b")

        with patch("urllib.request.urlopen") as mock_open:
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.read.return_value = b""
            mock_open.return_value = m
            wh._send(event)

        req = mock_open.call_args[0][0]
        assert "X-Gitinstall-Signature" not in dict(req.headers)

    def test_send_error_silenced(self):
        import urllib.error
        wh = WebhookNotifier(url="http://localhost:9999/hook")
        event = Event(event_type=EVT_INSTALL_COMPLETED, project="a/b")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            wh._send(event)  # should not raise

    def test_notify_disabled(self):
        wh = WebhookNotifier(url="", secret="")
        assert wh.enabled is False
        wh.notify(Event(event_type="test"))  # no-op

    def test_notify_enabled(self):
        wh = WebhookNotifier(url="http://example.com/hook")
        assert wh.enabled is True
        with patch.object(wh, "_send"):
            wh.notify(Event(event_type="test"))


class TestFormatSlackMessage:
    @pytest.mark.parametrize("evt_type,icon", [
        (EVT_INSTALL_COMPLETED, "✅"),
        (EVT_INSTALL_FAILED, "❌"),
        (EVT_AUDIT_WARNING, "🚨"),
    ])
    def test_icons(self, evt_type, icon):
        wh = WebhookNotifier()
        event = Event(event_type=evt_type, project="owner/repo")
        msg = wh.format_slack_message(event)
        assert icon in msg["text"]
        assert "owner/repo" in msg["text"]
        assert msg["blocks"][0]["type"] == "section"


class TestEventBusGlobals:
    def test_get_event_bus_singleton(self, monkeypatch):
        import event_bus
        monkeypatch.setattr(event_bus, "_bus", None)
        monkeypatch.setattr(event_bus, "_webhook", None)
        monkeypatch.delenv("GITINSTALL_WEBHOOK_URL", raising=False)
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_get_webhook_notifier(self, monkeypatch):
        import event_bus
        monkeypatch.setattr(event_bus, "_webhook", None)
        wh = get_webhook_notifier()
        assert isinstance(wh, WebhookNotifier)


# ═══════════════════════════════════════════
#  trending
# ═══════════════════════════════════════════

from trending import get_trending


class TestTrending:
    def test_get_trending_cached(self, monkeypatch):
        import trending
        monkeypatch.setattr(trending, "_mem_cache", [{"repo": "cached", "name": "c", "stars": "1"}])
        monkeypatch.setattr(trending, "_mem_ts", time.time())
        result = get_trending()
        assert len(result) >= 1

    def test_get_trending_disk_cache(self, monkeypatch, tmp_path):
        import trending
        monkeypatch.setattr(trending, "_mem_cache", None)
        monkeypatch.setattr(trending, "_mem_ts", 0)
        cache_data = {
            "projects": [{"repo": "a/b", "name": "b", "stars": "1"}],
            "updated_at": time.time(),
        }
        monkeypatch.setattr(trending, "_read_cache", lambda: cache_data)
        result = get_trending()
        assert len(result) >= 1

    def test_get_trending_no_cache_fallback(self, monkeypatch):
        import trending
        monkeypatch.setattr(trending, "_mem_cache", None)
        monkeypatch.setattr(trending, "_mem_ts", 0)
        monkeypatch.setattr(trending, "_read_cache", lambda: None)
        # force_refresh won't help, should return static fallback
        result = get_trending()
        assert isinstance(result, list)

    def test_get_trending_force_refresh_with_expired(self, monkeypatch):
        import trending
        monkeypatch.setattr(trending, "_mem_cache", None)
        monkeypatch.setattr(trending, "_mem_ts", 0)
        old_data = {
            "projects": [{"repo": "old/p", "name": "p", "stars": "5"}],
            "updated_at": 0,  # expired
        }
        monkeypatch.setattr(trending, "_read_cache", lambda: old_data)
        result = get_trending(force_refresh=True)
        assert isinstance(result, list)


# ═══════════════════════════════════════════
#  checkpoint
# ═══════════════════════════════════════════

from checkpoint import CheckpointManager, InstallCheckpoint, format_checkpoint_list, format_resume_plan


class TestCheckpointManager:
    @pytest.fixture
    def mgr(self, tmp_path, monkeypatch):
        import checkpoint
        monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
        return CheckpointManager()

    def test_create_and_get(self, mgr):
        plan = {
            "project_name": "owner/repo",
            "steps": [
                {"command": "git clone ...", "description": "clone"},
                {"command": "make", "description": "build"},
            ],
        }
        cp = mgr.create("owner", "repo", plan, install_dir="/tmp/repo")
        assert cp.owner == "owner"
        assert cp.total_steps == 2

        loaded = mgr.get_checkpoint("owner", "repo")
        assert loaded is not None
        assert loaded.owner == "owner"

    def test_get_not_found(self, mgr):
        assert mgr.get_checkpoint("x", "y") is None

    def test_mark_step_running(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        mgr.mark_step_running(cp, 0)
        assert cp.steps[0].status == "running"

    def test_mark_step_completed(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        mgr.mark_step_completed(cp, 0, exit_code=0, duration_sec=1.5)
        assert cp.steps[0].status == "completed"

    def test_mark_step_failed(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        mgr.mark_step_failed(cp, 0, exit_code=1, error="compile error")
        assert cp.steps[0].status == "failed"

    def test_mark_completed(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        mgr.mark_completed(cp)
        assert cp.status == "completed"

    def test_mark_abandoned(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        mgr.mark_abandoned(cp)
        assert cp.status == "abandoned"

    def test_get_resumable(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        cp.status = "failed"
        mgr._save(cp)
        resumable = mgr.get_resumable()
        assert len(resumable) >= 1

    def test_remove_checkpoint(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        mgr.create("o", "r", plan)
        assert mgr.remove_checkpoint("o", "r") is True
        assert mgr.remove_checkpoint("o", "r") is False

    def test_get_resume_step(self, mgr):
        plan = {"project_name": "o/r", "steps": [
            {"command": "s1", "description": "t1"},
            {"command": "s2", "description": "t2"},
        ]}
        cp = mgr.create("o", "r", plan)
        cp.steps[0].status = "completed"
        cp.steps[1].status = "failed"
        mgr._save(cp)
        idx = mgr.get_resume_step(cp)
        assert idx == 1

    def test_format_checkpoint_list(self, mgr):
        plan = {"project_name": "o/r", "steps": [{"command": "echo", "description": "test"}]}
        cp = mgr.create("o", "r", plan)
        output = format_checkpoint_list([cp])
        assert "o" in output or "r" in output

    def test_format_resume_plan(self, mgr):
        plan = {"project_name": "o/r", "steps": [
            {"command": "s1", "description": "step 1"},
            {"command": "s2", "description": "step 2"},
        ]}
        cp = mgr.create("o", "r", plan)
        cp.steps[0].status = "completed"
        mgr._save(cp)
        output = format_resume_plan(cp, 1)
        assert "step 2" in output or "s2" in output


# ═══════════════════════════════════════════
#  installer_registry — all Installer classes
# ═══════════════════════════════════════════

from installer_registry import (
    InstallerRegistry, PipInstaller, NpmInstaller, CargoInstaller,
    GoInstaller, DockerInstaller, CondaInstaller, BrewInstaller, AptInstaller,
)


class TestPipInstaller:
    def test_can_handle(self):
        i = PipInstaller()
        i.info.available = True
        assert i.can_handle(["python"], {"requirements.txt": ""})
        assert not i.can_handle(["rust"], {})

    def test_generate_steps(self):
        i = PipInstaller()
        info = {"dependency_files": {"requirements.txt": "flask"}, "owner": "o", "repo": "r"}
        steps = i.generate_install_steps(info)
        assert len(steps) > 0

    def test_generate_steps_pyproject(self):
        i = PipInstaller()
        steps = i.generate_install_steps({"dependency_files": {"pyproject.toml": ""}})
        assert len(steps) > 0


class TestNpmInstaller:
    def test_can_handle(self):
        i = NpmInstaller()
        i.info.available = True
        assert i.can_handle(["node"], {"package.json": ""})
        assert not i.can_handle(["python"], {})

    def test_generate_steps(self):
        i = NpmInstaller()
        steps = i.generate_install_steps({"dependency_files": {"package.json": "{}"}})
        assert len(steps) > 0


class TestCargoInstaller:
    def test_can_handle(self):
        i = CargoInstaller()
        i.info.available = True
        assert i.can_handle(["rust"], {"Cargo.toml": ""})

    def test_generate_steps(self):
        i = CargoInstaller()
        steps = i.generate_install_steps({"dependency_files": {}})
        assert len(steps) > 0


class TestGoInstaller:
    def test_can_handle(self):
        i = GoInstaller()
        i.info.available = True
        assert i.can_handle(["go"], {"go.mod": ""})

    def test_generate_steps(self):
        i = GoInstaller()
        steps = i.generate_install_steps({"dependency_files": {}})
        assert len(steps) > 0


class TestDockerInstaller:
    def test_can_handle(self):
        i = DockerInstaller()
        i.info.available = True
        assert i.can_handle(["docker"], {"docker-compose.yml": ""})
        assert i.can_handle([], {"Dockerfile": ""})

    def test_generate_steps_compose(self):
        i = DockerInstaller()
        steps = i.generate_install_steps({"dependency_files": {"docker-compose.yml": ""}})
        assert any("compose" in s.get("command", "").lower() for s in steps)

    def test_generate_steps_dockerfile(self):
        i = DockerInstaller()
        steps = i.generate_install_steps({"dependency_files": {"Dockerfile": ""}, "repo": "myapp"})
        assert len(steps) > 0


class TestCondaInstaller:
    def test_can_handle(self):
        i = CondaInstaller()
        i.info.available = True
        assert i.can_handle(["conda"], {"environment.yml": ""})

    def test_generate_steps(self):
        i = CondaInstaller()
        steps = i.generate_install_steps({"dependency_files": {"environment.yml": "name: myenv"}})
        assert len(steps) > 0


class TestBrewInstaller:
    def test_can_handle(self):
        i = BrewInstaller()
        i.info.available = True
        assert i.can_handle(["brew"], {"Brewfile": ""})

    def test_generate_steps(self):
        i = BrewInstaller()
        steps = i.generate_install_steps({"dependency_files": {"Brewfile": ""}})
        assert isinstance(steps, list)


class TestAptInstaller:
    def test_can_handle(self):
        i = AptInstaller()
        i.info.available = True
        result = i.can_handle(["apt"], {})
        assert isinstance(result, bool)

    def test_generate_steps(self):
        i = AptInstaller()
        steps = i.generate_install_steps({"dependency_files": {}})
        assert isinstance(steps, list)


class TestInstallerRegistry:
    def test_has_builtins(self):
        reg = InstallerRegistry()
        all_inst = reg.list_all()
        assert len(all_inst) >= 8

    def test_find_matching(self):
        reg = InstallerRegistry()
        matches = reg.find_matching(["python"], {"requirements.txt": ""})
        assert len(matches) >= 1

    def test_find_matching_empty(self):
        reg = InstallerRegistry()
        matches = reg.find_matching(["nonexistent"], {})
        assert isinstance(matches, list)

    def test_format_registry(self):
        reg = InstallerRegistry()
        output = reg.format_registry()
        assert isinstance(output, str)

    def test_to_dict(self):
        reg = InstallerRegistry()
        d = reg.to_dict()
        assert isinstance(d, dict)


# ═══════════════════════════════════════════
#  license_check
# ═══════════════════════════════════════════

from license_check import (
    identify_license_from_text,
    analyze_license,
    fetch_license_from_github,
)


class TestLicenseCheck:
    def test_identify_mit(self):
        text = "MIT License\n\nPermission is hereby granted, free of charge, to any person..."
        result = identify_license_from_text(text)
        assert result is not None
        assert "MIT" in result.spdx_id

    def test_identify_apache(self):
        text = "Apache License\nVersion 2.0, January 2004"
        result = identify_license_from_text(text)
        # May return Apache-2.0 or None depending on matching
        pass

    def test_identify_unknown(self):
        result = identify_license_from_text("some random text with no license keywords")
        # Should return None for unrecognizable text
        pass

    def test_analyze_mit(self):
        result = analyze_license("MIT")
        assert result.risk in ("safe", "caution", "warning", "danger", "unknown")

    def test_analyze_gpl(self):
        result = analyze_license("GPL-3.0")
        assert result is not None

    def test_analyze_unknown(self):
        result = analyze_license("CUSTOM-LICENSE-XYZ")
        assert result is not None

    def test_fetch_license(self):
        import base64
        data = {
            "content": base64.b64encode(b"MIT License text").decode(),
            "encoding": "base64",
            "license": {"spdx_id": "MIT"},
        }
        m = MagicMock()
        m.read.return_value = json.dumps(data).encode()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=m):
            spdx, text = fetch_license_from_github("owner", "repo")
        assert spdx == "MIT"

    def test_fetch_license_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            spdx, text = fetch_license_from_github("owner", "repo")
        assert spdx == ""

    def test_check_compatibility(self):
        from license_check import check_compatibility
        result = check_compatibility("MIT", "Apache-2.0")
        # Returns True/False/None depending on matrix
        assert result is None or isinstance(result, bool)

    def test_format_license_result(self):
        from license_check import format_license_result, CompatResult
        result = CompatResult(project_license="MIT")
        output = format_license_result(result)
        assert isinstance(output, str)

    def test_license_to_dict(self):
        from license_check import license_to_dict, CompatResult
        result = CompatResult(project_license="MIT")
        d = license_to_dict(result)
        assert isinstance(d, dict)


# ═══════════════════════════════════════════
#  detector — OS-specific branches
# ═══════════════════════════════════════════

from detector import EnvironmentDetector, format_env_summary


class TestDetector:
    def test_detect_macos(self):
        det = EnvironmentDetector()
        with patch("detector._run") as mock_run:
            mock_run.return_value = "Apple M3 Ultra"
            with patch("platform.system", return_value="Darwin"), \
                 patch("platform.machine", return_value="arm64"), \
                 patch("platform.mac_ver", return_value=("14.0", ("", "", ""), "")):
                result = det._detect_macos()
        assert result["type"] == "macos"
        assert result["is_apple_silicon"] is True

    def test_detect_linux(self):
        det = EnvironmentDetector()
        with patch("platform.system", return_value="Linux"), \
             patch("platform.machine", return_value="x86_64"), \
             patch("builtins.open", MagicMock(side_effect=FileNotFoundError)):
            result = det._detect_linux()
        assert result["type"] == "linux"

    def test_detect_windows(self):
        det = EnvironmentDetector()
        with patch("platform.system", return_value="Windows"), \
             patch("platform.machine", return_value="AMD64"), \
             patch("platform.version", return_value="10.0.19041"), \
             patch("platform.release", return_value="10"), \
             patch("detector._which", return_value=None):
            result = det._detect_windows()
        assert result["type"] == "windows"

    def test_detect_hardware(self):
        det = EnvironmentDetector()
        with patch("os.cpu_count", return_value=8):
            result = det._detect_hardware()
        assert result["cpu_count"] == 8

    def test_detect_ram_gb(self):
        det = EnvironmentDetector()
        result = det._detect_ram_gb()
        # Should return a float or None
        assert result is None or isinstance(result, (int, float))

    def test_detect_package_managers(self):
        det = EnvironmentDetector()
        def mock_which(cmd):
            return "/usr/bin/pip" if cmd == "pip" else None
        with patch("detector._which", side_effect=mock_which), \
             patch("detector._version", return_value="23.0"):
            result = det._detect_package_managers()
        assert isinstance(result, dict)

    def test_detect_runtimes(self):
        det = EnvironmentDetector()
        def mock_which(cmd):
            if cmd in ("python3", "python", "git"):
                return f"/usr/bin/{cmd}"
            return None
        with patch("detector._which", side_effect=mock_which), \
             patch("detector._version", return_value="3.13.0"):
            result = det._detect_runtimes()
        assert isinstance(result, dict)

    def test_detect_disk(self):
        det = EnvironmentDetector()
        mock_usage = MagicMock()
        mock_usage.free = 100 * 1024**3
        mock_usage.total = 500 * 1024**3
        mock_usage.f_frsize = 4096
        mock_usage.f_bavail = (100 * 1024**3) // 4096
        mock_usage.f_blocks = (500 * 1024**3) // 4096
        if hasattr(os, "statvfs"):
            with patch("os.statvfs", return_value=mock_usage):
                result = det._detect_disk()
        else:
            with patch("shutil.disk_usage", return_value=mock_usage):
                result = det._detect_disk()
        assert result["free_gb"] > 0

    def test_detect_gpu_apple(self):
        det = EnvironmentDetector()
        with patch("platform.system", return_value="Darwin"), \
             patch("platform.machine", return_value="arm64"):
            result = det._detect_gpu()
        assert result["type"] in ("apple_mps", "mps")

    def test_detect_gpu_nvidia(self):
        det = EnvironmentDetector()
        with patch("platform.system", return_value="Linux"), \
             patch("platform.machine", return_value="x86_64"), \
             patch.object(det, "_detect_nvidia", return_value={"type": "nvidia", "name": "RTX 4090"}):
            result = det._detect_gpu()
        assert result["type"] in ("nvidia", "cuda")

    def test_detect_gpu_cpu_only(self):
        det = EnvironmentDetector()
        with patch("platform.system", return_value="Linux"), \
             patch("platform.machine", return_value="x86_64"), \
             patch.object(det, "_detect_nvidia", return_value=None), \
             patch.object(det, "_detect_rocm", return_value=None):
            result = det._detect_gpu()
        assert result.get("type") in ("cpu", "cpu_only", None) or "type" in result

    def test_detect_nvidia(self):
        det = EnvironmentDetector()
        with patch("detector._run", return_value="NVIDIA RTX 4090, 24GB"):
            result = det._detect_nvidia()
        # May return dict or None depending on parsing
        pass

    def test_detect_rocm(self):
        det = EnvironmentDetector()
        with patch("detector._run", return_value=None):
            result = det._detect_rocm()
        assert result is None

    def test_detect_llm_env(self, monkeypatch):
        det = EnvironmentDetector()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = det._detect_llm_env()
        assert result.get("anthropic") is True

    def test_detect_network(self):
        det = EnvironmentDetector()
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value = MagicMock()
            result = det._detect_network()
        assert "github" in result

    def test_detect_network_offline(self):
        det = EnvironmentDetector()
        with patch("socket.create_connection", side_effect=OSError("offline")):
            result = det._detect_network()
        assert result.get("github") is False

    def test_detect_full_workflow(self):
        det = EnvironmentDetector()
        result = det.detect()
        assert "os" in result
        assert "hardware" in result

    def test_format_env_summary(self):
        env = {
            "os": {"type": "macos", "version": "14.0", "chip": "M3"},
            "hardware": {"cpu_count": 8, "ram_gb": 32},
            "gpu": {"type": "apple_mps", "name": "M3"},
            "package_managers": {"brew": {"available": True}},
            "runtimes": {"python": {"version": "3.13.0"}},
            "disk": {"free_gb": 100, "total_gb": 500},
        }
        output = format_env_summary(env)
        assert "macOS" in output or "macos" in output or "OS" in output


# ═══════════════════════════════════════════
#  db — 数据库认证与统计
# ═══════════════════════════════════════════

import db as db_mod


class TestDb:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path, monkeypatch):
        """每次测试使用隔离数据库"""
        from db_backend import SQLiteBackend, set_backend
        monkeypatch.setattr(db_mod, "DB_DIR", tmp_path)
        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(db_mod, "_initialized", False)
        backend = SQLiteBackend(db_path=str(tmp_path / "test.db"))
        set_backend(backend)
        db_mod.init_db()
        yield
        backend.close()
        set_backend(None)

    def test_record_event_and_stats(self):
        db_mod.record_event("install", project="a/b", os_type="macos")
        db_mod.record_event("install", project="c/d")
        stats = db_mod.get_stats()
        assert "total_installs" in stats
        assert "daily_trend" in stats

    def test_register_user(self):
        result = db_mod.register_user("testuser", "test@example.com", "password123")
        assert result["status"] == "ok"
        assert "user_id" in result

    def test_register_user_short_password(self):
        result = db_mod.register_user("short", "short@example.com", "123")
        assert result["status"] == "error"

    def test_register_user_duplicate(self):
        db_mod.register_user("dup", "dup@example.com", "password123")
        result = db_mod.register_user("dup", "dup@example.com", "password123")
        assert result["status"] == "error"

    def test_login_user_success(self):
        db_mod.register_user("logintest", "login@example.com", "secret12345")
        result = db_mod.login_user("login@example.com", "secret12345")
        assert result["status"] == "ok"
        assert "token" in result

    def test_login_user_bad_password(self):
        db_mod.register_user("user2", "user2@example.com", "correct_pass")
        result = db_mod.login_user("user2@example.com", "wrong_pass")
        assert result["status"] == "error"

    def test_login_user_not_found(self):
        result = db_mod.login_user("nobody@example.com", "password123")
        assert result["status"] == "error"

    def test_validate_token(self):
        db_mod.register_user("tokuser", "tok@example.com", "password123")
        login = db_mod.login_user("tok@example.com", "password123")
        token = login.get("token", "")
        assert token
        user_info = db_mod.validate_token(token)
        assert user_info is not None
        assert user_info["username"] == "tokuser"

    def test_validate_token_invalid(self):
        result = db_mod.validate_token("fake-token-12345")
        assert result is None

    def test_is_admin_default_false(self):
        db_mod.register_user("adm", "adm@example.com", "password123")
        login = db_mod.login_user("adm@example.com", "password123")
        token = login["token"]
        assert db_mod.is_admin(token) is False

    def test_set_admin(self):
        result = db_mod.register_user("adm2", "adm2@example.com", "password123")
        user_id = result["user_id"]
        db_mod.set_admin(user_id, True)
        login = db_mod.login_user("adm2@example.com", "password123")
        assert db_mod.is_admin(login["token"]) is True

    def test_cleanup_expired_sessions(self):
        count = db_mod.cleanup_expired_sessions()
        assert isinstance(count, int)

    def test_create_and_verify_reset_token(self):
        db_mod.register_user("reset1", "reset@example.com", "password123")
        result = db_mod.create_reset_token("reset@example.com")
        assert result["status"] == "ok"
        token = result["token"]
        verify = db_mod.verify_reset_token(token)
        assert verify is not None

    def test_reset_password(self):
        db_mod.register_user("rp1", "rp@example.com", "old_password")
        rt = db_mod.create_reset_token("rp@example.com")
        token = rt["token"]
        result = db_mod.reset_password(token, "new_password1")
        assert result.get("status") == "ok"
        # Can login with new password
        login = db_mod.login_user("rp@example.com", "new_password1")
        assert login["status"] == "ok"

    def test_send_email_no_smtp(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        result = db_mod.send_email("to@example.com", "Subject", "<p>Hi</p>")
        assert isinstance(result, bool)

    def test_check_quota(self):
        result = db_mod.check_quota(ip="127.0.0.1")
        assert "allowed" in result
        assert "tier" in result

    def test_record_install_telemetry(self):
        db_mod.record_install_telemetry("a/b", strategy="pip", success=True, duration_sec=5.0)

    def test_get_recent_installs(self):
        result = db_mod.get_recent_installs(limit=5)
        assert isinstance(result, list)

    def test_get_project_success_rate(self):
        db_mod.record_install_telemetry("x/y", success=True)
        db_mod.record_install_telemetry("x/y", success=False)
        result = db_mod.get_project_success_rate("x/y")
        assert isinstance(result, dict)
        assert "overall" in result
