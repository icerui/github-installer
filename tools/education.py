"""
education.py — 教育科技集成引擎
================================

目标市场：教育科技（~$35亿，★★★☆☆）

功能：
  1. 课堂模式（Classroom Mode）— 批量配置学生环境
  2. 作业分发 & 回收（基于 GitHub Classroom 模式）
  3. 分步引导安装（Step-by-step Guided Install）
  4. 项目难度评级（Difficulty Rating）
  5. 学习路径生成（Learning Path）
  6. Jupyter Notebook 集成
  7. 进度追踪 Dashboard

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class DifficultyRating:
    """项目难度评级"""
    level: int = 1              # 1-5 星
    label: str = "初学者"       # 初学者/基础/中级/进阶/专家
    factors: dict[str, int] = field(default_factory=dict)
    setup_time_min: int = 5     # 预估安装时间(分钟)
    prereqs: list[str] = field(default_factory=list)  # 前置知识
    explanation: str = ""


@dataclass
class GuidedStep:
    """引导安装步骤"""
    step_num: int = 0
    title: str = ""
    description: str = ""
    command: str = ""
    expected_output: str = ""
    troubleshooting: list[str] = field(default_factory=list)
    is_optional: bool = False
    checkpoint: str = ""        # 验证命令


@dataclass
class ClassroomConfig:
    """课堂配置"""
    classroom_id: str = ""
    name: str = ""
    instructor: str = ""
    created_at: str = ""
    projects: list[str] = field(default_factory=list)  # GitHub repo IDs
    student_count: int = 0
    python_version: str = ""
    extra_packages: list[str] = field(default_factory=list)
    deadline: str = ""
    notes: str = ""


@dataclass
class StudentProgress:
    """学生进度"""
    student_id: str = ""
    classroom_id: str = ""
    project: str = ""
    steps_completed: int = 0
    total_steps: int = 0
    started_at: str = ""
    completed_at: str = ""
    errors: list[str] = field(default_factory=list)
    status: str = "not_started"  # not_started | in_progress | completed | stuck


@dataclass
class LearningPath:
    """学习路径"""
    path_id: str = ""
    title: str = ""
    description: str = ""
    difficulty: str = ""        # 初学者/中级/高级
    projects: list[dict] = field(default_factory=list)
    estimated_hours: float = 0.0
    skills_gained: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  项目难度评级
# ─────────────────────────────────────────────

_DIFFICULTY_LABELS = {1: "初学者", 2: "基础", 3: "中级", 4: "进阶", 5: "专家"}

# 语言/技术栈复杂度权重
_LANG_COMPLEXITY = {
    "python": 1, "javascript": 1, "html": 1, "markdown": 0,
    "typescript": 2, "java": 2, "go": 2, "ruby": 1,
    "rust": 4, "c": 3, "cpp": 4, "haskell": 4, "scala": 3,
    "cuda": 5, "assembly": 5, "vhdl": 5,
}

# 工具复杂度
_TOOL_COMPLEXITY = {
    "docker": 2, "kubernetes": 4, "terraform": 3,
    "cmake": 3, "make": 2, "bazel": 3,
    "conda": 1, "pip": 1, "npm": 1, "cargo": 2,
    "gradle": 2, "maven": 2,
}


def rate_difficulty(project_dir: str, project_types: list[str] | None = None) -> DifficultyRating:
    """
    评估项目安装难度（1-5 星）。

    维度：
      - 语言复杂度
      - 依赖数量
      - 需要外部工具 (Docker, CUDA, etc.)
      - 配置步骤数
      - 文档完整性
    """
    root = Path(project_dir)
    factors = {}

    # 1. 语言复杂度
    lang_score = 1
    if project_types:
        for pt in project_types:
            pt_lower = pt.lower()
            for lang, score in _LANG_COMPLEXITY.items():
                if lang in pt_lower:
                    lang_score = max(lang_score, score)
    factors["language"] = lang_score

    # 2. 依赖数量
    dep_count = 0
    for dep_file in ("requirements.txt", "package.json", "Cargo.toml", "go.mod"):
        fp = root / dep_file
        if fp.exists():
            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
                if dep_file == "requirements.txt":
                    dep_count += len([l for l in content.splitlines()
                                     if l.strip() and not l.startswith("#")])
                elif dep_file == "package.json":
                    data = json.loads(content)
                    for s in ("dependencies", "devDependencies"):
                        dep_count += len(data.get(s, {}))
                elif dep_file == "Cargo.toml":
                    dep_count += content.count(" = ")
                elif dep_file == "go.mod":
                    dep_count += content.count("\n\t")
            except (OSError, json.JSONDecodeError):
                pass

    dep_score = 1 if dep_count < 5 else 2 if dep_count < 15 else 3 if dep_count < 40 else 4 if dep_count < 100 else 5
    factors["dependencies"] = dep_score

    # 3. 外部工具需求
    tool_score = 1
    readme_text = ""
    for rname in ("README.md", "README.rst", "README"):
        rp = root / rname
        if rp.exists():
            try:
                readme_text = rp.read_text(encoding="utf-8", errors="ignore")[:30000]
            except OSError:
                pass
            break

    if (root / "Dockerfile").exists() or "docker" in readme_text.lower():
        tool_score = max(tool_score, 2)
        factors["docker"] = 2
    if "cuda" in readme_text.lower() or "gpu" in readme_text.lower():
        tool_score = max(tool_score, 3)
        factors["gpu_required"] = 3
    if (root / "CMakeLists.txt").exists() or "cmake" in readme_text.lower():
        tool_score = max(tool_score, 3)
        factors["cmake"] = 3
    if "kubernetes" in readme_text.lower() or "k8s" in readme_text.lower():
        tool_score = max(tool_score, 4)
        factors["kubernetes"] = 4

    factors["tools"] = tool_score

    # 4. 配置步骤数（从 README 推断）
    setup_steps = len(re.findall(r'```(?:bash|shell|sh)', readme_text, re.IGNORECASE))
    step_score = 1 if setup_steps <= 2 else 2 if setup_steps <= 5 else 3 if setup_steps <= 10 else 4
    factors["setup_steps"] = step_score

    # 5. 文档完整性（好文档降低难度）
    doc_score = 0
    if re.search(r'##.*install|##.*setup|##.*getting.started', readme_text, re.IGNORECASE):
        doc_score += 1
    if re.search(r'##.*usage|##.*example|##.*tutorial', readme_text, re.IGNORECASE):
        doc_score += 1
    if (root / "docs").is_dir() or (root / "doc").is_dir():
        doc_score += 1
    doc_penalty = max(0, 3 - doc_score)  # 文档好 → 减分
    factors["doc_penalty"] = doc_penalty

    # 综合评分
    raw_score = (
        lang_score * 0.25 +
        dep_score * 0.15 +
        tool_score * 0.25 +
        step_score * 0.15 +
        doc_penalty * 0.20
    )

    level = max(1, min(5, round(raw_score)))

    # 前置知识
    prereqs = []
    if lang_score >= 3:
        prereqs.append("编程经验 (进阶)")
    elif lang_score >= 2:
        prereqs.append("基础编程")
    if tool_score >= 3:
        prereqs.append("命令行操作")
    if "gpu_required" in factors:
        prereqs.append("GPU 驱动 & CUDA")
    if "docker" in factors:
        prereqs.append("Docker 基础")

    # 估算安装时间
    time_min = 5 + dep_count // 5 + setup_steps * 2 + (10 if tool_score >= 3 else 0)

    return DifficultyRating(
        level=level,
        label=_DIFFICULTY_LABELS.get(level, "未知"),
        factors=factors,
        setup_time_min=time_min,
        prereqs=prereqs,
        explanation=_explain_difficulty(level, factors),
    )


def _explain_difficulty(level: int, factors: dict) -> str:
    """生成难度解释"""
    if level <= 1:
        return "📗 非常简单，初学者友好，几分钟即可完成安装"
    elif level == 2:
        return "📘 基础级别，需要基本命令行知识"
    elif level == 3:
        return "📙 中等难度，需要一定开发经验和工具链知识"
    elif level == 4:
        return "📕 较高难度，需要进阶技能和特定工具链"
    else:
        return "📓 专家级，需要深厚系统知识和专业工具链"


def format_difficulty(rating: DifficultyRating) -> str:
    """格式化难度评级"""
    stars = "⭐" * rating.level + "☆" * (5 - rating.level)
    lines = [
        f"难度: {stars} [{rating.label}]",
        f"  预估安装时间: ~{rating.setup_time_min} 分钟",
        rating.explanation,
    ]
    if rating.prereqs:
        lines.append(f"  前置知识: {', '.join(rating.prereqs)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  分步引导安装
# ─────────────────────────────────────────────

def generate_guided_steps(
    project_dir: str,
    plan: dict[str, Any],
    difficulty: DifficultyRating | None = None,
) -> list[GuidedStep]:
    """
    从安装计划生成分步引导教程。

    每步包含：标题、说明、命令、预期输出、排错提示。
    """
    steps = []
    step_num = 0

    # Step 0: 前置检查
    step_num += 1
    steps.append(GuidedStep(
        step_num=step_num,
        title="环境检查",
        description="首先确认您的系统是否满足要求",
        command="python --version && git --version",
        expected_output="Python 3.x.x\ngit version 2.x.x",
        troubleshooting=[
            "如果 python 命令不存在，请安装 Python: https://python.org",
            "如果 git 不存在，请安装 Git: https://git-scm.com",
        ],
        checkpoint="python --version",
    ))

    # Step 1: 克隆项目
    repo = plan.get("repo", "")
    if repo:
        step_num += 1
        steps.append(GuidedStep(
            step_num=step_num,
            title="获取代码",
            description=f"从 GitHub 克隆项目代码到本地",
            command=f"git clone https://github.com/{repo}.git && cd {repo.split('/')[-1] if '/' in repo else repo}",
            expected_output="Cloning into '...'...\ndone.",
            troubleshooting=[
                "如果速度慢，可使用镜像: git clone https://ghproxy.com/https://github.com/{repo}.git",
                "如果 SSH key 有问题，使用 HTTPS 方式克隆",
            ],
        ))

    # Step 2+: 从 plan steps 生成
    plan_steps = plan.get("steps", [])
    for ps in plan_steps:
        cmds = ps.get("commands", [])
        label = ps.get("label", "")
        note = ps.get("note", "")

        for cmd in cmds:
            step_num += 1
            cmd_str = cmd if isinstance(cmd, str) else str(cmd)

            # 根据命令类型生成说明
            description = note or _describe_command(cmd_str)
            troubleshooting = _troubleshoot_command(cmd_str)

            steps.append(GuidedStep(
                step_num=step_num,
                title=label or f"步骤 {step_num}",
                description=description,
                command=cmd_str,
                expected_output=_expected_output(cmd_str),
                troubleshooting=troubleshooting,
                is_optional=ps.get("optional", False),
            ))

    # 最后: 验证
    step_num += 1
    steps.append(GuidedStep(
        step_num=step_num,
        title="验证安装",
        description="确认项目安装成功",
        command=plan.get("verify_command", "echo '安装完成!'"),
        expected_output="安装完成!",
        checkpoint=plan.get("verify_command", ""),
    ))

    return steps


def _describe_command(cmd: str) -> str:
    """根据命令生成中文描述"""
    cmd_lower = cmd.lower()
    if "pip install" in cmd_lower:
        return "安装 Python 依赖包"
    if "npm install" in cmd_lower:
        return "安装 Node.js 依赖包"
    if "conda" in cmd_lower:
        return "使用 Conda 配置环境"
    if "docker" in cmd_lower:
        return "使用 Docker 构建/运行容器"
    if "cmake" in cmd_lower:
        return "使用 CMake 构建项目"
    if "make" in cmd_lower:
        return "编译项目"
    if "cargo" in cmd_lower:
        return "使用 Cargo 构建 Rust 项目"
    if "python" in cmd_lower and "venv" in cmd_lower:
        return "创建 Python 虚拟环境"
    if "git clone" in cmd_lower:
        return "克隆代码仓库"
    if "apt" in cmd_lower or "brew" in cmd_lower:
        return "安装系统依赖"
    return f"执行: {cmd[:60]}..."


def _troubleshoot_command(cmd: str) -> list[str]:
    """为命令生成排错提示"""
    tips = []
    cmd_lower = cmd.lower()

    if "pip install" in cmd_lower:
        tips.extend([
            "如果权限错误，添加 --user 参数或使用虚拟环境",
            "如果编译失败，可能需要安装 C/C++ 编译器",
            "国内用户可使用清华镜像: pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ...",
        ])
    elif "npm install" in cmd_lower:
        tips.extend([
            "如果权限错误，不要使用 sudo，改用 nvm 管理 Node",
            "国内用户可配置淘宝镜像: npm config set registry https://registry.npmmirror.com",
        ])
    elif "docker" in cmd_lower:
        tips.extend([
            "确保 Docker Desktop 已启动",
            "Linux 用户需要将当前用户加入 docker 组: sudo usermod -aG docker $USER",
        ])
    elif "cargo" in cmd_lower:
        tips.append("确保已安装 Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
    elif "cmake" in cmd_lower:
        tips.extend([
            "macOS: brew install cmake",
            "Ubuntu: sudo apt install cmake",
        ])

    return tips


def _expected_output(cmd: str) -> str:
    """预估命令的预期输出"""
    cmd_lower = cmd.lower()
    if "pip install" in cmd_lower:
        return "Successfully installed ..."
    if "npm install" in cmd_lower:
        return "added X packages in Xs"
    if "cmake" in cmd_lower:
        return "-- Build files have been written to: ..."
    if "make" in cmd_lower:
        return "[100%] Built target ..."
    return ""


def format_guided_steps(steps: list[GuidedStep]) -> str:
    """格式化引导步骤为美观的文本"""
    lines = [
        "🎓 分步安装指南",
        "=" * 50,
        "",
    ]
    for s in steps:
        optional_tag = " [可选]" if s.is_optional else ""
        lines.extend([
            f"📍 步骤 {s.step_num}: {s.title}{optional_tag}",
            f"   {s.description}",
            "",
            f"   $ {s.command}",
            "",
        ])
        if s.expected_output:
            lines.append(f"   预期输出: {s.expected_output}")
            lines.append("")
        if s.troubleshooting:
            lines.append("   ⚠️ 常见问题:")
            for t in s.troubleshooting:
                lines.append(f"     • {t}")
            lines.append("")
        lines.append("─" * 50)
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  课堂管理
# ─────────────────────────────────────────────

_CLASSROOM_DIR = os.path.expanduser("~/.gitinstall/classrooms")


def create_classroom(
    name: str,
    instructor: str,
    projects: list[str],
    student_count: int = 30,
    python_version: str = "",
    extra_packages: list[str] | None = None,
    deadline: str = "",
) -> ClassroomConfig:
    """创建课堂配置"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cid = hashlib.sha256(f"{name}-{instructor}-{now}".encode()).hexdigest()[:10]

    config = ClassroomConfig(
        classroom_id=cid,
        name=name,
        instructor=instructor,
        created_at=now,
        projects=projects,
        student_count=student_count,
        python_version=python_version,
        extra_packages=extra_packages or [],
        deadline=deadline,
    )

    # 持久化
    os.makedirs(_CLASSROOM_DIR, exist_ok=True)
    path = os.path.join(_CLASSROOM_DIR, f"{cid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "classroom_id": config.classroom_id,
            "name": config.name,
            "instructor": config.instructor,
            "created_at": config.created_at,
            "projects": config.projects,
            "student_count": config.student_count,
            "python_version": config.python_version,
            "extra_packages": config.extra_packages,
            "deadline": config.deadline,
            "notes": config.notes,
        }, f, indent=2, ensure_ascii=False)

    return config


def generate_student_setup_script(classroom: ClassroomConfig) -> str:
    """
    为课堂生成学生一键安装脚本。

    学生只需运行一个脚本即可配置好全部环境。
    """
    lines = [
        "#!/bin/bash",
        f"# 课堂环境配置脚本 — {classroom.name}",
        f"# 讲师: {classroom.instructor}",
        f"# 生成时间: {classroom.created_at}",
        "",
        'set -e',
        'echo "🎓 开始配置课堂环境..."',
        "",
    ]

    # Python 版本检查
    if classroom.python_version:
        lines.extend([
            f'echo "检查 Python 版本..."',
            f'PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP "\\d+\\.\\d+")',
            f'echo "当前 Python: $PYTHON_VERSION"',
            "",
        ])

    # 创建虚拟环境
    venv_name = f"classroom-{classroom.classroom_id}"
    lines.extend([
        f'echo "创建虚拟环境 {venv_name}..."',
        f'python3 -m venv {venv_name}',
        f'source {venv_name}/bin/activate',
        "",
        f'echo "升级 pip..."',
        f'pip install --upgrade pip',
        "",
    ])

    # 安装额外包
    if classroom.extra_packages:
        pkg_list = " ".join(classroom.extra_packages)
        lines.extend([
            f'echo "安装课程依赖..."',
            f'pip install {pkg_list}',
            "",
        ])

    # 克隆并安装每个项目
    for project in classroom.projects:
        lines.extend([
            f'echo "📦 安装项目: {project}"',
            f'if [ ! -d "{project.split("/")[-1]}" ]; then',
            f'    git clone https://github.com/{project}.git',
            f'fi',
            f'cd {project.split("/")[-1]}',
            f'if [ -f requirements.txt ]; then',
            f'    pip install -r requirements.txt',
            f'elif [ -f setup.py ] || [ -f pyproject.toml ]; then',
            f'    pip install -e .',
            f'fi',
            f'cd ..',
            "",
        ])

    lines.extend([
        'echo ""',
        'echo "✅ 课堂环境配置完成!"',
        f'echo "激活环境: source {venv_name}/bin/activate"',
    ])

    return "\n".join(lines)


def list_classrooms() -> list[dict]:
    """列出所有课堂"""
    if not os.path.isdir(_CLASSROOM_DIR):
        return []
    result = []
    for fname in sorted(os.listdir(_CLASSROOM_DIR)):
        if fname.endswith(".json"):
            path = os.path.join(_CLASSROOM_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append(data)
            except (json.JSONDecodeError, OSError):
                pass
    return result


# ─────────────────────────────────────────────
#  学习路径生成
# ─────────────────────────────────────────────

_LEARNING_PATHS: dict[str, LearningPath] = {
    "python_ml_beginner": LearningPath(
        path_id="python_ml_beginner",
        title="Python 机器学习入门",
        description="从零开始学习 Python 和 ML",
        difficulty="初学者",
        projects=[
            {"repo": "scikit-learn/scikit-learn", "name": "Scikit-learn", "skills": ["ML基础", "分类/回归"], "hours": 4},
            {"repo": "pandas-dev/pandas", "name": "Pandas", "skills": ["数据处理"], "hours": 3},
            {"repo": "matplotlib/matplotlib", "name": "Matplotlib", "skills": ["数据可视化"], "hours": 2},
            {"repo": "fastai/fastai", "name": "Fast.ai", "skills": ["深度学习入门"], "hours": 5},
        ],
        estimated_hours=14,
        skills_gained=["Python", "机器学习", "数据处理", "数据可视化", "深度学习入门"],
    ),
    "deep_learning": LearningPath(
        path_id="deep_learning",
        title="深度学习实战",
        description="掌握主流深度学习框架",
        difficulty="中级",
        projects=[
            {"repo": "pytorch/pytorch", "name": "PyTorch", "skills": ["张量、自动微分"], "hours": 8},
            {"repo": "huggingface/transformers", "name": "Transformers", "skills": ["NLP/LLM"], "hours": 6},
            {"repo": "ultralytics/ultralytics", "name": "YOLOv8", "skills": ["计算机视觉"], "hours": 4},
            {"repo": "openai/whisper", "name": "Whisper", "skills": ["语音识别"], "hours": 3},
        ],
        estimated_hours=21,
        skills_gained=["PyTorch", "Transformers", "NLP", "CV", "语音"],
    ),
    "llm_engineering": LearningPath(
        path_id="llm_engineering",
        title="LLM 工程实践",
        description="LLM 部署、微调、应用开发",
        difficulty="高级",
        projects=[
            {"repo": "vllm-project/vllm", "name": "vLLM", "skills": ["LLM 高性能推理"], "hours": 6},
            {"repo": "ggml-org/llama.cpp", "name": "llama.cpp", "skills": ["本地 LLM 运行"], "hours": 5},
            {"repo": "langchain-ai/langchain", "name": "LangChain", "skills": ["LLM App 开发"], "hours": 8},
            {"repo": "run-llama/llama_index", "name": "LlamaIndex", "skills": ["RAG 检索增强"], "hours": 6},
            {"repo": "mlc-ai/mlc-llm", "name": "MLC-LLM", "skills": ["多端部署"], "hours": 4},
        ],
        estimated_hours=29,
        skills_gained=["LLM部署", "量化", "RAG", "LLM应用开发", "多端推理"],
    ),
    "web_fullstack": LearningPath(
        path_id="web_fullstack",
        title="全栈 Web 开发",
        description="前后端一体化开发",
        difficulty="初学者",
        projects=[
            {"repo": "expressjs/express", "name": "Express", "skills": ["Node.js后端"], "hours": 4},
            {"repo": "vercel/next.js", "name": "Next.js", "skills": ["React SSR"], "hours": 6},
            {"repo": "fastapi/fastapi", "name": "FastAPI", "skills": ["Python API"], "hours": 4},
            {"repo": "prisma/prisma", "name": "Prisma", "skills": ["ORM/数据库"], "hours": 3},
        ],
        estimated_hours=17,
        skills_gained=["Node.js", "React", "Python API", "数据库", "全栈"],
    ),
    "rust_systems": LearningPath(
        path_id="rust_systems",
        title="Rust 系统编程",
        description="用 Rust 构建高性能系统",
        difficulty="高级",
        projects=[
            {"repo": "nickel-org/nickel.rs", "name": "Nickel", "skills": ["Rust Web"], "hours": 4},
            {"repo": "tokio-rs/tokio", "name": "Tokio", "skills": ["异步运行时"], "hours": 6},
            {"repo": "BurntSushi/ripgrep", "name": "ripgrep", "skills": ["CLI工具开发"], "hours": 5},
            {"repo": "denoland/deno", "name": "Deno", "skills": ["JS Runtime"], "hours": 8},
        ],
        estimated_hours=23,
        skills_gained=["Rust", "异步编程", "系统编程", "CLI工具", "性能优化"],
    ),
    "devops_cloud": LearningPath(
        path_id="devops_cloud",
        title="DevOps 与云原生",
        description="CI/CD、容器、编排",
        difficulty="中级",
        projects=[
            {"repo": "docker/compose", "name": "Docker Compose", "skills": ["容器编排"], "hours": 4},
            {"repo": "kubernetes/kubernetes", "name": "Kubernetes", "skills": ["集群管理"], "hours": 10},
            {"repo": "hashicorp/terraform", "name": "Terraform", "skills": ["IaC"], "hours": 6},
            {"repo": "prometheus/prometheus", "name": "Prometheus", "skills": ["监控告警"], "hours": 4},
        ],
        estimated_hours=24,
        skills_gained=["Docker", "Kubernetes", "IaC", "监控", "CI/CD"],
    ),
}


def get_learning_path(path_id: str) -> LearningPath:
    """获取学习路径"""
    return _LEARNING_PATHS.get(path_id, LearningPath())


def list_learning_paths() -> list[dict]:
    """列出所有学习路径"""
    result = []
    for path_id, lp in _LEARNING_PATHS.items():
        result.append({
            "path_id": path_id,
            "title": lp.title,
            "difficulty": lp.difficulty,
            "projects": len(lp.projects),
            "hours": lp.estimated_hours,
            "skills": lp.skills_gained,
        })
    return result


def recommend_learning_path(
    skill_level: str = "beginner",
    interests: list[str] | None = None,
) -> list[LearningPath]:
    """根据技能水平和兴趣推荐学习路径"""
    level_map = {
        "beginner": "初学者",
        "intermediate": "中级",
        "advanced": "高级",
    }
    target_level = level_map.get(skill_level, skill_level)
    interests = interests or []
    interests_lower = [i.lower() for i in interests]

    scored = []
    for lp in _LEARNING_PATHS.values():
        score = 0.0

        # 难度匹配
        if lp.difficulty == target_level:
            score += 50

        # 兴趣匹配
        for skill in lp.skills_gained:
            for interest in interests_lower:
                if interest in skill.lower():
                    score += 20

        scored.append((score, lp))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [lp for _, lp in scored[:3]]


def format_learning_path(lp: LearningPath) -> str:
    """格式化学习路径"""
    lines = [
        f"🗺️ {lp.title}",
        f"   {lp.description}",
        f"   难度: {lp.difficulty} | 预估: {lp.estimated_hours}小时",
        "",
        "   步骤:",
    ]
    for i, proj in enumerate(lp.projects, 1):
        skills_str = ", ".join(proj.get("skills", []))
        lines.append(f"   {i}. {proj['name']} ({proj['repo']}) — {skills_str} [{proj.get('hours', '?')}h]")

    lines.extend([
        "",
        f"   技能收获: {', '.join(lp.skills_gained)}",
    ])
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Jupyter Notebook 集成
# ─────────────────────────────────────────────

def generate_setup_notebook(
    project: str,
    plan: dict[str, Any] | None = None,
) -> dict:
    """
    生成 Jupyter Notebook (.ipynb) 内容，引导学生安装项目。

    Returns:
        dict — 合法的 .ipynb JSON 结构
    """
    cells = []

    # 标题 cell
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [
            f"# 📦 安装指南: {project}\n",
            f"\n",
            f"本 Notebook 将引导你完成 `{project}` 的环境配置和安装。\n",
            f"请按顺序执行每个代码单元格。\n",
        ],
    })

    # 环境检查
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": ["## 1. 环境检查\n"],
    })
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "import sys\n",
            "print(f'Python: {sys.version}')\n",
            "print(f'Platform: {sys.platform}')\n",
            "!git --version\n",
        ],
        "execution_count": None,
        "outputs": [],
    })

    # 安装步骤
    if plan and "steps" in plan:
        for i, step in enumerate(plan["steps"], 2):
            label = step.get("label", f"步骤 {i}")
            cmds = step.get("commands", [])
            note = step.get("note", "")

            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"## {i}. {label}\n"] + ([f"\n{note}\n"] if note else []),
            })
            cells.append({
                "cell_type": "code",
                "metadata": {},
                "source": [f"!{cmd}\n" for cmd in cmds if isinstance(cmd, str)],
                "execution_count": None,
                "outputs": [],
            })

    # 验证
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": ["## ✅ 验证安装\n"],
    })
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": ["print('🎉 安装完成!')\n"],
        "execution_count": None,
        "outputs": [],
    })

    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.10.0"},
        },
        "cells": cells,
    }
