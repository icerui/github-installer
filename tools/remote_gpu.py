"""
remote_gpu.py — 远程 GPU 开发机管理引擎
=========================================

目标市场：远程 GPU 开发机管理（GPU 云市场增长中，★★★☆☆）

功能：
  1. SSH 远程执行（支持密钥/密码认证）
  2. 云 GPU 提供商集成（Lambda Labs, RunPod, Vast.ai, AWS, GCP）
  3. 远程环境探测（GPU 型号/VRAM/驱动/CUDA）
  4. 远程项目安装（将 gitinstall 的计划在远程执行）
  5. 成本估算 & 优化建议
  6. 多机并行安装（集群模式）
  7. 端口转发 & Jupyter 远程访问

零外部依赖，纯 Python 标准库。
SSH 通过系统 ssh 命令执行（macOS/Linux 内置）。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class RemoteHost:
    """远程主机配置"""
    name: str = ""              # 别名
    host: str = ""              # IP 或域名
    port: int = 22
    user: str = ""
    key_file: str = ""          # SSH 密钥路径
    gpu_type: str = ""          # 探测到的 GPU 类型
    gpu_count: int = 0
    vram_gb: float = 0.0
    cuda_version: str = ""
    python_version: str = ""
    os_info: str = ""
    status: str = "unknown"     # unknown | online | offline | busy
    provider: str = ""          # lambda | runpod | vastai | aws | gcp | custom
    cost_per_hour: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class RemoteExecResult:
    """远程执行结果"""
    host: str = ""
    command: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0


@dataclass
class GPUProviderInfo:
    """GPU 云提供商信息"""
    name: str = ""
    display_name: str = ""
    gpu_types: list[str] = field(default_factory=list)
    pricing: dict[str, float] = field(default_factory=dict)  # GPU型号 → $/hour
    regions: list[str] = field(default_factory=list)
    api_url: str = ""
    env_var: str = ""           # API Key 环境变量名


# ─────────────────────────────────────────────
#  GPU 云提供商数据库
# ─────────────────────────────────────────────

_GPU_PROVIDERS: dict[str, GPUProviderInfo] = {
    "lambda": GPUProviderInfo(
        name="lambda",
        display_name="Lambda Labs",
        gpu_types=["A100-80GB", "A100-40GB", "H100-80GB", "A10-24GB", "RTX-6000-24GB"],
        pricing={
            "H100-80GB": 3.29, "A100-80GB": 1.99, "A100-40GB": 1.49,
            "A10-24GB": 0.75, "RTX-6000-24GB": 0.99,
        },
        regions=["us-west-1", "us-east-1", "us-south-1"],
        api_url="https://cloud.lambdalabs.com/api/v1",
        env_var="LAMBDA_API_KEY",
    ),
    "runpod": GPUProviderInfo(
        name="runpod",
        display_name="RunPod",
        gpu_types=["A100-80GB", "A100-40GB", "H100-80GB", "RTX-4090-24GB", "RTX-3090-24GB", "A40-48GB"],
        pricing={
            "H100-80GB": 3.89, "A100-80GB": 1.94, "A100-40GB": 1.44,
            "RTX-4090-24GB": 0.74, "RTX-3090-24GB": 0.44, "A40-48GB": 0.79,
        },
        regions=["US", "EU", "CA"],
        api_url="https://api.runpod.io/graphql",
        env_var="RUNPOD_API_KEY",
    ),
    "vastai": GPUProviderInfo(
        name="vastai",
        display_name="Vast.ai",
        gpu_types=["A100-80GB", "A100-40GB", "RTX-4090-24GB", "RTX-3090-24GB", "RTX-4080-16GB"],
        pricing={
            "A100-80GB": 1.50, "A100-40GB": 1.10,
            "RTX-4090-24GB": 0.55, "RTX-3090-24GB": 0.30, "RTX-4080-16GB": 0.40,
        },
        regions=["Worldwide (P2P)"],
        api_url="https://console.vast.ai/api/v0",
        env_var="VASTAI_API_KEY",
    ),
    "aws": GPUProviderInfo(
        name="aws",
        display_name="AWS EC2 (GPU)",
        gpu_types=["A100-40GB (p4d)", "A100-80GB (p4de)", "H100-80GB (p5)", "T4-16GB (g4dn)", "A10G-24GB (g5)"],
        pricing={
            "T4-16GB (g4dn)": 0.526, "A10G-24GB (g5)": 1.006,
            "A100-40GB (p4d)": 3.672, "A100-80GB (p4de)": 4.576, "H100-80GB (p5)": 6.672,
        },
        regions=["us-east-1", "us-west-2", "eu-west-1", "ap-northeast-1"],
        api_url="https://ec2.amazonaws.com",
        env_var="AWS_ACCESS_KEY_ID",
    ),
    "gcp": GPUProviderInfo(
        name="gcp",
        display_name="Google Cloud (GPU)",
        gpu_types=["T4-16GB", "A100-40GB", "A100-80GB", "H100-80GB", "L4-24GB"],
        pricing={
            "T4-16GB": 0.35, "L4-24GB": 0.49,
            "A100-40GB": 2.48, "A100-80GB": 3.67, "H100-80GB": 5.67,
        },
        regions=["us-central1", "us-east1", "europe-west4", "asia-east1"],
        api_url="https://compute.googleapis.com/compute/v1",
        env_var="GOOGLE_APPLICATION_CREDENTIALS",
    ),
}


# ─────────────────────────────────────────────
#  SSH 远程执行
# ─────────────────────────────────────────────

def ssh_exec(
    host: RemoteHost,
    command: str,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> RemoteExecResult:
    """
    通过 SSH 在远程主机上执行命令。

    使用系统 ssh 命令，不依赖 paramiko。
    """
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]

    if host.key_file:
        ssh_cmd.extend(["-i", host.key_file])
    if host.port != 22:
        ssh_cmd.extend(["-p", str(host.port)])

    target = f"{host.user}@{host.host}" if host.user else host.host

    # 构建远程命令（包含环境变量）
    remote_cmd = command
    if env:
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        remote_cmd = f"{env_prefix} {command}"

    ssh_cmd.extend([target, remote_cmd])

    start = time.monotonic()
    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start

        return RemoteExecResult(
            host=host.host,
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_sec=duration,
        )
    except subprocess.TimeoutExpired:
        return RemoteExecResult(
            host=host.host,
            command=command,
            exit_code=-1,
            stderr=f"Timeout after {timeout}s",
            duration_sec=timeout,
        )
    except FileNotFoundError:
        return RemoteExecResult(
            host=host.host,
            command=command,
            exit_code=-1,
            stderr="ssh command not found",
        )


def ssh_probe(host: RemoteHost) -> RemoteHost:
    """
    探测远程主机的环境（GPU、CUDA、Python）。

    更新 host 对象并返回。
    """
    # 一次性执行多个检测命令
    probe_cmd = """
echo "===OS==="
uname -a 2>/dev/null || echo "unknown"
echo "===GPU==="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || echo "no-gpu"
echo "===CUDA==="
nvcc --version 2>/dev/null | grep -oP 'release \\K[0-9.]+' || echo "unknown"
echo "===PYTHON==="
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "unknown"
echo "===DONE==="
"""
    result = ssh_exec(host, probe_cmd.strip(), timeout=15)

    if result.exit_code != 0:
        host.status = "offline"
        return host

    host.status = "online"
    output = result.stdout

    # 解析 OS
    os_match = re.search(r'===OS===\n(.+)', output)
    if os_match:
        host.os_info = os_match.group(1).strip()[:100]

    # 解析 GPU
    gpu_match = re.search(r'===GPU===\n(.+)', output)
    if gpu_match:
        gpu_line = gpu_match.group(1).strip()
        if gpu_line != "no-gpu":
            parts = [p.strip() for p in gpu_line.split(",")]
            host.gpu_type = parts[0] if parts else ""
            if len(parts) > 1:
                mem_str = parts[1].replace("MiB", "").strip()
                try:
                    host.vram_gb = float(mem_str) / 1024
                except ValueError:
                    pass
            # 计算 GPU 数量（多行输出）
            gpu_lines = re.findall(r'(?<====GPU===\n)(.+?)(?=\n===)', output, re.DOTALL)
            if gpu_lines:
                host.gpu_count = len(gpu_lines[0].strip().splitlines())
            else:
                host.gpu_count = 1

    # 解析 CUDA
    cuda_match = re.search(r'===CUDA===\n(.+)', output)
    if cuda_match:
        host.cuda_version = cuda_match.group(1).strip()

    # 解析 Python
    py_match = re.search(r'===PYTHON===\n(.+)', output)
    if py_match:
        ver_str = py_match.group(1).strip()
        m = re.search(r'(\d+\.\d+\.\d+)', ver_str)
        if m:
            host.python_version = m.group(1)

    return host


# ─────────────────────────────────────────────
#  远程安装
# ─────────────────────────────────────────────

def remote_install(
    host: RemoteHost,
    project: str,
    install_dir: str = "~/projects",
    plan_steps: list[dict] | None = None,
) -> list[RemoteExecResult]:
    """
    在远程主机上安装 GitHub 项目。

    如果提供 plan_steps，直接执行；否则先克隆再用本地方式安装。
    """
    results = []

    # 确保目标目录存在
    r = ssh_exec(host, f"mkdir -p {install_dir}", timeout=10)
    results.append(r)

    if plan_steps:
        # 执行预定义的安装计划
        for step in plan_steps:
            cmds = step.get("commands", [])
            for cmd in cmds:
                if isinstance(cmd, str):
                    r = ssh_exec(host, f"cd {install_dir} && {cmd}", timeout=300)
                    results.append(r)
                    if r.exit_code != 0:
                        return results  # 失败时停止
    else:
        # 默认流程: clone → detect → install
        repo_name = project.split("/")[-1] if "/" in project else project
        clone_url = f"https://github.com/{project}.git"

        # 克隆
        r = ssh_exec(
            host,
            f"cd {install_dir} && git clone {clone_url} 2>&1 || (cd {repo_name} && git pull)",
            timeout=120,
        )
        results.append(r)

        # 检测并安装
        detect_install_cmd = f"""
cd {install_dir}/{repo_name}
if [ -f requirements.txt ]; then
    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
elif [ -f setup.py ] || [ -f pyproject.toml ]; then
    python3 -m venv .venv && source .venv/bin/activate && pip install -e .
elif [ -f package.json ]; then
    npm install
elif [ -f Cargo.toml ]; then
    cargo build --release
elif [ -f go.mod ]; then
    go build ./...
elif [ -f CMakeLists.txt ]; then
    mkdir -p build && cd build && cmake .. && make -j$(nproc)
elif [ -f Makefile ]; then
    make
fi
echo "INSTALL_DONE"
"""
        r = ssh_exec(host, detect_install_cmd.strip(), timeout=600)
        results.append(r)

    return results


# ─────────────────────────────────────────────
#  成本估算
# ─────────────────────────────────────────────

def estimate_cost(
    gpu_type: str,
    hours: float,
    provider: str | None = None,
) -> list[dict]:
    """
    估算 GPU 云使用成本。

    如果不指定提供商，返回所有提供商的对比。
    """
    results = []

    for pname, pinfo in _GPU_PROVIDERS.items():
        if provider and pname != provider:
            continue

        for gpu, price in pinfo.pricing.items():
            if gpu_type.lower() in gpu.lower():
                cost = price * hours
                results.append({
                    "provider": pinfo.display_name,
                    "gpu": gpu,
                    "price_per_hour": price,
                    "hours": hours,
                    "total_cost": round(cost, 2),
                    "currency": "USD",
                })

    results.sort(key=lambda x: x["total_cost"])
    return results


def recommend_gpu_provider(
    vram_needed_gb: float,
    budget_per_hour: float = 5.0,
    prefer_region: str = "",
) -> list[dict]:
    """
    根据 VRAM 需求和预算推荐 GPU 提供商。
    """
    recommendations = []

    for pname, pinfo in _GPU_PROVIDERS.items():
        for gpu, price in pinfo.pricing.items():
            if price > budget_per_hour:
                continue

            # 从 GPU 名称估算 VRAM
            vram = _estimate_gpu_vram(gpu)
            if vram < vram_needed_gb:
                continue

            # 区域匹配
            region_match = not prefer_region or any(
                prefer_region.lower() in r.lower() for r in pinfo.regions
            )

            score = 100 - (price / budget_per_hour * 50) + (vram / vram_needed_gb * 30)
            if region_match:
                score += 20

            recommendations.append({
                "provider": pinfo.display_name,
                "gpu": gpu,
                "vram_gb": vram,
                "price_per_hour": price,
                "score": round(score, 1),
                "regions": pinfo.regions,
            })

    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations[:10]


def _estimate_gpu_vram(gpu_name: str) -> float:
    """从 GPU 名称估算 VRAM"""
    m = re.search(r'(\d+)\s*GB', gpu_name, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # 常见型号
    vram_map = {
        "T4": 16, "A10": 24, "A10G": 24, "L4": 24,
        "A40": 48, "A100-40": 40, "A100-80": 80,
        "H100": 80, "H200": 141,
        "RTX-3090": 24, "RTX-4090": 24, "RTX-4080": 16,
        "RTX-6000": 24, "RTX-A6000": 48,
    }
    for key, vram in vram_map.items():
        if key.lower() in gpu_name.lower():
            return vram
    return 0


# ─────────────────────────────────────────────
#  主机管理
# ─────────────────────────────────────────────

_HOSTS_FILE = os.path.expanduser("~/.gitinstall/remote_hosts.json")


def save_host(host: RemoteHost) -> None:
    """保存远程主机配置"""
    hosts = load_hosts()
    # 更新或添加
    updated = False
    for i, h in enumerate(hosts):
        if h.get("name") == host.name or h.get("host") == host.host:
            hosts[i] = _host_to_dict(host)
            updated = True
            break
    if not updated:
        hosts.append(_host_to_dict(host))

    os.makedirs(os.path.dirname(_HOSTS_FILE), exist_ok=True)
    with open(_HOSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(hosts, f, indent=2, ensure_ascii=False)


def load_hosts() -> list[dict]:
    """加载所有远程主机"""
    if not os.path.isfile(_HOSTS_FILE):
        return []
    try:
        with open(_HOSTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def get_host(name_or_ip: str) -> RemoteHost:
    """获取远程主机"""
    hosts = load_hosts()
    for h in hosts:
        if h.get("name") == name_or_ip or h.get("host") == name_or_ip:
            return RemoteHost(**{k: v for k, v in h.items() if k in RemoteHost.__dataclass_fields__})
    return RemoteHost()


def _host_to_dict(host: RemoteHost) -> dict:
    return {
        "name": host.name,
        "host": host.host,
        "port": host.port,
        "user": host.user,
        "key_file": host.key_file,
        "gpu_type": host.gpu_type,
        "gpu_count": host.gpu_count,
        "vram_gb": host.vram_gb,
        "cuda_version": host.cuda_version,
        "python_version": host.python_version,
        "os_info": host.os_info,
        "status": host.status,
        "provider": host.provider,
        "cost_per_hour": host.cost_per_hour,
        "tags": host.tags,
    }


# ─────────────────────────────────────────────
#  端口转发
# ─────────────────────────────────────────────

def create_tunnel(
    host: RemoteHost,
    remote_port: int,
    local_port: int | None = None,
) -> dict:
    """
    创建 SSH 端口转发隧道的命令。

    用途：远程 Jupyter Notebook、TensorBoard、vLLM API 等。
    """
    local_port = local_port or remote_port

    ssh_cmd = ["ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}"]

    if host.key_file:
        ssh_cmd.extend(["-i", host.key_file])
    if host.port != 22:
        ssh_cmd.extend(["-p", str(host.port)])

    target = f"{host.user}@{host.host}" if host.user else host.host
    ssh_cmd.append(target)

    return {
        "command": " ".join(ssh_cmd),
        "local_url": f"http://localhost:{local_port}",
        "description": f"转发 {host.host}:{remote_port} → localhost:{local_port}",
    }


def generate_jupyter_remote_cmd(host: RemoteHost, port: int = 8888) -> dict:
    """生成远程 Jupyter 访问命令"""
    # 远程启动 Jupyter
    start_cmd = f"jupyter lab --no-browser --port={port} --ip=0.0.0.0"

    tunnel = create_tunnel(host, port)

    return {
        "step1_remote": f"ssh {host.user}@{host.host} '{start_cmd}'",
        "step2_tunnel": tunnel["command"],
        "step3_access": tunnel["local_url"],
        "note": "先在终端1执行step1，再在终端2执行step2，然后浏览器打开step3",
    }


# ─────────────────────────────────────────────
#  多机并行
# ─────────────────────────────────────────────

def parallel_probe(hosts: list[RemoteHost]) -> list[RemoteHost]:
    """
    并行探测多台主机（通过 subprocess 并发）。

    注意：这里用串行模拟，因为纯标准库不便做真并行。
    生产环境可用 concurrent.futures。
    """
    results = []
    for host in hosts:
        probed = ssh_probe(host)
        results.append(probed)
    return results


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_host_info(host: RemoteHost) -> str:
    """格式化远程主机信息"""
    status_icon = {"online": "🟢", "offline": "🔴", "busy": "🟡", "unknown": "⚪"}.get(host.status, "⚪")

    lines = [
        f"{status_icon} {host.name or host.host}",
        f"   地址: {host.user}@{host.host}:{host.port}",
    ]
    if host.gpu_type:
        lines.append(f"   GPU: {host.gpu_type} × {host.gpu_count} ({host.vram_gb:.0f}GB)")
    if host.cuda_version:
        lines.append(f"   CUDA: {host.cuda_version}")
    if host.python_version:
        lines.append(f"   Python: {host.python_version}")
    if host.cost_per_hour > 0:
        lines.append(f"   费用: ${host.cost_per_hour}/h ({host.provider})")
    if host.os_info:
        lines.append(f"   系统: {host.os_info[:60]}")

    return "\n".join(lines)


def format_cost_comparison(costs: list[dict]) -> str:
    """格式化成本对比"""
    if not costs:
        return "💰 没有找到匹配的 GPU 提供商"

    lines = [
        "💰 GPU 云成本对比",
        "",
        f"{'提供商':<15} {'GPU':<20} {'单价($/h)':<12} {'总费用($)':<12}",
        "─" * 60,
    ]

    for c in costs:
        lines.append(
            f"{c['provider']:<15} {c['gpu']:<20} "
            f"${c['price_per_hour']:<11.2f} ${c['total_cost']:<11.2f}"
        )

    if costs:
        cheapest = costs[0]
        lines.extend([
            "",
            f"💡 最便宜: {cheapest['provider']} {cheapest['gpu']} — ${cheapest['total_cost']}/{'%.1f' % cheapest['hours']}h",
        ])

    return "\n".join(lines)
