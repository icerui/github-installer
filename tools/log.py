"""
log.py - gitinstall 统一结构化日志模块
========================================

企业级日志基础设施，零外部依赖。
支持：JSON 结构化输出、日志轮转、分级过滤、上下文注入。

用法：
    from log import get_logger
    logger = get_logger(__name__)
    logger.info("安装开始", project="comfyui", strategy="pip")
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
import threading
from pathlib import Path
from typing import Any

# ── 日志目录 ──
LOG_DIR = Path.home() / ".gitinstall" / "logs"

# ── 全局配置 ──
_configured = False
_config_lock = threading.Lock()


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器，支持 JSON 和人类可读两种模式"""

    def __init__(self, json_mode: bool = False):
        super().__init__()
        self.json_mode = json_mode

    def format(self, record: logging.LogRecord) -> str:
        # 提取额外字段（由 StructuredLoggerAdapter 注入）
        extra = getattr(record, "_structured_extra", {})

        if self.json_mode:
            log_entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "module": record.module,
                "func": record.funcName,
                "line": record.lineno,
            }
            if extra:
                log_entry["extra"] = extra
            if record.exc_info and record.exc_info[0]:
                log_entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_entry, ensure_ascii=False, default=str)
        else:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            base = f"{ts} [{record.levelname:>7s}] {record.name}: {record.getMessage()}"
            if extra:
                kv = " ".join(f"{k}={v}" for k, v in extra.items())
                base += f" | {kv}"
            if record.exc_info and record.exc_info[0]:
                base += "\n" + self.formatException(record.exc_info)
            return base


class StructuredLoggerAdapter(logging.LoggerAdapter):
    """支持结构化字段的日志适配器

    用法：
        logger.info("消息", project="foo", step=3)
    """

    def process(self, msg, kwargs):
        # 从 kwargs 中提取非标准字段作为结构化数据
        extra = {}
        standard_keys = {"exc_info", "stack_info", "stacklevel", "extra"}
        for k in list(kwargs.keys()):
            if k not in standard_keys:
                extra[k] = kwargs.pop(k)

        # 合并到 extra dict
        if "extra" not in kwargs:
            kwargs["extra"] = {}
        kwargs["extra"]["_structured_extra"] = {
            **self.extra,
            **extra,
        }
        return msg, kwargs


def _ensure_log_dir():
    """确保日志目录存在并设置安全权限"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(LOG_DIR, 0o700)
    except OSError:
        pass


def configure(
    level: str = None,
    json_mode: bool = None,
    log_file: bool = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
):
    """
    配置全局日志系统。只在首次调用时生效。

    环境变量覆盖：
        GITINSTALL_LOG_LEVEL   = DEBUG|INFO|WARNING|ERROR (默认 INFO)
        GITINSTALL_LOG_JSON    = 1|0 (默认 0)
        GITINSTALL_LOG_FILE    = 1|0 (默认 1)

    Args:
        level: 日志级别
        json_mode: 是否使用 JSON 格式输出
        log_file: 是否写入文件
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的旧日志文件数量
    """
    global _configured
    if _configured:
        return
    with _config_lock:
        if _configured:
            return

        # 从环境变量读取配置
        env_level = os.environ.get("GITINSTALL_LOG_LEVEL", "INFO").upper()
        env_json = os.environ.get("GITINSTALL_LOG_JSON", "0") == "1"
        env_file = os.environ.get("GITINSTALL_LOG_FILE", "1") == "1"

        log_level = getattr(logging, level or env_level, logging.INFO)
        use_json = json_mode if json_mode is not None else env_json
        use_file = log_file if log_file is not None else env_file

        root = logging.getLogger("gitinstall")
        root.setLevel(log_level)

        # 清除已有 handler（防止重复配置）
        root.handlers.clear()

        # stderr handler（人类可读格式，始终附加）
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(StructuredFormatter(json_mode=False))
        stderr_handler.setLevel(log_level)
        root.addHandler(stderr_handler)

        # 文件 handler（JSON 格式，带轮转）
        if use_file:
            try:
                _ensure_log_dir()
                file_handler = logging.handlers.RotatingFileHandler(
                    LOG_DIR / "gitinstall.log",
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(StructuredFormatter(json_mode=True))
                file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
                root.addHandler(file_handler)
                # 硬化日志文件权限
                log_path = LOG_DIR / "gitinstall.log"
                if log_path.exists():
                    try:
                        os.chmod(log_path, 0o600)
                    except OSError:
                        pass
            except (OSError, PermissionError):
                pass  # 无法写文件时静默降级

        _configured = True


def get_logger(name: str, **default_extra) -> StructuredLoggerAdapter:
    """
    获取结构化日志记录器。

    Args:
        name: 模块名（建议用 __name__）
        **default_extra: 每条日志自动附加的字段

    Returns:
        StructuredLoggerAdapter 实例

    用法：
        logger = get_logger(__name__)
        logger.info("安装完成", project="foo", duration=12.3)
        logger.error("安装失败", project="bar", exc_info=True)
    """
    # 自动配置（首次调用时）
    configure()

    # 规范化名称：确保在 gitinstall 命名空间下
    if not name.startswith("gitinstall."):
        # 从 tools/xxx.py 提取模块名
        short = name.rsplit(".", 1)[-1] if "." in name else name
        full_name = f"gitinstall.{short}"
    else:
        full_name = name

    underlying = logging.getLogger(full_name)
    return StructuredLoggerAdapter(underlying, default_extra)


# ── 便捷接口：CLI 进度输出 ──
# 用于替换 print(..., file=sys.stderr) 的进度消息

def progress(msg: str, **extra):
    """输出进度信息到 stderr（替代 print(msg, file=sys.stderr)）"""
    logger = get_logger("gitinstall.cli")
    logger.info(msg, **extra)


def debug(msg: str, **extra):
    """输出调试信息"""
    logger = get_logger("gitinstall.cli")
    logger.debug(msg, **extra)


def warn(msg: str, **extra):
    """输出警告信息"""
    logger = get_logger("gitinstall.cli")
    logger.warning(msg, **extra)


def error(msg: str, **extra):
    """输出错误信息"""
    logger = get_logger("gitinstall.cli")
    logger.error(msg, **extra)
