"""
skills.py - gitinstall Skills 插件系统
=======================================

灵感来源：OpenClaw ClawHub Skills 注册表

Skills 是社区贡献的安装策略扩展，允许用户：
  1. 安装社区共享的安装策略（类似 OpenClaw 的 Skills）
  2. 创建自己的安装策略并分享
  3. 自动发现并使用匹配的 Skill

Skill 结构：
  ~/.gitinstall/skills/<skill-name>/
    ├── skill.json      # 元数据（名称、版本、匹配规则）
    ├── install.json    # 安装步骤定义
    └── README.md       # 说明文档

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Skills 根目录 ──
SKILLS_DIR = Path.home() / ".gitinstall" / "skills"

# ── 内置 Skills 注册表 URL ──
REGISTRY_URL = "https://raw.githubusercontent.com/gitinstall/skills-registry/main/registry.json"


@dataclass
class SkillMeta:
    """Skill 元数据"""
    name: str
    version: str
    description: str
    author: str = ""
    match_repos: list[str] = field(default_factory=list)     # 精确匹配: ["owner/repo"]
    match_patterns: list[str] = field(default_factory=list)   # 正则匹配项目类型
    match_languages: list[str] = field(default_factory=list)  # 匹配编程语言
    match_files: list[str] = field(default_factory=list)      # 匹配项目中包含的文件
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    min_gitinstall_version: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SkillMeta":
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "0.1.0")),
            description=str(d.get("description", "")),
            author=str(d.get("author", "")),
            match_repos=[str(x) for x in d.get("match_repos", [])],
            match_patterns=[str(x) for x in d.get("match_patterns", [])],
            match_languages=[str(x) for x in d.get("match_languages", [])],
            match_files=[str(x) for x in d.get("match_files", [])],
            tags=[str(x) for x in d.get("tags", [])],
            homepage=str(d.get("homepage", "")),
            min_gitinstall_version=str(d.get("min_gitinstall_version", "")),
        )


@dataclass
class SkillInstallPlan:
    """Skill 定义的安装计划"""
    steps: list[dict] = field(default_factory=list)
    launch_command: str = ""
    env_vars: dict = field(default_factory=dict)
    pre_checks: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SkillInstallPlan":
        return cls(
            steps=[s for s in d.get("steps", []) if isinstance(s, dict)],
            launch_command=str(d.get("launch_command", "")),
            env_vars={str(k): str(v) for k, v in d.get("env_vars", {}).items()},
            pre_checks=[str(x) for x in d.get("pre_checks", [])],
            notes=str(d.get("notes", "")),
        )


@dataclass
class Skill:
    """完整的 Skill 对象"""
    meta: SkillMeta
    plan: SkillInstallPlan
    path: Path
    enabled: bool = True


# ─────────────────────────────────────────────
#  Skill 管理
# ─────────────────────────────────────────────

class SkillManager:
    """Skills 管理器"""

    def __init__(self, skills_dir: Path = None):
        self.skills_dir = skills_dir or SKILLS_DIR

    def _ensure_dir(self):
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[Skill]:
        """列出所有已安装的 Skills"""
        if not self.skills_dir.exists():
            return []

        skills = []
        for d in sorted(self.skills_dir.iterdir()):
            if not d.is_dir():
                continue
            skill = self._load_skill(d)
            if skill:
                skills.append(skill)
        return skills

    def _load_skill(self, skill_dir: Path) -> Optional[Skill]:
        """从目录加载一个 Skill"""
        meta_file = skill_dir / "skill.json"
        plan_file = skill_dir / "install.json"

        if not meta_file.exists():
            return None

        try:
            with open(meta_file, encoding="utf-8") as f:
                meta_data = json.load(f)
            meta = SkillMeta.from_dict(meta_data)

            plan = SkillInstallPlan()
            if plan_file.exists():
                with open(plan_file, encoding="utf-8") as f:
                    plan_data = json.load(f)
                plan = SkillInstallPlan.from_dict(plan_data)

            enabled = not (skill_dir / ".disabled").exists()

            return Skill(meta=meta, plan=plan, path=skill_dir, enabled=enabled)
        except (json.JSONDecodeError, KeyError):
            return None

    def find_matching_skills(
        self,
        owner: str = "",
        repo: str = "",
        project_types: list[str] = None,
        language: str = "",
        file_list: list[str] = None,
    ) -> list[Skill]:
        """
        查找匹配当前项目的 Skills。

        匹配优先级：
          1. 精确匹配 owner/repo → 最高置信度
          2. 语言匹配 → 中等置信度
          3. 文件匹配 → 中等置信度
          4. 项目类型正则匹配 → 低置信度
        """
        full_name = f"{owner}/{repo}".lower() if owner and repo else ""
        project_types = project_types or []
        file_list = file_list or []
        language_lower = language.lower() if language else ""

        matched = []
        for skill in self.list_skills():
            if not skill.enabled:
                continue

            # 精确仓库匹配
            if full_name and full_name in [r.lower() for r in skill.meta.match_repos]:
                matched.append(skill)
                continue

            # 语言匹配
            if language_lower and language_lower in [l.lower() for l in skill.meta.match_languages]:
                matched.append(skill)
                continue

            # 文件匹配
            if file_list and skill.meta.match_files:
                if any(f in file_list for f in skill.meta.match_files):
                    matched.append(skill)
                    continue

            # 项目类型正则匹配
            if project_types and skill.meta.match_patterns:
                for pat in skill.meta.match_patterns:
                    try:
                        regex = re.compile(pat, re.IGNORECASE)
                        if any(regex.search(pt) for pt in project_types):
                            matched.append(skill)
                            break
                    except re.error:
                        pass

        return matched

    def create_skill(
        self,
        name: str,
        description: str,
        steps: list[dict],
        launch_command: str = "",
        match_repos: list[str] = None,
        match_languages: list[str] = None,
        match_files: list[str] = None,
        match_patterns: list[str] = None,
        author: str = "",
        tags: list[str] = None,
    ) -> Path:
        """
        创建一个新的 Skill。

        参数：
          name: Skill 名称（英文、连字符，如 "pytorch-cuda"）
          steps: 安装步骤列表
          match_repos: 精确匹配的 owner/repo 列表
        """
        # 验证名称格式
        if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name) and len(name) > 2:
            if not re.match(r'^[a-z0-9]+$', name):
                raise ValueError(f"Skill 名称无效: {name}（仅允许小写字母、数字、连字符）")

        self._ensure_dir()
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            raise FileExistsError(f"Skill '{name}' 已存在: {skill_dir}")

        skill_dir.mkdir(parents=True)

        # 写入 skill.json
        meta = {
            "name": name,
            "version": "1.0.0",
            "description": description,
            "author": author or os.getenv("USER", "unknown"),
            "match_repos": match_repos or [],
            "match_languages": match_languages or [],
            "match_files": match_files or [],
            "match_patterns": match_patterns or [],
            "tags": tags or [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(skill_dir / "skill.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        # 写入 install.json
        plan = {
            "steps": steps,
            "launch_command": launch_command,
        }
        with open(skill_dir / "install.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        # 写入 README.md
        readme = f"# {name}\n\n{description}\n\n## 安装步骤\n\n"
        for i, step in enumerate(steps, 1):
            readme += f"{i}. {step.get('description', '')}\n   ```\n   {step.get('command', '')}\n   ```\n\n"
        with open(skill_dir / "README.md", "w", encoding="utf-8") as f:
            f.write(readme)

        return skill_dir

    def remove_skill(self, name: str) -> bool:
        """删除一个 Skill"""
        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            return False
        # 安全检查：确保路径在 skills 目录内
        try:
            skill_dir.resolve().relative_to(self.skills_dir.resolve())
        except ValueError:
            raise ValueError(f"路径越界: {skill_dir}")
        shutil.rmtree(skill_dir)
        return True

    def toggle_skill(self, name: str, enabled: bool) -> bool:
        """启用/禁用 Skill"""
        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            return False
        flag = skill_dir / ".disabled"
        if enabled:
            flag.unlink(missing_ok=True)
        else:
            flag.touch()
        return True

    def export_skill(self, name: str) -> Optional[dict]:
        """导出 Skill 为 JSON（用于分享）"""
        skill = self._load_skill(self.skills_dir / name)
        if not skill:
            return None
        meta_file = skill.path / "skill.json"
        plan_file = skill.path / "install.json"
        result = {}
        with open(meta_file, encoding="utf-8") as f:
            result["skill"] = json.load(f)
        if plan_file.exists():
            with open(plan_file, encoding="utf-8") as f:
                result["install"] = json.load(f)
        return result

    def import_skill(self, data: dict) -> Path:
        """从 JSON 导入 Skill"""
        skill_data = data.get("skill", {})
        install_data = data.get("install", {})
        name = skill_data.get("name", "")
        if not name:
            raise ValueError("Skill 数据缺少 name 字段")

        steps = install_data.get("steps", [])
        return self.create_skill(
            name=name,
            description=skill_data.get("description", ""),
            steps=steps,
            launch_command=install_data.get("launch_command", ""),
            match_repos=skill_data.get("match_repos", []),
            match_languages=skill_data.get("match_languages", []),
            match_files=skill_data.get("match_files", []),
            match_patterns=skill_data.get("match_patterns", []),
            author=skill_data.get("author", ""),
            tags=skill_data.get("tags", []),
        )


# ─────────────────────────────────────────────
#  内建 Skills（开箱即用）
# ─────────────────────────────────────────────

BUILTIN_SKILLS = [
    {
        "name": "project-health",
        "description": "安装前检查项目健康度（stars、最近更新、issues 数量）",
        "tags": ["analysis", "safety"],
    },
    {
        "name": "auto-venv",
        "description": "Python 项目自动创建 virtualenv 并隔离安装",
        "tags": ["python", "isolation"],
        "match_languages": ["python"],
        "match_files": ["requirements.txt", "setup.py", "pyproject.toml"],
    },
    {
        "name": "docker-prefer",
        "description": "优先使用 Docker 安装（当项目提供 Dockerfile 时）",
        "tags": ["docker", "isolation"],
        "match_files": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    },
    {
        "name": "gpu-optimizer",
        "description": "根据本机 GPU 自动选择最优 AI/ML 框架版本",
        "tags": ["ai", "gpu", "optimization"],
        "match_patterns": ["pytorch", "tensorflow", "mlx", "cuda"],
    },
    {
        "name": "batch-install",
        "description": "从清单文件批量安装多个 GitHub 项目",
        "tags": ["batch", "automation"],
    },
    {
        "name": "env-snapshot",
        "description": "安装前保存环境快照，支持回滚",
        "tags": ["safety", "rollback"],
    },
    {
        "name": "post-install-test",
        "description": "安装后自动运行项目测试验证安装成功",
        "tags": ["testing", "verification"],
    },
    {
        "name": "changelog-notify",
        "description": "跟踪已安装项目的更新，通知版本变化",
        "tags": ["update", "tracking"],
    },
    # ── 第二批 Skills（安全、合规、多语言、AI 增强） ──
    {
        "name": "dependency-audit",
        "description": "安装前扫描依赖的 CVE 漏洞、误植攻击、废弃包",
        "tags": ["security", "audit"],
        "match_files": ["requirements.txt", "package.json", "Cargo.toml", "go.mod"],
    },
    {
        "name": "license-guard",
        "description": "检查项目许可证兼容性，标记 GPL/AGPL 传染风险",
        "tags": ["compliance", "license"],
    },
    {
        "name": "auto-updater",
        "description": "追踪已安装项目版本，检测并通知新 release",
        "tags": ["update", "tracking"],
    },
    {
        "name": "clean-uninstall",
        "description": "安全卸载项目：清理 venv、容器、缓存、编译产物",
        "tags": ["cleanup", "uninstall"],
    },
    {
        "name": "monorepo-nav",
        "description": "自动识别 monorepo 结构，安装指定子包",
        "tags": ["monorepo", "navigation"],
        "match_files": ["lerna.json", "pnpm-workspace.yaml", "Cargo.toml"],
        "match_patterns": ["monorepo", "workspace"],
    },
    {
        "name": "proxy-tunnel",
        "description": "受限网络环境自动配置代理（GitHub/PyPI/npm/Docker 镜像）",
        "tags": ["network", "proxy", "mirror"],
    },
    {
        "name": "devcontainer",
        "description": "检测 .devcontainer 配置，使用 VS Code Dev Container 启动",
        "tags": ["docker", "devcontainer", "vscode"],
        "match_files": [".devcontainer/devcontainer.json", ".devcontainer.json"],
    },
    {
        "name": "cost-estimator",
        "description": "估算 AI/ML 项目运行所需的计算资源和云服务成本",
        "tags": ["ai", "cost", "estimation"],
        "match_patterns": ["pytorch", "tensorflow", "mlx", "transformers", "diffusion"],
    },
]


def ensure_builtin_skills():
    """确保内建 Skills 已注册（首次运行时自动调用）"""
    mgr = SkillManager()
    existing = {s.meta.name for s in mgr.list_skills()}

    for bs in BUILTIN_SKILLS:
        if bs["name"] in existing:
            continue
        try:
            mgr.create_skill(
                name=bs["name"],
                description=bs["description"],
                steps=[],  # 内建 Skills 的逻辑在代码中实现，不需要 install.json 步骤
                match_languages=bs.get("match_languages", []),
                match_files=bs.get("match_files", []),
                match_patterns=bs.get("match_patterns", []),
                tags=bs.get("tags", []),
            )
        except (FileExistsError, ValueError):
            pass


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_skills_list(skills: list[Skill]) -> str:
    """格式化 Skills 列表"""
    if not skills:
        return "  （未安装任何 Skill）\n  使用 'gitinstall skills init' 初始化内建 Skills"

    lines = []
    for s in skills:
        status = "✅" if s.enabled else "⏸️ "
        tags = ", ".join(s.meta.tags[:3]) if s.meta.tags else ""
        tag_str = f" [{tags}]" if tags else ""
        lines.append(f"  {status} {s.meta.name} v{s.meta.version}{tag_str}")
        lines.append(f"     {s.meta.description}")
    return "\n".join(lines)
