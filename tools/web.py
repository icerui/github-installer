"""
web.py - gitinstall Web UI 服务器
=================================

在浏览器中交互式安装 GitHub 开源项目。

启动方式：
  python tools/main.py web              # 默认 8080 端口
  python tools/main.py web --port 9090  # 指定端口
  python tools/web.py                   # 直接运行
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import StringIO
from pathlib import Path
from socketserver import ThreadingMixIn

# ── 确保 tools 目录可导入 ──
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import db as _db
from log import get_logger
from i18n import t

logger = get_logger(__name__)

# ── 请求体大小限制（1 MB） ──
MAX_BODY_SIZE = 1 * 1024 * 1024

# ── Rate Limiting（内存，按 IP） ──
_rate_limits: dict[str, list[float]] = defaultdict(list)
_rate_lock = __import__("threading").Lock()

# 单位：秒窗口内最大请求数  {路由前缀: (窗口秒数, 最大次数)}
_RATE_RULES: dict[str, tuple[int, int]] = {
    "/api/login":            (60, 10),    # 登录：60s 内最多 10 次
    "/api/register":         (60, 5),     # 注册：60s 内最多 5 次
    "/api/forgot-password":  (60, 3),     # 忘记密码：60s 内最多 3 次
    "/api/reset-password":   (60, 5),     # 重置密码：60s 内最多 5 次
    "/api/admin/set":        (60, 3),     # 管理员设置：60s 内最多 3 次
    "/api/plan":             (60, 15),    # 生成计划：60s 内最多 15 次
    "/api/install":          (60, 10),    # 安装：60s 内最多 10 次
    "/api/search":           (60, 30),    # 搜索：60s 内最多 30 次
    "/api/audit":            (60, 15),    # 审计：60s 内最多 15 次
    "/api/license":          (60, 15),    # 许可证：60s 内最多 15 次
    "/api/updates":          (60, 20),    # 更新检查：60s 内最多 20 次
    "/api/uninstall":        (60, 5),     # 卸载：60s 内最多 5 次
    "/api/chain":            (60, 10),    # 依赖链：60s 内最多 10 次
    "/api/kb/search":        (60, 20),    # 知识库搜索：60s 内最多 20 次
}


def _check_rate_limit(ip: str, path: str) -> bool:
    """检查是否超出频率限制。返回 True 表示被限制。"""
    rule = _RATE_RULES.get(path)
    if not rule:
        return False
    window, max_count = rule
    key = f"{ip}:{path}"
    now = time.time()
    with _rate_lock:
        hits = _rate_limits[key]
        # 清除过期记录
        cutoff = now - window
        _rate_limits[key] = [t_ for t_ in hits if t_ > cutoff]
        if len(_rate_limits[key]) >= max_count:
            return True
        _rate_limits[key].append(now)
    return False


# ── CSRF Token 管理 ──
_csrf_tokens: dict[str, float] = {}  # {token: timestamp}
_csrf_lock = __import__("threading").Lock()
_CSRF_TTL = 3600  # 1 小时有效


def _generate_csrf_token() -> str:
    """生成 CSRF token"""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _csrf_lock:
        # 清理过期 token
        expired = [k for k, ts in _csrf_tokens.items() if now - ts > _CSRF_TTL]
        for k in expired:
            del _csrf_tokens[k]
        _csrf_tokens[token] = now
    return token


def _validate_csrf_token(token: str) -> bool:
    """验证 CSRF token"""
    if not token:
        return False
    with _csrf_lock:
        ts = _csrf_tokens.get(token)
        if ts is None:
            return False
        if time.time() - ts > _CSRF_TTL:
            del _csrf_tokens[token]
            return False
        # 一次性使用：验证后删除
        del _csrf_tokens[token]
        return True


# ── 计划缓存（内存，最多保留 20 条，5 分钟过期） ──
_plan_cache: dict[str, tuple[float, dict]] = {}  # {plan_id: (timestamp, result)}
_PLAN_TTL = 300  # 5 分钟


def _make_plan_id() -> str:
    """生成高熵 plan_id（256-bit 安全随机）"""
    return secrets.token_urlsafe(32)


def _cache_plan(plan_id: str, result: dict) -> None:
    now = time.time()
    # 先清理过期条目
    expired = [k for k, (ts, _) in _plan_cache.items() if now - ts > _PLAN_TTL]
    for k in expired:
        del _plan_cache[k]
    _plan_cache[plan_id] = (now, result)
    if len(_plan_cache) > 20:
        oldest_key = min(_plan_cache, key=lambda k: _plan_cache[k][0])
        del _plan_cache[oldest_key]


def _pop_plan(plan_id: str) -> dict | None:
    """取出并移除缓存的计划，同时检查是否过期。"""
    entry = _plan_cache.pop(plan_id, None)
    if entry is None:
        return None
    ts, result = entry
    if time.time() - ts > _PLAN_TTL:
        return None  # 已过期
    return result


# ── SSE 并发连接限制（防资源耗尽 DoS） ──
_active_installs: dict[str, int] = defaultdict(int)
_active_installs_lock = __import__("threading").Lock()
MAX_CONCURRENT_INSTALLS_PER_IP = 3

# ── 全局并发 plan 生成限制 ──
_active_plans = 0
_plan_lock = __import__("threading").Lock()
MAX_CONCURRENT_PLANS = 3


# ─────────────────────────────────────────────
#  HTTP 服务器
# ─────────────────────────────────────────────

class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    """gitinstall Web UI 请求处理器"""

    # 隐藏服务器版本信息
    server_version = "gitinstall"
    sys_version = ""

    # 抑制默认日志（使用 logging 替代）
    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    # ── 安全响应头 ────────────────────────────

    def _add_security_headers(self):
        """为所有响应添加安全头"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.loli.net; font-src 'self' https://fonts.gstatic.com https://gstatic.loli.net; img-src 'self' data:; connect-src 'self'")

    # ── Rate Limiting 检查 ────────────────────

    def _rate_limited(self, path: str) -> bool:
        """如果被限制则返回 429 并返回 True"""
        ip = self._client_ip()
        if _check_rate_limit(ip, path):
            body = json.dumps({"status": "error", "message": t("api.rate_limited")}, ensure_ascii=False).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "60")
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(body)
            return True
        return False

    def _check_csrf(self) -> bool:
        """检查 POST 请求的 CSRF token。返回 True 表示通过。"""
        # Bearer token API 调用免 CSRF（非浏览器场景）
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return True
        # 检查 X-CSRF-Token header
        csrf_token = self.headers.get("X-CSRF-Token", "")
        if _validate_csrf_token(csrf_token):
            return True
        # CSRF 验证失败
        body = json.dumps({"status": "error", "message": "CSRF token invalid or missing"}, ensure_ascii=False).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(body)
        return False

    # ── 路由 ──────────────────────────────────

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # ── API v1 路由别名（向前兼容）──
        if path.startswith("/api/v1/"):
            path = "/api/" + path[8:]  # strip /api/v1/ → /api/

        routes = {
            "/": self._serve_ui,
            "/admin": self._serve_admin,
            "/clawhub": self._serve_clawhub,
            "/health": self._health_check,
            "/readiness": self._readiness_check,
            "/api/csrf-token": self._api_csrf_token,
            "/api/detect": self._api_detect,
            "/api/doctor": self._api_doctor,
            "/api/platforms": self._api_platforms,
            "/api/trending": self._api_trending,
            "/api/trending/refresh": self._api_trending_refresh,
            "/api/stats": self._api_stats,
            "/api/user": self._api_user,
            "/api/audit": self._api_audit,
            "/api/license": self._api_license,
            "/api/updates": self._api_updates,
            "/api/flags": self._api_flags,
            "/api/registry": self._api_registry,
            "/api/events": self._api_events,
            "/api/kb/stats": self._api_kb_stats,
        }
        if path == "/api/install":
            if self._rate_limited(path):
                return
            return self._api_install_stream(qs)
        if path == "/api/search":
            if self._rate_limited(path):
                return
            return self._api_search(qs)
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # ── API v1 路由别名 ──
        if path.startswith("/api/v1/"):
            path = "/api/" + path[8:]

        if self._rate_limited(path):
            return

        # CSRF 保护：所有 POST 请求需要 CSRF token 或 Bearer auth
        if not self._check_csrf():
            return

        post_routes = {
            "/api/plan": self._api_plan,
            "/api/register": self._api_register,
            "/api/login": self._api_login,
            "/api/forgot-password": self._api_forgot_password,
            "/api/reset-password": self._api_reset_password,
            "/api/admin/set": self._api_admin_set,
            "/api/uninstall": self._api_uninstall,
            "/api/chain": self._api_chain,
            "/api/kb/search": self._api_kb_search,
        }
        handler = post_routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(404)

    # ── 工具：获取客户端 IP ─────────────────

    def _client_ip(self) -> str:
        # 仅在反向代理场景信任 X-Forwarded-For（由 nginx 设置）
        # 直接暴露时应只用 client_address
        xff = self.headers.get("X-Forwarded-For")
        if xff and self.client_address[0] in ("127.0.0.1", "::1"):
            return xff.split(",")[0].strip()
        return self.client_address[0]

    # ── 页面 ──────────────────────────────────

    def _serve_ui(self):
        html_path = _THIS_DIR / "web_ui.html"
        if not html_path.exists():
            self.send_error(500, "web_ui.html not found")
            return
        content = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(content)
        try:
            _db.record_event("page_view", ip=self._client_ip())
        except Exception:
            pass

    def _serve_admin(self):
        html_path = _THIS_DIR / "admin.html"
        if not html_path.exists():
            self.send_error(500, "admin.html not found")
            return
        content = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _serve_clawhub(self):
        html_path = _THIS_DIR / "clawhub.html"
        if not html_path.exists():
            self.send_error(500, "clawhub.html not found")
            return
        content = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(content)

    # ── Health Check / Readiness ─────────────

    def _health_check(self):
        """健康检查端点（用于负载均衡/Kubernetes 探针）"""
        checks = {"status": "ok", "timestamp": time.time()}
        # 检查数据库连接
        try:
            _db.init_db()
            conn = _db._get_conn()
            conn.execute("SELECT 1").fetchone()
            checks["db"] = "ok"
        except Exception:
            checks["db"] = "error"
            checks["status"] = "degraded"
        self._json(checks, 200 if checks["status"] == "ok" else 503)

    def _readiness_check(self):
        """就绪检查端点"""
        self._json({"status": "ok", "ready": True, "version": "1.0.0"})

    # ── CSRF Token 端点 ──────────────────────

    def _api_csrf_token(self):
        """获取 CSRF token（GET 请求，浏览器调用后附加到 POST 请求头中）"""
        token = _generate_csrf_token()
        self._json({"csrf_token": token})

    # ── API: 环境检测 ─────────────────────────

    def _api_detect(self):
        from detector import EnvironmentDetector
        env = EnvironmentDetector().detect()
        # 只返回必要信息，不暴露完整系统指纹
        safe_env = {
            "os": env.get("os", {}).get("type", ""),
            "arch": env.get("os", {}).get("arch", ""),
            "gpu": env.get("gpu", {}).get("type", "none"),
            "has_python": bool(env.get("runtimes", {}).get("python")),
            "has_node": bool(env.get("runtimes", {}).get("node")),
            "has_docker": bool(env.get("runtimes", {}).get("docker")),
        }
        self._json({"status": "ok", "env": safe_env})

    # ── API: 环境诊断 ─────────────────────────

    def _api_doctor(self):
        try:
            from doctor import run_doctor, doctor_to_dict
            report = run_doctor()
            self._json(doctor_to_dict(report))
        except Exception as e:
            logger.warning("doctor failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 支持平台 ─────────────────────────

    def _api_platforms(self):
        try:
            from multi_source import get_supported_platforms
            platforms = get_supported_platforms()
            self._json({"status": "ok", "platforms": platforms})
        except Exception as e:
            logger.warning("platforms query failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 热门项目 ─────────────────────────

    def _api_trending(self):
        """返回当前热门开源项目列表（动态爬取 + 缓存）"""
        from trending import get_trending
        projects = get_trending()
        self._json({"status": "ok", "projects": projects})
        try:
            _db.record_event("trending_view", ip=self._client_ip())
        except Exception:
            pass

    def _api_trending_refresh(self):
        """强制刷新热门项目缓存（仅管理员）"""
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not _db.is_admin(token):
            return self._json({"status": "error", "message": t("auth.admin_required")}, 403)
        from trending import get_trending
        projects = get_trending(force_refresh=True)
        self._json({"status": "ok", "projects": projects, "refreshed": True})

    # ── API: GitHub 搜索 ──────────────────────

    def _api_search(self, qs: dict):
        """搜索 GitHub 仓库"""
        query = qs.get("q", [""])[0].strip()
        if not query:
            return self._json({"status": "error", "message": t("api.search_keyword_required")}, 400)

        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
            "q": query, "sort": "stars", "per_page": "12",
        })
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gitinstall/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = []
            for item in data.get("items", []):
                results.append({
                    "repo": item["full_name"],
                    "name": item["name"],
                    "desc": (item.get("description") or "")[:120],
                    "stars": item.get("stargazers_count", 0),
                    "lang": item.get("language") or "",
                })
            self._json({"status": "ok", "results": results, "total": data.get("total_count", 0)})
            try:
                _db.record_event("search", project=query, ip=self._client_ip())
            except Exception:
                pass
        except Exception as e:
            logger.warning("搜索失败: %s", e)
            self._json({"status": "error", "message": t("api.search_failed")}, 502)

    # ── API: 生成计划 ─────────────────────────

    def _api_plan(self):
        global _active_plans
        # 全局并发 plan 限制（防资源耗尽）
        with _plan_lock:
            if _active_plans >= MAX_CONCURRENT_PLANS:
                return self._json({"status": "error", "message": t("api.system_busy")}, 503)
            _active_plans += 1
        try:
            self._do_api_plan()
        finally:
            with _plan_lock:
                _active_plans -= 1

    def _do_api_plan(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)

        project = data.get("project", "").strip()
        if not project:
            return self._json({"status": "error", "message": t("api.project_required")}, 400)

        llm = data.get("llm")
        local_mode = data.get("local", False)

        # 抑制 cmd_plan 的 stderr 输出（进度信息）
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            from main import cmd_plan
            result = cmd_plan(project, llm_force=llm, use_local=local_mode)
        finally:
            log = sys.stderr.getvalue()
            sys.stderr = old_stderr

        result["_log"] = log

        # 缓存计划供 install 使用
        if result.get("status") == "ok":
            plan_id = _make_plan_id()
            _cache_plan(plan_id, result)
            result["plan_id"] = plan_id

            # 记录事件 + 保存历史
            try:
                plan_data = result.get("plan", {})
                _db.record_event(
                    "plan_generated",
                    project=project,
                    detail={"strategy": result.get("strategy"), "confidence": result.get("confidence")},
                    ip=self._client_ip(),
                )
                _db.save_plan_history(
                    project=project,
                    strategy=result.get("strategy"),
                    confidence=result.get("confidence"),
                    steps=plan_data.get("steps"),
                )
            except Exception:
                pass

        # 安全：不向客户端暴露内部调试信息
        for _k in ("_log", "strategy", "confidence", "_stderr"):
            result.pop(_k, None)

        self._json(result)

    # ── API: 安装（SSE 流） ──────────────────

    def _api_install_stream(self, qs: dict):
        plan_id = qs.get("plan_id", [""])[0]
        project = qs.get("project", [""])[0]
        install_dir = qs.get("install_dir", [""])[0]

        if not plan_id and not project:
            self.send_error(400, "Missing plan_id or project")
            return

        # 并发安装限制（防资源耗尽）
        ip = self._client_ip()
        with _active_installs_lock:
            if _active_installs[ip] >= MAX_CONCURRENT_INSTALLS_PER_IP:
                body = json.dumps({"status": "error", "message": t("api.too_many_installs")}, ensure_ascii=False).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._add_security_headers()
                self.end_headers()
                self.wfile.write(body)
                return
            _active_installs[ip] += 1

        try:
            self._do_install_stream(qs, plan_id, project, install_dir)
        finally:
            with _active_installs_lock:
                _active_installs[ip] = max(0, _active_installs[ip] - 1)

    def _do_install_stream(self, qs: dict, plan_id: str, project: str, install_dir: str):

        # SSE headers
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._add_security_headers()
        self.end_headers()

        alive = True

        def sse(event: str, data: dict) -> bool:
            nonlocal alive
            if not alive:
                return False
            msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(msg.encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                alive = False
                return False

        # 1. 取计划（优先用缓存）
        plan_result = _pop_plan(plan_id) if plan_id else None

        if not plan_result:
            if not project:
                sse("step_error", {"message": t("install.plan_expired")})
                sse("done", {"success": False, "message": t("install.plan_expired")})
                return
            sse("phase", {"name": "plan", "message": t("install.regenerating_plan")})
            old_stderr = sys.stderr
            sys.stderr = StringIO()
            try:
                from main import cmd_plan
                plan_result = cmd_plan(project)
            finally:
                sys.stderr = old_stderr
            if plan_result.get("status") != "ok":
                sse("step_error", {"message": plan_result.get("message", t("install.plan_failed"))})
                sse("done", {"success": False, "message": t("install.plan_failed")})
                return

        plan = plan_result["plan"]
        steps = plan.get("steps", [])

        if not steps:
            sse("step_error", {"message": t("install.no_steps")})
            sse("done", {"success": False, "message": t("install.no_steps")})
            return

        sse("plan", {
            "project": plan_result.get("project", project),
            "steps": steps,
            "launch_command": plan.get("launch_command", ""),
        })

        # 2. 逐步执行
        home = os.path.realpath(os.path.expanduser("~"))
        if install_dir:
            work_dir = os.path.realpath(os.path.expanduser(install_dir))
            # 安全检查：只允许在用户家目录下安装
            if not work_dir.startswith(home + os.sep) and work_dir != home:
                sse("step_error", {"message": t("install.dir_security")})
                sse("done", {"success": False, "message": t("install.dir_invalid")})
                return
            os.makedirs(work_dir, exist_ok=True)
            # TOCTOU 防护：mkdir 后再次验证路径
            work_dir = os.path.realpath(work_dir)
            if not work_dir.startswith(home + os.sep) and work_dir != home:
                sse("step_error", {"message": t("install.dir_path_changed")})
                sse("done", {"success": False, "message": t("install.dir_invalid")})
                return
        else:
            work_dir = home
        all_ok = True
        t_total = time.time()

        from executor import check_command_safety

        for i, step in enumerate(steps):
            cmd = step.get("command", "").strip()
            desc = step.get("description", "")

            if not sse("step_start", {
                "index": i, "total": len(steps),
                "description": desc, "command": cmd,
            }):
                return

            if not cmd:
                sse("step_done", {"index": i, "success": True, "duration": 0})
                continue

            # 命令安全检查
            is_safe, safety_msg = check_command_safety(cmd)
            if not is_safe:
                sse("step_error", {"index": i, "message": safety_msg})
                sse("done", {"success": False, "message": t("install.unsafe_command")})
                return

            t0 = time.time()
            try:
                proc = subprocess.Popen(
                    cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=work_dir, text=True, bufsize=1,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                output_lines = 0
                MAX_OUTPUT_LINES = 10000
                for line in proc.stdout:
                    output_lines += 1
                    if output_lines > MAX_OUTPUT_LINES:
                        if output_lines == MAX_OUTPUT_LINES + 1:
                            sse("output", {"index": i, "line": t("install.output_truncated")})
                        continue
                    if not sse("output", {"index": i, "line": line.rstrip("\n")}):
                        proc.kill()
                        return
                proc.wait(timeout=600)
                dur = round(time.time() - t0, 1)
                ok = proc.returncode == 0

                sse("step_done", {
                    "index": i, "success": ok,
                    "exit_code": proc.returncode, "duration": dur,
                })
                if not ok:
                    all_ok = False
                    sse("step_error", {
                        "index": i,
                        "message": t("install.cmd_exit_code", code=proc.returncode),
                    })
                    break

            except subprocess.TimeoutExpired:
                proc.kill()
                all_ok = False
                sse("step_done", {"index": i, "success": False, "duration": 600})
                sse("step_error", {"index": i, "message": t("install.cmd_timeout", minutes=10)})
                break
            except Exception as e:
                all_ok = False
                dur = round(time.time() - t0, 1)
                sse("step_done", {"index": i, "success": False, "duration": dur})
                sse("step_error", {"index": i, "message": str(e)})
                break

        total_dur = round(time.time() - t_total, 1)
        launch = plan.get("launch_command", "")
        sse("done", {
            "success": all_ok,
            "message": t("install.complete") if all_ok else t("install.not_complete"),
            "launch_command": launch,
            "total_duration": total_dur,
        })

        # 记录安装结果
        try:
            evt = "install_done" if all_ok else "install_failed"
            proj = plan_result.get("project", project)
            _db.record_event(evt, project=proj, detail={"duration": total_dur}, ip=self._client_ip())
        except Exception:
            pass

    # ── API: 统计信息 ─────────────────

    def _api_stats(self):
        """返回统计信息（仅管理员）"""
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not _db.is_admin(token):
            return self._json({"status": "error", "message": t("auth.admin_required")}, 403)
        try:
            stats = _db.get_stats()
            stats["recent_installs"] = _db.get_recent_installs(10)
            self._json({"status": "ok", **stats})
        except Exception:
            logger.exception("获取统计信息失败")
            self._json({"status": "error", "message": t("server.stats_error")}, 500)

    # ── API: 设置管理员 ───────────────

    def _api_admin_set(self):
        """
        首次设置管理员：需要环境变量 GITINSTALL_ADMIN_SECRET 匹配。
        后续可由已有管理员提升他人。
        """
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)

        target_user_id = data.get("user_id")
        if not target_user_id:
            return self._json({"status": "error", "message": t("api.missing_user_id")}, 400)

        # 方式 1：通过 admin_secret（首次设置管理员）
        admin_secret = data.get("admin_secret", "")
        env_secret = os.environ.get("GITINSTALL_ADMIN_SECRET", "")
        if env_secret and admin_secret == env_secret:
            _db.set_admin(target_user_id, True)
            return self._json({"status": "ok", "message": t("auth.admin_set_ok")})

        # 方式 2：已有管理员授权
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if _db.is_admin(token):
            _db.set_admin(target_user_id, True)
            return self._json({"status": "ok", "message": t("auth.admin_set_ok")})

        self._json({"status": "error", "message": t("auth.no_permission")}, 403)

    # ── API: 用户信息 ─────────────────

    def _api_user(self):
        """获取当前用户信息 + 配额"""
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        user = _db.validate_token(token) if token else None
        quota = _db.check_quota(
            user_id=user["id"] if user else None,
            ip=self._client_ip(),
        )
        result = {"status": "ok", "quota": quota}
        if user:
            result["user"] = {"id": user["id"], "username": user["username"], "tier": user["tier"]}
        self._json(result)

    # ── API: 注册 ─────────────────────

    def _api_register(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        result = _db.register_user(
            username=data.get("username", ""),
            email=data.get("email", ""),
            password=data.get("password", ""),
        )
        code = 200 if result["status"] == "ok" else 400
        # 注册成功后发送欢迎邮件（后台发送，不阻塞响应）
        if result["status"] == "ok":
            try:
                import threading as _th
                _th.Thread(
                    target=_db.send_welcome_email,
                    args=(data.get("email", ""), data.get("username", "")),
                    daemon=True,
                ).start()
            except Exception:
                pass
        self._json(result, code)

    # ── API: 登录 ─────────────────────

    def _api_login(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        result = _db.login_user(
            email=data.get("email", ""),
            password=data.get("password", ""),
        )
        code = 200 if result["status"] == "ok" else 401
        self._json(result, code)

    # ── API: 忘记密码 ─────────────────

    def _api_forgot_password(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        email = data.get("email", "").strip()
        if not email:
            return self._json({"status": "error", "message": t("auth.enter_email")}, 400)

        result = _db.create_reset_token(email)
        if result["token"]:
            # 后台发送重置邮件
            try:
                import threading as _th
                _th.Thread(
                    target=_db.send_reset_email,
                    args=(email, result["username"], result["token"]),
                    daemon=True,
                ).start()
            except Exception:
                pass
        # 无论邮箱是否存在，统一返回成功（防止枚举邮箱）
        self._json({"status": "ok", "message": t("auth.reset_email_sent")})

    # ── API: 重置密码 ─────────────────

    def _api_reset_password(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        token = data.get("token", "")
        new_password = data.get("password", "")
        if not token or not new_password:
            return self._json({"status": "error", "message": t("auth.params_incomplete")}, 400)
        result = _db.reset_password(token, new_password)
        code = 200 if result["status"] == "ok" else 400
        self._json(result, code)

    # ── API: 依赖安全审计 ─────────────────────

    def _api_audit(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        project = qs.get("project", [""])[0].strip()
        if not project:
            return self._json({"status": "error", "message": t("api.missing_param", param="project")}, 400)
        if not re.match(r'^[\w\-\.]+/[\w\-\.]+$', project):
            return self._json({"status": "error", "message": t("api.param_format_error")}, 400)
        try:
            from fetcher import fetch_project
            from dependency_audit import audit_project, audit_to_dict
            info = fetch_project(project)
            if not info.dependency_files:
                return self._json({"status": "ok", "message": t("audit.no_deps"), "results": []})
            results = audit_project(info.dependency_files)
            self._json({"status": "ok", **audit_to_dict(results)})
            try:
                _db.record_event("audit", project=project, ip=self._client_ip())
            except Exception:
                pass
        except Exception as e:
            logger.warning("audit failed: %s", e)
            self._json({"status": "error", "message": t("audit.failed")}, 502)

    # ── API: 许可证检查 ────────────────────────

    def _api_license(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        project = qs.get("project", [""])[0].strip()
        if not project:
            return self._json({"status": "error", "message": t("api.missing_param", param="project")}, 400)
        if not re.match(r'^[\w\-\.]+/[\w\-\.]+$', project):
            return self._json({"status": "error", "message": t("api.param_format_error")}, 400)
        try:
            parts = project.split("/")
            from license_check import fetch_license_from_github, analyze_license, license_to_dict
            spdx_id, license_text = fetch_license_from_github(parts[0], parts[1])
            if not spdx_id and not license_text:
                return self._json({"status": "ok", "message": t("license.no_license"), "risk": "warning"})
            result = analyze_license(spdx_id, license_text)
            self._json({"status": "ok", **license_to_dict(result)})
            try:
                _db.record_event("license_check", project=project, ip=self._client_ip())
            except Exception:
                pass
        except Exception as e:
            logger.warning("license check failed: %s", e)
            self._json({"status": "error", "message": t("license.check_failed")}, 502)

    # ── API: 更新检查 ──────────────────────────

    def _api_updates(self):
        try:
            from auto_update import InstallTracker, check_all_updates, updates_to_dict
            tracker = InstallTracker()
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            action = qs.get("action", ["list"])[0]

            if action == "check":
                results = check_all_updates(tracker)
                self._json({"status": "ok", **updates_to_dict(results)})
            else:
                projects = tracker.list_installed()
                self._json({
                    "status": "ok",
                    "installed": [p.to_dict() for p in projects],
                    "total": len(projects),
                })
            try:
                _db.record_event("updates_check", ip=self._client_ip())
            except Exception:
                pass
        except Exception as e:
            logger.warning("update check failed: %s", e)
            self._json({"status": "error", "message": t("update.check_failed")}, 502)

    # ── API: 卸载项目 ──────────────────────────

    def _api_uninstall(self):
        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)

        project = data.get("project", "").strip()
        if not project:
            return self._json({"status": "error", "message": t("api.missing_param", param="project")}, 400)
        if not re.match(r'^[\w\-\.]+/[\w\-\.]+$', project):
            return self._json({"status": "error", "message": t("api.param_format_error")}, 400)

        keep_config = data.get("keep_config", False)
        confirm = data.get("confirm", False)

        try:
            from auto_update import InstallTracker
            from uninstaller import plan_uninstall, execute_uninstall, uninstall_to_dict
            from fetcher import parse_repo_identifier

            owner, repo = parse_repo_identifier(project)
            tracker = InstallTracker()
            proj = tracker.get_project(owner, repo)

            if not proj:
                return self._json({"status": "error", "message": t("uninstall.not_found", project=project)}, 404)

            plan = plan_uninstall(owner, repo, proj.install_dir,
                                  keep_config=keep_config, clean_only=False)

            if not confirm:
                return self._json({"status": "ok", "action": "dry_run", **uninstall_to_dict(plan)})

            result = execute_uninstall(plan, keep_config=keep_config)
            if result["success"]:
                tracker.remove_project(owner, repo)

            self._json({"status": "ok" if result["success"] else "partial", **result})
            try:
                _db.record_event("uninstall", project=project, ip=self._client_ip())
            except Exception:
                pass
        except Exception as e:
            logger.warning("uninstall failed: %s", e)
            self._json({"status": "error", "message": t("uninstall.failed")}, 502)

    # ── API: 功能开关 ─────────────────────────

    def _api_flags(self):
        try:
            from feature_flags import get_all_status, format_flags_table
            status = get_all_status()
            self._json({"status": "ok", "flags": status})
        except Exception as e:
            logger.warning("flags query failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 安装器注册表 ─────────────────────

    def _api_registry(self):
        try:
            from installer_registry import InstallerRegistry
            registry = InstallerRegistry()
            self._json({
                "status": "ok",
                "total": len(registry.list_all()),
                "available": [i.info.name for i in registry.list_available()],
                "installers": registry.to_dict(),
            })
        except Exception as e:
            logger.warning("registry query failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 事件历史 ─────────────────────────

    def _api_events(self):
        try:
            from event_bus import get_event_bus
            bus = get_event_bus()
            history = bus.get_history(limit=50)
            self._json({
                "status": "ok",
                "events": [e.to_dict() for e in history],
                "total": len(history),
            })
        except Exception as e:
            logger.warning("events query failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 知识库统计 ───────────────────────

    def _api_kb_stats(self):
        try:
            from knowledge_base import KnowledgeBase
            kb = KnowledgeBase()
            stats = kb.get_stats()
            self._json({"status": "ok", **stats})
        except Exception as e:
            logger.warning("kb stats query failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── API: 知识库搜索 ───────────────────────

    def _api_kb_search(self):
        try:
            body = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        query = str(body.get("query", "")).strip()
        if not query:
            return self._json({"status": "error", "message": t("api.missing_param", param="query")}, 400)
        try:
            from knowledge_base import KnowledgeBase
            kb = KnowledgeBase()
            results = kb.search(project=query, limit=10)
            self._json({
                "status": "ok",
                "results": [{
                    "project": r.entry.project,
                    "score": r.score,
                    "success": r.entry.success,
                    "strategy": r.entry.strategy,
                    "reasons": r.match_reasons,
                } for r in results],
            })
        except Exception as e:
            logger.warning("kb search failed: %s", e)
            self._json({"status": "error", "message": t("api.search_failed")}, 502)

    # ── API: 依赖链分析 ───────────────────────

    def _api_chain(self):
        try:
            body = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            return self._json({"status": "error", "message": t("api.invalid_json")}, 400)
        project = str(body.get("project", "")).strip()
        if not project:
            return self._json({"status": "error", "message": t("api.missing_param", param="project")}, 400)
        try:
            from main import cmd_plan
            from dep_chain import build_chain_from_plan
            plan_result = cmd_plan(project)
            if plan_result.get("status") != "ok":
                return self._json(plan_result, 400)
            chain = build_chain_from_plan(plan_result["plan"])
            self._json({
                "status": "ok",
                "project": project,
                "chain": chain.to_dict(),
                "has_cycle": chain.has_cycle(),
            })
        except Exception as e:
            logger.warning("chain analysis failed: %s", e)
            self._json({"status": "error", "message": t("api.query_failed")}, 502)

    # ── 工具方法 ──────────────────────────────

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY_SIZE:
            raise ValueError(t("api.body_too_large"))
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")


# ─────────────────────────────────────────────
#  启动入口
# ─────────────────────────────────────────────

def start_server(
    port: int = 8080,
    host: str = "",
    open_browser: bool = True,
    ssl_certfile: str = None,
    ssl_keyfile: str = None,
):
    """启动 gitinstall Web UI 服务器

    Args:
        port: 端口号，默认 8080
        host: 绑定地址，默认读取 GITINSTALL_HOST 环境变量，否则 127.0.0.1
        open_browser: 是否自动打开浏览器
        ssl_certfile: TLS 证书文件路径（启用 HTTPS）
        ssl_keyfile: TLS 私钥文件路径
    """
    from log import configure
    configure()

    bind_host = host or os.environ.get("GITINSTALL_HOST", "127.0.0.1")

    # 环境变量覆盖 TLS 配置
    ssl_certfile = ssl_certfile or os.environ.get("GITINSTALL_TLS_CERT", "")
    ssl_keyfile = ssl_keyfile or os.environ.get("GITINSTALL_TLS_KEY", "")
    use_tls = bool(ssl_certfile and ssl_keyfile)

    # 尝试多个端口
    server = None
    for p in range(port, port + 10):
        try:
            server = _ThreadedServer((bind_host, p), _Handler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print(t("server.port_unavailable", start=port, end=port + 9))
        sys.exit(1)

    # 启用 HTTPS/TLS
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(ssl_certfile, ssl_keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    protocol = "https" if use_tls else "http"
    display_host = bind_host if bind_host != "0.0.0.0" else "127.0.0.1"
    url = f"{protocol}://{display_host}:{port}"
    print()
    print("  ┌──────────────────────────────────────┐")
    print("  │  🚀 gitinstall Web UI                │")
    print(f"  │  🌐 {url:<30s} │")
    if use_tls:
        print("  │  🔒 HTTPS/TLS enabled                │")
    if bind_host == "0.0.0.0":
        print(f"  │  {t('server.listening_all'):<35s} │")
        print(f"  │  {t('server.exposed_warning'):<35s} │")
    print("  │  Ctrl+C to stop                      │")
    print("  └──────────────────────────────────────┘")
    print()

    logger.info(t("server.started", host=bind_host, port=port))

    # 定期清理过期 session（每小时一次）
    def _periodic_cleanup():
        import threading
        while True:
            time.sleep(3600)
            try:
                n = _db.cleanup_expired_sessions()
                if n:
                    logger.info(t("server.session_cleanup", n=n))
            except Exception:
                logger.exception("清理过期会话失败")

    _cleanup_thread = __import__("threading").Thread(target=_periodic_cleanup, daemon=True)
    _cleanup_thread.start()

    if open_browser:
        import webbrowser
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{t('server.stopped')}")
        server.server_close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="gitinstall Web UI")
    p.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    p.add_argument("--no-open", action="store_true", help="Don't open browser")
    p.add_argument("--ssl-cert", default="", help="TLS cert file for HTTPS")
    p.add_argument("--ssl-key", default="", help="TLS key file for HTTPS")
    args = p.parse_args()
    start_server(
        port=args.port,
        open_browser=not args.no_open,
        ssl_certfile=args.ssl_cert or None,
        ssl_keyfile=args.ssl_key or None,
    )
