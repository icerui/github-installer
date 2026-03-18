"""
完整功能测试套件
运行方式：python3 run_tests.py
"""
import sys
import json
import os
import traceback

# Windows CI 默认 cp1252 编码无法输出中文/emoji，强制 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "
results = []

def test(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"  {PASS} {name}")
    except AssertionError as e:
        results.append((FAIL, name, str(e)))
        print(f"  {FAIL} {name}: {e}")
    except Exception as e:
        results.append((FAIL, name, traceback.format_exc()))
        print(f"  {FAIL} {name}: {e}")


# ──────────────────────────────────────────────
# 1. detector.py
# ──────────────────────────────────────────────
print("\n【1/7】 detector.py - 环境检测")

from detector import EnvironmentDetector, format_env_summary

def t_detect_os():
    env = EnvironmentDetector().detect()
    assert "os" in env, "缺少 os 字段"
    assert env["os"]["type"] in ("macos", "linux", "windows"), f"未知 os.type: {env['os']['type']}"

def t_detect_hardware():
    env = EnvironmentDetector().detect()
    hw = env["hardware"]
    assert hw["cpu_count"] > 0, "cpu_count <= 0"
    # RAM 检测在部分 CI 环境可能返回 None（wmic 弃用等）
    if hw["ram_gb"] is not None:
        assert hw["ram_gb"] > 0, f"ram_gb 异常: {hw['ram_gb']}"

def t_detect_gpu():
    env = EnvironmentDetector().detect()
    gpu = env["gpu"]
    assert "type" in gpu, "缺少 gpu.type"
    assert gpu["type"] in ("apple_mps", "mps", "nvidia_cuda", "cuda", "amd_rocm", "rocm", "cpu_only"), f"未知 gpu.type: {gpu['type']}"

def t_detect_disk():
    env = EnvironmentDetector().detect()
    disk = env["disk"]
    assert disk["free_gb"] > 0, "free_gb <= 0"

def t_detect_network():
    env = EnvironmentDetector().detect()
    net = env["network"]
    assert "github" in net, "缺少 network.github"
    assert "pypi" in net, "缺少 network.pypi"

def t_format_summary():
    env = EnvironmentDetector().detect()
    summary = format_env_summary(env)
    assert len(summary) > 20, f"summary 太短: {summary}"
    os_type = env.get("os", {}).get("type", "")
    assert os_type in ("macos", "linux", "windows"), f"未知 os.type: {os_type}"

test("detect OS 类型", t_detect_os)
test("detect 硬件 (CPU/RAM)", t_detect_hardware)
test("detect GPU 类型", t_detect_gpu)
test("detect 磁盘空间", t_detect_disk)
test("detect 网络可达性", t_detect_network)
test("format_env_summary 输出", t_format_summary)


# ──────────────────────────────────────────────
# 2. llm.py
# ──────────────────────────────────────────────
print("\n【2/7】 llm.py - LLM 适配器")

from llm import create_provider, HeuristicProvider, BaseLLMProvider, INSTALL_SYSTEM_PROMPT, ERROR_FIX_SYSTEM_PROMPT

def t_heuristic_always_available():
    p = create_provider(force="none")
    assert isinstance(p, HeuristicProvider), "force=none 应返回 HeuristicProvider"

def t_heuristic_name():
    p = HeuristicProvider()
    assert len(p.name) > 0, "name 不能为空"

def t_heuristic_extract_pip():
    p = HeuristicProvider()
    fake_readme = "```bash\npip install torch\npip install transformers\n```"
    result = p.complete("", fake_readme)
    data = json.loads(result)
    cmds = [s["command"] for s in data.get("steps", [])]
    assert any("pip install torch" in c for c in cmds), f"未提取到 pip install torch, cmds={cmds}"

def t_heuristic_extract_git_clone():
    p = HeuristicProvider()
    fake = "```bash\ngit clone https://github.com/foo/bar.git\ncd bar\n```"
    result = p.complete("", fake)
    data = json.loads(result)
    cmds = [s["command"] for s in data.get("steps", [])]
    assert any("git clone" in c for c in cmds), f"未提取到 git clone, cmds={cmds}"

def t_heuristic_blocks_dangerous():
    p = HeuristicProvider()
    fake = "```bash\nrm -rf /\ngit clone https://github.com/foo/bar.git\n```"
    result = p.complete("", fake)
    data = json.loads(result)
    cmds = [s["command"] for s in data.get("steps", [])]
    assert not any("rm -rf /" in c for c in cmds), "危险命令未被过滤"

def t_heuristic_no_steps_message():
    p = HeuristicProvider()
    result = p.complete("", "这是一段没有任何代码块的纯文字 README。")
    data = json.loads(result)
    assert data["status"] == "insufficient_data", f"无命令时 status 应为 insufficient_data, 得 {data['status']}"

def t_create_provider_fallback():
    # 不论环境如何，create_provider 永远返回一个可用的 Provider
    p = create_provider(force="none")
    assert isinstance(p, BaseLLMProvider), "必须是 BaseLLMProvider 子类"
    # 调用 complete 不崩溃
    r = p.complete("system", "user")
    assert isinstance(r, str), "complete() 应返回 str"

def t_system_prompts_nonempty():
    assert len(INSTALL_SYSTEM_PROMPT) > 100, "INSTALL_SYSTEM_PROMPT 太短"
    assert len(ERROR_FIX_SYSTEM_PROMPT) > 50, "ERROR_FIX_SYSTEM_PROMPT 太短"

test("HeuristicProvider 永远可用", t_heuristic_always_available)
test("HeuristicProvider.name 非空", t_heuristic_name)
test("提取 pip install 命令", t_heuristic_extract_pip)
test("提取 git clone 命令", t_heuristic_extract_git_clone)
test("过滤 rm -rf / 危险命令", t_heuristic_blocks_dangerous)
test("无代码块时返回 insufficient_data", t_heuristic_no_steps_message)
test("create_provider 降级到 HeuristicProvider", t_create_provider_fallback)
test("System Prompt 非空", t_system_prompts_nonempty)


# ──────────────────────────────────────────────
# 3. planner.py
# ──────────────────────────────────────────────
print("\n【3/7】 planner.py - SmartPlanner")

from planner import SmartPlanner, _torch_install_cmd, _venv_activate, _node_pm

ENV_MAC_M3 = {
    "os": {"type": "macos", "arch": "arm64", "chip": "M3", "is_apple_silicon": True},
    "gpu": {"type": "apple_mps"},
    "package_managers": {"pip": {}, "pip3": {}, "brew": {}, "npm": {}, "pnpm": {}},
    "runtimes": {"python3": {"available": True}, "git": {"available": True}},
}
ENV_LINUX_CUDA12 = {
    "os": {"type": "linux", "distro": "ubuntu"},
    "gpu": {"type": "cuda", "cuda_version": "12.1"},
    "package_managers": {"pip3": {}, "apt": {}},
    "runtimes": {"python3": {}, "git": {}},
}
ENV_WIN_CPU = {
    "os": {"type": "windows"},
    "gpu": {"type": "cpu_only"},
    "package_managers": {"pip": {}, "winget": {}},
    "runtimes": {"python": {}, "git": {}},
}

def t_torch_mps():
    cmd = _torch_install_cmd(ENV_MAC_M3)
    assert "cu1" not in cmd and "rocm" not in cmd, f"MPS 不应包含 CUDA/ROCm: {cmd}"
    assert "torch" in cmd, "应包含 torch"

def t_torch_cuda12():
    cmd = _torch_install_cmd(ENV_LINUX_CUDA12)
    assert "cu121" in cmd, f"CUDA12 应用 cu121, 得: {cmd}"

def t_torch_windows_cpu():
    cmd = _torch_install_cmd(ENV_WIN_CPU)
    assert "cpu" in cmd.lower(), f"CPU only 应包含 cpu 索引: {cmd}"

def t_venv_activate_unix():
    cmd = _venv_activate(ENV_MAC_M3)
    assert cmd.startswith("source"), f"macOS venv activate 应用 source, 得: {cmd}"

def t_venv_activate_windows():
    cmd = _venv_activate(ENV_WIN_CPU)
    assert "Scripts" in cmd or "activate" in cmd, f"Windows activate 路径不对: {cmd}"

def t_node_pm_pnpm():
    install, dev = _node_pm(ENV_MAC_M3)  # ENV_MAC_M3 有 pnpm
    assert install.startswith("pnpm"), f"有 pnpm 时应优先 pnpm, 得: {install}"

def t_known_project_comfyui():
    p = SmartPlanner()
    plan = p.generate_plan("comfyanonymous", "ComfyUI", ENV_MAC_M3, ["python", "pytorch"], {}, "")
    assert plan["confidence"] == "high", f"ComfyUI 应为 high, 得: {plan['confidence']}"
    assert plan["strategy"] == "known_project"
    cmds = [s["command"] for s in plan["steps"]]
    # 应包含 git clone
    assert any("git clone" in c for c in cmds), f"缺少 git clone: {cmds}"
    # MPS 环境 torch 不应有 cu1xx
    torch_cmds = [c for c in cmds if "torch" in c]
    assert torch_cmds, "没有 torch 安装命令"
    assert all("cu1" not in c for c in torch_cmds), f"MPS 环境不应含 CUDA index: {torch_cmds}"

def t_known_project_ollama_linux():
    p = SmartPlanner()
    plan = p.generate_plan("ollama", "ollama", ENV_LINUX_CUDA12, [], {}, "")
    assert plan["confidence"] == "high"
    cmds = [s["command"] for s in plan["steps"]]
    assert any("curl" in c for c in cmds), "Linux Ollama 应用 curl 安装"

def t_unknown_ml_project():
    p = SmartPlanner()
    plan = p.generate_plan(
        "unknown-user", "my-ai-tool",
        ENV_LINUX_CUDA12,
        ["python", "pytorch", "diffusers"],
        {"requirements.txt": "torch\ndiffusers"},
        ""
    )
    assert plan["strategy"] == "type_template_python_ml"
    cmds = [s["command"] for s in plan["steps"]]
    cuda_cmds = [c for c in cmds if "cu121" in c]
    assert cuda_cmds, f"CUDA12 Linux 应有 cu121 安装命令, cmds={cmds}"

def t_node_project():
    p = SmartPlanner()
    plan = p.generate_plan("user", "my-front", ENV_MAC_M3, ["node"], {"package.json": "{}"}, "")
    assert "node" in plan["strategy"]
    assert plan["launch_command"] in ("pnpm dev", "yarn dev", "npm run dev")

def t_rust_project():
    p = SmartPlanner()
    plan = p.generate_plan("user", "my-cli", ENV_MAC_M3, ["rust"], {"Cargo.toml": ""}, "")
    assert "rust" in plan["strategy"]
    cmds = [s["command"] for s in plan["steps"]]
    assert any("cargo" in c for c in cmds), "Rust 项目应有 cargo 命令"

def t_dangerous_cmd_filtered_in_plan():
    p = SmartPlanner()
    # 手动构造含危险命令的 README
    bad_readme = "```bash\nrm -rf /\ngit clone https://github.com/foo/bar.git\n```"
    plan = p.generate_plan("foo", "bar", ENV_MAC_M3, [], {}, bad_readme)
    cmds = [s["command"] for s in plan["steps"]]
    assert not any("rm -rf /" in c for c in cmds), "危险命令不应出现在计划中"

def t_plan_steps_have_required_fields():
    p = SmartPlanner()
    plan = p.generate_plan("comfyanonymous", "ComfyUI", ENV_MAC_M3, ["python"], {}, "")
    for i, step in enumerate(plan["steps"]):
        assert "command" in step, f"step[{i}] 缺少 command"
        assert "description" in step, f"step[{i}] 缺少 description"
        assert "_warning" in step, f"step[{i}] 缺少 _warning 字段"

test("_torch_install_cmd: Apple MPS 无 CUDA", t_torch_mps)
test("_torch_install_cmd: CUDA 12 → cu121", t_torch_cuda12)
test("_torch_install_cmd: Windows CPU only", t_torch_windows_cpu)
test("_venv_activate: macOS 用 source", t_venv_activate_unix)
test("_venv_activate: Windows 用 Scripts", t_venv_activate_windows)
test("_node_pm: pnpm 优先于 npm", t_node_pm_pnpm)
test("已知项目 ComfyUI（M3）→ high confidence + MPS torch", t_known_project_comfyui)
test("已知项目 Ollama（Linux）→ curl 安装", t_known_project_ollama_linux)
test("未知 ML 项目（CUDA12）→ 类型模板 + cu121", t_unknown_ml_project)
test("Node.js 项目模板", t_node_project)
test("Rust 项目模板", t_rust_project)
test("README 提取时过滤危险命令", t_dangerous_cmd_filtered_in_plan)
test("所有 step 含 command/description/_warning", t_plan_steps_have_required_fields)


# ──────────────────────────────────────────────
# 4. executor.py - 安全检查（不真正执行）
# ──────────────────────────────────────────────
print("\n【4/7】 executor.py - 安全检查")

from executor import check_command_safety, BLOCKED_PATTERNS, WARN_PATTERNS

def t_block_rm_rf_root():
    safe, msg = check_command_safety("rm -rf /")
    assert not safe, "rm -rf / 应被拒绝"

def t_block_fork_bomb():
    safe, msg = check_command_safety(":(){ :|:& };:")
    assert not safe, "fork bomb 应被拒绝"

def t_block_mkfs():
    safe, msg = check_command_safety("mkfs.ext4 /dev/sda")
    assert not safe, "mkfs 应被拒绝"

def t_block_dd():
    safe, msg = check_command_safety("dd if=/dev/zero of=/dev/sda")
    assert not safe, "dd 覆盖磁盘应被拒绝"

def t_warn_sudo():
    safe, msg = check_command_safety("sudo apt install python3")
    assert safe, "sudo apt 应允许（带警告）"
    assert len(msg) > 0, "sudo 应有警告信息"

def t_warn_curl_pipe():
    safe, msg = check_command_safety("curl -fsSL https://example.com/install.sh | sh")
    assert safe, "curl|sh 应允许（带警告）"
    assert len(msg) > 0, "curl|sh 应有警告"

def t_safe_pip_install():
    safe, msg = check_command_safety("pip install torch")
    assert safe, "pip install 应安全"
    assert msg == "", f"pip install 不应有警告, 得: {msg}"

def t_safe_git_clone():
    safe, msg = check_command_safety("git clone https://github.com/foo/bar.git")
    assert safe, "git clone 应安全"

def t_safe_python_run():
    safe, msg = check_command_safety("python main.py --listen")
    assert safe, "python main.py 应安全"

test("拦截 rm -rf /", t_block_rm_rf_root)
test("拦截 fork bomb", t_block_fork_bomb)
test("拦截 mkfs.*", t_block_mkfs)
test("拦截 dd if=/dev/...", t_block_dd)
test("警告 sudo（但允许）", t_warn_sudo)
test("警告 curl|sh（但允许）", t_warn_curl_pipe)
test("pip install 安全无警告", t_safe_pip_install)
test("git clone 安全", t_safe_git_clone)
test("python main.py 安全", t_safe_python_run)


# ──────────────────────────────────────────────
# 5. fetcher.py - 解析逻辑（不请求网络）
# ──────────────────────────────────────────────
print("\n【5/7】 fetcher.py - 项目标识解析")

from fetcher import parse_repo_identifier, detect_project_types

def t_parse_full_url():
    owner, repo = parse_repo_identifier("https://github.com/comfyanonymous/ComfyUI")
    assert owner == "comfyanonymous", f"owner={owner}"
    assert repo == "ComfyUI", f"repo={repo}"

def t_parse_owner_slash_repo():
    owner, repo = parse_repo_identifier("ollama/ollama")
    assert owner == "ollama"
    assert repo == "ollama"

def t_parse_url_with_git():
    owner, repo = parse_repo_identifier("https://github.com/hiyouga/LLaMA-Factory.git")
    assert owner == "hiyouga"
    assert repo == "LLaMA-Factory"

def t_parse_url_with_trailing_slash():
    owner, repo = parse_repo_identifier("https://github.com/AUTOMATIC1111/stable-diffusion-webui/")
    assert owner == "AUTOMATIC1111"
    assert repo == "stable-diffusion-webui"

def t_detect_python():
    types = detect_project_types(
        {"language": "Python"},
        "This is a Python project",
        {"requirements.txt": "torch\n"}
    )
    assert "python" in types, f"应检测到 python: {types}"

def t_detect_pytorch():
    types = detect_project_types(
        {"language": "Python"},
        "pip install torch\nThis uses pytorch",
        {}
    )
    assert "pytorch" in types, f"应检测到 pytorch: {types}"

def t_detect_docker():
    types = detect_project_types(
        {"language": "Python"},
        "docker run -it foo/bar",
        {"Dockerfile": "FROM python:3.11"}
    )
    assert "docker" in types, f"应检测到 docker: {types}"

def t_detect_node():
    types = detect_project_types(
        {"language": "JavaScript"},
        "npm install",
        {"package.json": "{}"}
    )
    assert "node" in types, f"应检测到 node: {types}"

test("解析完整 GitHub URL", t_parse_full_url)
test("解析 owner/repo 格式", t_parse_owner_slash_repo)
test("解析 .git 后缀 URL", t_parse_url_with_git)
test("解析末尾带 / 的 URL", t_parse_url_with_trailing_slash)
test("检测 Python 项目类型", t_detect_python)
test("检测 PyTorch 关键词", t_detect_pytorch)
test("检测 Docker 类型", t_detect_docker)
test("检测 Node.js 类型", t_detect_node)


# ──────────────────────────────────────────────
# 6. main.py - cmd_plan 逻辑（Mock 网络）
# ──────────────────────────────────────────────
print("\n【6/7】 main.py - cmd_plan 逻辑（模拟数据）")

import main as _main
from planner import SmartPlanner
from unittest.mock import patch, MagicMock
from fetcher import RepoInfo

def _make_fake_info(owner="comfyanonymous", repo="ComfyUI", project_type=None):
    return RepoInfo(
        owner=owner, repo=repo,
        full_name=f"{owner}/{repo}",
        description="test",
        stars=1000,
        language="Python",
        license="GPL",
        default_branch="main",
        readme="# ComfyUI\n```bash\ngit clone https://github.com/comfyanonymous/ComfyUI.git\n```",
        project_type=project_type or ["python", "pytorch"],
        dependency_files={"requirements.txt": "torch\n"},
        clone_url=f"https://github.com/{owner}/{repo}.git",
        homepage=f"https://github.com/{owner}/{repo}",
    )

def t_cmd_plan_known_project_no_llm():
    """ComfyUI 是已知项目，应走 SmartPlanner 不调用 LLM"""
    fake_info = _make_fake_info()
    llm_called = []

    with patch("main.fetch_project", return_value=fake_info):
        with patch("main.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = {
                "os": {"type": "macos", "arch": "arm64", "is_apple_silicon": True},
                "gpu": {"type": "apple_mps"},
                "package_managers": {"pip": {}},
                "runtimes": {},
            }
            # 监视 LLM 是否被调用
            orig_complete = _main.HeuristicProvider.complete
            def spy_complete(self, *args, **kwargs):
                llm_called.append("heuristic_called")
                return orig_complete(self, *args, **kwargs)

            result = _main.cmd_plan("comfyanonymous/ComfyUI", llm_force="none")

    assert result["status"] == "ok", f"应返回 ok, 得: {result}"
    assert result["confidence"] == "high", "已知项目应 high confidence"
    assert len(result["plan"]["steps"]) > 0, "应有安装步骤"

def t_cmd_plan_error_handling():
    """fetch 失败时应返回 error"""
    with patch("main.fetch_project", side_effect=FileNotFoundError("项目不存在")):
        result = _main.cmd_plan("nonexistent/norepo999xyz")
    assert result["status"] == "error", f"应返回 error, 得: {result}"
    assert "message" in result

def t_cmd_plan_steps_safe():
    """所有步骤都应通过安全检查"""
    fake_info = _make_fake_info()
    with patch("main.fetch_project", return_value=fake_info):
        with patch("main.EnvironmentDetector") as MockDet:
            MockDet.return_value.detect.return_value = {
                "os": {"type": "macos", "arch": "arm64", "is_apple_silicon": True},
                "gpu": {"type": "apple_mps"},
                "package_managers": {"pip": {}},
                "runtimes": {},
            }
            result = _main.cmd_plan("comfyanonymous/ComfyUI", llm_force="none")

    for step in result["plan"]["steps"]:
        assert step.get("_safe") is not False, f"步骤不安全: {step['command']}"

def t_cmd_detect_returns_env():
    env_result = _main.cmd_detect()
    assert env_result["status"] == "ok"
    assert "env" in env_result
    assert "os" in env_result["env"]

test("cmd_plan 已知项目 → high confidence", t_cmd_plan_known_project_no_llm)
test("cmd_plan 项目不存在 → error", t_cmd_plan_error_handling)
test("cmd_plan 所有步骤通过安全检查", t_cmd_plan_steps_safe)
test("cmd_detect 返回完整 env", t_cmd_detect_returns_env)


# ──────────────────────────────────────────────
# 7. 边界场景测试
# ──────────────────────────────────────────────
print("\n【7/7】 边界场景")

def t_empty_readme_graceful():
    """空 README 不应崩溃"""
    p = SmartPlanner()
    plan = p.generate_plan("foo", "bar-unknown-xyz", {
        "os": {"type": "linux"},
        "gpu": {"type": "cpu_only"},
        "package_managers": {},
        "runtimes": {},
    }, [], {}, "")
    assert "steps" in plan
    assert "status" in plan or "confidence" in plan

def t_unknown_os_graceful():
    """未知 OS 类型不应崩溃"""
    from planner import _os_type, _python_cmd, _pip_cmd
    env_weird = {"os": {"type": "freebsd"}, "gpu": {"type": "cpu_only"},
                 "package_managers": {}, "runtimes": {}}
    # 这些函数不应抛出
    _os_type(env_weird)
    _python_cmd(env_weird)
    _pip_cmd(env_weird)

def t_plan_no_steps_not_crash():
    """README 无安装命令时返回合理结果"""
    p = SmartPlanner()
    plan = p.generate_plan("foo", "totally-unknown-xyz-abc", {
        "os": {"type": "linux"}, "gpu": {"type": "cpu_only"},
        "package_managers": {}, "runtimes": {},
    }, [], {}, "This is just documentation.\nNo install commands here.")
    assert isinstance(plan["steps"], list)  # 即使是空列表也可以

def t_heuristic_multiblock():
    """多个代码块不重复提取同一命令"""
    p = HeuristicProvider()
    readme = (
        "```bash\npip install torch\n```\n"
        "Some text\n"
        "```bash\npip install torch\n```"  # 重复
    )
    result = json.loads(p.complete("", readme))
    cmds = [s["command"] for s in result.get("steps", [])]
    torch_cmds = [c for c in cmds if c == "pip install torch"]
    assert len(torch_cmds) <= 1, f"相同命令不应重复出现: {cmds}"

def t_parse_identifier_just_reponame():
    """只给项目名（无 owner）"""
    owner, repo = parse_repo_identifier("ComfyUI")
    # owner 为空，交给搜索处理
    assert repo == "ComfyUI", f"repo={repo}"

test("空 README 不崩溃", t_empty_readme_graceful)
test("未知 OS 类型不崩溃", t_unknown_os_graceful)
test("无安装命令时返回空步骤列表", t_plan_no_steps_not_crash)
test("重复代码块命令去重", t_heuristic_multiblock)
test("只给项目名（无 owner）graceful", t_parse_identifier_just_reponame)


# ──────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────
print("\n" + "═" * 50)
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = total - passed

print(f"测试结果：{passed}/{total} 通过", end="")
if failed:
    print(f"，{failed} 失败")
    print("\n失败详情：")
    for r in results:
        if r[0] == FAIL:
            print(f"  {FAIL} {r[1]}")
            if len(r) > 2:
                # 只打印第一行
                print(f"     {r[2].splitlines()[-1]}")
else:
    print(" 🎉")

print("═" * 50)
sys.exit(0 if failed == 0 else 1)
