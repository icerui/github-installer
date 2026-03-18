"""
executor.py - 安全的跨平台命令执行器
=====================================

设计原则：
  1. 安全第一：执行前过滤危险命令，有白名单机制
  2. 实时输出：边执行边打印，用户知道发生了什么
  3. 错误恢复：捕获报错，调用 LLM 分析修复方案
  4. 跨平台：macOS / Linux / Windows 三端适配
  5. 可回滚：记录每一步，出错可提示用户撤销
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from log import get_logger
from i18n import t

logger = get_logger(__name__)


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class StepResult:
    step_id: int
    command: str
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_sec: float
    error_message: str = ""
    fix_applied: bool = False
    fix_command: str = ""


@dataclass
class InstallResult:
    project: str
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    launch_command: str = ""
    install_dir: str = ""
    error_summary: str = ""


# ─────────────────────────────────────────────
#  危险命令检测
# ─────────────────────────────────────────────

# 绝对禁止执行的命令模式（正则）
BLOCKED_PATTERNS = [
    r'(?:^|/)rm\s+-[^\s]*r[^\s]*\s+/',          # rm -rf /
    r'(?:^|/)rm\s+-[^\s]*r[^\s]*\s+~\b',        # rm -rf ~
    r':\(\)\s*\{',                                # fork bomb
    r'\bformat\s+[cCdDeE]:',                      # Windows format
    r'\bmkfs\.\w',                                 # mkfs.*
    r'\bdd\s+if=/',                                # dd if=/dev/...
    r'(?:^|/)chmod\s+777\s+/',                     # chmod 777 /
    r'(?:^|/)chown\s+.*\s+/',                      # chown ... /
    r'>\s*/dev/sda',                               # overwrite disk
    r'\biptables\s+-F',                            # flush firewall
    r'\buserdel\s+-r\b',                           # delete user
    r'shutdown\s+-[hrPf]',                         # system shutdown
    r'\breboot\b',                                 # reboot
    r'curl[^\n]+\|\s*sudo\s+(?:bash|sh)\b',       # curl | sudo bash
    r'wget[^\n]+\|\s*sudo\s+(?:bash|sh)\b',       # wget | sudo sh
    # ── G1: 编码绕过防护 ──
    r'base64\s+(?:-d|--decode)\s*\|',             # base64 -d | ...
    r'xxd\s+(?:-r|--revert)\s*\|',               # xxd -r | ...
    r"""python3?\s+-c\s+.*(?:base64|codecs|decode|exec|eval|import\s+os|import\s+subprocess|import\s+shutil)""",  # python -c payload
    r'printf\s+[\'"]\\x[0-9a-f].*\|\s*(?:bash|sh)',  # printf hex | bash
    r'echo\s+-e\s+.*\\x[0-9a-f].*\|\s*(?:bash|sh)', # echo -e hex | bash
    # ── G2: heredoc 绕过防护 ──
    r'(?:bash|sh|zsh)\s*<<',                       # bash << heredoc
    r'\beval\s+',                                  # eval "..."
    # ── G4: 额外磁盘/设备防护 ──
    r'\bdd\s+.*\bof=\s*/dev/',                     # dd of=/dev/xxx
    r'>\s*/dev/(?:sd|nvme|vd|hd)',                 # redirect to disk
    # ── 补充：rm -rf 不带空格变体 ──
    r'rm\s+-rf\s+/(?:\s|$)',                       # rm -rf / (strict)
]

# 需要用户额外确认的高风险命令
WARN_PATTERNS = [
    (r'\bsudo\b', "⚠️ 该命令需要管理员权限"),
    (r'\bchmod\b', "⚠️ 该命令修改文件权限"),
    (r'\bcurl[^\n]+\|', "⚠️ 该命令从网络下载并执行脚本"),
    (r'\bwget[^\n]+\|', "⚠️ 该命令从网络下载并执行脚本"),
    (r'\bdocker run[^\n]+--privileged', "⚠️ 该 Docker 容器以特权模式运行"),
    (r'\$\{?\w+\}?\s*/', "⚠️ 命令使用变量引用路径，可能含风险"),
]


def _strip_comments(cmd: str) -> str:
    """移除 shell 行尾注释（简单场景，不处理引号内的 #）。"""
    return re.sub(r'(?<!\S)#.*$', '', cmd, flags=re.MULTILINE)


def check_command_safety(command: str) -> tuple[bool, str]:
    """
    检查命令安全性。
    Returns:
        (is_safe, warning_message)
        is_safe=False 表示绝对禁止，warning_message 非空表示需要确认
    """
    # G6: 先移除注释，防止注释混淆
    command_clean = _strip_comments(command)

    # 拆分 &&、||、; 分隔的子命令段，每段都要检查
    segments = re.split(r'\s*(?:&&|\|\||;)\s*', command_clean)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, segment, re.IGNORECASE):
                return False, f"🚫 危险命令，已拒绝执行：{command}"

    # 也对完整命令做一次检查（跨段的管道模式）
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command_clean, re.IGNORECASE):
            return False, f"🚫 危险命令，已拒绝执行：{command}"

    warnings = []
    for pattern, msg in WARN_PATTERNS:
        if re.search(pattern, command_clean, re.IGNORECASE):
            warnings.append(msg)

    return True, "\n".join(warnings)


# ─────────────────────────────────────────────
#  路径适配
# ─────────────────────────────────────────────

def adapt_path_for_os(path: str) -> str:
    """将路径中的 ~ 展开，并处理 Windows 路径分隔符"""
    path = path.replace("~", str(Path.home()))
    if platform.system() == "Windows":
        # 只转换本地路径中的分隔符，不影响 URL
        if not re.match(r'https?://', path):
            path = path.replace("/", "\\")
    return path


def get_shell() -> list[str]:
    """根据平台返回合适的 shell 前缀"""
    if platform.system() == "Windows":
        # 优先 PowerShell，其次 cmd
        if os.path.exists(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"):
            return ["powershell", "-NonInteractive", "-Command"]
        return ["cmd", "/c"]
    else:
        shell = os.environ.get("SHELL", "/bin/bash")
        return [shell, "-c"]


# ─────────────────────────────────────────────
#  命令执行器
# ─────────────────────────────────────────────

class CommandExecutor:
    """
    安全的命令执行器，支持实时输出、超时控制、错误捕获。
    """

    def __init__(
        self,
        work_dir: Optional[str] = None,
        timeout_sec: int = 600,    # 默认 10 分钟超时
        verbose: bool = True,
    ):
        self.work_dir = work_dir or str(Path.home())
        self.timeout_sec = timeout_sec
        self.verbose = verbose
        self._current_dir = self.work_dir
        self._base_env = os.environ.copy()
        self._env = self._base_env.copy()

    def reset(self, work_dir: Optional[str] = None):
        """重置工作目录（用于回退策略切换）"""
        if work_dir:
            self.work_dir = work_dir
        self._current_dir = self.work_dir
        self._env = self._base_env.copy()

    def _activate_virtualenv(self, command: str, cwd: str) -> StepResult | None:
        """处理虚拟环境激活命令，并将环境持久化到后续步骤。"""
        activate_cmd = command.strip()
        activate_path = ""

        if activate_cmd.startswith("source "):
            activate_path = activate_cmd[len("source "):].strip().strip('"').strip("'")
        elif activate_cmd.endswith("activate") or activate_cmd.endswith("Activate.ps1"):
            activate_path = activate_cmd.strip().strip('"').strip("'")
        else:
            return None

        activate_file = Path(adapt_path_for_os(activate_path))
        if not activate_file.is_absolute():
            activate_file = Path(cwd) / activate_file
        activate_file = activate_file.resolve()

        if not activate_file.is_file():
            # Windows: 尝试 Scripts\activate 替代 bin/activate
            if platform.system() == "Windows":
                alt = activate_file.parent.parent / "Scripts" / "activate"
                if alt.is_file():
                    activate_file = alt
                else:
                    return None
            else:
                return None

        bin_dir = activate_file.parent
        venv_dir = bin_dir.parent
        old_path = self._env.get("PATH", "")
        path_parts = [part for part in old_path.split(os.pathsep) if part]
        bin_dir_str = str(bin_dir)
        path_parts = [part for part in path_parts if part != bin_dir_str]
        self._env["VIRTUAL_ENV"] = str(venv_dir)
        self._env["PATH"] = os.pathsep.join([bin_dir_str] + path_parts)

        return StepResult(0, command, True, "", "", 0, 0.0)

    def run(self, command: str, working_dir: Optional[str] = None) -> StepResult:
        """
        执行单条命令。
        
        - 自动处理 cd 命令（更新当前目录状态）
        - 实时打印输出
        - 返回详细执行结果
        """
        cwd = working_dir or self._current_dir
        cwd = adapt_path_for_os(cwd)
        command = command.strip()

        # 处理 cd 命令（不真正执行，只更新目录状态）
        if command.startswith("cd "):
            target = command[3:].strip().strip('"').strip("'")
            target = adapt_path_for_os(target)
            # 处理相对路径
            if not os.path.isabs(target):
                target = str(Path(cwd) / target)
            if os.path.isdir(target):
                self._current_dir = target
                return StepResult(0, command, True, "", "", 0, 0.0)
            else:
                return StepResult(
                    0, command, False, "", f"目录不存在: {target}", 1, 0.0,
                    error_message=f"目录不存在: {target}",
                )

        venv_result = self._activate_virtualenv(command, cwd)
        if venv_result is not None:
            return venv_result

        # 处理 mkdir 命令（提前创建目录）
        if re.match(r'mkdir\s+(?:-p\s+)?(.+)', command):
            match = re.match(r'mkdir\s+(?:-p\s+)?(.+)', command)
            if match:
                dir_path = adapt_path_for_os(match.group(1).strip())
                if not os.path.isabs(dir_path):
                    dir_path = str(Path(cwd) / dir_path)
                Path(dir_path).mkdir(parents=True, exist_ok=True)
                return StepResult(0, command, True, "", "", 0, 0.0)

        if self.verbose:
            logger.info("  $ %s", command)

        start = time.time()
        stdout_lines = []
        stderr_lines = []

        try:
            # 确保目录存在
            Path(cwd).mkdir(parents=True, exist_ok=True)
            
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env=self._env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Windows 需要这个避免弹出额外窗口
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
            )

            # 实时读取输出
            import threading

            def read_stream(stream, lines, prefix=""):
                for line in stream:
                    lines.append(line.rstrip())
                    if self.verbose:
                        sys.stdout.write(f"  {prefix}{line}")
                        sys.stdout.flush()

            t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_lines))
            t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_lines, "⚠ "))
            t_out.start()
            t_err.start()

            try:
                proc.wait(timeout=self.timeout_sec)
            except subprocess.TimeoutExpired:
                # 渐进式关闭：先 SIGTERM（graceful），等 5 秒，再 SIGKILL
                import signal
                if platform.system() != "Windows":
                    try:
                        proc.send_signal(signal.SIGTERM)
                        proc.wait(timeout=5)
                    except (subprocess.TimeoutExpired, OSError):
                        proc.kill()
                else:
                    proc.kill()
                return StepResult(
                    0, command, False, "", "命令超时", -1,
                    time.time() - start,
                    error_message=f"命令执行超时（{self.timeout_sec}秒）",
                )

            t_out.join()
            t_err.join()

            duration = time.time() - start
            success = proc.returncode == 0
            stdout = "\n".join(stdout_lines)
            stderr = "\n".join(stderr_lines)

            return StepResult(
                step_id=0,
                command=command,
                success=success,
                stdout=stdout[-3000:],   # 只保留最后 3000 字符
                stderr=stderr[-3000:],
                exit_code=proc.returncode,
                duration_sec=round(duration, 1),
                error_message=stderr[:500] if not success else "",
            )

        except Exception as e:
            return StepResult(
                0, command, False, "", str(e), -1,
                time.time() - start,
                error_message=str(e),
            )


# ─────────────────────────────────────────────
#  安装计划执行器
# ─────────────────────────────────────────────

class InstallExecutor:
    """
    执行完整安装计划，支持自动错误修复。
    """

    def __init__(self, llm_provider=None, verbose: bool = True):
        self.llm = llm_provider
        self.verbose = verbose
        self.executor = CommandExecutor(verbose=verbose)

    def execute_plan(self, plan: dict, project_name: str = "") -> InstallResult:
        """
        执行 LLM 生成的安装计划。
        
        Args:
            plan: {"steps": [...], "launch_command": "...", ...}
            project_name: 项目名称（用于日志）
        """
        steps = plan.get("steps", [])
        launch_command = plan.get("launch_command", "")
        
        result = InstallResult(
            project=project_name,
            success=False,
            launch_command=launch_command,
        )

        logger.info("="*50)
        logger.info(t("exec.install_start", project=project_name, steps=len(steps)))
        logger.info("="*50)

        for i, step in enumerate(steps, 1):
            command = step.get("command", "").strip()
            description = step.get("description", f"步骤 {i}")
            working_dir = step.get("working_dir", "")
            
            if not command:
                continue

            # 扩展路径中的 ~
            if working_dir:
                working_dir = adapt_path_for_os(working_dir)

            logger.info(t("exec.step_progress", current=i, total=len(steps), description=description))

            # 安全检查
            is_safe, warning = check_command_safety(command)
            if not is_safe:
                logger.warning(warning)
                result.error_summary = warning
                return result

            if warning and self.verbose:
                logger.warning(warning)

            # 执行命令
            step_result = self.executor.run(command, working_dir or None)
            step_result.step_id = i
            result.steps.append(step_result)

            if step_result.success:
                dur = step_result.duration_sec
                logger.info(t("exec.step_done", duration=dur))
            else:
                logger.error(t("exec.step_failed", code=step_result.exit_code))

                # 尝试自动修复（规则引擎优先，LLM 兜底）
                if step_result.stderr or step_result.stdout:
                    fixed = self._try_fix(step_result, i, len(steps))
                    if fixed:
                        step_result.fix_applied = True
                        result.steps[-1] = step_result
                        continue

                # 无法修复，终止
                result.error_summary = (
                    f"第 {i} 步失败：{description}\n"
                    f"命令：{command}\n"
                    f"报错：{step_result.stderr[:500]}"
                )
                return result

        # 记录安装目录（通常是第一个 git clone 后的目录）
        result.install_dir = self.executor._current_dir
        result.success = True
        logger.info("="*50)
        logger.info(t("exec.install_done", project=project_name))
        if launch_command:
            logger.info(t("exec.launch_cmd", cmd=launch_command))
        logger.info(t("exec.install_dir", dir=result.install_dir))
        logger.info("="*50)

        return result

    def _try_fix(self, step_result: StepResult, step_num: int, total: int) -> bool:
        """
        尝试自动修复失败的命令。
        策略：规则引擎优先（确定性修复）→ LLM 兜底（智能分析）。
        返回 True 表示修复成功。
        """
        # ── 阶段1：规则引擎（无需 LLM，毫秒级响应）──
        try:
            from .error_fixer import diagnose
        except ImportError:
            from error_fixer import diagnose

        fix = diagnose(step_result.command, step_result.stderr, step_result.stdout)
        if fix:
            logger.info(t("exec.rule_diagnosis", cause=fix.root_cause, confidence=fix.confidence))

            if not fix.fix_commands and not fix.retry_original:
                if fix.outcome == "trusted_failure":
                    step_result.error_message = fix.root_cause
                    return False
                # 无需修复，直接标记成功（如 git clone 目录已存在、npm audit 警告）
                step_result.success = True
                step_result.fix_applied = True
                step_result.fix_command = "(跳过)"
                return True

            # 执行修复命令
            for fix_cmd in fix.fix_commands:
                logger.info(t("exec.fix_cmd", cmd=fix_cmd))
                fix_result = self.executor.run(fix_cmd)
                if not fix_result.success:
                    logger.warning(t("exec.fix_failed"))
                    break
            else:
                # 所有修复命令成功
                if fix.retry_original:
                    logger.info(t("exec.retrying"))
                    retry_result = self.executor.run(step_result.command)
                    if retry_result.success:
                        logger.info(t("exec.rule_fix_ok"))
                        step_result.fix_applied = True
                        step_result.fix_command = " && ".join(fix.fix_commands)
                        step_result.success = True
                        return True
                else:
                    # fix_commands 自身就是替代方案，不需要重试原命令
                    logger.info(t("exec.rule_fix_ok"))
                    step_result.fix_applied = True
                    step_result.fix_command = " && ".join(fix.fix_commands)
                    step_result.success = True
                    return True

        # ── 阶段2：LLM 智能修复（需要 provider 可用）──
        if not self.llm:
            return False

        logger.info(t("exec.llm_fallback"))

        try:
            from .llm import ERROR_FIX_SYSTEM_PROMPT
        except ImportError:
            from llm import ERROR_FIX_SYSTEM_PROMPT
        user_prompt = f"""
报错命令：{step_result.command}

STDERR 输出：
{step_result.stderr[:2000]}

STDOUT 输出（最后部分）：
{step_result.stdout[-1000:]}

系统：{platform.system()} {platform.machine()}
"""
        try:
            response = self.llm.complete(ERROR_FIX_SYSTEM_PROMPT, user_prompt, max_tokens=1024)
            fix_data = json.loads(response)
            fix_commands = fix_data.get("fix_commands", [])
            root_cause = fix_data.get("root_cause", "")
            
            if root_cause:
                logger.info(t("exec.llm_root_cause", cause=root_cause))

            if not fix_commands:
                return False

            # 执行修复命令
            for fix_cmd in fix_commands:
                # 安全检查：LLM 生成的修复命令也必须通过安全过滤
                is_safe, safety_msg = check_command_safety(fix_cmd)
                if not is_safe:
                    logger.warning(t("exec.fix_rejected", cmd=fix_cmd))
                    return False
                logger.info(t("exec.fix_cmd", cmd=fix_cmd))
                fix_result = self.executor.run(fix_cmd)
                if not fix_result.success:
                    logger.error(t("exec.fix_cmd_failed"))
                    return False

            # 重新执行原命令
            logger.info(t("exec.retrying"))
            retry_result = self.executor.run(step_result.command)
            if retry_result.success:
                logger.info(t("exec.llm_fix_ok"))
                step_result.fix_applied = True
                step_result.fix_command = " && ".join(fix_commands)
                step_result.success = True
                return True

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(t("exec.llm_fix_error", error=e))

        return False
