"""
cicd.py - CI/CD 集成模块
==========================

为 GitHub Actions、GitLab CI、Jenkins、Azure Pipelines 等
CI/CD 平台提供 gitinstall 集成能力。

功能：
  1. 生成 CI/CD 配置文件（GitHub Actions YAML 等）
  2. CI 环境检测和自适应
  3. 批量安装 + 缓存策略
  4. 安装结果报告（JUnit / JSON）
  5. 安装锁文件（可复现安装）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
#  CI 环境检测
# ─────────────────────────────────────────────

@dataclass
class CIEnvironment:
    """CI/CD 环境信息"""
    is_ci: bool = False
    platform: str = ""           # github_actions, gitlab_ci, jenkins, azure, circle, travis
    runner_os: str = ""          # linux, macos, windows
    runner_arch: str = ""        # x64, arm64
    branch: str = ""
    commit_sha: str = ""
    pr_number: str = ""
    repo_url: str = ""
    workspace: str = ""
    job_name: str = ""
    run_id: str = ""
    cache_dir: str = ""


def detect_ci_environment() -> CIEnvironment:
    """自动检测当前 CI/CD 环境"""
    env = CIEnvironment()

    # GitHub Actions
    if os.getenv("GITHUB_ACTIONS") == "true":
        env.is_ci = True
        env.platform = "github_actions"
        env.runner_os = os.getenv("RUNNER_OS", "").lower()
        env.runner_arch = os.getenv("RUNNER_ARCH", "").lower()
        env.branch = os.getenv("GITHUB_REF_NAME", "")
        env.commit_sha = os.getenv("GITHUB_SHA", "")
        env.pr_number = os.getenv("GITHUB_EVENT_NUMBER", "")
        env.repo_url = f"https://github.com/{os.getenv('GITHUB_REPOSITORY', '')}"
        env.workspace = os.getenv("GITHUB_WORKSPACE", "")
        env.job_name = os.getenv("GITHUB_JOB", "")
        env.run_id = os.getenv("GITHUB_RUN_ID", "")
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    # GitLab CI
    if os.getenv("GITLAB_CI") == "true":
        env.is_ci = True
        env.platform = "gitlab_ci"
        env.branch = os.getenv("CI_COMMIT_REF_NAME", "")
        env.commit_sha = os.getenv("CI_COMMIT_SHA", "")
        env.pr_number = os.getenv("CI_MERGE_REQUEST_IID", "")
        env.repo_url = os.getenv("CI_PROJECT_URL", "")
        env.workspace = os.getenv("CI_PROJECT_DIR", "")
        env.job_name = os.getenv("CI_JOB_NAME", "")
        env.run_id = os.getenv("CI_PIPELINE_ID", "")
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    # Jenkins
    if os.getenv("JENKINS_URL"):
        env.is_ci = True
        env.platform = "jenkins"
        env.branch = os.getenv("GIT_BRANCH", "")
        env.commit_sha = os.getenv("GIT_COMMIT", "")
        env.workspace = os.getenv("WORKSPACE", "")
        env.job_name = os.getenv("JOB_NAME", "")
        env.run_id = os.getenv("BUILD_NUMBER", "")
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    # Azure Pipelines
    if os.getenv("TF_BUILD") == "True":
        env.is_ci = True
        env.platform = "azure"
        env.runner_os = os.getenv("Agent.OS", "").lower()
        env.branch = os.getenv("Build.SourceBranchName", "")
        env.commit_sha = os.getenv("Build.SourceVersion", "")
        env.pr_number = os.getenv("System.PullRequest.PullRequestId", "")
        env.workspace = os.getenv("Build.SourcesDirectory", "")
        env.run_id = os.getenv("Build.BuildId", "")
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    # CircleCI
    if os.getenv("CIRCLECI") == "true":
        env.is_ci = True
        env.platform = "circle"
        env.branch = os.getenv("CIRCLE_BRANCH", "")
        env.commit_sha = os.getenv("CIRCLE_SHA1", "")
        env.pr_number = os.getenv("CIRCLE_PR_NUMBER", "")
        env.repo_url = os.getenv("CIRCLE_REPOSITORY_URL", "")
        env.workspace = os.getenv("CIRCLE_WORKING_DIRECTORY", "")
        env.job_name = os.getenv("CIRCLE_JOB", "")
        env.run_id = os.getenv("CIRCLE_BUILD_NUM", "")
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    # 通用 CI 检测
    if os.getenv("CI") == "true" or os.getenv("CI") == "1":
        env.is_ci = True
        env.platform = "unknown"
        env.cache_dir = os.path.expanduser("~/.cache/gitinstall")
        return env

    return env


# ─────────────────────────────────────────────
#  GitHub Actions 配置生成
# ─────────────────────────────────────────────

def generate_github_action(
    repos: list[str],
    python_version: str = "3.12",
    os_list: Optional[list[str]] = None,
    cache_enabled: bool = True,
    sbom_export: bool = False,
    audit_enabled: bool = True,
) -> str:
    """
    生成 GitHub Actions workflow YAML。

    Args:
        repos: 要安装的仓库列表
        python_version: Python 版本
        os_list: 运行平台列表
        cache_enabled: 是否启用缓存
        sbom_export: 是否导出 SBOM
        audit_enabled: 是否运行安全审计

    Returns:
        YAML 字符串
    """
    if os_list is None:
        os_list = ["ubuntu-latest"]

    # 使用字符串拼接而非 YAML 库（零依赖）
    lines = [
        "name: gitinstall CI",
        "",
        "on:",
        "  push:",
        "    branches: [main, master]",
        "  pull_request:",
        "    branches: [main, master]",
        "  workflow_dispatch:",
        "",
        "jobs:",
        "  install-test:",
        f"    runs-on: ${{{{ matrix.os }}}}",
        "    strategy:",
        "      matrix:",
        f"        os: [{', '.join(os_list)}]",
        "      fail-fast: false",
        "",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "",
        f"      - name: Set up Python {python_version}",
        "        uses: actions/setup-python@v5",
        "        with:",
        f"          python-version: '{python_version}'",
        "",
        "      - name: Install gitinstall",
        "        run: pip install gitinstall",
        "",
    ]

    if cache_enabled:
        lines.extend([
            "      - name: Cache gitinstall data",
            "        uses: actions/cache@v4",
            "        with:",
            "          path: ~/.cache/gitinstall",
            "          key: gitinstall-${{ runner.os }}-${{ hashFiles('**/requirements*.txt') }}",
            "          restore-keys: |",
            "            gitinstall-${{ runner.os }}-",
            "",
        ])

    if audit_enabled:
        lines.extend([
            "      - name: Security audit",
            "        run: gitinstall audit --format json --output audit-report.json",
            "",
        ])

    # 安装各仓库
    for repo in repos:
        safe_name = repo.replace("/", "-").replace(".", "-")
        lines.extend([
            f"      - name: Install {repo}",
            f"        run: gitinstall install {repo} --ci --json-report install-{safe_name}.json",
            "",
        ])

    if sbom_export:
        lines.extend([
            "      - name: Generate SBOM",
            "        run: gitinstall sbom --format cyclonedx --output sbom.cdx.json",
            "",
            "      - name: Upload SBOM",
            "        uses: actions/upload-artifact@v4",
            "        with:",
            "          name: sbom-${{ matrix.os }}",
            "          path: sbom.cdx.json",
            "",
        ])

    # 上传报告
    lines.extend([
        "      - name: Upload install reports",
        "        if: always()",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: install-reports-${{ matrix.os }}",
        "          path: |",
        "            install-*.json",
        "            audit-report.json",
        "",
    ])

    return "\n".join(lines)


def generate_gitlab_ci(
    repos: list[str],
    python_version: str = "3.12",
    audit_enabled: bool = True,
) -> str:
    """生成 GitLab CI 配置"""
    lines = [
        f"image: python:{python_version}-slim",
        "",
        "variables:",
        "  PIP_CACHE_DIR: $CI_PROJECT_DIR/.pip-cache",
        "",
        "cache:",
        "  paths:",
        "    - .pip-cache/",
        "    - .cache/gitinstall/",
        "",
        "stages:",
        "  - audit",
        "  - install",
        "",
    ]

    if audit_enabled:
        lines.extend([
            "security-audit:",
            "  stage: audit",
            "  script:",
            "    - pip install gitinstall",
            "    - gitinstall audit --format json --output audit-report.json",
            "  artifacts:",
            "    reports:",
            "      dependency_scanning: audit-report.json",
            "    expire_in: 1 week",
            "",
        ])

    for repo in repos:
        safe_name = repo.replace("/", "-").replace(".", "-")
        lines.extend([
            f"install-{safe_name}:",
            "  stage: install",
            "  script:",
            "    - pip install gitinstall",
            f"    - gitinstall install {repo} --ci",
            "  allow_failure: true",
            "",
        ])

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  安装锁文件（可复现安装）
# ─────────────────────────────────────────────

@dataclass
class InstallLockEntry:
    """锁文件中的单条记录"""
    repo_url: str
    commit_sha: str = ""       # 锁定的 commit
    tag: str = ""              # 锁定的 tag
    install_commands: list[str] = field(default_factory=list)
    checksum: str = ""         # 仓库文件的 hash
    installed_at: float = 0.0
    environment: dict = field(default_factory=dict)  # os, python, arch


def generate_install_lock(
    installs: list[dict],
    output_path: str = "gitinstall.lock.json",
) -> str:
    """
    生成安装锁文件，确保可复现安装。

    Args:
        installs: 安装记录列表 [{"repo": "...", "commit": "...", "commands": [...]}]
        output_path: 输出路径

    Returns:
        输出文件路径
    """
    import platform

    lock = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "gitinstall/1.1.0",
        "environment": {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "python": platform.python_version(),
        },
        "installs": [],
    }

    for inst in installs:
        entry = {
            "repo": inst.get("repo", ""),
            "commit_sha": inst.get("commit", ""),
            "tag": inst.get("tag", ""),
            "commands": inst.get("commands", []),
            "installed_at": inst.get("installed_at", time.time()),
        }
        # 生成 checksum
        content = json.dumps(entry, sort_keys=True)
        entry["checksum"] = hashlib.sha256(content.encode()).hexdigest()[:16]
        lock["installs"].append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2, ensure_ascii=False)

    return output_path


def load_install_lock(lock_path: str = "gitinstall.lock.json") -> list[dict]:
    """加载安装锁文件"""
    if not os.path.isfile(lock_path):
        return []
    with open(lock_path, encoding="utf-8") as f:
        lock = json.load(f)
    return lock.get("installs", [])


# ─────────────────────────────────────────────
#  JUnit XML 报告生成（CI 友好）
# ─────────────────────────────────────────────

def generate_junit_report(
    results: list[dict],
    output_path: str = "gitinstall-results.xml",
) -> str:
    """
    将安装结果转换为 JUnit XML 格式。

    CI 平台（GitHub Actions, GitLab, Jenkins）都支持 JUnit 报告。

    Args:
        results: [{"repo": "...", "success": bool, "duration": float, "error": "..."}]
        output_path: 输出路径
    """
    total = len(results)
    failures = sum(1 for r in results if not r.get("success"))
    total_time = sum(r.get("duration", 0) for r in results)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuites tests="{total}" failures="{failures}" time="{total_time:.2f}">',
        f'  <testsuite name="gitinstall" tests="{total}" failures="{failures}" time="{total_time:.2f}">',
    ]

    for r in results:
        repo = _xml_escape(r.get("repo", "unknown"))
        duration = r.get("duration", 0)
        lines.append(f'    <testcase name="{repo}" time="{duration:.2f}">')
        if not r.get("success"):
            error = _xml_escape(r.get("error", "Unknown error"))
            lines.append(f'      <failure message="Installation failed">{error}</failure>')
        lines.append("    </testcase>")

    lines.extend([
        "  </testsuite>",
        "</testsuites>",
    ])

    xml = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)

    return output_path


def _xml_escape(s: str) -> str:
    """XML 字符转义"""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;")
             .replace("'", "&apos;"))


# ─────────────────────────────────────────────
#  批量安装（CI 模式）
# ─────────────────────────────────────────────

@dataclass
class BatchInstallResult:
    """批量安装结果"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[dict] = field(default_factory=list)
    duration: float = 0.0


def plan_batch_install(
    repos: list[str],
    parallelism: int = 1,
    fail_fast: bool = False,
    skip_audit: bool = False,
) -> dict:
    """
    规划批量安装策略。

    Args:
        repos: 仓库列表
        parallelism: 并行度
        fail_fast: 失败时立即停止
        skip_audit: 跳过安全审计

    Returns:
        安装计划 dict
    """
    ci = detect_ci_environment()

    plan = {
        "ci_detected": ci.is_ci,
        "ci_platform": ci.platform,
        "total_repos": len(repos),
        "parallelism": min(parallelism, len(repos)),
        "fail_fast": fail_fast,
        "skip_audit": skip_audit,
        "phases": [],
    }

    # Phase 1: 安全审计（如果启用）
    if not skip_audit:
        plan["phases"].append({
            "name": "security_audit",
            "description": "安全审计扫描",
            "repos": repos,
        })

    # Phase 2: 分批安装
    batch_size = max(1, parallelism)
    for i in range(0, len(repos), batch_size):
        batch = repos[i:i + batch_size]
        plan["phases"].append({
            "name": f"install_batch_{i // batch_size + 1}",
            "description": f"安装批次 {i // batch_size + 1}",
            "repos": batch,
        })

    # Phase 3: 报告生成
    plan["phases"].append({
        "name": "report",
        "description": "生成安装报告",
        "outputs": ["junit_xml", "json_report"],
    })

    return plan
