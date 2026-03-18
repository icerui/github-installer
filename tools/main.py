"""
main.py - gitinstall 总入口
=====================================

支持两种调用方式：

  1. OpenClaw Skill 工具调用：
     python main.py detect
     python main.py fetch comfyanonymous/ComfyUI
     python main.py plan  comfyanonymous/ComfyUI
     python main.py install comfyanonymous/ComfyUI [--dir ~/AI] [--llm none]

  2. 独立 CLI（pip install gitinstall 后）：
     gitinstall comfyanonymous/ComfyUI
     gitinstall --dry-run AUTOMATIC1111/stable-diffusion-webui
     gitinstall --llm groq ollama/ollama

所有输出为 JSON（方便 OpenClaw 解析）+ 人类可读的进度信息（stderr）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ── 将 tools 目录加入 Python 路径（兼容直接执行和 import）──
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from log import get_logger
from i18n import t
from detector import EnvironmentDetector, format_env_summary
from fetcher import fetch_project, fetch_project_local, fetch_project_from_path, is_local_path, format_project_summary
from llm import create_provider, INSTALL_SYSTEM_PROMPT, INSTALL_SYSTEM_PROMPT_SMALL, HeuristicProvider
from executor import InstallExecutor, check_command_safety
from planner import SmartPlanner
from hw_detect import get_gpu_info, check_pytorch_compatibility, get_full_ai_hardware_report
from planner_known_projects import check_hardware_compatibility

logger = get_logger(__name__)


# ─────────────────────────────────────────────
#  子命令：detect
# ─────────────────────────────────────────────

def cmd_detect() -> dict:
    """检测当前系统环境，输出 JSON"""
    logger.info(t("install.detecting_env"))
    env = EnvironmentDetector().detect()
    logger.info(format_env_summary(env))
    return {"status": "ok", "env": env}


# ─────────────────────────────────────────────
#  子命令：fetch
# ─────────────────────────────────────────────

def cmd_fetch(identifier: str) -> dict:
    """获取 GitHub 项目信息，输出 JSON"""
    logger.info(t("install.fetching", project=identifier))
    try:
        info = fetch_project(identifier)
        logger.info(format_project_summary(info))
        return {
            "status": "ok",
            "project": {
                "full_name": info.full_name,
                "description": info.description,
                "stars": info.stars,
                "language": info.language,
                "project_type": info.project_type,
                "clone_url": info.clone_url,
                "homepage": info.homepage,
                "has_dockerfile": "docker" in info.project_type,
                "dependency_files": list(info.dependency_files.keys()),
                "readme_preview": info.readme[:500],
            },
        }
    except FileNotFoundError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"获取失败：{e}"}


# ─────────────────────────────────────────────
#  子命令：plan
# ─────────────────────────────────────────────

def cmd_plan(identifier: str, llm_force: str = None, use_local: bool = False) -> dict:
    """
    生成安装计划（不执行）。

    生成策略（按优先级）：
      1. SmartPlanner 已知项目数据库    → 无需任何 AI，命中率最高
      2. SmartPlanner 类型模板          → 无需任何 AI，依赖文件驱动
      3. 真实 LLM（如有配置）           → 分析 README 生成个性化步骤
         LLM 仅作为增强，不是必需项

    数据获取模式：
      use_local=True  → git clone --depth 1 本地分析（无 API 限额）
      use_local=False → GitHub REST API（默认，受 60 次/小时限制）
    """
    # 1. 环境检测
    logger.info("🔍 检测环境...")
    env = EnvironmentDetector().detect()

    # 2. 项目信息（本地路径 vs 本地 clone vs API 模式）
    source_path = ""
    try:
        if is_local_path(identifier):
            logger.info(f"📂 本地路径模式：直接分析目录（无需网络）...")
            info = fetch_project_from_path(identifier)
            source_path = str(info.clone_url)  # 实际是解析后的本地绝对路径
        elif use_local:
            logger.info("📥 本地模式：git clone + 本地文件分析（无 API 限额）...")
            info = fetch_project_local(identifier)
        else:
            logger.info("📡 获取项目信息...")
            info = fetch_project(identifier)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # 3. 首先用 SmartPlanner（零 AI，零 API Key）
    logger.info(t("install.analyzing"))
    planner = SmartPlanner()
    smart_plan = planner.generate_plan(
        owner=info.owner,
        repo=info.repo,
        env=env,
        project_types=info.project_type,
        dependency_files=info.dependency_files,
        readme=info.readme,
        clone_url=info.clone_url if not source_path else "",
        source_path=source_path,
    )
    confidence = smart_plan.get("confidence", "low")
    strategy = smart_plan.get("strategy", "")

    # 4. 判断是否需要 LLM
    #    - 已知项目（high）：直接用 SmartPlanner 结果，最准确
    #    - 明确指定 --llm none：不用 LLM
    #    - 类型模板/提取（medium/low）且有可用 LLM：用 LLM 补充
    llm = create_provider(force=llm_force)
    use_llm = (
        confidence != "high"           # 非已知项目才需要 LLM
        and llm_force != "none"        # 未强制禁用 LLM
        and not isinstance(llm, HeuristicProvider)  # 有真正的 LLM 可用
    )

    plan = smart_plan  # 默认用 SmartPlanner

    if use_llm:
        logger.info(f"🤖 SmartPlanner 置信度 {confidence}，尝试 LLM 补充分析（{llm.name}）...")
        try:
            user_prompt = _build_plan_prompt(env, info)
            # 小模型（名字含 1b/1.5b/1.7b/3b）用精简 prompt + 较小 max_tokens
            model_name = llm.name.lower()
            is_small_model = any(x in model_name for x in ["1b", "1.5b", "1.7b", "3b"])
            sys_prompt = INSTALL_SYSTEM_PROMPT_SMALL if is_small_model else INSTALL_SYSTEM_PROMPT
            max_tok = 800 if is_small_model else 3000
            response = llm.complete(sys_prompt, user_prompt, max_tokens=max_tok)
            llm_plan = _validate_plan_schema(_parse_plan_response(response))
            # LLM 成功且步骤更丰富时采用 LLM 结果
            if llm_plan.get("steps") and len(llm_plan["steps"]) >= len(smart_plan.get("steps", [])):
                plan = llm_plan
                plan["strategy"] = f"llm_enhanced({strategy})"
        except Exception as ex:
            logger.warning(t("llm.api_error") + f" ({ex})")
    else:
        source = "已知项目数据库" if confidence == "high" else "类型模板"
        logger.info(f"✅ SmartPlanner {source} 命中（无需 LLM）")

    # 5. 安全预检每个步骤
    safe_steps = []
    for step in plan.get("steps", []):
        cmd = step.get("command", "")
        is_safe, safety_warning = check_command_safety(cmd)
        step["_safe"] = is_safe
        if not step.get("_warning"):
            step["_warning"] = safety_warning
        if is_safe:
            safe_steps.append(step)
        else:
            logger.info(f"🚫 已过滤危险命令：{cmd}")
    plan["steps"] = safe_steps

    llm_desc = llm.name if use_llm else "SmartPlanner（无需 AI Key）"

    # 6. AI 硬件智能检测 + 兼容性检查
    gpu_info = get_gpu_info()
    hw_compat = check_hardware_compatibility(f"{info.owner}/{info.repo}", gpu_info, env)
    if hw_compat.get("warnings"):
        logger.info("\n⚡ 硬件兼容性提示：")
        for w in hw_compat["warnings"]:
            logger.warning(f"   ⚠️  {w}")
        for r in hw_compat.get("recommendations", []):
            logger.info(f"   💡 {r}")

    return {
        "status": "ok",
        "plan": plan,
        "llm_used": llm_desc,
        "confidence": confidence,
        "project": info.full_name,
        "hardware_check": hw_compat,
        "gpu_info": {
            "type": gpu_info.get("type"),
            "name": gpu_info.get("name"),
            "vram_gb": gpu_info.get("vram_gb"),
        },
        # 供韧性层（resilience）使用
        "_owner": info.owner,
        "_repo": info.repo,
        "_project_types": info.project_type,
        "_dependency_files": info.dependency_files,
        "_env": env,
    }


# ─────────────────────────────────────────────
#  子命令：install
# ─────────────────────────────────────────────

def cmd_install(
    identifier: str,
    install_dir: str = None,
    llm_force: str = None,
    dry_run: bool = False,
    use_local: bool = False,
) -> dict:
    """
    端到端安装：环境检测 → 项目信息 → 生成计划 → 执行 → 错误修复
    新增：预检层 + 多策略回退 + 安全审计 + 许可证检查 + Skills 匹配
    """
    from resilience import preflight_check, generate_fallback_plans

    # 1. 获取安装计划
    plan_result = cmd_plan(identifier, llm_force=llm_force, use_local=use_local)
    if plan_result["status"] != "ok":
        return plan_result

    plan = plan_result["plan"]
    steps = plan.get("steps", [])
    owner = plan_result.get("_owner", "")
    repo = plan_result.get("_repo", "")
    project_types = plan_result.get("_project_types", [])
    dependency_files = plan_result.get("_dependency_files", {})
    env = plan_result.get("_env", {})

    # 1b. 安全审计：扫描依赖中的 CVE/恶意包/typosquatting
    audit_warnings = []
    if dependency_files:
        try:
            from dependency_audit import audit_project, RISK_CRITICAL, RISK_HIGH
            audit_results = audit_project(dependency_files)
            for ar in audit_results:
                for vuln in ar.vulnerabilities:
                    if vuln.risk in (RISK_CRITICAL, RISK_HIGH):
                        audit_warnings.append(f"  🚨 [{vuln.risk.upper()}] {vuln.package}: {vuln.description}")
            if audit_warnings:
                logger.warning(f"\n⚠️  依赖安全审计发现 {len(audit_warnings)} 个高危问题：")
                for w in audit_warnings[:5]:
                    logger.warning(w)
                if len(audit_warnings) > 5:
                    logger.warning(f"  ... 还有 {len(audit_warnings) - 5} 个")
            else:
                logger.info("✅ 依赖安全审计通过")
        except Exception:
            pass  # 审计失败不阻塞安装

    # 1c. 许可证检查
    license_risk = ""
    if owner and repo:
        try:
            from license_check import fetch_license_from_github, analyze_license
            spdx_id, license_text = fetch_license_from_github(owner, repo)
            if spdx_id or license_text:
                lic_result = analyze_license(spdx_id, license_text)
                license_risk = lic_result.risk
                if lic_result.issues:
                    logger.info(f"\n📜 许可证（{spdx_id or '未知'}）：")
                    for issue in lic_result.issues[:3]:
                        logger.info(f"  {issue}")
                else:
                    logger.info(f"📜 许可证：{spdx_id or '未知'} ✅")
        except Exception:
            pass  # 许可证检查失败不阻塞安装

    # 1d. Skills 匹配：查找社区安装策略
    matched_skills = []
    try:
        from skills import SkillManager
        sm = SkillManager()
        matched_skills = sm.find_matching_skills(
            owner=owner, repo=repo,
            project_types=project_types,
            file_list=list(dependency_files.keys()),
        )
        if matched_skills:
            skill_names = [s.meta.name for s in matched_skills[:3]]
            logger.info(f"\n🧩 匹配到 {len(matched_skills)} 个 Skills：{', '.join(skill_names)}")
    except Exception:
        pass  # Skills 匹配失败不阻塞安装

    if not steps:
        return {"status": "error", "message": "未能生成有效的安装步骤，请手动查阅 README。"}

    # 2. 预检层：检查计划中缺失的工具，提前安装
    pf = preflight_check(steps)
    if not pf.all_ready:
        logger.info(f"\n🔧 预检发现 {len(pf.missing_tools)} 个缺失工具：{', '.join(pf.missing_tools)}")
        # 把预检安装步骤插入到计划最前面
        steps = pf.install_commands + steps
        plan["steps"] = steps

    # 3. 展示计划（dry-run 在此结束）
    logger.info("\n" + "─" * 50)
    logger.info(f"📋 安装计划：{plan_result['project']}")
    logger.info(f"   LLM：{plan_result['llm_used']}")
    logger.info("─" * 50)
    for i, step in enumerate(steps, 1):
        warning = step.get("_warning", "")
        warn_icon = "⚠️ " if warning else ""
        logger.info(f"  {i}. {warn_icon}{step.get('description', '')}:")
        logger.info(f"     $ {step.get('command', '')}")
    if plan.get("launch_command"):
        logger.info(f"\n  ▶ 启动命令：{plan['launch_command']}")
    logger.info("─" * 50)

    if dry_run:
        logger.info("\n(--dry-run 模式，不执行任何命令)")
        return {"status": "ok", "dry_run": True, "plan": plan}

    # 4. 执行主计划
    llm = create_provider(force=llm_force)
    executor = InstallExecutor(llm_provider=llm, verbose=True)

    # 调整第一步的工作目录（如果指定了安装目录）
    base_dir = install_dir
    if base_dir:
        base_dir = str(Path(base_dir).expanduser())
        Path(base_dir).mkdir(parents=True, exist_ok=True)
        executor.executor.work_dir = base_dir
        executor.executor._current_dir = base_dir

    result = executor.execute_plan(plan, project_name=plan_result["project"])

    # 5. 主计划失败 → 自动触发多策略回退
    if not result.success and owner and repo:
        primary_strategy = plan.get("strategy", "")
        logger.info(f"\n{'='*50}")
        logger.info(f"⚡ 主计划失败（策略：{primary_strategy}），启动多策略回退...")

        fallback_plans = generate_fallback_plans(owner, repo, project_types, env, dependency_files=dependency_files)
        tried = {primary_strategy}

        for fb in fallback_plans:
            if fb.strategy in tried:
                continue
            tried.add(fb.strategy)

            logger.info(f"\n🔄 尝试回退策略 Tier-{fb.tier}：{fb.strategy}（置信度：{fb.confidence}）")

            # 构建回退 plan dict
            fb_plan = {
                "steps": fb.steps,
                "launch_command": plan.get("launch_command", ""),
                "strategy": fb.strategy,
            }

            # 重置执行器状态
            executor.executor.reset(base_dir)

            fb_result = executor.execute_plan(fb_plan, project_name=plan_result["project"])
            if fb_result.success:
                logger.info(f"\n✅ 回退策略 {fb.strategy} 成功！")
                result = fb_result
                break
            else:
                logger.warning(f"  ❌ 回退策略 {fb.strategy} 也失败")

    # 6. 序列化输出 + 安装遥测
    try:
        from db import record_install_telemetry
        record_install_telemetry(
            project=plan_result["project"],
            strategy=plan.get("strategy", ""),
            gpu_info=plan_result.get("gpu_info"),
            env=env,
            success=result.success,
            error_type="step_failed" if not result.success else None,
            error_message=result.error_summary if not result.success else None,
            duration_sec=None,
            steps_total=len(result.steps),
            steps_completed=sum(1 for s in result.steps if s.success),
        )
    except Exception:
        pass  # 遥测失败不影响安装流程

    # 7. 安装成功 → 记录到 InstallTracker（供 updates/uninstall 使用）
    if result.success and owner and repo:
        try:
            from auto_update import InstallTracker
            tracker = InstallTracker()
            tracker.record_install(
                owner=owner,
                repo=repo,
                install_dir=result.install_dir or install_dir or "",
            )
            logger.info("📝 已记录安装信息（支持 updates/uninstall）")
        except Exception:
            pass  # 记录失败不影响安装结果

    return {
        "status": "ok" if result.success else "error",
        "project": result.project,
        "success": result.success,
        "plan_strategy": plan.get("strategy", ""),
        "install_dir": result.install_dir,
        "launch_command": result.launch_command,
        "plan": plan,
        "error_summary": result.error_summary,
        "steps_completed": sum(1 for s in result.steps if s.success),
        "steps_total": len(result.steps),
        "audit_warnings": len(audit_warnings),
        "license_risk": license_risk,
        "matched_skills": [s.meta.name for s in matched_skills],
    }


# ─────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────

def _sanitize_for_prompt(text: str) -> str:
    """
    清洗用户可控文本（README、依赖文件等），防止 prompt 注入。
    移除可能影响 LLM 指令理解的标记。
    """
    import re as _re
    # 移除常见 prompt 注入标记
    injection_patterns = [
        r'(?i)ignore\s+(?:all\s+)?previous\s+instructions?',
        r'(?i)forget\s+(?:all\s+)?(?:above|previous)',
        r'(?i)you\s+are\s+now\s+(?:a|an)',
        r'(?i)new\s+instructions?\s*:',
        r'(?i)system\s*(?:prompt|message)\s*:',
        r'(?i)assistant\s*(?:prompt|message)\s*:',
        r'(?i)\[INST\]',
        r'(?i)<\|(?:im_start|im_end|system|user|assistant)\|>',
    ]
    cleaned = text
    for pat in injection_patterns:
        cleaned = _re.sub(pat, '[FILTERED]', cleaned)
    return cleaned


def _build_plan_prompt(env: dict, info) -> str:
    """构建发给 LLM 的安装计划请求 prompt"""
    os_info = env.get("os", {})
    gpu_info = env.get("gpu", {})
    pms = env.get("package_managers", {})
    runtimes = env.get("runtimes", {})

    available_pms = [k for k, v in pms.items() if v.get("available")]
    python_ver = runtimes.get("python", {}).get("version", "unknown")
    has_git = runtimes.get("git", {}).get("available", False)
    has_docker = runtimes.get("docker", {}).get("available", False)

    # 依赖文件摘要（限制长度防止注入）
    dep_summary = ""
    for fname, content in info.dependency_files.items():
        safe_content = _sanitize_for_prompt(content[:500])
        dep_summary += f"\n### {fname}\n```\n{safe_content}\n```\n"

    # 清洗 README 内容，防止 prompt 注入
    safe_readme = _sanitize_for_prompt(info.readme[:8000])

    return f"""
## 目标项目
仓库：{info.full_name}
描述：{info.description}
主要语言：{info.language}
项目类型：{', '.join(info.project_type)}
克隆地址：{info.clone_url}

## 当前用户系统环境
操作系统：{os_info.get('type', 'unknown')} {os_info.get('version', '')} ({os_info.get('arch', '')})
{"芯片：" + os_info.get('chip', '') if os_info.get('chip') else ''}
{"WSL2：是" if os_info.get('is_wsl') else ''}
GPU：{gpu_info.get('name', '无')} - {gpu_info.get('type', 'cpu_only')}
{"CUDA：" + gpu_info.get('cuda_version', '') if gpu_info.get('cuda_version') else ''}
Python：{python_ver}
Git：{'已安装' if has_git else '未安装'}
Docker：{'已安装' if has_docker else '未安装'}
可用包管理器：{', '.join(available_pms)}

## 项目 README（节选）
{safe_readme}

## 依赖文件
{dep_summary if dep_summary else "（无检测到的依赖文件）"}

## 要求
请根据以上环境，生成适配 {os_info.get('type', '')} {os_info.get('arch', '')} 的完整安装步骤。
安装目标目录：~/（用户主目录）
"""


def _parse_plan_response(response: str) -> dict:
    """解析 LLM 返回的安装计划 JSON"""
    # 尝试直接解析
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # 提取代码块中的 JSON
    import re
    match = re.search(r'```(?:json)?\n(.*?)```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找到第一个 { 开始的 JSON
    start = response.find("{")
    end = response.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

    # 无法解析，返回空计划
    return {"project_name": "", "steps": [], "launch_command": ""}


def _validate_plan_schema(plan: dict) -> dict:
    """
    验证 LLM 返回的 plan 结构，防止 prompt 注入攻击。
    仅保留合法字段，过滤异常数据。
    """
    safe = {
        "project_name": str(plan.get("project_name", ""))[:200],
        "steps": [],
        "launch_command": str(plan.get("launch_command", ""))[:500],
    }
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        s = {
            "description": str(step.get("description", ""))[:500],
            "command": str(step.get("command", ""))[:2000],
        }
        # 只保留非空命令
        if s["command"].strip():
            safe["steps"].append(s)
    return safe


# ─────────────────────────────────────────────
#  子命令：doctor（系统诊断）
# ─────────────────────────────────────────────

def cmd_doctor(json_output: bool = False) -> dict:
    """运行系统诊断"""
    from doctor import run_doctor, format_doctor_report, doctor_to_dict

    logger.info("🩺 正在运行系统诊断...")
    report = run_doctor()

    if json_output:
        return doctor_to_dict(report)

    logger.info(format_doctor_report(report))
    return doctor_to_dict(report)


# ─────────────────────────────────────────────
#  子命令：skills（插件管理）
# ─────────────────────────────────────────────

def cmd_skills(args) -> dict:
    """Skills 插件管理"""
    from skills import SkillManager, ensure_builtin_skills, format_skills_list

    mgr = SkillManager()

    if args.skills_action == "list":
        skills = mgr.list_skills()
        logger.info(f"\n🔧 已安装 Skills ({len(skills)} 个):")
        logger.info(format_skills_list(skills))
        return {
            "status": "ok",
            "count": len(skills),
            "skills": [{"name": s.meta.name, "version": s.meta.version,
                        "description": s.meta.description, "enabled": s.enabled}
                       for s in skills],
        }

    elif args.skills_action == "init":
        ensure_builtin_skills()
        skills = mgr.list_skills()
        logger.info(f"✅ 已初始化 {len(skills)} 个内建 Skills")
        return {"status": "ok", "initialized": len(skills)}

    elif args.skills_action == "create":
        try:
            path = mgr.create_skill(
                name=args.name,
                description=args.desc,
                steps=[],
            )
            logger.info(f"✅ Skill '{args.name}' 已创建: {path}")
            return {"status": "ok", "name": args.name, "path": str(path)}
        except (ValueError, FileExistsError) as e:
            return {"status": "error", "message": str(e)}

    elif args.skills_action == "remove":
        if mgr.remove_skill(args.name):
            logger.info(f"✅ Skill '{args.name}' 已删除")
            return {"status": "ok", "removed": args.name}
        return {"status": "error", "message": f"Skill '{args.name}' 不存在"}

    elif args.skills_action == "export":
        data = mgr.export_skill(args.name)
        if data:
            return {"status": "ok", "skill_data": data}
        return {"status": "error", "message": f"Skill '{args.name}' 不存在"}

    return {"status": "error", "message": "未知 skills 子命令"}


# ─────────────────────────────────────────────
#  子命令：config（配置管理）
# ─────────────────────────────────────────────

def cmd_config(args) -> dict:
    """配置文件管理"""
    from config_schema import load_and_validate, format_validation_result
    from onboard import CONFIG_FILE

    if args.config_action == "show":
        result = load_and_validate()
        # 隐藏敏感信息
        display_config = dict(result.config)
        for key in ["github_token"]:
            if display_config.get(key):
                display_config[key] = display_config[key][:8] + "..."
        if display_config.get("llm_key"):
            display_config["llm_key"] = {k: v[:8] + "..." for k, v in display_config["llm_key"].items()}
        logger.info(json.dumps(display_config, indent=2, ensure_ascii=False))
        return {"status": "ok", "config": display_config}

    elif args.config_action == "validate":
        result = load_and_validate()
        logger.info(format_validation_result(result))
        return {
            "status": "ok" if result.valid else "error",
            "valid": result.valid,
            "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            "warnings": result.warnings,
        }

    elif args.config_action == "path":
        logger.info(str(CONFIG_FILE))
        return {"status": "ok", "path": str(CONFIG_FILE)}

    return {"status": "error", "message": "未知 config 子命令"}


# ─────────────────────────────────────────────
#  子命令：platforms（平台列表）
# ─────────────────────────────────────────────

def cmd_platforms() -> dict:
    """列出支持的代码托管平台"""
    from multi_source import get_supported_platforms

    platforms = get_supported_platforms()
    logger.info("\n📦 支持的代码托管平台:")
    for p in platforms:
        token_status = ""
        if p["env_token"]:
            import os
            has_token = bool(os.getenv(p["env_token"], "").strip())
            token_status = " ✅" if has_token else f" (设置 {p['env_token']} 提升配额)"
        logger.info(f"  • {p['name']:12s} {p['domain']}{token_status}")
    logger.info("  用法: gitinstall install <platform-url>/owner/repo")
    logger.info("  示例: gitinstall install gitlab.com/inkscape/inkscape")
    return {"status": "ok", "platforms": platforms}


# ─────────────────────────────────────────────
#  子命令：audit（依赖安全审计）
# ─────────────────────────────────────────────

def cmd_audit(identifier: str, online: bool = False) -> dict:
    """审计项目依赖的安全风险"""
    from dependency_audit import audit_project, format_audit_results, audit_to_dict

    logger.info(f"🔍 正在审计 {identifier} 的依赖安全...")
    try:
        info = fetch_project(identifier)
        if not info.dependency_files:
            logger.warning("  ⚠️  未找到依赖文件")
            return {"status": "ok", "message": "未找到依赖文件", "results": []}

        results = audit_project(info.dependency_files, online=online)
        logger.info(format_audit_results(results))
        return {"status": "ok", **audit_to_dict(results)}
    except Exception as e:
        return {"status": "error", "message": f"审计失败：{e}"}


# ─────────────────────────────────────────────
#  子命令：license（许可证检查）
# ─────────────────────────────────────────────

def cmd_license(identifier: str) -> dict:
    """检查项目许可证兼容性"""
    from license_check import (
        analyze_license, format_license_result, license_to_dict,
        fetch_license_from_github,
    )
    from fetcher import parse_repo_identifier

    logger.info(f"📜 正在检查 {identifier} 的许可证...")
    try:
        owner, repo = parse_repo_identifier(identifier)
        spdx_id, license_text = fetch_license_from_github(owner, repo)

        if not spdx_id and not license_text:
            logger.warning("  ⚠️  未找到许可证信息")
            return {"status": "ok", "message": "项目未声明许可证", "risk": "warning"}

        result = analyze_license(spdx_id, license_text)
        logger.info(format_license_result(result))
        return {"status": "ok", **license_to_dict(result)}
    except Exception as e:
        return {"status": "error", "message": f"许可证检查失败：{e}"}


# ─────────────────────────────────────────────
#  子命令：updates（更新检查）
# ─────────────────────────────────────────────

def cmd_updates(args) -> dict:
    """管理已安装项目和更新检查"""
    from auto_update import (
        InstallTracker, check_all_updates,
        format_installed_list, format_update_results, updates_to_dict,
    )

    tracker = InstallTracker()

    if args.updates_action == "list":
        projects = tracker.list_installed()
        logger.info(format_installed_list(projects))
        return {
            "status": "ok",
            "installed": [p.to_dict() for p in projects],
            "total": len(projects),
        }

    elif args.updates_action == "check":
        logger.info("🔄 正在检查所有项目的更新...")
        results = check_all_updates(tracker)
        logger.info(format_update_results(results))
        return {"status": "ok", **updates_to_dict(results)}

    elif args.updates_action == "remove":
        name = args.name
        parts = name.split("/")
        if len(parts) != 2:
            return {"status": "error", "message": f"格式错误：应为 owner/repo，收到 '{name}'"}
        ok = tracker.remove_project(parts[0], parts[1])
        if ok:
            logger.info(f"  ✅ 已移除 {name} 的记录")
        else:
            logger.warning(f"  ❌ 未找到 {name}")
        return {"status": "ok" if ok else "error", "removed": ok}

    return {"status": "error", "message": "未知子命令"}


# ─────────────────────────────────────────────
#  子命令：resume（断点恢复）
# ─────────────────────────────────────────────

def cmd_resume(identifier: str = None, llm_force: str = None,
               install_dir: str = None) -> dict:
    """恢复中断的安装"""
    from checkpoint import CheckpointManager, format_checkpoint_list, format_resume_plan

    mgr = CheckpointManager()

    if not identifier:
        # 列出可恢复的安装
        resumable = mgr.get_resumable()
        if not resumable:
            logger.info("  ✅ 没有中断的安装任务")
            return {"status": "ok", "resumable": []}
        logger.info(format_checkpoint_list(resumable))
        return {
            "status": "ok",
            "resumable": [cp.project for cp in resumable],
        }

    # 找到指定项目的断点
    from fetcher import parse_repo_identifier
    try:
        owner, repo = parse_repo_identifier(identifier)
    except Exception as e:
        return {"status": "error", "message": f"无法解析项目: {e}"}

    cp = mgr.get_checkpoint(owner, repo)
    if not cp:
        return {"status": "error", "message": f"未找到 {owner}/{repo} 的断点记录"}

    resume_idx = mgr.get_resume_step(owner, repo)
    if resume_idx is None:
        return {"status": "ok", "message": "该安装已完成，无需恢复"}

    logger.info(format_resume_plan(cp, resume_idx))

    # 从断点恢复执行
    remaining_steps = [s for s in cp.steps[resume_idx:]
                      if s.status in ("pending", "failed")]
    if not remaining_steps:
        return {"status": "ok", "message": "所有步骤已完成"}

    plan = {
        "steps": [{"command": s.command, "description": s.description}
                  for s in remaining_steps],
        "launch_command": cp.plan.get("launch_command", ""),
    }

    result = cmd_install(identifier, install_dir=install_dir,
                        llm_force=llm_force)
    return result


# ─────────────────────────────────────────────
#  子命令：flags（功能开关）
# ─────────────────────────────────────────────

def cmd_flags(args) -> dict:
    """查看/管理功能开关"""
    from feature_flags import get_all_status, format_flags_table, list_flags, is_enabled

    if args.flags_action == "list":
        group = getattr(args, "group", None)
        if group:
            flags = list_flags(group)
            logger.info(f"\n🚩 功能开关（{group} 组）：")
            for f in flags:
                icon = "✅" if is_enabled(f.name) else "❌"
                logger.info(f"  {icon} {f.name}: {f.description}")
            return {"status": "ok", "flags": [{"name": f.name, "enabled": is_enabled(f.name), "description": f.description} for f in flags]}
        status = get_all_status()
        logger.info(format_flags_table())
        return {"status": "ok", "flags": status}

    elif args.flags_action == "show":
        status = get_all_status()
        logger.info(format_flags_table())
        return {"status": "ok", "flags": status}

    return {"status": "error", "message": "未知 flags 子命令"}


# ─────────────────────────────────────────────
#  子命令：registry（安装器注册表）
# ─────────────────────────────────────────────

def cmd_registry(args) -> dict:
    """查看安装器注册表"""
    from installer_registry import InstallerRegistry

    registry = InstallerRegistry()

    if args.registry_action == "list":
        all_installers = registry.list_all()
        available = registry.list_available()
        logger.info(registry.format_registry())
        return {
            "status": "ok",
            "total": len(all_installers),
            "available": [i.info.name for i in available],
            "installers": registry.to_dict(),
        }

    elif args.registry_action == "detect":
        available = registry.list_available()
        logger.info(f"\n🔧 检测到 {len(available)} 个可用安装器：")
        for inst in available:
            logger.info(f"  ✅ {inst.info.name} v{inst.info.version or '?'}")
        return {
            "status": "ok",
            "available": [{
                "name": inst.info.name,
                "version": inst.info.version or "",
                "ecosystems": inst.info.ecosystems,
            } for inst in available],
        }

    return {"status": "error", "message": "未知 registry 子命令"}


# ─────────────────────────────────────────────
#  子命令：events（事件历史）
# ─────────────────────────────────────────────

def cmd_events(args) -> dict:
    """查看事件历史"""
    from event_bus import get_event_bus

    bus = get_event_bus()
    event_type = getattr(args, "type", None)
    limit = getattr(args, "limit", 50)
    history = bus.get_history(event_type=event_type, limit=limit)

    if not history:
        logger.info("  📭 暂无事件记录")
        return {"status": "ok", "events": [], "total": 0}

    logger.info(f"\n📡 事件历史（最近 {len(history)} 条）：")
    for evt in history:
        logger.info(f"  [{evt.timestamp}] {evt.event_type} - {evt.project}")
        if evt.data:
            for k, v in list(evt.data.items())[:3]:
                logger.info(f"    {k}: {v}")

    return {
        "status": "ok",
        "events": [e.to_dict() for e in history],
        "total": len(history),
    }


# ─────────────────────────────────────────────
#  子命令：chain（依赖链）
# ─────────────────────────────────────────────

def cmd_chain(identifier: str, llm_force: str = None,
              use_local: bool = False) -> dict:
    """可视化项目安装依赖链"""
    from dep_chain import build_chain_from_plan, format_dep_chain

    plan_result = cmd_plan(identifier, llm_force=llm_force, use_local=use_local)
    if plan_result["status"] != "ok":
        return plan_result

    plan = plan_result["plan"]
    chain = build_chain_from_plan(plan)
    logger.info(format_dep_chain(chain))

    return {
        "status": "ok",
        "project": plan_result.get("project", identifier),
        "chain": chain.to_dict(),
        "has_cycle": chain.has_cycle(),
        "node_count": len(chain.nodes),
    }


# ─────────────────────────────────────────────
#  子命令：kb（安装知识库）
# ─────────────────────────────────────────────

def cmd_kb(args) -> dict:
    """管理安装知识库"""
    from knowledge_base import KnowledgeBase, format_kb_stats, format_search_results

    kb = KnowledgeBase()

    if args.kb_action == "stats":
        stats = kb.get_stats()
        logger.info(format_kb_stats(stats))
        return {"status": "ok", **stats}

    elif args.kb_action == "search":
        query = getattr(args, "query", "")
        if not query:
            return {"status": "error", "message": "请指定搜索关键词"}
        results = kb.search(project=query)
        logger.info(format_search_results(results))
        return {
            "status": "ok",
            "results": [{
                "project": r.entry.project,
                "score": r.score,
                "success": r.entry.success,
                "strategy": r.entry.strategy,
                "reasons": r.match_reasons,
            } for r in results],
        }

    elif args.kb_action == "rate":
        project = getattr(args, "project", "")
        rate_info = kb.get_success_rate(project)
        rate_pct = f"{rate_info['rate']:.1%}"
        logger.info(f"  📊 成功率：{rate_pct}（{rate_info['success']}/{rate_info['total']}）")
        return {"status": "ok", **rate_info}

    return {"status": "error", "message": "未知 kb 子命令"}


# ─────────────────────────────────────────────
#  子命令：autopilot（批量安装）
# ─────────────────────────────────────────────

def cmd_autopilot(args) -> dict:
    """批量自动安装"""
    from autopilot import (
        parse_project_list, run_autopilot, resume_autopilot,
        format_batch_result,
    )

    if getattr(args, "autopilot_action", "") == "resume":
        logger.info("🚗 恢复上次自动驾驶...")
        result = resume_autopilot(
            llm_force=getattr(args, "llm", None),
            install_dir=getattr(args, "dir", None),
        )
        if not result:
            return {"status": "error", "message": "没有可恢复的自动驾驶任务"}
        logger.info(format_batch_result(result))
        return {"status": "ok", **result.to_dict()}

    # run
    source = getattr(args, "projects", "")
    if not source:
        return {"status": "error", "message": "请指定项目列表或文件"}

    projects = parse_project_list(source)
    if not projects:
        return {"status": "error", "message": f"未能解析出有效项目：{source}"}

    logger.info(f"🚗 自动驾驶模式：{len(projects)} 个项目")
    for i, p in enumerate(projects, 1):
        logger.info(f"  {i}. {p}")

    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        return {
            "status": "ok",
            "dry_run": True,
            "projects": projects,
            "total": len(projects),
        }

    result = run_autopilot(
        projects,
        install_dir=getattr(args, "dir", None),
        llm_force=getattr(args, "llm", None),
    )
    logger.info(format_batch_result(result))
    return {"status": "ok", **result.to_dict()}


# ─────────────────────────────────────────────
#  子命令：uninstall（安全卸载）
# ─────────────────────────────────────────────

def cmd_uninstall(identifier: str, keep_config: bool = False,
                  clean_only: bool = False, confirm: bool = False) -> dict:
    """安全卸载已安装的项目"""
    from auto_update import InstallTracker
    from uninstaller import (
        plan_uninstall, execute_uninstall,
        format_uninstall_plan, uninstall_to_dict,
    )
    from fetcher import parse_repo_identifier

    try:
        owner, repo = parse_repo_identifier(identifier)
    except Exception as e:
        return {"status": "error", "message": f"无法解析项目: {e}"}

    tracker = InstallTracker()
    project = tracker.get_project(owner, repo)

    if not project:
        return {"status": "error", "message": f"未找到 {owner}/{repo} 的安装记录。使用 'gitinstall updates list' 查看已安装项目。"}

    plan = plan_uninstall(
        owner, repo, project.install_dir,
        keep_config=keep_config, clean_only=clean_only,
    )

    logger.info(format_uninstall_plan(plan))

    if plan.error:
        return {"status": "error", **uninstall_to_dict(plan)}

    if not confirm:
        logger.warning("\n  ⚠️  添加 --confirm 确认执行卸载")
        return {"status": "ok", "action": "dry_run", **uninstall_to_dict(plan)}

    # 执行卸载
    result = execute_uninstall(plan, keep_config=keep_config)
    if result["success"]:
        tracker.remove_project(owner, repo)
        logger.info(f"\n  ✅ 已卸载 {owner}/{repo}，释放 {result['freed_mb']} MB")
    else:
        logger.warning(f"\n  ⚠️  部分清理失败: {result['errors']}")

    return {"status": "ok" if result["success"] else "partial", **result}


# ─────────────────────────────────────────────
#  CLI 入口
# ─────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="gitinstall",
        description="让你轻松安装开源项目（支持 GitHub/GitLab/Bitbucket/Gitee/Codeberg/本地路径）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python main.py web                              # 启动 Web 界面（推荐）
  python main.py detect
  python main.py plan  comfyanonymous/ComfyUI
  python main.py install comfyanonymous/ComfyUI
  python main.py install gitlab.com/user/project  # GitLab 项目
  python main.py install gitee.com/user/project   # Gitee 国内项目
  python main.py install ./my-local-project        # 本地目录（无需网络）
  python main.py install ~/projects/my-app         # 本地绝对路径
  python main.py install comfyanonymous/ComfyUI --dir ~/AI --llm groq
  python main.py install comfyanonymous/ComfyUI --dry-run
  python main.py doctor                           # 系统诊断
  python main.py onboard                          # 交互式引导
  python main.py skills list                      # 查看已安装 Skills
  python main.py audit pytorch/pytorch            # 依赖安全审计
  python main.py license torvalds/linux           # 许可证检查
  python main.py updates check                    # 检查已安装项目更新
  python main.py uninstall owner/repo --confirm   # 安全卸载项目
  python main.py validate                         # 验证 Top 100 兼容性
  python main.py validate --quick                 # 仅验证新入榜项目
  python main.py resume                           # 列出可恢复的安装
  python main.py flags list                       # 查看功能开关
  python main.py registry list                    # 查看安装器注册表
  python main.py events                           # 查看事件历史
  python main.py chain owner/repo                 # 可视化依赖链
  python main.py kb stats                         # 知识库统计
  python main.py autopilot run projects.txt       # 批量自动安装
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # detect
    subparsers.add_parser("detect", help="检测当前系统环境")

    # fetch
    fetch_p = subparsers.add_parser("fetch", help="获取项目信息（支持多平台）")
    fetch_p.add_argument("project", help="URL / owner/repo（支持 GitHub/GitLab/Bitbucket/Gitee/Codeberg）")

    # plan
    plan_p = subparsers.add_parser("plan", help="生成安装计划（不执行）")
    plan_p.add_argument("project", help="URL / owner/repo / 本地路径（支持多平台）")
    plan_p.add_argument("--llm", default=None, help="指定 LLM: anthropic/openai/groq/ollama/lmstudio/none")
    plan_p.add_argument("--local", action="store_true", help="本地模式：git clone 后本地分析（不消耗 API 配额）")

    # install
    install_p = subparsers.add_parser("install", help="安装开源项目（支持多平台）")
    install_p.add_argument("project", help="URL / owner/repo / 本地路径（支持 GitHub/GitLab/Bitbucket/Gitee/Codeberg/本地目录）")
    install_p.add_argument("--dir", default=None, help="指定安装目录（默认 ~/项目名）")
    install_p.add_argument("--llm", default=None, help="指定 LLM: anthropic/openai/groq/ollama/lmstudio/none")
    install_p.add_argument("--local", action="store_true", help="本地模式：git clone 后本地分析（不消耗 API 配额）")
    install_p.add_argument("--dry-run", action="store_true", help="只生成计划，不执行")

    # doctor
    doctor_p = subparsers.add_parser("doctor", help="🩺 系统诊断（检查环境、API、缓存、GPU）")
    doctor_p.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON 格式")

    # onboard
    subparsers.add_parser("onboard", help="🦀 交互式引导向导（首次使用推荐）")

    # skills
    skills_p = subparsers.add_parser("skills", help="🔧 Skills 插件管理")
    skills_sub = skills_p.add_subparsers(dest="skills_action", required=True)
    skills_sub.add_parser("list", help="列出已安装的 Skills")
    skills_sub.add_parser("init", help="初始化内建 Skills")
    skills_create = skills_sub.add_parser("create", help="创建新 Skill")
    skills_create.add_argument("name", help="Skill 名称（小写字母+连字符）")
    skills_create.add_argument("--desc", required=True, help="Skill 描述")
    skills_remove = skills_sub.add_parser("remove", help="删除 Skill")
    skills_remove.add_argument("name", help="要删除的 Skill 名称")
    skills_export = skills_sub.add_parser("export", help="导出 Skill 为 JSON")
    skills_export.add_argument("name", help="要导出的 Skill 名称")

    # config
    config_p = subparsers.add_parser("config", help="⚙️  配置管理")
    config_sub = config_p.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("show", help="显示当前配置")
    config_sub.add_parser("validate", help="验证配置文件")
    config_sub.add_parser("path", help="显示配置文件路径")

    # platforms
    subparsers.add_parser("platforms", help="📦 列出支持的代码托管平台")

    # audit
    audit_p = subparsers.add_parser("audit", help="🔒 依赖安全审计（CVE/误植/废弃包）")
    audit_p.add_argument("project", help="URL / owner/repo")
    audit_p.add_argument("--online", action="store_true", help="查询在线漏洞数据库（更全面但较慢）")

    # license
    license_p = subparsers.add_parser("license", help="📜 许可证兼容性检查")
    license_p.add_argument("project", help="URL / owner/repo")

    # updates
    updates_p = subparsers.add_parser("updates", help="🔄 已安装项目更新管理")
    updates_sub = updates_p.add_subparsers(dest="updates_action", required=True)
    updates_sub.add_parser("list", help="列出已安装项目")
    updates_sub.add_parser("check", help="检查所有项目更新")
    updates_remove = updates_sub.add_parser("remove", help="移除安装记录")
    updates_remove.add_argument("name", help="owner/repo")

    # uninstall
    uninstall_p = subparsers.add_parser("uninstall", help="🗑️  安全卸载已安装项目")
    uninstall_p.add_argument("project", help="owner/repo")
    uninstall_p.add_argument("--keep-config", action="store_true", help="保留配置文件")
    uninstall_p.add_argument("--clean-only", action="store_true", help="仅清理缓存和编译产物")
    uninstall_p.add_argument("--confirm", action="store_true", help="确认执行卸载（否则仅预览）")

    # mcp
    subparsers.add_parser("mcp", help="🤖 启动 MCP 服务器（供 Claude Desktop / Cursor 等 AI 工具调用）")

    # schema
    schema_p = subparsers.add_parser("schema", help="📋 输出 AI 工具调用 Schema（OpenAI/Anthropic/Gemini/JSON）")
    schema_p.add_argument("--format", default="openai",
                          choices=["openai", "anthropic", "gemini", "json_schema"],
                          help="Schema 格式（默认: openai，兼容 Ollama/vLLM/LM Studio/任意 OpenAI 兼容 API）")

    # web
    web_p = subparsers.add_parser("web", help="启动 Web 图形界面（推荐）")
    web_p.add_argument("--port", type=int, default=8080, help="端口号 (默认: 8080)")
    web_p.add_argument("--host", default="", help="绑定地址 (默认: 127.0.0.1，生产环境用 0.0.0.0)")
    web_p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    # validate
    val_p = subparsers.add_parser("validate", help="验证 Top 100 热门项目兼容性（内部 CI）")
    val_p.add_argument("--quick", action="store_true", help="仅验证新入榜项目")
    val_p.add_argument("--report", action="store_true", help="仅显示上次验证报告")
    val_p.add_argument("--category", default=None, help="按分类过滤: AI/Web/工具/IoT")

    # resume
    resume_p = subparsers.add_parser("resume", help="🔄 恢复中断的安装（断点续装）")
    resume_p.add_argument("project", nargs="?", default=None, help="owner/repo（不指定则列出可恢复的）")
    resume_p.add_argument("--dir", default=None, help="安装目录")
    resume_p.add_argument("--llm", default=None, help="指定 LLM")

    # flags
    flags_p = subparsers.add_parser("flags", help="🚩 功能开关管理")
    flags_sub = flags_p.add_subparsers(dest="flags_action", required=True)
    flags_list = flags_sub.add_parser("list", help="列出所有功能开关")
    flags_list.add_argument("--group", default=None, help="按组过滤: security/experimental/performance/general")
    flags_sub.add_parser("show", help="显示功能开关状态")

    # registry
    registry_p = subparsers.add_parser("registry", help="🔧 安装器注册表")
    registry_sub = registry_p.add_subparsers(dest="registry_action", required=True)
    registry_sub.add_parser("list", help="列出所有安装器")
    registry_sub.add_parser("detect", help="检测可用安装器")

    # events
    events_p = subparsers.add_parser("events", help="📡 安装事件历史")
    events_p.add_argument("--type", default=None, help="筛选事件类型")
    events_p.add_argument("--limit", type=int, default=50, help="最大条数")

    # chain
    chain_p = subparsers.add_parser("chain", help="🔗 可视化安装依赖链")
    chain_p.add_argument("project", help="owner/repo")
    chain_p.add_argument("--llm", default=None, help="指定 LLM")
    chain_p.add_argument("--local", action="store_true", help="本地模式")

    # kb
    kb_p = subparsers.add_parser("kb", help="📚 安装知识库")
    kb_sub = kb_p.add_subparsers(dest="kb_action", required=True)
    kb_sub.add_parser("stats", help="知识库统计")
    kb_search = kb_sub.add_parser("search", help="搜索相似安装案例")
    kb_search.add_argument("query", help="搜索关键词（project/type/language）")
    kb_rate = kb_sub.add_parser("rate", help="查看项目安装成功率")
    kb_rate.add_argument("project", nargs="?", default="", help="owner/repo（不指定则全局）")

    # autopilot
    autopilot_p = subparsers.add_parser("autopilot", help="🚗 批量自动安装")
    autopilot_sub = autopilot_p.add_subparsers(dest="autopilot_action", required=True)
    ap_run = autopilot_sub.add_parser("run", help="执行批量安装")
    ap_run.add_argument("projects", help="项目列表（文件路径 / 空格分隔 owner/repo）")
    ap_run.add_argument("--dir", default=None, help="安装目录")
    ap_run.add_argument("--llm", default=None, help="指定 LLM")
    ap_run.add_argument("--dry-run", action="store_true", help="仅预览")
    autopilot_sub.add_parser("resume", help="恢复上次自动驾驶")

    args = parser.parse_args()

    # 路由到对应命令
    if args.command == "detect":
        result = cmd_detect()
    elif args.command == "fetch":
        result = cmd_fetch(args.project)
    elif args.command == "plan":
        result = cmd_plan(args.project, llm_force=args.llm, use_local=args.local)
    elif args.command == "install":
        result = cmd_install(
            args.project,
            install_dir=args.dir,
            llm_force=args.llm,
            dry_run=args.dry_run,
            use_local=args.local,
        )
    elif args.command == "doctor":
        result = cmd_doctor(json_output=args.json_output)
    elif args.command == "onboard":
        from onboard import run_onboard
        run_onboard()
        return
    elif args.command == "skills":
        result = cmd_skills(args)
    elif args.command == "config":
        result = cmd_config(args)
    elif args.command == "platforms":
        result = cmd_platforms()
    elif args.command == "audit":
        result = cmd_audit(args.project, online=args.online)
    elif args.command == "license":
        result = cmd_license(args.project)
    elif args.command == "updates":
        result = cmd_updates(args)
    elif args.command == "uninstall":
        result = cmd_uninstall(
            args.project,
            keep_config=args.keep_config,
            clean_only=args.clean_only,
            confirm=args.confirm,
        )
    elif args.command == "mcp":
        from mcp_server import serve
        serve()
        return
    elif args.command == "schema":
        from tool_schemas import to_json
        print(to_json(args.format))
        return
    elif args.command == "web":
        from web import start_server
        start_server(port=args.port, host=args.host, open_browser=not args.no_open)
        return
    elif args.command == "validate":
        from validate_top100 import cmd_validate
        result = cmd_validate(
            quick=args.quick,
            report_only=args.report,
            category=args.category,
        )
    elif args.command == "resume":
        result = cmd_resume(
            identifier=args.project,
            llm_force=args.llm,
            install_dir=args.dir,
        )
    elif args.command == "flags":
        result = cmd_flags(args)
    elif args.command == "registry":
        result = cmd_registry(args)
    elif args.command == "events":
        result = cmd_events(args)
    elif args.command == "chain":
        result = cmd_chain(args.project, llm_force=args.llm, use_local=args.local)
    elif args.command == "kb":
        result = cmd_kb(args)
    elif args.command == "autopilot":
        result = cmd_autopilot(args)
    else:
        parser.print_help()
        sys.exit(1)

    # 输出 JSON（OpenClaw 读取 stdout）
    _output_and_exit(result)


def _output_and_exit(result: dict):
    """CLI 专用：输出 JSON 并根据状态设置退出码。SDK 不调用此函数。"""
    import json as _json
    print(_json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "error":
        sys.exit(1)


# pyproject.toml 入口别名
cli_main = main


if __name__ == "__main__":
    main()
