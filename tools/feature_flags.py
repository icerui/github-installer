"""
feature_flags.py - 功能开关系统
=================================

灵感来源：ICE-cluade-SCompany 的 Feature Flag 架构

通过环境变量控制功能模块的开关，支持：
  1. 灰度发布：新功能先小范围测试
  2. 运行时切换：无需重启即可开关功能
  3. 默认值管理：每个 flag 有合理默认值
  4. 分组管理：按模块分组管理 flags

环境变量格式：GITINSTALL_<FLAG_NAME>=1|0|true|false

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeatureFlag:
    """单个功能开关"""
    name: str
    description: str
    default: bool = False
    group: str = "general"       # general, security, experimental, performance
    env_var: str = ""            # 自动生成: GITINSTALL_<NAME>

    def __post_init__(self):
        if not self.env_var:
            self.env_var = f"GITINSTALL_{self.name.upper()}"


# ── 全局功能开关注册表 ──
_FLAGS: dict[str, FeatureFlag] = {}


def _register(name: str, description: str, default: bool = False,
              group: str = "general") -> FeatureFlag:
    """注册一个功能开关"""
    flag = FeatureFlag(name=name, description=description,
                       default=default, group=group)
    _FLAGS[name] = flag
    return flag


# ─────────────────────────────────────────────
#  内置功能开关定义
# ─────────────────────────────────────────────

# 安全相关
_register("pre_audit", "安装前自动执行依赖安全审计", default=True, group="security")
_register("license_check", "安装前自动检查许可证兼容性", default=True, group="security")

# 实验性功能
_register("knowledge_base", "启用安装知识库（记录+匹配历史案例）", default=True, group="experimental")
_register("checkpoint_resume", "启用安装断点恢复", default=True, group="experimental")
_register("watchdog", "启用安装看门狗（超时监控）", default=True, group="experimental")
_register("event_bus", "启用事件总线 + Webhook 通知", default=False, group="experimental")
_register("dep_chain", "启用依赖链可视化", default=True, group="experimental")

# 性能相关
_register("autopilot", "启用批量安装自动驾驶模式", default=False, group="performance")
_register("parallel_preflight", "预检层并行执行", default=False, group="performance")

# 通用
_register("skills_match", "安装时自动匹配社区 Skills", default=True, group="general")
_register("telemetry", "发送匿名安装遥测", default=True, group="general")
_register("auto_track", "安装成功后自动记录到 InstallTracker", default=True, group="general")


# ─────────────────────────────────────────────
#  核心 API
# ─────────────────────────────────────────────

def is_enabled(name: str) -> bool:
    """检查功能开关是否启用"""
    flag = _FLAGS.get(name)
    if not flag:
        return False

    # 环境变量优先
    env_val = os.environ.get(flag.env_var, "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return True
    if env_val in ("0", "false", "no", "off"):
        return False

    # 使用默认值
    return flag.default


def get_flag(name: str) -> Optional[FeatureFlag]:
    """获取功能开关定义"""
    return _FLAGS.get(name)


def list_flags(group: str = None) -> list[FeatureFlag]:
    """列出所有功能开关"""
    flags = list(_FLAGS.values())
    if group:
        flags = [f for f in flags if f.group == group]
    return flags


def get_all_status() -> dict[str, bool]:
    """获取所有功能开关的当前状态"""
    return {name: is_enabled(name) for name in _FLAGS}


def format_flags_table() -> str:
    """格式化功能开关状态表"""
    lines = ["🚩 功能开关状态：", ""]
    groups: dict[str, list] = {}
    for flag in _FLAGS.values():
        groups.setdefault(flag.group, []).append(flag)

    group_icons = {"security": "🔒", "experimental": "🧪",
                   "performance": "⚡", "general": "⚙️"}

    for group_name in ("security", "general", "experimental", "performance"):
        flags = groups.get(group_name, [])
        if not flags:
            continue
        icon = group_icons.get(group_name, "")
        lines.append(f"  {icon} {group_name.upper()}")
        for f in flags:
            status = "✅ ON " if is_enabled(f.name) else "❌ OFF"
            lines.append(f"    {status}  {f.name:<22} {f.description}")
            lines.append(f"           env: {f.env_var}=1|0")
        lines.append("")

    return "\n".join(lines)
