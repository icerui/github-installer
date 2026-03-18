"""
config_schema.py - gitinstall 配置 Schema 验证
================================================

灵感来源：OpenClaw 的 Zod Schema 配置验证 + 热重载

功能：
  1. 定义配置文件的完整 Schema（类似 Zod 但纯 Python）
  2. 验证 ~/.gitinstall/config.json 格式合法性
  3. 提供默认值合并
  4. 类型检查 + 范围校验
  5. 友好的错误提示

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ValidationError:
    """验证错误"""
    path: str       # 配置路径，如 "install_mode"
    message: str    # 错误消息
    expected: str   # 期望值描述
    actual: Any     # 实际值


@dataclass
class ValidationResult:
    """验证结果"""
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)  # 合并默认值后的完整配置


# ─────────────────────────────────────────────
#  Schema 定义
# ─────────────────────────────────────────────

# 配置项 Schema：(类型, 默认值, 描述, 可选的验证函数)
CONFIG_SCHEMA = {
    "version": {
        "type": str,
        "default": "1.0",
        "description": "配置文件版本",
    },
    "default_install_dir": {
        "type": str,
        "default": "~/projects",
        "description": "默认安装目录",
    },
    "install_mode": {
        "type": str,
        "default": "safe",
        "description": "安装模式",
        "enum": ["safe", "fast", "strict"],
    },
    "telemetry": {
        "type": bool,
        "default": True,
        "description": "是否启用匿名遥测",
    },
    "github_token": {
        "type": str,
        "default": "",
        "description": "GitHub Personal Access Token",
        "secret": True,
    },
    "llm_preference": {
        "type": str,
        "default": "",
        "description": "首选 LLM 服务",
        "enum": ["", "anthropic", "openai", "groq", "deepseek", "gemini", "ollama", "lmstudio", "none"],
    },
    "llm_key": {
        "type": dict,
        "default": {},
        "description": "LLM API Key 存储",
    },
    "cache_ttl": {
        "type": int,
        "default": 86400,
        "description": "缓存 TTL（秒）",
        "min": 0,
        "max": 604800,  # 最多 7 天
    },
    "max_readme_size": {
        "type": int,
        "default": 50000,
        "description": "README 最大读取字节数",
        "min": 1000,
        "max": 500000,
    },
    "auto_venv": {
        "type": bool,
        "default": True,
        "description": "Python 项目自动创建 virtualenv",
    },
    "docker_prefer": {
        "type": bool,
        "default": False,
        "description": "优先使用 Docker（当 Dockerfile 存在时）",
    },
    "skills_initialized": {
        "type": bool,
        "default": False,
        "description": "内建 Skills 是否已初始化",
    },
    "onboard_completed": {
        "type": bool,
        "default": False,
        "description": "引导向导是否已完成",
    },
    "web_port": {
        "type": int,
        "default": 8080,
        "description": "Web UI 端口",
        "min": 1024,
        "max": 65535,
    },
    "web_host": {
        "type": str,
        "default": "127.0.0.1",
        "description": "Web UI 绑定地址",
    },
    "preferred_platform": {
        "type": str,
        "default": "github",
        "description": "默认代码托管平台",
        "enum": ["github", "gitlab", "bitbucket", "gitee", "codeberg"],
    },
    "detected_env": {
        "type": dict,
        "default": {},
        "description": "检测到的系统环境信息（自动填充）",
    },
    # ── 镜像加速（中国用户 / 企业内网）──
    "mirror_pypi": {
        "type": str,
        "default": "",
        "description": "PyPI 镜像（如 https://pypi.tuna.tsinghua.edu.cn/simple）",
    },
    "mirror_npm": {
        "type": str,
        "default": "",
        "description": "npm 镜像（如 https://registry.npmmirror.com）",
    },
    "mirror_github": {
        "type": str,
        "default": "",
        "description": "GitHub 加速镜像（如 https://ghproxy.com）",
    },
    # ── 代理 ──
    "http_proxy": {
        "type": str,
        "default": "",
        "description": "HTTP/HTTPS 代理（如 http://127.0.0.1:7890）",
    },
    # ── GPU / 硬件 ──
    "gpu_memory_override_gb": {
        "type": float,
        "default": 0.0,
        "description": "手动指定 GPU 显存（GB），0 表示自动检测",
        "min": 0.0,
        "max": 1024.0,
    },
    # ── 并发 ──
    "max_concurrent_downloads": {
        "type": int,
        "default": 3,
        "description": "最大并发下载数",
        "min": 1,
        "max": 16,
    },
    # ── 企业 ──
    "enterprise_mode": {
        "type": bool,
        "default": False,
        "description": "启用企业功能（SSO/RBAC/审计）",
    },
    "sso_provider": {
        "type": str,
        "default": "",
        "description": "SSO 提供商",
        "enum": ["", "oidc", "saml"],
    },
    "sso_issuer": {
        "type": str,
        "default": "",
        "description": "SSO OIDC Issuer URL",
    },
}


# ─────────────────────────────────────────────
#  验证逻辑
# ─────────────────────────────────────────────

def validate_config(config: dict) -> ValidationResult:
    """
    验证配置文件。

    返回 ValidationResult:
      - valid: 是否通过验证（有 error 则为 False）
      - errors: 错误列表
      - warnings: 警告列表
      - config: 合并默认值后的完整配置
    """
    errors = []
    warnings = []
    merged = {}

    # 类型防御：config 必须是 dict
    if not isinstance(config, dict):
        return ValidationResult(
            valid=False,
            errors=[ValidationError(
                path="<root>",
                message="配置必须是 dict 类型",
                expected="dict",
                actual=type(config).__name__,
            )],
            warnings=[],
            config={key: schema["default"] for key, schema in CONFIG_SCHEMA.items()},
        )

    # 合并默认值
    for key, schema in CONFIG_SCHEMA.items():
        if key in config:
            merged[key] = config[key]
        else:
            merged[key] = schema["default"]

    # 检查未知字段
    known_keys = set(CONFIG_SCHEMA.keys())
    # 允许一些额外字段
    extra_allowed = {"onboard_time"}
    for key in config:
        if key not in known_keys and key not in extra_allowed:
            warnings.append(f"未知配置项 '{key}' 将被忽略")

    # 逐项验证
    for key, schema in CONFIG_SCHEMA.items():
        if key not in config:
            continue

        value = config[key]
        expected_type = schema["type"]

        # 类型检查
        if not isinstance(value, expected_type):
            # int 和 float 互容
            if expected_type in (int, float) and isinstance(value, (int, float)):
                merged[key] = expected_type(value)
            else:
                errors.append(ValidationError(
                    path=key,
                    message=f"类型错误",
                    expected=expected_type.__name__,
                    actual=type(value).__name__,
                ))
                continue

        # 枚举检查
        if "enum" in schema and value not in schema["enum"]:
            errors.append(ValidationError(
                path=key,
                message=f"值不在允许范围内",
                expected=f"之一: {schema['enum']}",
                actual=value,
            ))

        # 数值范围检查
        if "min" in schema and isinstance(value, (int, float)):
            if value < schema["min"]:
                errors.append(ValidationError(
                    path=key,
                    message=f"值过小",
                    expected=f">= {schema['min']}",
                    actual=value,
                ))
        if "max" in schema and isinstance(value, (int, float)):
            if value > schema["max"]:
                errors.append(ValidationError(
                    path=key,
                    message=f"值过大",
                    expected=f"<= {schema['max']}",
                    actual=value,
                ))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        config=merged,
    )


def load_and_validate(config_path: Path = None) -> ValidationResult:
    """加载并验证配置文件"""
    config_path = config_path or (Path.home() / ".gitinstall" / "config.json")

    if not config_path.exists():
        # 配置文件不存在，返回全默认值
        defaults = {k: v["default"] for k, v in CONFIG_SCHEMA.items()}
        return ValidationResult(valid=True, config=defaults)

    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult(
            valid=False,
            errors=[ValidationError("(file)", f"JSON 解析失败: {e}", "合法 JSON", str(e))],
        )
    except OSError as e:
        return ValidationResult(
            valid=False,
            errors=[ValidationError("(file)", f"文件读取失败: {e}", "可读文件", str(e))],
        )

    return validate_config(config)


def get_config_value(key: str, fallback=None):
    """快速获取单个配置值（带默认值回退）"""
    result = load_and_validate()
    return result.config.get(key, fallback)


def format_validation_result(result: ValidationResult) -> str:
    """格式化验证结果"""
    lines = []
    if result.valid:
        lines.append("  ✅ 配置文件验证通过")
    else:
        lines.append("  ❌ 配置文件存在错误：")
        for err in result.errors:
            lines.append(f"    • {err.path}: {err.message} (期望: {err.expected}, 实际: {err.actual})")

    if result.warnings:
        for w in result.warnings:
            lines.append(f"    ⚠️  {w}")

    return "\n".join(lines)
