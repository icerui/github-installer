"""
i18n.py - gitinstall 国际化框架
=================================

零外部依赖的轻量 i18n 系统。
支持：消息目录、区域切换、参数插值、复数形式。

用法：
    from i18n import t, set_locale
    set_locale("en")
    print(t("install.complete"))            # "Installation complete!"
    print(t("error.password_min", n=8))     # "Password must be at least 8 characters"
"""

from __future__ import annotations

import os
import threading
from typing import Any

# ── 当前区域设置 ──
_current_locale = "zh"
_locale_lock = threading.Lock()

# ── 支持的区域 ──
SUPPORTED_LOCALES = ("zh", "en")
DEFAULT_LOCALE = "zh"

# ─────────────────────────────────────────────
#  消息目录
# ─────────────────────────────────────────────

_MESSAGES: dict[str, dict[str, str]] = {
    # ── 通用 ──
    "common.ok": {
        "zh": "成功",
        "en": "OK",
    },
    "common.error": {
        "zh": "错误",
        "en": "Error",
    },
    "common.retry_later": {
        "zh": "请稍后重试",
        "en": "Please try again later",
    },

    # ── 认证相关 ──
    "auth.fields_required": {
        "zh": "用户名、邮箱、密码均不能为空",
        "en": "Username, email, and password are required",
    },
    "auth.password_min": {
        "zh": "密码至少 {n} 位",
        "en": "Password must be at least {n} characters",
    },
    "auth.email_exists": {
        "zh": "该邮箱已注册",
        "en": "This email is already registered",
    },
    "auth.username_taken": {
        "zh": "用户名已被使用",
        "en": "Username is already taken",
    },
    "auth.register_failed": {
        "zh": "注册失败，请重试",
        "en": "Registration failed, please try again",
    },
    "auth.invalid_credentials": {
        "zh": "邮箱或密码错误",
        "en": "Invalid email or password",
    },
    "auth.reset_expired": {
        "zh": "重置链接已过期或无效，请重新申请",
        "en": "Reset link has expired or is invalid, please request a new one",
    },
    "auth.password_reset_ok": {
        "zh": "密码已重置，请重新登录",
        "en": "Password has been reset, please log in again",
    },
    "auth.admin_required": {
        "zh": "需要管理员权限",
        "en": "Admin privileges required",
    },
    "auth.admin_set_ok": {
        "zh": "管理员已设置",
        "en": "Admin privileges granted",
    },
    "auth.no_permission": {
        "zh": "无权限",
        "en": "Permission denied",
    },
    "auth.enter_email": {
        "zh": "请输入邮箱",
        "en": "Please enter your email",
    },
    "auth.params_incomplete": {
        "zh": "参数不完整",
        "en": "Incomplete parameters",
    },
    "auth.reset_email_sent": {
        "zh": "如果该邮箱已注册，重置链接已发送到你的邮箱",
        "en": "If this email is registered, a reset link has been sent",
    },

    # ── Web API ──
    "api.rate_limited": {
        "zh": "请求过于频繁，请稍后再试",
        "en": "Too many requests, please try again later",
    },
    "api.invalid_json": {
        "zh": "无效的 JSON 请求",
        "en": "Invalid JSON request",
    },
    "api.search_keyword_required": {
        "zh": "请输入搜索关键词",
        "en": "Please enter a search keyword",
    },
    "api.search_failed": {
        "zh": "搜索失败，请稍后重试",
        "en": "Search failed, please try again later",
    },
    "api.system_busy": {
        "zh": "系统繁忙，请稍后重试",
        "en": "System is busy, please try again later",
    },
    "api.project_required": {
        "zh": "请输入项目名称",
        "en": "Please enter a project name",
    },
    "api.missing_param": {
        "zh": "缺少 {param} 参数",
        "en": "Missing {param} parameter",
    },
    "api.param_format_error": {
        "zh": "参数格式错误",
        "en": "Invalid parameter format",
    },
    "api.query_failed": {
        "zh": "查询失败",
        "en": "Query failed",
    },
    "api.body_too_large": {
        "zh": "请求体过大",
        "en": "Request body too large",
    },
    "api.missing_user_id": {
        "zh": "缺少 user_id",
        "en": "Missing user_id",
    },
    "api.too_many_installs": {
        "zh": "同时安装任务过多，请等待当前任务完成",
        "en": "Too many concurrent installs, please wait",
    },

    # ── 安装相关 ──
    "install.detecting_env": {
        "zh": "🔍 正在检测系统环境...",
        "en": "🔍 Detecting system environment...",
    },
    "install.fetching": {
        "zh": "📡 正在获取 {project} 的项目信息...",
        "en": "📡 Fetching project info for {project}...",
    },
    "install.analyzing": {
        "zh": "🧠 SmartPlanner 分析中...",
        "en": "🧠 SmartPlanner analyzing...",
    },
    "install.plan_generated": {
        "zh": "✅ 安装计划已生成",
        "en": "✅ Installation plan generated",
    },
    "install.executing": {
        "zh": "🔧 正在执行...",
        "en": "🔧 Executing...",
    },
    "install.step_done": {
        "zh": "✅ 完成",
        "en": "✅ Done",
    },
    "install.step_failed": {
        "zh": "❌ 失败",
        "en": "❌ Failed",
    },
    "install.complete": {
        "zh": "🎉 安装完成！",
        "en": "🎉 Installation complete!",
    },
    "install.not_complete": {
        "zh": "安装未成功，请检查上方错误信息",
        "en": "Installation failed, check errors above",
    },
    "install.plan_expired": {
        "zh": "计划已过期，请重新分析",
        "en": "Plan expired, please re-analyze",
    },
    "install.regenerating_plan": {
        "zh": "重新生成安装计划...",
        "en": "Regenerating installation plan...",
    },
    "install.plan_failed": {
        "zh": "生成计划失败",
        "en": "Plan generation failed",
    },
    "install.no_steps": {
        "zh": "未能生成有效的安装步骤",
        "en": "No valid installation steps generated",
    },
    "install.unsafe_command": {
        "zh": "检测到不安全命令，已终止",
        "en": "Unsafe command detected, terminated",
    },
    "install.dir_security": {
        "zh": "安全限制：安装目录必须在用户家目录下",
        "en": "Security: install directory must be under home",
    },
    "install.dir_path_changed": {
        "zh": "安全限制：检测到路径异常变化",
        "en": "Security: abnormal path change detected",
    },
    "install.dir_invalid": {
        "zh": "安装目录不合法",
        "en": "Invalid installation directory",
    },
    "install.output_truncated": {
        "zh": "...[输出过多，已截断]...",
        "en": "...[output truncated]...",
    },
    "install.cmd_timeout": {
        "zh": "命令超时（{minutes} 分钟）",
        "en": "Command timed out ({minutes} min)",
    },
    "install.cmd_exit_code": {
        "zh": "命令返回 exit code {code}",
        "en": "Command returned exit code {code}",
    },
    "install.dangerous_cmd_rejected": {
        "zh": "🚫 危险命令，已拒绝执行：{cmd}",
        "en": "🚫 Dangerous command rejected: {cmd}",
    },

    # ── 审计/许可证 ──
    "audit.failed": {
        "zh": "审计失败，请稍后重试",
        "en": "Audit failed, please try again later",
    },
    "audit.no_deps": {
        "zh": "未找到依赖文件",
        "en": "No dependency files found",
    },
    "license.no_license": {
        "zh": "项目未声明许可证",
        "en": "Project has no declared license",
    },
    "license.check_failed": {
        "zh": "许可证检查失败，请稍后重试",
        "en": "License check failed, please try again later",
    },

    # ── 更新/卸载 ──
    "update.check_failed": {
        "zh": "更新检查失败",
        "en": "Update check failed",
    },
    "uninstall.failed": {
        "zh": "卸载失败，请稍后重试",
        "en": "Uninstall failed, please try again later",
    },
    "uninstall.not_found": {
        "zh": "未找到 {project} 的安装记录",
        "en": "No install record found for {project}",
    },

    # ── LLM ──
    "llm.using_heuristic": {
        "zh": "[LLM] 使用规则引擎模式（无 LLM）",
        "en": "[LLM] Using heuristic mode (no LLM)",
    },
    "llm.using_provider": {
        "zh": "[LLM] 使用 {provider}: {endpoint}",
        "en": "[LLM] Using {provider}: {endpoint}",
    },
    "llm.using_named": {
        "zh": "[LLM] 使用 {name}",
        "en": "[LLM] Using {name}",
    },
    "llm.using_with_model": {
        "zh": "[LLM] 使用 {name}: {model}",
        "en": "[LLM] Using {name}: {model}",
    },
    "llm.detected_local": {
        "zh": "[LLM] 检测到 {name}，使用模型：{model}",
        "en": "[LLM] Detected {name}, using model: {model}",
    },
    "llm.ollama_hint": {
        "zh": "[LLM] 提示：默认使用 {model}（~1GB）。更大模型质量更好但需要更多内存。",
        "en": "[LLM] Hint: using {model} (~1GB) by default. Larger models give better results but need more memory.",
    },
    "llm.no_provider": {
        "zh": "[LLM] 未检测到任何 LLM，使用规则引擎模式（功能完整，对未知项目质量略低）",
        "en": "[LLM] No LLM detected, using heuristic mode (fully functional, slightly lower quality for unknown projects)",
    },
    "llm.hint_ollama": {
        "zh": "[LLM] 免费方案 1：安装 Ollama + ollama pull qwen2.5:1.5b（推荐，~1GB，本地运行）",
        "en": "[LLM] Free option 1: Install Ollama + ollama pull qwen2.5:1.5b (recommended, ~1GB, local)",
    },
    "llm.hint_groq": {
        "zh": "[LLM] 免费方案 2：注册 groq.com 设置 GROQ_API_KEY（云端免费额度）",
        "en": "[LLM] Free option 2: Register at groq.com and set GROQ_API_KEY (free cloud quota)",
    },
    "llm.api_error": {
        "zh": "[LLM] API 调用失败",
        "en": "[LLM] API call failed",
    },

    # ── 执行器 ──
    "exec.install_start": {
        "zh": "🚀 开始安装 {project}（共 {steps} 步）",
        "en": "🚀 Installing {project} ({steps} steps)",
    },
    "exec.step_progress": {
        "zh": "[{current}/{total}] {description}",
        "en": "[{current}/{total}] {description}",
    },
    "exec.step_done": {
        "zh": "  ✅ 完成（{duration}s）",
        "en": "  ✅ Done ({duration}s)",
    },
    "exec.step_failed": {
        "zh": "  ❌ 失败（退出码 {code}）",
        "en": "  ❌ Failed (exit code {code})",
    },
    "exec.install_done": {
        "zh": "🎉 {project} 安装完成！",
        "en": "🎉 {project} installed successfully!",
    },
    "exec.launch_cmd": {
        "zh": "▶  启动命令：{cmd}",
        "en": "▶  Launch command: {cmd}",
    },
    "exec.install_dir": {
        "zh": "📁 安装目录：{dir}",
        "en": "📁 Install directory: {dir}",
    },
    "exec.rule_diagnosis": {
        "zh": "  🔧 规则引擎诊断：{cause}（置信度：{confidence}）",
        "en": "  🔧 Rule engine diagnosis: {cause} (confidence: {confidence})",
    },
    "exec.fix_cmd": {
        "zh": "  🔧 修复：{cmd}",
        "en": "  🔧 Fix: {cmd}",
    },
    "exec.fix_failed": {
        "zh": "  ⚠ 修复命令失败，跳过规则修复",
        "en": "  ⚠ Fix command failed, skipping rule-based fix",
    },
    "exec.retrying": {
        "zh": "  🔄 重试原始命令...",
        "en": "  🔄 Retrying original command...",
    },
    "exec.rule_fix_ok": {
        "zh": "  ✅ 规则修复成功！",
        "en": "  ✅ Rule-based fix succeeded!",
    },
    "exec.llm_fallback": {
        "zh": "  🔧 规则引擎无法修复，调用 LLM 分析...",
        "en": "  🔧 Rule engine cannot fix, calling LLM...",
    },
    "exec.llm_root_cause": {
        "zh": "  💡 根本原因：{cause}",
        "en": "  💡 Root cause: {cause}",
    },
    "exec.fix_rejected": {
        "zh": "  🚫 修复命令被安全策略拒绝：{cmd}",
        "en": "  🚫 Fix command rejected by security policy: {cmd}",
    },
    "exec.fix_cmd_failed": {
        "zh": "  ❌ 修复命令失败",
        "en": "  ❌ Fix command failed",
    },
    "exec.llm_fix_ok": {
        "zh": "  ✅ LLM 修复成功！",
        "en": "  ✅ LLM fix succeeded!",
    },
    "exec.llm_fix_error": {
        "zh": "  ⚠️ LLM 自动修复失败（{error}），跳过",
        "en": "  ⚠️ LLM auto-fix failed ({error}), skipping",
    },

    # ── 错误修复 ──
    "fixer.diagnosing": {
        "zh": "🔧 规则引擎诊断",
        "en": "🔧 Rule engine diagnosis",
    },
    "fixer.missing_dep": {
        "zh": "缺依赖",
        "en": "Missing dependency",
    },
    "fixer.permission_denied": {
        "zh": "权限不足",
        "en": "Permission denied",
    },
    "fixer.use_venv": {
        "zh": "需使用虚拟环境",
        "en": "Virtual environment required",
    },
    "fixer.system_python_protected": {
        "zh": "系统 Python 受保护",
        "en": "System Python is protected",
    },
    "fixer.pip_permission": {
        "zh": "pip 权限",
        "en": "pip permission",
    },

    # ── 服务器 ──
    "server.started": {
        "zh": "服务器已启动: {host}:{port}",
        "en": "Server started: {host}:{port}",
    },
    "server.port_unavailable": {
        "zh": "❌ 端口 {start}~{end} 都被占用，请指定其他端口",
        "en": "❌ Ports {start}~{end} are all in use, please specify another",
    },
    "server.stopped": {
        "zh": "👋 服务器已停止",
        "en": "👋 Server stopped",
    },
    "server.listening_all": {
        "zh": "📡 监听所有网络接口",
        "en": "📡 Listening on all interfaces",
    },
    "server.exposed_warning": {
        "zh": "⚠️  警告：已暴露到外网",
        "en": "⚠️  Warning: exposed to external network",
    },
    "server.session_cleanup": {
        "zh": "已清理 {n} 个过期会话/token",
        "en": "Cleaned up {n} expired sessions/tokens",
    },
    "server.stats_error": {
        "zh": "获取统计信息时出错",
        "en": "Error fetching statistics",
    },

    # ── 邮件 ──
    "email.welcome_subject": {
        "zh": "欢迎加入 gitinstall",
        "en": "Welcome to gitinstall",
    },
    "email.reset_subject": {
        "zh": "gitinstall 密码重置",
        "en": "gitinstall Password Reset",
    },
    "email.welcome_greeting": {
        "zh": "🎉 欢迎加入 gitinstall！",
        "en": "🎉 Welcome to gitinstall!",
    },
    "email.register_success": {
        "zh": "你已成功注册 gitinstall 账号。",
        "en": "You have successfully registered a gitinstall account.",
    },
    "email.account_info": {
        "zh": "你的账号信息：",
        "en": "Your account information:",
    },
    "email.tier_free": {
        "zh": "免费用户（每月 20 次计划生成）",
        "en": "Free tier (20 plan generations per month)",
    },
    "email.start_using": {
        "zh": "开始使用",
        "en": "Get Started",
    },
    "email.forgot_password_hint": {
        "zh": "如需找回密码，请在登录页点击「忘记密码」。",
        "en": "To recover your password, click 'Forgot Password' on the login page.",
    },
    "email.auto_sent": {
        "zh": "此邮件由 gitinstall 自动发送，无需回复。",
        "en": "This email was sent automatically by gitinstall. No reply needed.",
    },
    "email.reset_title": {
        "zh": "🔑 重置密码",
        "en": "🔑 Reset Password",
    },
    "email.reset_request": {
        "zh": "我们收到了你的密码重置请求。点击下方按钮设置新密码：",
        "en": "We received your password reset request. Click the button below to set a new password:",
    },
    "email.reset_button": {
        "zh": "重置密码",
        "en": "Reset Password",
    },
    "email.reset_validity": {
        "zh": "此链接 30 分钟内有效。如果不是你本人操作，请忽略此邮件。",
        "en": "This link is valid for 30 minutes. If you didn't request this, please ignore.",
    },
    "email.reset_fallback": {
        "zh": "如果按钮无法点击，请复制以下链接到浏览器：",
        "en": "If the button doesn't work, copy this link to your browser:",
    },

    # ── Fetcher ──
    "fetcher.not_found": {
        "zh": "GitHub 上找不到该资源",
        "en": "Resource not found on GitHub",
    },
    "fetcher.rate_limit": {
        "zh": "RATELIMIT: GitHub API 频率超限",
        "en": "RATELIMIT: GitHub API rate limit exceeded",
    },
    "fetcher.network_failed": {
        "zh": "网络连接失败",
        "en": "Network connection failed",
    },
    "fetcher.searching": {
        "zh": "  📡 正在搜索 GitHub 上的 {repo}...",
        "en": "  📡 Searching GitHub for {repo}...",
    },
    "fetcher.fetching_info": {
        "zh": "  📡 获取 {owner}/{repo} 信息...",
        "en": "  📡 Fetching {owner}/{repo} info...",
    },
    "fetcher.reading_readme": {
        "zh": "  📖 读取 README...",
        "en": "  📖 Reading README...",
    },
    "fetcher.detecting_deps": {
        "zh": "  🔍 检测依赖文件...",
        "en": "  🔍 Detecting dependency files...",
    },
    "fetcher.cloning": {
        "zh": "  📥 git clone --depth 1 {owner}/{repo}...",
        "en": "  📥 git clone --depth 1 {owner}/{repo}...",
    },
    "fetcher.local_analysis": {
        "zh": "  🔍 本地分析项目文件...",
        "en": "  🔍 Analyzing local project files...",
    },

    # ── Autopilot ──
    "autopilot.title": {
        "zh": "🚗 自动驾驶",
        "en": "🚗 Autopilot",
    },
    "autopilot.progress": {
        "zh": "🚗 自动驾驶 [{current}/{total}] 安装：{identifier}",
        "en": "🚗 Autopilot [{current}/{total}] Installing: {identifier}",
    },
    "autopilot.success": {
        "zh": "  ✅ 安装成功（{duration}s）",
        "en": "  ✅ Installed successfully ({duration}s)",
    },
    "autopilot.install_failed": {
        "zh": "  ❌ 安装失败：{error}",
        "en": "  ❌ Installation failed: {error}",
    },
    "autopilot.user_interrupted": {
        "zh": "  ⏹️  用户中断",
        "en": "  ⏹️  User interrupted",
    },
    "autopilot.exception": {
        "zh": "  ❌ 异常：{error}",
        "en": "  ❌ Exception: {error}",
    },

    # ── 环境检测 ──
    "detect.chip": {
        "zh": "芯片：{chip}",
        "en": "Chip: {chip}",
    },
    "detect.wsl2": {
        "zh": "WSL2：{status}",
        "en": "WSL2: {status}",
    },
    "detect.gpu": {
        "zh": "GPU：{gpu}",
        "en": "GPU: {gpu}",
    },

    # ── Resilience ──
    "resilience.preflight_found": {
        "zh": "预检发现",
        "en": "Preflight check found",
    },
    "resilience.missing_tool": {
        "zh": "缺失工具",
        "en": "Missing tool",
    },

    # ── Health ──
    "health.ok": {
        "zh": "服务正常",
        "en": "Service healthy",
    },
    "health.degraded": {
        "zh": "服务降级",
        "en": "Service degraded",
    },
}


def set_locale(locale: str):
    """
    设置当前区域。

    Args:
        locale: 语言代码 ("zh", "en")

    环境变量 GITINSTALL_LANG 优先。
    """
    global _current_locale
    with _locale_lock:
        loc = locale.lower().split("_")[0].split("-")[0]  # "zh_CN" -> "zh"
        if loc in SUPPORTED_LOCALES:
            _current_locale = loc


def get_locale() -> str:
    """获取当前区域"""
    return _current_locale


def t(key: str, **kwargs) -> str:
    """
    翻译消息键。

    Args:
        key: 消息键 (如 "auth.password_min")
        **kwargs: 插值参数 (如 n=8)

    Returns:
        翻译后的字符串。若键不存在，返回键名本身。
    """
    msg_entry = _MESSAGES.get(key)
    if not msg_entry:
        return key

    text = msg_entry.get(_current_locale) or msg_entry.get(DEFAULT_LOCALE, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # 插值失败时返回原文

    return text


def register_messages(messages: dict[str, dict[str, str]]):
    """
    注册额外的消息条目（供插件/扩展模块使用）。

    Args:
        messages: {"msg.key": {"zh": "中文", "en": "English"}, ...}
    """
    _MESSAGES.update(messages)


# ── 初始化：从环境变量读取语言 ──
_env_lang = os.environ.get("GITINSTALL_LANG", "")
if _env_lang:
    set_locale(_env_lang)
