#!/usr/bin/env python3
"""
github-installer 集成测试套件
=====================================
对真实 GitHub 项目进行 fetch + plan 测试。

原则：
  - 只做 fetch 和 plan，绝不执行 install，不修改任何文件
  - 全部使用 llm_force="none"（不需要任何 API Key）
  - 访问 GitHub API（只读），结果保存到 tests/results/

运行：
  python3 test_real_projects.py
  python3 test_real_projects.py --category "已知项目数据库"
  python3 test_real_projects.py --id TC-01
  python3 test_real_projects.py --offline   （跳过网络，只测本地解析逻辑）
"""

from __future__ import annotations
import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Any

# ── 路径设置 ──────────────────────────────────────────────────────────────────
TESTS_DIR   = Path(__file__).parent.parent          # tests/
TOOLS_DIR   = TESTS_DIR.parent / "tools"
RESULTS_DIR = TESTS_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# 屏蔽 LLM 模块的调试输出（stderr），保持测试输出整洁
import io, contextlib

# ── ANSI 颜色 ─────────────────────────────────────────────────────────────────
G  = "\033[32m"   # green
R  = "\033[31m"   # red
Y  = "\033[33m"   # yellow
C  = "\033[36m"   # cyan
B  = "\033[34m"   # blue
M  = "\033[35m"   # magenta
W  = "\033[37m"   # white
BD = "\033[1m"    # bold
DM = "\033[2m"    # dim
RS = "\033[0m"    # reset

PASS = f"{G}✅{RS}"
FAIL = f"{R}❌{RS}"
WARN = f"{Y}⚠️ {RS}"
SKIP = f"{DM}⏭  {RS}"
INFO = f"{C}ℹ️ {RS}"


# ══════════════════════════════════════════════════════════════════════════════
#  测试用例定义
#  每个 case 字段说明：
#    id                    唯一编号
#    category              分类名
#    identifier            传给 cmd_plan/cmd_fetch 的参数
#    description           测试说明
#    expect_fetch_ok       bool: fetch 是否应该成功
#    expect_confidence     "high"/"medium"/"low"/None(不检查)
#    expect_confidence_not 不应该等于这个值
#    expect_strategy_contains  strategy 字段应包含的子串
#    expect_step_commands  所有步骤中，【至少一步】包含这些子串（AND）
#    expect_step_commands_any  所有步骤中，【至少一步】包含这些子串中任一（OR）
#    expect_no_commands    任何步骤都不应包含这些子串
#    expect_launch_contains launch_command 应包含的子串（可选）
#    expect_plan_status    plan 结果的 status 字段（默认 "ok"）
# ══════════════════════════════════════════════════════════════════════════════

TEST_CASES: list[dict[str, Any]] = [

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 1：已知项目数据库命中
    #   核心验证：confidence=high，strategy=known_project，步骤正确
    #   GPU 验证：当前机器是 Apple M3 MPS，所有 torch 命令不应含 CUDA URL
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-01",
        "category": "已知项目数据库",
        "identifier": "comfyanonymous/ComfyUI",
        "description": "ComfyUI — 最热门的 Stable Diffusion 节点式 UI（Python + 深度学习）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone", "venv", "torch"],
        "expect_no_commands": ["rm -rf /", "mkfs", "dd if=", "cu121", "cu118", "rocm"],
        "expect_launch_contains": "main.py",
        "note": "MPS 环境下 torch 不应有 CUDA/ROCm index URL",
    },
    {
        "id": "TC-02",
        "category": "已知项目数据库",
        "identifier": "ollama/ollama",
        "description": "Ollama — 最简单的本地 LLM 运行工具",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands_any": ["brew install", "curl", "winget install", "apt"],
        "expect_no_commands": ["rm -rf /", "mkfs"],
        "expect_launch_contains": "ollama",
    },
    {
        "id": "TC-03",
        "category": "已知项目数据库",
        "identifier": "AUTOMATIC1111/stable-diffusion-webui",
        "description": "A1111 WebUI — 最流行的 SD Web 界面，bash 脚本安装",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone"],
        "expect_no_commands": ["rm -rf /"],
    },
    {
        "id": "TC-04",
        "category": "已知项目数据库",
        "identifier": "hiyouga/LLaMA-Factory",
        "description": "LLaMA-Factory — 最热门 LLM 微调框架（LoRA/全量）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone", "venv", "torch"],
        "expect_no_commands": ["rm -rf /", "cu121", "cu118"],
        "expect_launch_contains": "llamafactory",
    },
    {
        "id": "TC-05",
        "category": "已知项目数据库",
        "identifier": "zhayujie/chatgpt-on-wechat",
        "description": "微信/飞书/钉钉接 AI — 中文场景热门项目",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone", "requirements"],
        "expect_no_commands": ["rm -rf /"],
        "expect_launch_contains": "app.py",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 2：未知 Python ML 项目（类型模板兜底）
    #   核心验证：confidence=medium，strategy 含 python_ml，GPU 自适应正确
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-06",
        "category": "Python ML 类型模板",
        "identifier": "huggingface/diffusers",
        "description": "Diffusers — HuggingFace 扩散模型库（有 environment.yml，可能选 conda 模板）",
        "expect_fetch_ok": True,
        "expect_confidence": "medium",
        # diffusers 同时有 environment.yml 和 setup.py，SmartPlanner 可能选 conda 或 python 模板，两者皆正确
        "expect_strategy_contains_any": ["python", "conda"],
        "expect_step_commands": ["git clone"],
        "expect_no_commands": ["rm -rf /", "cu121", "cu118"],
        "note": "MPS 环境下 torch/conda 均不应有 CUDA URL",
    },
    {
        "id": "TC-07",
        "category": "Python ML 类型模板",
        "identifier": "facebookresearch/detectron2",
        "description": "Detectron2 — Facebook 目标检测框架（已加入已知数据库）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone"],
        "expect_no_commands": ["rm -rf /"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 3：Node.js 项目
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-08",
        "category": "Node.js 类型模板",
        "identifier": "freeCodeCamp/freeCodeCamp",
        "description": "freeCodeCamp — 大型 Node.js 教育平台（不在数据库中）",
        "expect_fetch_ok": True,
        "expect_confidence_not": "high",     # 不是已知项目
        "expect_strategy_contains": "node",
        "expect_step_commands": ["git clone"],
        "expect_no_commands": ["rm -rf /"],
    },
    {
        "id": "TC-09",
        "category": "Node.js 类型模板",
        "identifier": "lobehub/lobe-chat",
        "description": "Lobe-Chat — 现代化 AI 对话界面（已在数据库中，Node.js）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands": ["git clone"],
        "expect_no_commands": ["rm -rf /"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 4：Rust 项目
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-10",
        "category": "Rust 类型模板",
        "identifier": "BurntSushi/ripgrep",
        "description": "ripgrep — 极速文本搜索工具（已加入已知数据库，macOS 用 brew）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands_any": ["cargo install", "cargo build", "brew install"],
        "expect_no_commands": ["rm -rf /", "pip install"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 5：Go 项目
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-11",
        "category": "Go 类型模板",
        "identifier": "cli/cli",
        "description": "GitHub CLI — 已加入已知数据库（macOS 用 brew）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands_any": ["go install", "brew install"],
        "expect_no_commands": ["rm -rf /", "pip install"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 6：Docker 项目
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-12",
        "category": "Docker 类型模板",
        "identifier": "portainer/portainer",
        "description": "Portainer — 已加入已知数据库（Docker 方式运行）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_step_commands_any": ["docker run", "docker-compose", "docker volume"],
        "expect_no_commands": ["rm -rf /"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 7：输入格式兼容性
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-13",
        "category": "输入格式兼容",
        "identifier": "https://github.com/comfyanonymous/ComfyUI",
        "description": "完整 HTTPS URL 格式（应解析到同一个项目）",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_no_commands": ["rm -rf /"],
        "note": "与 TC-01 应生成相同结果",
    },
    {
        "id": "TC-14",
        "category": "输入格式兼容",
        "identifier": "https://github.com/ollama/ollama.git",
        "description": ".git 后缀 URL 格式",
        "expect_fetch_ok": True,
        "expect_confidence": "high",
        "expect_strategy_contains": "known_project",
        "expect_no_commands": ["rm -rf /"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 8：错误处理
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-15",
        "category": "错误处理",
        "identifier": "nonexistent-user-xyz-abc/totally-fake-repo-000",
        "description": "完全不存在的项目 — 应优雅报错，不崩溃",
        "expect_fetch_ok": False,
        "expect_plan_status": "error",
        "expect_step_commands": [],
        "expect_no_commands": [],
        "note": "应返回 status=error 并有 message 字段，不抛出异常",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # 分类 9：小模型 LLM 集成（Ollama 1.5B）
    #   核心验证：面向普通用户的 1.5B 小模型能否生成有效的安装步骤
    #   要求：Ollama 正在运行且已拉取 qwen2.5:1.5b（否则自动跳过）
    # ─────────────────────────────────────────────────────────────────────────
    {
        "id": "TC-16",
        "category": "小模型 LLM 集成",
        "identifier": "huggingface/diffusers",
        "description": "用 qwen2.5:1.5b 生成安装方案 — 验证 1.5B 小模型对普通用户可用性",
        "llm_force": "ollama",
        "expect_fetch_ok": True,
        "expect_plan_status": "ok",
        # diffusers 是 pip 库，正确安装方式是 pip/conda install（不一定需要 git clone）
        "expect_step_commands_any": ["pip install", "conda install", "git clone"],
        "expect_no_commands": ["rm -rf /", "mkfs", "dd if="],
        "note": "如果 Ollama 未运行或模型未拉取，自动跳过（非测试失败）",
        "requires_ollama": True,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  断言辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def get_all_commands(plan: dict) -> list[str]:
    return [s.get("command", "") for s in plan.get("steps", [])]


class TestResult:
    def __init__(self, case_id: str, category: str, identifier: str, description: str):
        self.id = case_id
        self.category = category
        self.identifier = identifier
        self.description = description
        self.assertions: list[tuple[bool, str]] = []   # (passed, message)
        self.fetch_data: dict = {}
        self.plan_data: dict = {}
        self.duration_sec: float = 0.0
        self.skipped = False
        self.skip_reason = ""

    @property
    def passed(self) -> bool:
        if self.skipped:
            return True  # 跳过不算失败
        return all(ok for ok, _ in self.assertions)

    @property
    def failed_assertions(self) -> list[str]:
        return [msg for ok, msg in self.assertions if not ok]

    def add(self, passed: bool, message: str):
        self.assertions.append((passed, message))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "identifier": self.identifier,
            "description": self.description,
            "passed": self.passed,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "duration_sec": round(self.duration_sec, 2),
            "assertions": [
                {"passed": ok, "message": msg}
                for ok, msg in self.assertions
            ],
            "plan_confidence": self.plan_data.get("confidence", ""),
            "plan_strategy": self.plan_data.get("plan", {}).get("strategy", ""),
            "plan_steps_count": len(self.plan_data.get("plan", {}).get("steps", [])),
            "plan_launch": self.plan_data.get("plan", {}).get("launch_command", ""),
            "fetch_stars": self.fetch_data.get("project", {}).get("stars", 0),
            "fetch_language": self.fetch_data.get("project", {}).get("language", ""),
            "fetch_type": self.fetch_data.get("project", {}).get("project_type", []),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  主测试运行器
# ══════════════════════════════════════════════════════════════════════════════

def _check_ollama_available(model: str = "qwen2.5:1.5b") -> tuple[bool, str]:
    """检查 Ollama 是否运行且模型已拉取，返回 (available, reason)"""
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            headers={"User-Agent": "github-installer-test/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            # 检查是否有匹配的模型（允许 "qwen2.5:1.5b" 匹配 "qwen2.5:1.5b" 或 "qwen2.5:1.5b-instruct-*"）
            base_name = model.split(":")[0] + ":" + model.split(":")[1] if ":" in model else model
            found = any(m.startswith(base_name) for m in models)
            if not found:
                return False, f"模型 {model!r} 未拉取（已有：{models[:3]}）"
            return True, "ok"
    except (urllib.error.URLError, OSError):
        return False, "Ollama 服务未运行（请执行 ollama serve）"
    except Exception as e:
        return False, f"Ollama 检查失败：{e}"


def run_case(case: dict, offline: bool = False) -> TestResult:
    import main as _main

    r = TestResult(
        case_id=case["id"],
        category=case["category"],
        identifier=case["identifier"],
        description=case["description"],
    )

    # offline 模式跳过所有需要网络的测试
    if offline:
        r.skipped = True
        r.skip_reason = "offline 模式"
        return r

    # 需要 Ollama 的测试：先检查可用性
    if case.get("requires_ollama"):
        ok, reason = _check_ollama_available("qwen2.5:1.5b")
        if not ok:
            r.skipped = True
            r.skip_reason = f"Ollama 不可用（{reason}）— 运行 'ollama serve && ollama pull qwen2.5:1.5b'"
            return r

    t0 = time.time()

    # ── 只调用 cmd_plan（内部已包含 fetch + SmartPlanner，不重复 API 请求）────
    # 注：不单独调用 cmd_fetch，避免浪费 GitHub API 配额（无 token 时上限 60次/小时）
    llm_mode = case.get("llm_force", "none")
    print(f"    {DM}plan  {case['identifier']}  [llm={llm_mode}]...{RS}", end="", flush=True)

    plan_stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(plan_stderr):
            plan_result = _main.cmd_plan(case["identifier"], llm_force=llm_mode)
        r.plan_data = plan_result
        plan_ok = plan_result.get("status") == "ok"
    except Exception as e:
        plan_ok = False
        r.plan_data = {"status": "error", "message": str(e)}

    print(f" {'ok' if plan_ok else 'error'}", flush=True)

    # 检测 GitHub API 限速 → 跳过该测试（非代码 bug，是环境限制）
    err_msg = r.plan_data.get("message", "")
    RATE_LIMIT_KEYWORDS = ["频率超限", "rate limit", "secondary rate", "403"]
    is_rate_limited = not plan_ok and any(k.lower() in err_msg.lower() for k in RATE_LIMIT_KEYWORDS)
    if is_rate_limited and case.get("expect_fetch_ok", True):
        r.skipped = True
        r.skip_reason = f"GitHub API 限速（设置 GITHUB_TOKEN 可解除）"
        r.duration_sec = time.time() - t0
        return r

    # fetch 是否成功：通过 plan status 间接验证（plan 内部做 fetch，fetch 失败则 plan status=error）
    fetch_ok = plan_ok  # plan ok ⟹ fetch ok
    if not plan_ok and r.plan_data.get("status") == "error":
        fetch_ok = False
    expected_fetch_ok = case.get("expect_fetch_ok", True)
    r.add(
        fetch_ok == expected_fetch_ok,
        f"fetch/plan 结果: {'ok' if fetch_ok else 'error'}"
        + (f"（期望 {'ok' if expected_fetch_ok else 'error'}）" if fetch_ok != expected_fetch_ok else ""),
    )

    # ── 断言验证 ──────────────────────────────────────────────────────────────
    expected_status = case.get("expect_plan_status", "ok")
    r.add(
        r.plan_data.get("status") == expected_status,
        f"plan status={r.plan_data.get('status')}（期望 {expected_status}）",
    )

    if plan_ok:
        plan = r.plan_data.get("plan", {})
        confidence = r.plan_data.get("confidence", "")
        strategy = plan.get("strategy", "")
        cmds = get_all_commands(plan)
        all_text = " ".join(cmds).lower()

        # confidence 精确匹配
        if case.get("expect_confidence"):
            exp = case["expect_confidence"]
            r.add(confidence == exp, f"confidence={confidence!r}（期望 {exp!r}）")

        # confidence 不应等于
        if case.get("expect_confidence_not"):
            exp_not = case["expect_confidence_not"]
            r.add(confidence != exp_not, f"confidence={confidence!r}（不应为 {exp_not!r}）")

        # strategy 包含某子串
        if case.get("expect_strategy_contains"):
            exp = case["expect_strategy_contains"]
            r.add(exp in strategy, f"strategy={strategy!r}（期望含 {exp!r}）")

        # strategy 包含若干值之一（OR）
        if case.get("expect_strategy_contains_any"):
            opts = case["expect_strategy_contains_any"]
            found = any(o in strategy for o in opts)
            r.add(found, f"strategy={strategy!r}（期望含以下任一 {opts}）")

        # 每个关键字至少出现在一条命令中（AND）
        for kw in case.get("expect_step_commands", []):
            found = any(kw.lower() in c.lower() for c in cmds)
            r.add(found, f"步骤中应含 {kw!r}（未找到，步骤数={len(cmds)}）")

        # OR 关键字（至少一个命中）
        kw_any = case.get("expect_step_commands_any", [])
        if kw_any:
            found = any(any(kw.lower() in c.lower() for kw in kw_any) for c in cmds)
            r.add(found, f"步骤中应含以下任一 {kw_any}（未找到）")

        # 禁止出现的关键字
        for bad in case.get("expect_no_commands", []):
            found_bad = any(bad.lower() in c.lower() for c in cmds)
            r.add(not found_bad, f"危险/错误关键字 {bad!r} 出现在步骤中")

        # launch 命令
        if case.get("expect_launch_contains"):
            launch = plan.get("launch_command", "")
            exp = case["expect_launch_contains"]
            r.add(exp.lower() in launch.lower(), f"launch={launch!r}（期望含 {exp!r}）")

        # 步骤字段完整性（至少有 command + description）
        for i, step in enumerate(plan.get("steps", [])):
            if not step.get("command"):
                r.add(False, f"步骤[{i}] 缺少 command 字段")
                break
            if not step.get("description"):
                r.add(False, f"步骤[{i}] 缺少 description 字段")
                break
        else:
            if plan.get("steps"):
                r.add(True, f"所有 {len(plan['steps'])} 步均含 command+description 字段")

    r.duration_sec = time.time() - t0
    return r


def print_case_result(r: TestResult):
    icon = SKIP if r.skipped else (PASS if r.passed else FAIL)
    print(f"\n  {icon} {BD}{r.id}{RS} {C}{r.category}{RS}")
    print(f"     {r.identifier}")
    print(f"     {DM}{r.description}{RS}")

    if r.skipped:
        print(f"     {SKIP}跳过：{r.skip_reason}")
        return

    if r.plan_data.get("status") == "ok":
        plan = r.plan_data.get("plan", {})
        conf = r.plan_data.get("confidence", "?")
        strat = plan.get("strategy", "?")
        steps_n = len(plan.get("steps", []))
        launch = plan.get("launch_command", "")
        conf_color = G if conf == "high" else (Y if conf == "medium" else R)
        print(f"     confidence={conf_color}{conf}{RS}  strategy={strat}  steps={steps_n}", end="")
        if launch:
            print(f"  launch={DM}{launch}{RS}", end="")
        print()

        # 打印步骤摘要（最多显示前 6 步）
        for i, step in enumerate(plan.get("steps", [])[:6]):
            w = f" {Y}⚠️{RS}" if step.get("_warning") else ""
            print(f"     {DM}{i+1}. {step.get('description','')}{RS}{w}")
            print(f"        {DM}$ {step.get('command','')[:80]}{RS}")
        if steps_n > 6:
            print(f"     {DM}... 还有 {steps_n - 6} 步{RS}")

    elif r.plan_data.get("status") == "error":
        msg = r.plan_data.get("message", "")
        print(f"     {R}plan error: {msg[:80]}{RS}")

    # 打印断言结果
    for ok, msg in r.assertions:
        if ok:
            print(f"     {G}  ✓ {msg}{RS}")
        else:
            print(f"     {R}  ✗ {msg}{RS}")

    print(f"     {DM}耗时 {r.duration_sec:.1f}s{RS}")


def save_report(results: list[TestResult]):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"report_{ts}.json"
    report = {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed and not r.skipped),
        "failed": sum(1 for r in results if not r.passed and not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
        "total_duration_sec": round(sum(r.duration_sec for r in results), 1),
        "cases": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return path


def main():
    parser = argparse.ArgumentParser(description="github-installer 集成测试")
    parser.add_argument("--category", help="只运行某个分类")
    parser.add_argument("--id",       help="只运行某个用例（如 TC-01）")
    parser.add_argument("--offline",  action="store_true", help="不发网络请求（跳过所有需要 GitHub API 的测试）")
    parser.add_argument("--llm-test", action="store_true", dest="llm_test",
                        help="包含需要 Ollama 的 LLM 测试（TC-16）；默认跳过 requires_ollama 用例")
    parser.add_argument("--delay",    type=float, default=2.0, help="每个测试之间的间隔秒数（避免 GitHub API 限速，默认 2s）")
    args = parser.parse_args()

    # 筛选用例
    cases = TEST_CASES
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    elif args.category:
        cases = [c for c in cases if c["category"] == args.category]
    elif not args.llm_test:
        # 默认不包含 requires_ollama 的测试（避免首次运行需要 Ollama 环境）
        cases = [c for c in cases if not c.get("requires_ollama")]

    if not cases:
        print(f"{R}未找到匹配的测试用例{RS}")
        sys.exit(1)

    print(f"\n{BD}{'═'*60}{RS}")
    print(f"{BD}  github-installer 集成测试{RS}")
    print(f"  项目路径：{TOOLS_DIR}")
    print(f"  测试数量：{len(cases)} 个用例")
    if args.llm_test:
        ollama_ok, ollama_reason = _check_ollama_available("qwen2.5:1.5b")
        ollama_icon = G if ollama_ok else Y
        print(f"  LLM 模式：{'✓ Ollama 1.5B 就绪' if ollama_ok else f'⚠  {ollama_reason}'}"
              .replace("✓", f"{G}✓{RS}").replace("⚠", f"{Y}⚠{RS}"))
    else:
        print(f"  LLM 模式：SmartPlanner 零 AI（--llm none）"
              f"  {DM}加 --llm-test 包含 Ollama 测试{RS}")
    if args.offline:
        print(f"  {Y}网络模式：offline（跳过 GitHub API 请求）{RS}")
    else:
        # 显示 GitHub Token 状态和 API 配额
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            print(f"  {G}GITHUB_TOKEN：已配置（5000 请求/小时）{RS}")
        else:
            # 简单查询当前 API 配额
            try:
                import urllib.request
                req = urllib.request.Request(
                    "https://api.github.com/rate_limit",
                    headers={"User-Agent": "github-installer-test/1.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    quota = json.loads(resp.read())["resources"]["core"]
                    remaining = quota["remaining"]
                    color = G if remaining > 40 else (Y if remaining > 15 else R)
                    print(f"  {color}GitHub API：{remaining}/60 请求剩余（无 token）{RS}")
                    if remaining < len(cases) * 5:
                        print(f"  {Y}⚠️  建议设置 GITHUB_TOKEN 提高配额：export GITHUB_TOKEN=xxx{RS}")
            except Exception:
                print(f"  {DM}GitHub API 配额：无法查询{RS}")
    print(f"{BD}{'═'*60}{RS}")

    results: list[TestResult] = []
    categories_seen: set[str] = set()

    for i, case in enumerate(cases):
        cat = case["category"]
        if cat not in categories_seen:
            categories_seen.add(cat)
            print(f"\n{BD}{M}【{cat}】{RS}")

        print(f"\n  {DM}→ 运行 {case['id']}: {case['identifier']}{RS}")
        result = run_case(case, offline=args.offline)
        results.append(result)
        print_case_result(result)

        # 测试间等待（避免 GitHub API 60次/小时限速）
        if i < len(cases) - 1 and not args.offline and not result.skipped:
            time.sleep(args.delay)

    # ── 最终汇总 ─────────────────────────────────────────────────────────────
    total     = len(results)
    passed    = sum(1 for r in results if r.passed and not r.skipped)
    failed    = sum(1 for r in results if not r.passed and not r.skipped)
    skipped   = sum(1 for r in results if r.skipped)
    total_sec = sum(r.duration_sec for r in results)

    print(f"\n{BD}{'═'*60}{RS}")
    print(f"{BD}  测试结果汇总{RS}")
    print(f"{'═'*60}")
    print(f"  总计：{total} 用例  "
          f"{G}{passed} 通过{RS}  "
          f"{R}{failed} 失败{RS}  "
          f"{DM}{skipped} 跳过{RS}")
    print(f"  总耗时：{total_sec:.1f}s")

    if failed:
        print(f"\n{R}{BD}  失败用例：{RS}")
        for r in results:
            if not r.passed and not r.skipped:
                print(f"  {FAIL} {r.id}  {r.identifier}")
                for msg in r.failed_assertions:
                    print(f"       {R}✗ {msg}{RS}")

    report_path = save_report(results)
    print(f"\n  {INFO}详细报告已保存：{report_path.relative_to(TESTS_DIR.parent)}")
    print(f"{'═'*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
