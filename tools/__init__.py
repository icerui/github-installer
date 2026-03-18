"""
gitinstall — 嵌入式软件安装引擎
================================

在任何项目中嵌入 gitinstall，获得跨平台软件安装能力。

核心函数::

    import gitinstall

    env    = gitinstall.detect()                          # 检测系统环境
    plan   = gitinstall.plan("owner/repo", env=env)       # 生成安装方案
    result = gitinstall.install(plan)                     # 执行安装
    fix    = gitinstall.diagnose(stderr, command)         # 报错诊断

辅助函数::

    info   = gitinstall.fetch("owner/repo")               # 获取项目信息
    report = gitinstall.doctor()                           # 系统诊断
    audit  = gitinstall.audit("owner/repo")                # 依赖安全审计

零外部依赖 · 跨平台 · 线程安全 · AI 可选增强
"""

from ._sdk import (
    detect,
    plan,
    install,
    diagnose,
    fetch,
    doctor,
    audit,
    __version__,
)

from .tool_schemas import (
    openai_tools,
    anthropic_tools,
    gemini_tools,
    json_schemas,
    call_tool,
    tool_names,
)

__all__ = [
    "detect",
    "plan",
    "install",
    "diagnose",
    "fetch",
    "doctor",
    "audit",
    "__version__",
    # AI 集成
    "openai_tools",
    "anthropic_tools",
    "gemini_tools",
    "json_schemas",
    "call_tool",
    "tool_names",
]
