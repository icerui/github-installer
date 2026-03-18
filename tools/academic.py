"""
academic.py — 学术论文代码复现引擎
====================================

目标市场：学术论文代码复现（15万篇/年，★★★★☆）

功能：
  1. arXiv / Semantic Scholar / Papers With Code 论文元数据提取
  2. 论文→代码仓库自动关联
  3. 可复现性评分（Reproducibility Score）
  4. 环境快照 & 恢复（冻结 Python/CUDA/库版本）
  5. 实验追踪（参数、指标、结果对比）
  6. BibTeX / 引用管理

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class PaperInfo:
    """论文元数据"""
    paper_id: str = ""          # arXiv ID 或 DOI
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    published: str = ""         # ISO 日期
    categories: list[str] = field(default_factory=list)
    pdf_url: str = ""
    code_urls: list[str] = field(default_factory=list)  # GitHub 链接
    bibtex: str = ""
    source: str = ""            # "arxiv" | "semantic_scholar" | "pwc"


@dataclass
class ReproducibilityScore:
    """可复现性评分"""
    total: float = 0.0          # 0-100
    has_code: bool = False
    has_requirements: bool = False
    has_dockerfile: bool = False
    has_pretrained: bool = False
    has_data_script: bool = False
    has_readme_instructions: bool = False
    has_config_files: bool = False
    has_tests: bool = False
    has_ci: bool = False
    pinned_deps: float = 0.0    # 0-1 比例
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnvironmentSnapshot:
    """环境快照"""
    snapshot_id: str = ""
    paper_id: str = ""
    created_at: str = ""
    python_version: str = ""
    cuda_version: str = ""
    os_info: str = ""
    pip_freeze: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    gpu_info: str = ""
    notes: str = ""


@dataclass
class ExperimentRun:
    """实验运行记录"""
    run_id: str = ""
    paper_id: str = ""
    timestamp: str = ""
    command: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    duration_sec: float = 0.0
    status: str = "pending"     # pending | running | success | failed
    output_path: str = ""
    snapshot_id: str = ""
    notes: str = ""


# ─────────────────────────────────────────────
#  arXiv API
# ─────────────────────────────────────────────

_ARXIV_ID_PATTERN = re.compile(r'(\d{4}\.\d{4,5})(v\d+)?')
_ARXIV_URL_PATTERN = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})')


def parse_arxiv_id(text: str) -> str:
    """从文本中提取 arXiv ID"""
    m = _ARXIV_URL_PATTERN.search(text)
    if m:
        return m.group(1)
    m = _ARXIV_ID_PATTERN.search(text)
    if m:
        return m.group(1)
    return ""


def fetch_arxiv_paper(arxiv_id: str, timeout: int = 15) -> PaperInfo:
    """
    通过 arXiv API 获取论文信息。

    >>> info = fetch_arxiv_paper("2301.13688")
    """
    arxiv_id = parse_arxiv_id(arxiv_id) or arxiv_id
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gitinstall/1.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_text = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError):
        return PaperInfo(paper_id=arxiv_id, source="arxiv")

    return _parse_arxiv_xml(xml_text, arxiv_id)


def _parse_arxiv_xml(xml_text: str, arxiv_id: str) -> PaperInfo:
    """解析 arXiv Atom XML（纯正则，不依赖 xml 库）"""
    def _extract(tag: str, text: str) -> str:
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _extract_all(tag: str, text: str) -> list[str]:
        return [m.strip() for m in re.findall(rf'<{tag}[^>]*>(.*?)</{tag}>', text, re.DOTALL)]

    # 取第一个 entry
    entry_m = re.search(r'<entry>(.*?)</entry>', xml_text, re.DOTALL)
    if not entry_m:
        return PaperInfo(paper_id=arxiv_id, source="arxiv")

    entry = entry_m.group(1)

    title = _extract("title", entry)
    title = re.sub(r'\s+', ' ', title)  # 去除多余空白

    abstract = _extract("summary", entry)
    abstract = re.sub(r'\s+', ' ', abstract)

    authors = []
    for author_block in re.findall(r'<author>(.*?)</author>', entry, re.DOTALL):
        name = _extract("name", author_block)
        if name:
            authors.append(name)

    published = _extract("published", entry)[:10]  # YYYY-MM-DD

    categories = re.findall(r'<category[^>]*term="([^"]+)"', entry)

    # 提取 PDF 链接
    pdf_url = ""
    for link in re.findall(r'<link[^>]*>', entry):
        if 'title="pdf"' in link:
            m2 = re.search(r'href="([^"]+)"', link)
            if m2:
                pdf_url = m2.group(1)

    if not pdf_url:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    # 从摘要中提取 GitHub 链接
    code_urls = re.findall(r'https?://github\.com/[\w.-]+/[\w.-]+', abstract)

    # 生成 BibTeX
    first_author = authors[0].split()[-1] if authors else "unknown"
    year = published[:4] if published else "2024"
    bibtex = (
        f"@article{{{first_author}{year}arxiv,\n"
        f"  title={{{title}}},\n"
        f"  author={{{' and '.join(authors)}}},\n"
        f"  journal={{arXiv preprint arXiv:{arxiv_id}}},\n"
        f"  year={{{year}}}\n"
        f"}}"
    )

    return PaperInfo(
        paper_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
        categories=categories,
        pdf_url=pdf_url,
        code_urls=code_urls,
        bibtex=bibtex,
        source="arxiv",
    )


# ─────────────────────────────────────────────
#  Papers With Code 关联
# ─────────────────────────────────────────────

def search_papers_with_code(arxiv_id: str, timeout: int = 10) -> list[str]:
    """
    通过 Papers With Code API 查找论文关联的代码仓库。

    返回 GitHub URL 列表。
    """
    url = f"https://paperswithcode.com/api/v1/papers/?arxiv_id={arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gitinstall/1.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []

    repos = []
    results = data.get("results", [])
    if not results:
        return repos

    paper_id = results[0].get("id", "")
    if not paper_id:
        return repos

    # 查询关联仓库
    repo_url = f"https://paperswithcode.com/api/v1/papers/{paper_id}/repositories/"
    try:
        req = urllib.request.Request(repo_url, headers={"User-Agent": "gitinstall/1.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            repo_data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return repos

    for r in repo_data.get("results", []):
        gh_url = r.get("url", "")
        if "github.com" in gh_url:
            repos.append(gh_url)

    return repos


# ─────────────────────────────────────────────
#  可复现性评分
# ─────────────────────────────────────────────

def score_reproducibility(project_dir: str) -> ReproducibilityScore:
    """
    对项目目录评分，判断论文代码的可复现性。

    评分维度（满分100）：
      - 有代码仓库: 15分
      - 有 requirements/依赖声明: 15分
      - 有 Dockerfile: 10分
      - 有预训练模型链接 / weights: 10分
      - 有数据下载脚本: 10分
      - README 有安装/运行说明: 10分
      - 有配置文件(yaml/json/toml): 5分
      - 有测试: 10分
      - 有 CI 配置: 5分
      - 依赖版本固定比例: 10分
    """
    score = ReproducibilityScore(has_code=True)
    total = 15.0  # 有代码本身

    root = Path(project_dir)
    if not root.is_dir():
        return ReproducibilityScore()

    # 依赖声明
    dep_files = [
        "requirements.txt", "setup.py", "pyproject.toml", "setup.cfg",
        "environment.yml", "Pipfile", "package.json", "Cargo.toml", "go.mod",
    ]
    for f in dep_files:
        if (root / f).exists():
            score.has_requirements = True
            total += 15
            break

    # Dockerfile
    for f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".devcontainer/devcontainer.json"):
        if (root / f).exists():
            score.has_dockerfile = True
            total += 10
            break

    # 预训练模型
    readme_text = ""
    for rname in ("README.md", "README.rst", "README.txt", "README"):
        rpath = root / rname
        if rpath.exists():
            try:
                readme_text = rpath.read_text(encoding="utf-8", errors="ignore")[:50000]
            except OSError:
                pass
            break

    all_text = readme_text
    for pyf in root.glob("**/*.py"):
        if pyf.stat().st_size < 100000:
            try:
                all_text += "\n" + pyf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
        if len(all_text) > 500000:
            break

    pretrained_patterns = [
        r'huggingface\.co/', r'drive\.google\.com', r'\.ckpt', r'\.safetensors',
        r'pretrained', r'model\.pth', r'weights/', r'checkpoint',
        r'from_pretrained', r'load_state_dict',
    ]
    for pat in pretrained_patterns:
        if re.search(pat, all_text, re.IGNORECASE):
            score.has_pretrained = True
            total += 10
            break

    # 数据下载脚本
    data_patterns = [
        r'download.*data', r'prepare.*dataset', r'fetch.*data',
        r'wget\s+.*\.tar', r'curl\s+.*\.zip',
    ]
    data_files = ["download_data.sh", "prepare_data.py", "data/download.sh", "scripts/download.sh"]
    for f in data_files:
        if (root / f).exists():
            score.has_data_script = True
            total += 10
            break
    if not score.has_data_script:
        for pat in data_patterns:
            if re.search(pat, all_text, re.IGNORECASE):
                score.has_data_script = True
                total += 10
                break

    # README 安装说明
    install_patterns = [
        r'##.*install', r'##.*setup', r'##.*getting.started',
        r'##.*usage', r'##.*quick.start', r'pip install',
        r'conda install', r'npm install',
    ]
    for pat in install_patterns:
        if re.search(pat, readme_text, re.IGNORECASE):
            score.has_readme_instructions = True
            total += 10
            break

    # 配置文件
    config_patterns = ["*.yaml", "*.yml", "*.toml", "*.json", "*.cfg", "*.ini"]
    config_dirs = ["configs", "config", "conf"]
    for cd in config_dirs:
        if (root / cd).is_dir():
            score.has_config_files = True
            total += 5
            break
    if not score.has_config_files:
        for pat in config_patterns:
            matches = list(root.glob(pat))
            # 排除 package.json 等非配置文件
            real_configs = [m for m in matches if m.name not in (
                "package.json", "package-lock.json", "tsconfig.json",
                "pyproject.toml", "Cargo.toml",
            )]
            if real_configs:
                score.has_config_files = True
                total += 5
                break

    # 测试
    test_indicators = ["tests/", "test/", "test_*.py", "*_test.py", "*_test.go", "*.test.js", "*.test.ts"]
    for pat in test_indicators:
        if list(root.glob(pat)):
            score.has_tests = True
            total += 10
            break

    # CI
    ci_files = [
        ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
        ".circleci", ".travis.yml", "azure-pipelines.yml",
    ]
    for f in ci_files:
        if (root / f).exists():
            score.has_ci = True
            total += 5
            break

    # 依赖版本固定比例
    pinned = _check_pinned_deps(root)
    score.pinned_deps = pinned
    total += pinned * 10

    score.total = min(total, 100.0)
    return score


def _check_pinned_deps(root: Path) -> float:
    """检查依赖版本固定比例"""
    req_path = root / "requirements.txt"
    if not req_path.exists():
        return 0.0

    try:
        content = req_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0.0

    total_deps = 0
    pinned_deps = 0
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        total_deps += 1
        if "==" in line:
            pinned_deps += 1

    return pinned_deps / total_deps if total_deps > 0 else 0.0


def format_reproducibility_score(score: ReproducibilityScore) -> str:
    """格式化可复现性评分"""
    if score.total >= 80:
        grade = "A (优秀)"
    elif score.total >= 60:
        grade = "B (良好)"
    elif score.total >= 40:
        grade = "C (一般)"
    elif score.total >= 20:
        grade = "D (较差)"
    else:
        grade = "F (极差)"

    checks = [
        ("📦 代码仓库", score.has_code),
        ("📋 依赖声明", score.has_requirements),
        ("🐳 Dockerfile", score.has_dockerfile),
        ("🧠 预训练权重", score.has_pretrained),
        ("📊 数据下载", score.has_data_script),
        ("📖 安装说明", score.has_readme_instructions),
        ("⚙️ 配置文件", score.has_config_files),
        ("🧪 测试用例", score.has_tests),
        ("🔄 CI 配置", score.has_ci),
    ]

    lines = [
        f"📊 可复现性评分: {score.total:.0f}/100 [{grade}]",
        "",
    ]
    for label, ok in checks:
        lines.append(f"  {label}: {'✅' if ok else '❌'}")
    lines.append(f"  📌 依赖固定率: {score.pinned_deps:.0%}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  环境快照
# ─────────────────────────────────────────────

_SNAPSHOT_DIR = os.path.expanduser("~/.gitinstall/snapshots")


def create_snapshot(paper_id: str = "", notes: str = "") -> EnvironmentSnapshot:
    """
    创建当前环境快照。

    捕获 Python 版本、CUDA 版本、pip freeze、环境变量等。
    """
    import platform
    import subprocess
    import sys

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap_id = hashlib.sha256(f"{now}-{paper_id}".encode()).hexdigest()[:12]

    # pip freeze
    pip_freeze = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--local"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            pip_freeze = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # CUDA 版本
    cuda_version = ""
    try:
        result = subprocess.run(
            ["nvcc", "--version"], capture_output=True, text=True, timeout=5,
        )
        m = re.search(r'release (\d+\.\d+)', result.stdout)
        if m:
            cuda_version = m.group(1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        cuda_version = os.environ.get("CUDA_VERSION", "")

    # GPU 信息
    gpu_info = ""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_info = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # macOS Apple Silicon
        if platform.machine() == "arm64" and platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=5,
                )
                gpu_info = f"Apple Silicon ({result.stdout.strip()})"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    # 安全过滤环境变量（只保留开发相关的，不泄露密钥）
    safe_env_prefixes = (
        "PYTHON", "CUDA", "CONDA", "VIRTUAL_ENV", "PATH",
        "LD_LIBRARY", "DYLD_", "CC", "CXX", "CMAKE",
    )
    env_vars = {}
    for k, v in os.environ.items():
        if any(k.startswith(p) for p in safe_env_prefixes):
            # 不记录包含 KEY/TOKEN/SECRET/PASSWORD 的变量
            if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                env_vars[k] = v

    snap = EnvironmentSnapshot(
        snapshot_id=snap_id,
        paper_id=paper_id,
        created_at=now,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        cuda_version=cuda_version,
        os_info=f"{platform.system()} {platform.release()} ({platform.machine()})",
        pip_freeze=pip_freeze,
        env_vars=env_vars,
        gpu_info=gpu_info,
        notes=notes,
    )

    # 保存到磁盘
    _save_snapshot(snap)
    return snap


def _save_snapshot(snap: EnvironmentSnapshot) -> str:
    """保存快照到文件"""
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(_SNAPSHOT_DIR, f"{snap.snapshot_id}.json")
    data = {
        "snapshot_id": snap.snapshot_id,
        "paper_id": snap.paper_id,
        "created_at": snap.created_at,
        "python_version": snap.python_version,
        "cuda_version": snap.cuda_version,
        "os_info": snap.os_info,
        "pip_freeze": snap.pip_freeze,
        "env_vars": snap.env_vars,
        "gpu_info": snap.gpu_info,
        "notes": snap.notes,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def load_snapshot(snapshot_id: str) -> EnvironmentSnapshot:
    """加载快照"""
    path = os.path.join(_SNAPSHOT_DIR, f"{snapshot_id}.json")
    if not os.path.isfile(path):
        return EnvironmentSnapshot()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return EnvironmentSnapshot(**data)


def list_snapshots() -> list[dict]:
    """列出所有快照"""
    if not os.path.isdir(_SNAPSHOT_DIR):
        return []
    result = []
    for fname in sorted(os.listdir(_SNAPSHOT_DIR)):
        if fname.endswith(".json"):
            path = os.path.join(_SNAPSHOT_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "snapshot_id": data.get("snapshot_id", ""),
                    "paper_id": data.get("paper_id", ""),
                    "created_at": data.get("created_at", ""),
                    "python_version": data.get("python_version", ""),
                })
            except (json.JSONDecodeError, OSError):
                pass
    return result


def generate_restore_commands(snapshot_id: str) -> list[str]:
    """生成恢复环境的命令列表"""
    snap = load_snapshot(snapshot_id)
    if not snap.snapshot_id:
        return [f"# 快照 {snapshot_id} 不存在"]

    cmds = [
        f"# 环境恢复 — 快照 {snap.snapshot_id}",
        f"# 创建于: {snap.created_at}",
        f"# Python: {snap.python_version}",
        "",
    ]

    if snap.cuda_version:
        cmds.append(f"# CUDA: {snap.cuda_version}")

    cmds.extend([
        f"python -m venv .venv-{snap.snapshot_id}",
        f"source .venv-{snap.snapshot_id}/bin/activate",
    ])

    if snap.pip_freeze:
        req_file = f"requirements-{snap.snapshot_id}.txt"
        cmds.append(f"cat > {req_file} << 'EOF'")
        cmds.extend(snap.pip_freeze)
        cmds.append("EOF")
        cmds.append(f"pip install -r {req_file}")

    return cmds


# ─────────────────────────────────────────────
#  实验追踪
# ─────────────────────────────────────────────

_EXPERIMENTS_DIR = os.path.expanduser("~/.gitinstall/experiments")


def log_experiment(
    paper_id: str,
    command: str,
    params: dict[str, Any] | None = None,
    metrics: dict[str, float] | None = None,
    duration_sec: float = 0.0,
    status: str = "success",
    output_path: str = "",
    snapshot_id: str = "",
    notes: str = "",
) -> ExperimentRun:
    """记录一次实验运行"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = hashlib.sha256(f"{now}-{paper_id}-{command}".encode()).hexdigest()[:12]

    run = ExperimentRun(
        run_id=run_id,
        paper_id=paper_id,
        timestamp=now,
        command=command,
        params=params or {},
        metrics=metrics or {},
        duration_sec=duration_sec,
        status=status,
        output_path=output_path,
        snapshot_id=snapshot_id,
        notes=notes,
    )

    # 持久化
    paper_dir = os.path.join(_EXPERIMENTS_DIR, paper_id.replace("/", "_"))
    os.makedirs(paper_dir, exist_ok=True)
    path = os.path.join(paper_dir, f"{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run.run_id,
            "paper_id": run.paper_id,
            "timestamp": run.timestamp,
            "command": run.command,
            "params": run.params,
            "metrics": run.metrics,
            "duration_sec": run.duration_sec,
            "status": run.status,
            "output_path": run.output_path,
            "snapshot_id": run.snapshot_id,
            "notes": run.notes,
        }, f, indent=2, ensure_ascii=False)

    return run


def list_experiments(paper_id: str) -> list[ExperimentRun]:
    """列出论文的所有实验"""
    paper_dir = os.path.join(_EXPERIMENTS_DIR, paper_id.replace("/", "_"))
    if not os.path.isdir(paper_dir):
        return []

    runs = []
    for fname in sorted(os.listdir(paper_dir)):
        if fname.endswith(".json"):
            path = os.path.join(paper_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                runs.append(ExperimentRun(**data))
            except (json.JSONDecodeError, OSError, TypeError):
                pass
    return runs


def compare_experiments(paper_id: str) -> str:
    """对比论文的所有实验结果"""
    runs = list_experiments(paper_id)
    if not runs:
        return f"论文 {paper_id} 没有实验记录"

    # 收集所有指标
    all_metrics = set()
    for r in runs:
        all_metrics.update(r.metrics.keys())
    all_metrics = sorted(all_metrics)

    lines = [
        f"📊 实验对比 — {paper_id}",
        f"   共 {len(runs)} 次实验",
        "",
        "运行ID     | 状态    | 耗时     | " + " | ".join(all_metrics),
        "-" * (50 + 15 * len(all_metrics)),
    ]

    for r in runs:
        duration_str = f"{r.duration_sec:.1f}s" if r.duration_sec else "N/A"
        metric_vals = [f"{r.metrics.get(m, '-')}" for m in all_metrics]
        lines.append(
            f"{r.run_id:10s} | {r.status:7s} | {duration_str:8s} | " +
            " | ".join(f"{v:>12s}" for v in metric_vals)
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  论文→安装 一键流水线
# ─────────────────────────────────────────────

def paper_to_install_plan(paper_input: str) -> dict:
    """
    从论文 ID/URL 生成安装计划。

    流程：
      1. 解析 arXiv ID
      2. 获取论文元数据
      3. 查找关联代码仓库（Papers With Code）
      4. 返回 repo URL + 论文信息

    Args:
        paper_input: arXiv ID、URL、或论文标题

    Returns:
        {"paper": PaperInfo, "repos": [...], "suggested_repo": "...", "install_cmd": "..."}
    """
    arxiv_id = parse_arxiv_id(paper_input)
    if not arxiv_id:
        return {
            "error": f"无法识别 arXiv ID: {paper_input}",
            "hint": "请输入 arXiv ID (如 2301.13688) 或 URL",
        }

    paper = fetch_arxiv_paper(arxiv_id)

    # 查找代码仓库
    repos = list(paper.code_urls)  # 从摘要中提取的
    pwc_repos = search_papers_with_code(arxiv_id)
    for r in pwc_repos:
        if r not in repos:
            repos.append(r)

    suggested = repos[0] if repos else ""

    # 生成安装命令
    install_cmd = ""
    if suggested:
        # 提取 owner/repo
        m = re.search(r'github\.com/([\w.-]+/[\w.-]+)', suggested)
        if m:
            install_cmd = f"gitinstall install {m.group(1)}"

    return {
        "paper": {
            "id": paper.paper_id,
            "title": paper.title,
            "authors": paper.authors,
            "published": paper.published,
            "categories": paper.categories,
            "pdf_url": paper.pdf_url,
            "bibtex": paper.bibtex,
        },
        "repos": repos,
        "suggested_repo": suggested,
        "install_cmd": install_cmd,
    }


def format_paper_info(paper: PaperInfo) -> str:
    """格式化论文信息"""
    lines = [
        f"📄 {paper.title}",
        f"   作者: {', '.join(paper.authors[:5])}" + (" 等" if len(paper.authors) > 5 else ""),
        f"   发布: {paper.published}",
        f"   分类: {', '.join(paper.categories[:3])}",
    ]
    if paper.pdf_url:
        lines.append(f"   PDF: {paper.pdf_url}")
    if paper.code_urls:
        lines.append(f"   代码: {', '.join(paper.code_urls)}")
    return "\n".join(lines)
