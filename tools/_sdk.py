"""
gitinstall SDK — 嵌入式软件安装引擎的公共 API
==============================================

四个核心函数：
    detect()   → 检测系统环境
    plan()     → 生成安装方案
    install()  → 执行安装
    diagnose() → 报错诊断

辅助函数：
    fetch()    → 获取项目信息
    doctor()   → 系统诊断
    audit()    → 依赖安全审计

用法::

    import gitinstall

    env = gitinstall.detect()
    plan = gitinstall.plan("comfyanonymous/ComfyUI", env=env)
    result = gitinstall.install(plan)

    if not result["success"]:
        fix = gitinstall.diagnose(result["error_message"], result["last_command"])
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

# 确保 tools/ 目录在 sys.path 中，使内部 bare import 正常工作
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))


# ── 核心函数 1：detect ───────────────────────

def detect() -> dict:
    """检测当前系统环境。

    Returns:
        dict: 包含 os/hardware/gpu/runtimes/package_managers/disk 等信息。

    示例::

        env = gitinstall.detect()
        print(env["gpu"]["type"])   # "apple_mps"
        print(env["os"]["arch"])    # "arm64"
    """
    from detector import EnvironmentDetector
    return EnvironmentDetector().detect()


# ── 核心函数 2：plan ─────────────────────────

def plan(
    identifier: str,
    *,
    env: dict = None,
    llm: str = None,
    local: bool = False,
) -> dict:
    """为指定项目生成安装方案。

    Args:
        identifier: GitHub 项目标识，如 "comfyanonymous/ComfyUI" 或 URL。
        env: 环境信息 dict（来自 detect()）。省略则自动检测。
        llm: 指定 LLM（如 "ollama"/"openai"/"none"）。省略则自动选择。
        local: True 时用 git clone 本地分析，避免 GitHub API 限额。

    Returns:
        dict: 包含 steps/launch_command/confidence/strategy 等字段。
              失败时 status="error"。

    示例::

        plan = gitinstall.plan("pytorch/pytorch")
        for step in plan["steps"]:
            print(step["command"])
    """
    from main import cmd_plan
    result = cmd_plan(identifier, llm_force=llm, use_local=local)

    # 如果调用方提供了 env 但 cmd_plan 内部会自行检测，
    # 这里的 env 参数用于未来优化（避免重复检测）。
    # 当前版本 cmd_plan 总是自行检测环境。

    return result


# ── 核心函数 3：install ──────────────────────

def install(
    plan_or_identifier,
    *,
    install_dir: str = None,
    llm: str = None,
    local: bool = False,
    dry_run: bool = False,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """执行安装。

    可以传入 plan() 返回的 dict，或直接传项目标识符（会自动调用 plan）。

    Args:
        plan_or_identifier: plan() 的返回值 dict，或项目标识字符串。
        install_dir: 安装目录。省略则使用默认目录。
        llm: 指定 LLM。
        local: 使用本地模式获取项目信息。
        dry_run: True 时只展示计划不执行。
        on_progress: 进度回调函数，接收 event dict。

    Returns:
        dict: 包含 success/install_dir/launch_command/steps_completed 等字段。

    回调事件::

        on_progress 会收到以下类型的 event：
        {"type": "plan_ready", "steps": [...], "project": "..."}
        {"type": "step_start", "step": 1, "total": 5, "command": "...", "description": "..."}
        {"type": "step_done",  "step": 1, "total": 5, "success": True, "duration": 2.3}
        {"type": "step_failed","step": 1, "total": 5, "error": "..."}
        {"type": "install_done", "success": True, "install_dir": "..."}

    示例::

        # 方式 1：直接传标识符
        result = gitinstall.install("comfyanonymous/ComfyUI")

        # 方式 2：传 plan dict
        p = gitinstall.plan("comfyanonymous/ComfyUI")
        result = gitinstall.install(p)

        # 方式 3：带进度回调
        def progress(event):
            print(f"[{event.get('step', '?')}/{event.get('total', '?')}] {event.get('type')}")
        result = gitinstall.install("comfyanonymous/ComfyUI", on_progress=progress)
    """
    # 如果传入的是字符串，走完整的 cmd_install 路径
    if isinstance(plan_or_identifier, str):
        if on_progress:
            return _install_with_progress(
                plan_or_identifier,
                install_dir=install_dir,
                llm=llm,
                local=local,
                dry_run=dry_run,
                on_progress=on_progress,
            )
        from main import cmd_install
        return cmd_install(
            plan_or_identifier,
            install_dir=install_dir,
            llm_force=llm,
            dry_run=dry_run,
            use_local=local,
        )

    # 如果传入的是 plan dict，直接执行
    if isinstance(plan_or_identifier, dict):
        plan_dict = plan_or_identifier
        if plan_dict.get("status") == "error":
            return plan_dict

        plan_data = plan_dict.get("plan", plan_dict)
        project_name = plan_dict.get("project", "")
        steps = plan_data.get("steps", [])

        if not steps:
            return {"status": "error", "message": "安装方案中没有步骤。", "success": False}

        if dry_run:
            return {"status": "ok", "dry_run": True, "plan": plan_data, "success": True}

        from executor import InstallExecutor
        from llm import create_provider
        from pathlib import Path

        llm_provider = create_provider(force=llm)
        executor = InstallExecutor(llm_provider=llm_provider, verbose=True)

        if install_dir:
            base_dir = str(Path(install_dir).expanduser())
            Path(base_dir).mkdir(parents=True, exist_ok=True)
            executor.executor.work_dir = base_dir
            executor.executor._current_dir = base_dir

        if on_progress:
            on_progress({
                "type": "plan_ready",
                "steps": [s.get("description", "") for s in steps],
                "project": project_name,
            })

        result = _execute_with_callbacks(executor, plan_data, project_name, on_progress)
        return result

    raise TypeError(f"install() 需要 str 或 dict，收到 {type(plan_or_identifier).__name__}")


def _install_with_progress(
    identifier: str,
    *,
    install_dir: str = None,
    llm: str = None,
    local: bool = False,
    dry_run: bool = False,
    on_progress: Callable,
) -> dict:
    """带进度回调的完整安装流程。"""
    # 1. 规划
    on_progress({"type": "detecting", "message": "检测系统环境..."})
    plan_result = plan(identifier, llm=llm, local=local)

    if plan_result.get("status") != "ok":
        return plan_result

    plan_data = plan_result["plan"]
    project_name = plan_result.get("project", identifier)
    steps = plan_data.get("steps", [])

    on_progress({
        "type": "plan_ready",
        "steps": [s.get("description", "") for s in steps],
        "project": project_name,
        "confidence": plan_result.get("confidence", ""),
        "strategy": plan_data.get("strategy", ""),
    })

    if dry_run:
        return {"status": "ok", "dry_run": True, "plan": plan_data, "success": True}

    # 2. 预检
    from resilience import preflight_check
    pf = preflight_check(steps)
    if not pf.all_ready:
        on_progress({
            "type": "preflight",
            "missing_tools": pf.missing_tools,
            "install_commands": [s.get("command", "") for s in pf.install_commands],
        })
        steps = pf.install_commands + steps
        plan_data["steps"] = steps

    # 3. 执行
    from executor import InstallExecutor
    from llm import create_provider
    from pathlib import Path

    llm_provider = create_provider(force=llm)
    executor = InstallExecutor(llm_provider=llm_provider, verbose=True)

    if install_dir:
        base_dir = str(Path(install_dir).expanduser())
        Path(base_dir).mkdir(parents=True, exist_ok=True)
        executor.executor.work_dir = base_dir
        executor.executor._current_dir = base_dir

    result = _execute_with_callbacks(executor, plan_data, project_name, on_progress)

    # 4. 失败时尝试回退
    if not result["success"]:
        from resilience import generate_fallback_plans
        owner, _, repo = identifier.partition("/")
        env = detect()
        project_types = plan_result.get("_project_types", [])
        dep_files = plan_result.get("_dependency_files", {})
        primary_strategy = plan_data.get("strategy", "")

        fallback_plans = generate_fallback_plans(
            owner, repo, project_types, env, dependency_files=dep_files,
        )
        tried = {primary_strategy}

        for fb in fallback_plans:
            if fb.strategy in tried:
                continue
            tried.add(fb.strategy)

            on_progress({
                "type": "fallback_start",
                "strategy": fb.strategy,
                "tier": fb.tier,
            })

            fb_plan = {
                "steps": fb.steps,
                "launch_command": plan_data.get("launch_command", ""),
                "strategy": fb.strategy,
            }
            executor.executor.reset(install_dir)
            fb_result = _execute_with_callbacks(executor, fb_plan, project_name, on_progress)

            if fb_result["success"]:
                result = fb_result
                break

    # 5. 遥测记录
    try:
        from db import record_install_telemetry
        record_install_telemetry(
            project=project_name,
            strategy=plan_data.get("strategy", ""),
            gpu_info=plan_result.get("gpu_info"),
            env=plan_result.get("_env") or {},
            success=result["success"],
            error_type="step_failed" if not result["success"] else None,
            error_message=result.get("error_summary") if not result["success"] else None,
            duration_sec=None,
            steps_total=result.get("steps_total", 0),
            steps_completed=result.get("steps_completed", 0),
        )
    except Exception:
        pass

    return result


def _execute_with_callbacks(
    executor,
    plan_data: dict,
    project_name: str,
    on_progress: Optional[Callable] = None,
) -> dict:
    """执行安装计划，在每步前后触发回调。"""
    from executor import check_command_safety

    steps = plan_data.get("steps", [])
    launch_command = plan_data.get("launch_command", "")
    completed = []
    error_summary = ""

    for i, step in enumerate(steps, 1):
        command = step.get("command", "").strip()
        description = step.get("description", f"步骤 {i}")
        working_dir = step.get("working_dir", "")

        if not command:
            continue

        is_safe, warning = check_command_safety(command)
        if not is_safe:
            error_summary = warning
            if on_progress:
                on_progress({
                    "type": "step_blocked",
                    "step": i, "total": len(steps),
                    "command": command, "reason": warning,
                })
            break

        if on_progress:
            on_progress({
                "type": "step_start",
                "step": i, "total": len(steps),
                "command": command, "description": description,
            })

        step_result = executor.executor.run(command, working_dir or None)

        if step_result.success:
            completed.append(step_result)
            if on_progress:
                on_progress({
                    "type": "step_done",
                    "step": i, "total": len(steps),
                    "success": True,
                    "duration": step_result.duration_sec,
                })
        else:
            # 尝试自动修复
            fixed = executor._try_fix(step_result, i, len(steps))
            if fixed:
                completed.append(step_result)
                if on_progress:
                    on_progress({
                        "type": "step_fixed",
                        "step": i, "total": len(steps),
                        "fix": step_result.fix_command,
                    })
            else:
                error_summary = (
                    f"第 {i} 步失败：{description}\n"
                    f"命令：{command}\n"
                    f"报错：{step_result.stderr[:500]}"
                )
                if on_progress:
                    on_progress({
                        "type": "step_failed",
                        "step": i, "total": len(steps),
                        "error": step_result.stderr[:500],
                        "command": command,
                    })
                break

    success = len(completed) == len([s for s in steps if s.get("command", "").strip()])
    install_dir = executor.executor._current_dir

    if on_progress:
        on_progress({
            "type": "install_done",
            "success": success,
            "install_dir": install_dir,
            "steps_completed": len(completed),
            "steps_total": len(steps),
        })

    return {
        "status": "ok" if success else "error",
        "success": success,
        "project": project_name,
        "install_dir": install_dir,
        "launch_command": launch_command,
        "error_summary": error_summary,
        "steps_completed": len(completed),
        "steps_total": len(steps),
        "plan_strategy": plan_data.get("strategy", ""),
    }


# ── 核心函数 4：diagnose ────────────────────

def diagnose(
    stderr: str,
    command: str = "",
    stdout: str = "",
) -> dict | None:
    """诊断安装报错并返回修复建议。

    Args:
        stderr: 错误输出文本。
        command: 引发错误的命令。
        stdout: 标准输出文本（可选）。

    Returns:
        dict: 包含 root_cause/fix_commands/confidence 等字段。
        如果无法诊断，返回 None。

    示例::

        fix = gitinstall.diagnose(
            stderr="error: externally-managed-environment",
            command="pip install pandas"
        )
        if fix:
            print(fix["fix_commands"])  # ["pip install --break-system-packages pandas"]
    """
    from error_fixer import diagnose as _diagnose
    result = _diagnose(command, stderr, stdout)
    if result is None:
        return None
    return {
        "root_cause": result.root_cause,
        "fix_commands": result.fix_commands,
        "retry_original": result.retry_original,
        "confidence": result.confidence,
        "outcome": result.outcome,
    }


# ── 辅助函数 ────────────────────────────────

def fetch(identifier: str, *, local: bool = False) -> dict:
    """获取 GitHub 项目信息。

    Args:
        identifier: 项目标识（"owner/repo" 或 URL）。
        local: True 时用 git clone 本地分析。

    Returns:
        dict: 包含项目名、描述、星标、语言、类型、依赖文件等。
    """
    if local:
        from fetcher import fetch_project_local
        info = fetch_project_local(identifier)
    else:
        from fetcher import fetch_project
        info = fetch_project(identifier)
    return {
        "full_name": info.full_name,
        "description": info.description,
        "stars": info.stars,
        "language": info.language,
        "license": info.license,
        "project_type": info.project_type,
        "clone_url": info.clone_url,
        "homepage": info.homepage,
        "dependency_files": list(info.dependency_files.keys()),
        "readme_preview": info.readme[:500] if info.readme else "",
    }


def doctor() -> dict:
    """运行系统诊断，返回检查结果。

    Returns:
        dict: 包含 checks 列表和 all_ok 布尔值。
    """
    from doctor import run_doctor
    report = run_doctor()
    return {
        "status": "ok",
        "all_ok": report.all_ok,
        "ok_count": report.ok_count,
        "warn_count": report.warn_count,
        "error_count": report.error_count,
        "checks": [
            {
                "name": c.name,
                "level": c.level,
                "message": c.message,
                "detail": c.detail,
                "fix_hint": c.fix_hint,
            }
            for c in report.checks
        ],
    }


def audit(identifier: str, *, online: bool = False) -> dict:
    """审计项目依赖的安全漏洞。

    Args:
        identifier: 项目标识。
        online: True 时联网查询 CVE 数据库。

    Returns:
        dict: 包含各生态系统的漏洞报告。
    """
    from main import cmd_audit
    return cmd_audit(identifier, online=online)


def uninstall(
    identifier: str,
    *,
    install_dir: str = None,
    keep_config: bool = False,
    confirm: bool = False,
) -> dict:
    """安全卸载已安装的 GitHub 项目。

    Args:
        identifier: 项目标识，如 "owner/repo"。
        install_dir: 安装目录。省略则自动探测。
        keep_config: True 时保留配置文件。
        confirm: True 时执行卸载，False 时只显示计划。

    Returns:
        dict: 包含卸载计划或执行结果。
    """
    from main import cmd_uninstall
    return cmd_uninstall(
        identifier,
        keep_config=keep_config,
        clean_only=False,
        confirm=confirm,
        install_dir=install_dir,
    )


# ── 版本信息 ─────────────────────────────────

__version__ = "1.1.0"
