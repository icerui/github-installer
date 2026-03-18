"""
fetcher.py - GitHub 项目信息抓取与解析
=====================================

功能：
  1. 解析各种格式的项目标识：URL / "owner/repo" / 项目名
  2. 通过 GitHub API 或 git clone --depth 1 本地分析 获取项目信息
  3. 下载并解析 README（支持 .md / .rst / .txt）
  4. 提取项目类型（Python/Node/Rust/Go/Docker 等）
  5. 提取依赖文件（requirements.txt / package.json / Cargo.toml 等）

两种模式：
  - API 模式（默认）：使用 GitHub REST API，受限 60 次/小时
  - 本地模式（推荐）：git clone --depth 1 后本地分析，无任何限制

只使用 Python 标准库，无需安装任何第三方包。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from log import get_logger
from i18n import t

logger = get_logger(__name__)


_LOCAL_ANALYSIS_SKIP_DIRS = {
    ".git", "node_modules", "vendor", "third_party", "thirdparty",
    "target", "dist", "build", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache",
}


# ─────────────────────────────────────────────
#  API 响应缓存
# ─────────────────────────────────────────────

_CACHE_DIR = Path.home() / ".cache" / "gitinstall" / "api"
_CACHE_TTL = int(os.getenv("GITINSTALL_CACHE_TTL", str(24 * 3600)))  # 默认 24 小时
_NO_CACHE = os.getenv("GITINSTALL_NO_CACHE", "").strip() in ("1", "true", "yes")


def _cache_path(url: str) -> Path:
    """URL → 缓存文件路径（SHA-256 前 16 位）"""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{h}.json"


def _cache_read(url: str):
    """读取缓存。命中返回 data，未命中/过期返回 None。"""
    if _NO_CACHE:
        return None
    p = _cache_path(url)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text("utf-8"))
        if time.time() - raw.get("ts", 0) > _CACHE_TTL:
            return None  # 过期但不删除 — 留给 ETag 条件请求复用
        return raw["data"]
    except Exception:
        return None


def _cache_read_etag(url: str) -> tuple:
    """读取缓存中的 ETag 和过期数据（用于条件请求）。
    返回 (etag, data) 若有，否则 (None, None)。"""
    if _NO_CACHE:
        return None, None
    p = _cache_path(url)
    if not p.exists():
        return None, None
    try:
        raw = json.loads(p.read_text("utf-8"))
        return raw.get("etag"), raw.get("data")
    except Exception:
        return None, None


def _cache_write(url: str, data, etag: str = None) -> None:
    """写入缓存（含 ETag）。失败静默，不阻塞主流程。"""
    if _NO_CACHE:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {"url": url, "ts": time.time(), "data": data}
        if etag:
            entry["etag"] = etag
        _cache_path(url).write_text(
            json.dumps(entry, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────

@dataclass
class RepoInfo:
    owner: str
    repo: str
    full_name: str          # "owner/repo"
    description: str
    stars: int
    language: str           # 主要语言
    license: str
    default_branch: str
    readme: str             # README 全文
    project_type: list[str] # ["python", "docker"] 等
    dependency_files: dict  # {"requirements.txt": "内容", ...}
    clone_url: str
    homepage: str


# ─────────────────────────────────────────────
#  URL / 名称解析
# ─────────────────────────────────────────────

def parse_repo_identifier(identifier: str) -> tuple[str, str]:
    """
    解析各种格式的项目标识，返回 (owner, repo)
    
    支持格式：
      - https://github.com/comfyanonymous/ComfyUI
      - https://gitlab.com/user/project
      - https://gitee.com/user/project
      - https://bitbucket.org/user/project
      - https://codeberg.org/user/project
      - github.com/comfyanonymous/ComfyUI
      - comfyanonymous/ComfyUI
      - comfyanonymous/ComfyUI/tree/main
      - ComfyUI  (仅项目名，会尝试搜索)
    """
    identifier = identifier.strip()

    # 提取 URL 中的 owner/repo（支持多平台）
    patterns = [
        r'github\.com[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:[/\s]|$)',
        r'gitlab\.com[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:[/\s]|$)',
        r'bitbucket\.org[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:[/\s]|$)',
        r'gitee\.com[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:[/\s]|$)',
        r'codeberg\.org[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:[/\s]|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, identifier, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)

    # "owner/repo" 格式
    if "/" in identifier and not identifier.startswith("http"):
        parts = identifier.split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            # 验证 owner/repo 格式：仅允许字母数字、连字符、下划线、点
            # 禁止路径遍历（. 或 ..）
            if not re.match(r'^[a-zA-Z0-9_-]+$', owner):
                raise ValueError(f"无效的仓库所有者: {owner}")
            if not re.match(r'^[a-zA-Z0-9_.-]+$', repo) or repo in ('.', '..'):
                raise ValueError(f"无效的仓库名: {repo}")
            return owner, repo

    # 仅项目名，需要搜索
    return "", identifier


# ─────────────────────────────────────────────
#  GitHub API 客户端
# ─────────────────────────────────────────────

class GitHubFetcher:
    """
    GitHub REST API v3 封装。
    公开仓库无需认证（每小时 60 次请求限制）。
    设置 GITHUB_TOKEN 环境变量可提升到 5000 次/小时。
    """

    API_BASE = "https://api.github.com"
    RAW_BASE = "https://raw.githubusercontent.com"

    def __init__(self):
        import os
        token = os.getenv("GITHUB_TOKEN", "").strip()
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gitinstall/1.0",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str, timeout: int = 15, _retries: int = 2) -> Optional[dict | list | str]:
        """发送 GET 请求，返回解析后的 JSON 或原始文本。

        缓存策略：
        1. TTL 内直接返回缓存（零网络开销）
        2. TTL 过期但有 ETag → 发送条件请求 If-None-Match
           - 304 Not Modified → 复用缓存数据（不消耗 API 配额）
           - 200 → 更新缓存
        3. 无缓存 → 正常请求
        """
        cached = _cache_read(url)
        if cached is not None:
            return cached

        # 检查是否有过期缓存的 ETag（用于条件请求）
        old_etag, old_data = _cache_read_etag(url)

        req = urllib.request.Request(url, headers=self._headers)
        if old_etag:
            req.add_header("If-None-Match", old_etag)

        for attempt in range(_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    resp_etag = resp.headers.get("ETag")
                    body = resp.read().decode("utf-8", errors="replace")
                    if "json" in content_type:
                        result = json.loads(body)
                    else:
                        result = body
                    _cache_write(url, result, etag=resp_etag)
                    return result
            except urllib.error.HTTPError as e:
                if e.code == 304 and old_data is not None:
                    # 304 Not Modified — 数据未变，复用缓存（不消耗配额）
                    _cache_write(url, old_data, etag=old_etag)  # 刷新 TTL
                    return old_data
                elif e.code == 404:
                    raise FileNotFoundError(f"GitHub 上找不到该资源：{url}") from e
                elif e.code == 403:
                    # 检查 Retry-After header，等待后重试
                    retry_after = e.headers.get("Retry-After")
                    if retry_after and attempt < _retries:
                        import time
                        wait = min(int(retry_after), 60)
                        time.sleep(wait)
                        continue
                    # 被限速但有过期缓存 → 降级使用旧数据
                    if old_data is not None:
                        return old_data
                    raise PermissionError(
                        "RATELIMIT: GitHub API 频率超限。\n"
                        "设置 GITHUB_TOKEN 环境变量可提升到 5000次/小时。\n"
                        "获取 Token：https://github.com/settings/tokens"
                    ) from e
                elif e.code >= 500 and attempt < _retries:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"GitHub API 错误 {e.code}: {url}") from e
            except urllib.error.URLError as e:
                if attempt < _retries:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                # 网络失败但有过期缓存 → 降级使用旧数据
                if old_data is not None:
                    return old_data
                raise RuntimeError(f"网络连接失败，请检查网络：{e.reason}") from e

    def search_repo(self, query: str) -> tuple[str, str]:
        """当只有项目名时，通过搜索找到 owner/repo"""
        url = f"{self.API_BASE}/search/repositories?q={urllib.parse.quote(query)}&per_page=1"
        data = self._get(url)
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            raise FileNotFoundError(f"在 GitHub 上找不到项目：{query}")
        repo = items[0]
        return repo["owner"]["login"], repo["name"]

    def fetch_repo_info(self, owner: str, repo: str) -> dict:
        """获取仓库基本信息"""
        url = f"{self.API_BASE}/repos/{owner}/{repo}"
        return self._get(url)

    def _get_raw(self, url: str, timeout: int = 10) -> Optional[str]:
        """GET 原始文件 URL，带缓存。仅允许 GitHub 域名。"""
        # C2: SSRF 防护 — 仅允许 GitHub 域名
        if not url.startswith(("https://raw.githubusercontent.com/", "https://api.github.com/")):
            return None
        cached = _cache_read(url)
        if cached is not None:
            return cached
        try:
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                _cache_write(url, text)
                return text
        except (urllib.error.HTTPError, urllib.error.URLError):
            return None

    def fetch_readme(self, owner: str, repo: str, branch: str = "main") -> str:
        """
        获取 README 内容。
        优先使用 GitHub API /readme 端点（1次请求），失败后降级到原始 URL。
        """
        import base64
        # 方式 1：GitHub API /repos/.../readme — 自动识别文件名和分支，1次请求
        try:
            data = self._get(f"{self.API_BASE}/repos/{owner}/{repo}/readme")
            if isinstance(data, dict) and data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except (FileNotFoundError, RuntimeError, PermissionError):
            pass

        # 方式 2（降级）：直接访问已知 default_branch 下的 README.md
        url = f"{self.RAW_BASE}/{owner}/{repo}/{branch}/README.md"
        text = self._get_raw(url)
        if text is not None:
            return text

        return ""   # README 不存在也不阻塞安装计划生成

    def fetch_file(self, owner: str, repo: str, path: str, branch: str = "main") -> Optional[str]:
        """获取仓库中的特定文件"""
        branches = [branch, "master", "main"]
        for b in branches:
            url = f"{self.RAW_BASE}/{owner}/{repo}/{b}/{path}"
            text = self._get_raw(url)
            if text is not None:
                return text
        return None


# ─────────────────────────────────────────────
#  项目类型识别
# ─────────────────────────────────────────────

def detect_project_types(
    repo_data: dict,
    readme: str,
    dependency_files: dict,
) -> list[str]:
    """
    识别项目技术栈，返回类型列表（可多个）。
    
    Returns: ["python", "pytorch", "docker"] 等
    """
    types = set()

    # 从 GitHub 主语言字段
    lang = (repo_data.get("language") or "").lower()
    _LANG_MAP = {
        "python": "python", "javascript": "node", "typescript": "node",
        "rust": "rust", "go": "go", "java": "java", "kotlin": "kotlin",
        "c++": "cpp", "c": "c", "ruby": "ruby", "php": "php",
        "c#": "dotnet", "swift": "swift", "dart": "dart",
        "scala": "scala", "shell": "shell",
        "elixir": "elixir", "erlang": "erlang", "haskell": "haskell",
        "lua": "lua", "perl": "perl", "r": "r", "julia": "julia",
        "zig": "zig", "clojure": "clojure", "nim": "nim",
        "crystal": "crystal", "hcl": "hcl",
    }
    if lang in _LANG_MAP:
        types.add(_LANG_MAP[lang])

    # 从依赖文件名
    dep_file_indicators = {
        "requirements.txt": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "pyproject.toml": "python",
        "environment.yml": "conda",
        "Pipfile": "python",
        "package.json": "node",
        "yarn.lock": "node",
        "pnpm-lock.yaml": "node",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java",
        "build.gradle": "java",
        "Dockerfile": "docker",
        "docker-compose.yml": "docker",
        "docker-compose.yaml": "docker",
        "Makefile": "make",
        "CMakeLists.txt": "cmake",
        "configure": "autotools",
        "configure.ac": "autotools",
        "Makefile.am": "autotools",
        "build.gradle.kts": "java",
        "Gemfile": "ruby",
        "composer.json": "php",
        "Package.swift": "swift",
        "mix.exs": "elixir",
        "rebar.config": "erlang",
        "pubspec.yaml": "dart",
        "build.sbt": "scala",
        "meson.build": "meson",
        "WORKSPACE": "bazel",
        "BUILD.bazel": "bazel",
        "stack.yaml": "haskell",
        "project.clj": "clojure",
        "DESCRIPTION": "r",
        "Project.toml": "julia",
        "build.zig": "zig",
        "nimble": "nim",
        "shard.yml": "crystal",
        "cpanfile": "perl",
        "Makefile.PL": "perl",
        "Build.PL": "perl",
    }
    dep_names = {Path(fname).name for fname in dependency_files}
    for fname, ptype in dep_file_indicators.items():
        if fname in dep_names:
            types.add(ptype)

    # 检测 glob 模式的依赖文件（.cabal / .nimble 文件名不固定）
    for fname in dep_names:
        if fname.endswith(".cabal"):
            types.add("haskell")
        elif fname.endswith(".nimble"):
            types.add("nim")
        elif fname.endswith(".ino"):
            types.add("arduino")

    # PlatformIO 检测
    if "platformio.ini" in dep_names:
        types.add("platformio")
    if "library.json" in dep_names or "library.properties" in dep_names:
        types.add("platformio")

    # 从 README 关键词识别深度学习框架
    readme_lower = readme.lower()
    framework_keywords = {
        "pytorch": ["torch", "pytorch", "pip install torch"],
        "tensorflow": ["tensorflow", "pip install tensorflow"],
        "diffusers": ["diffusers", "stable diffusion", "stable-diffusion"],
        "ollama": ["ollama"],
        "docker": ["docker-compose", "dockerfile"],
        "comfyui": ["comfyui"],
        "gradio": ["gradio"],
        "fastapi": ["fastapi"],
        "nextjs": ["next.js", "nextjs"],
    }
    for fw, keywords in framework_keywords.items():
        if any(kw in readme_lower for kw in keywords):
            types.add(fw)

    # conda/anaconda/miniconda 需要词边界匹配（避免 "secondary" 中的 "conda" 误判）
    if re.search(r'\bconda\b|\banaconda\b|\bminiconda\b', readme_lower):
        types.add("conda")
    # docker 单独处理（"docker-compose" 和 "dockerfile" 已在上面，这里加 docker 命令）
    if re.search(r'\bdocker\b', readme_lower):
        types.add("docker")

    return sorted(types)


# 我们关心的依赖文件集合（用于快速 set 查询）
_KNOWN_DEP_FILES = {
    # Python
    "requirements.txt", "requirements-dev.txt", "setup.py", "pyproject.toml",
    # Node.js
    "package.json",
    # Rust / Go
    "Cargo.toml", "go.mod",
    # Docker
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    # Conda
    "environment.yml",
    # Java / Kotlin
    "pom.xml", "build.gradle", "build.gradle.kts",
    # Scala
    "build.sbt",
    # Ruby
    "Gemfile",
    # PHP
    "composer.json",
    # .NET / C#
    # 注：.csproj/.sln 通常不在根目录，靠语言字段检测
    # C/C++
    "CMakeLists.txt", "Makefile", "configure", "configure.ac", "Makefile.am",
    # Swift
    "Package.swift",
    # Dart / Flutter
    "pubspec.yaml",
    # Elixir / Erlang
    "mix.exs", "rebar.config",
    # Haskell
    "stack.yaml",
    # Zig
    "build.zig", "build.zig.zon", ".zig-version",
    # Clojure
    "project.clj",
    # Julia
    "Project.toml",
    # R
    "DESCRIPTION",
    # Meson / Bazel
    "meson.build", "WORKSPACE", "BUILD.bazel",
    # Perl
    "cpanfile", "Makefile.PL", "Build.PL",
    # Crystal
    "shard.yml",
    # Nim
    # nim 用 .nimble 文件但名字不固定，靠语言检测
}


def extract_dependency_files(
    fetcher: GitHubFetcher,
    owner: str,
    repo: str,
    branch: str,
) -> dict:
    """
    获取项目依赖文件。

    优化策略：先用 GitHub Contents API 获取根目录清单（1次请求），
    再只下载清单中存在的依赖文件，避免对每个文件都盲目尝试多个分支。
    """
    # 1. 获取根目录文件清单（1 次 API 请求）
    try:
        contents = fetcher._get(
            f"{fetcher.API_BASE}/repos/{owner}/{repo}/contents/?ref={branch}"
        )
        if not isinstance(contents, list):
            raise RuntimeError("contents 返回非列表")
        root_files = {item["name"] for item in contents if item.get("type") == "file"}
    except Exception:
        # API 失败时回退：手动尝试所有文件（兼容性保底）
        root_files = _KNOWN_DEP_FILES

    # 2. 只下载清单中实际存在的依赖文件
    to_fetch = root_files & _KNOWN_DEP_FILES
    result = {}
    raw_base = f"{fetcher.RAW_BASE}/{owner}/{repo}/{branch}"
    for fname in sorted(to_fetch):
        text = fetcher._get_raw(f"{raw_base}/{fname}")
        if text is not None:
            result[fname] = text[:15000]
    return result


# ─────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────

def fetch_project(identifier: str) -> RepoInfo:
    """
    一站式获取项目的所有安装相关信息。

    支持多平台自动路由：
      - GitHub URL / owner/repo      → GitHub API（带缓存+ETag）
      - GitLab/Bitbucket/Gitee/Codeberg URL → multi_source Provider
      - 本地路径                     → 请用 fetch_project_from_path()

    Args:
        identifier: 平台 URL / "owner/repo" / 项目名

    Returns:
        RepoInfo 包含 README、依赖文件、项目类型等
    """
    from multi_source import detect_platform, get_provider

    platform, ms_owner, ms_repo = detect_platform(identifier)

    # ── 非 GitHub 平台：走 multi_source Provider ──
    if platform != "github":
        logger.info(f"🌐 检测到 {platform} 平台，使用对应 Provider...")
        provider = get_provider(platform)
        meta = provider.get_repo_metadata(ms_owner, ms_repo)
        readme = provider.get_readme(ms_owner, ms_repo, meta.default_branch)

        # 获取依赖文件
        dep_files = {}
        for fname in sorted(_KNOWN_DEP_FILES):
            content = provider.get_file_content(ms_owner, ms_repo, fname, meta.default_branch)
            if content:
                dep_files[fname] = content[:15000]

        repo_data = {"language": meta.language}
        project_types = detect_project_types(repo_data, readme, dep_files)

        return RepoInfo(
            owner=meta.owner,
            repo=meta.repo,
            full_name=meta.full_name,
            description=meta.description,
            stars=meta.stars,
            language=meta.language or "Unknown",
            license=meta.license or "Unknown",
            default_branch=meta.default_branch,
            readme=readme[:15000],
            project_type=project_types,
            dependency_files=dep_files,
            clone_url=meta.clone_url,
            homepage=meta.homepage,
        )

    # ── GitHub：保留原有流程（带缓存 + ETag + 搜索） ──
    fetcher = GitHubFetcher()

    # 1. 解析 owner/repo
    owner, repo = parse_repo_identifier(identifier)
    if not owner:
        logger.info(t("fetcher.searching", repo=repo))
        owner, repo = fetcher.search_repo(repo)
    
    logger.info(t("fetcher.fetching_info", owner=owner, repo=repo))
    
    # 2. 基本信息
    repo_data = fetcher.fetch_repo_info(owner, repo)
    branch = repo_data.get("default_branch", "main")
    
    # 3. README
    logger.info(t("fetcher.reading_readme"))
    readme = fetcher.fetch_readme(owner, repo, branch)
    
    # 4. 依赖文件
    logger.info(t("fetcher.detecting_deps"))
    dep_files = extract_dependency_files(fetcher, owner, repo, branch)
    
    # 5. 项目类型
    project_types = detect_project_types(repo_data, readme, dep_files)
    
    return RepoInfo(
        owner=owner,
        repo=repo,
        full_name=f"{owner}/{repo}",
        description=repo_data.get("description") or "",
        stars=repo_data.get("stargazers_count", 0),
        language=repo_data.get("language") or "Unknown",
        license=(repo_data.get("license") or {}).get("spdx_id", "Unknown"),
        default_branch=branch,
        readme=readme[:15000],   # 限制 README 长度，节省 LLM token
        project_type=project_types,
        dependency_files=dep_files,
        clone_url=repo_data.get("clone_url", f"https://github.com/{owner}/{repo}.git"),
        homepage=repo_data.get("homepage") or f"https://github.com/{owner}/{repo}",
    )


def format_project_summary(info: RepoInfo) -> str:
    """格式化项目摘要"""
    stars = f"{info.stars:,}"
    types = " | ".join(info.project_type) or "Unknown"
    return (
        f"📦 {info.full_name}\n"
        f"   ⭐ {stars} stars | 语言：{info.language} | 类型：{types}\n"
        f"   📝 {info.description[:100]}\n"
        f"   🔗 {info.homepage}"
    )


# ─────────────────────────────────────────────
#  本地模式：git clone --depth 1 分析
# ─────────────────────────────────────────────

def _detect_language_from_files(root: Path) -> str:
    """通过文件扩展名统计推断主要语言"""
    ext_lang = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust",
        ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".swift": "Swift",
        ".c": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".h": "C", ".hpp": "C++",
        ".dart": "Dart", ".scala": "Scala", ".sh": "Shell",
        ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
        ".hs": "Haskell", ".lua": "Lua", ".pl": "Perl", ".pm": "Perl",
        ".r": "R", ".R": "R", ".jl": "Julia", ".zig": "Zig",
        ".clj": "Clojure", ".nim": "Nim", ".cr": "Crystal",
        ".tf": "HCL", ".hcl": "HCL",
    }
    # 排除测试/vendor 目录（与 GitHub Linguist 同理，避免测试脚本干扰主语言检测）
    _SKIP_DIRS = {"t", "test", "tests", "spec", "vendor", "node_modules",
                  "third_party", "thirdparty", "fixtures", "testdata"}
    counts: dict[str, int] = {}
    try:
        for f in root.rglob("*"):
            if f.is_file() and not any(p.startswith(".") for p in f.relative_to(root).parts):
                # 跳过测试/第三方目录中的文件
                rel_parts = f.relative_to(root).parts
                if rel_parts and rel_parts[0].lower() in _SKIP_DIRS:
                    continue
                lang = ext_lang.get(f.suffix.lower())
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1
    except Exception:
        pass
    if not counts:
        return "Unknown"
    return max(counts, key=counts.get)


def _find_readme(root: Path) -> str:
    """在仓库根目录找 README 文件并读内容"""
    candidates = ["README.md", "readme.md", "README.rst", "README.txt", "README"]
    for name in candidates:
        p = root / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:15000]
            except Exception:
                pass
    return ""


def _extract_local_dep_files(root: Path) -> dict[str, str]:
    """从本地仓库递归提取依赖文件，保留相对路径。"""
    result = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = rel.parts
        name = p.name
        if name not in _KNOWN_DEP_FILES and not name.endswith((".cabal", ".nimble")):
            continue
        if any(part.lower() in _LOCAL_ANALYSIS_SKIP_DIRS for part in parts[:-1]):
            continue
        if any(part.startswith(".") for part in parts[:-1]):
            continue
        if len(parts) > 1 and parts[-1].startswith("."):
            continue
        try:
            result[rel.as_posix()] = p.read_text(encoding="utf-8", errors="replace")[:15000]
        except Exception:
            continue
    return result


def fetch_project_local(identifier: str) -> RepoInfo:
    """
    本地模式：git clone --depth 1 后分析项目信息。

    优势：
      - 无 API 限额（不受 60 次/小时限制）
      - 可离线工作（只需 git 可用）
      - 获取完整的根目录文件

    支持所有平台的 URL 和 owner/repo 格式。

    Args:
        identifier: 任意平台 URL / "owner/repo"

    Returns:
        RepoInfo 包含 README、依赖文件、项目类型等
    """
    from multi_source import detect_platform

    platform, owner, repo = detect_platform(identifier)
    if not owner:
        raise ValueError(
            f"本地模式需要完整的 owner/repo 格式，无法仅通过项目名 '{repo}' 分析。\n"
            f"请使用完整格式，如：owner/{repo}"
        )

    # 根据平台生成 clone URL
    if platform == "github":
        clone_url = f"https://github.com/{owner}/{repo}.git"
    elif platform == "gitlab":
        clone_url = f"https://gitlab.com/{owner}/{repo}.git"
    elif platform == "bitbucket":
        clone_url = f"https://bitbucket.org/{owner}/{repo}.git"
    elif platform == "gitee":
        clone_url = f"https://gitee.com/{owner}/{repo}.git"
    elif platform == "codeberg":
        clone_url = f"https://codeberg.org/{owner}/{repo}.git"
    else:
        clone_url = f"https://github.com/{owner}/{repo}.git"

    homepage = clone_url.removesuffix(".git")
    
    # 使用临时目录进行 shallow clone
    tmp_dir = tempfile.mkdtemp(prefix="gitinstall_")
    clone_path = Path(tmp_dir) / repo
    
    try:
        logger.info(t("fetcher.cloning", owner=owner, repo=repo))
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", clone_url, str(clone_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "not found" in stderr.lower() or "does not exist" in stderr.lower():
                raise FileNotFoundError(f"GitHub 上找不到项目：{owner}/{repo}")
            raise RuntimeError(f"git clone 失败：{stderr}")
        
        # 本地分析
        logger.info(t("fetcher.local_analysis"))
        readme = _find_readme(clone_path)
        dep_files = _extract_local_dep_files(clone_path)
        language = _detect_language_from_files(clone_path)
        
        # 构建类似 GitHub API 返回的 repo_data 结构
        repo_data = {"language": language}
        project_types = detect_project_types(repo_data, readme, dep_files)
        
        return RepoInfo(
            owner=owner,
            repo=repo,
            full_name=f"{owner}/{repo}",
            description="",   # 本地模式无法获取描述
            stars=0,           # 本地模式无法获取 stars
            language=language,
            license="Unknown", # 可后续从 LICENSE 文件检测
            default_branch="main",
            readme=readme,
            project_type=project_types,
            dependency_files=dep_files,
            clone_url=clone_url,
            homepage=homepage,
        )
    finally:
        # 清理临时目录
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─────────────────────────────────────────────
#  本地路径模式：直接分析已有目录
# ─────────────────────────────────────────────

def is_local_path(identifier: str) -> bool:
    """判断标识符是否为本地文件系统路径。"""
    s = identifier.strip()
    return (
        s.startswith("/")
        or s.startswith("./")
        or s.startswith("../")
        or s.startswith("~/")
        or s == "."
    )


def fetch_project_from_path(path: str) -> RepoInfo:
    """
    直接分析本地目录中的项目信息，不做任何网络请求。

    与 fetch_project_local() 的区别：
      - fetch_project_local()  → git clone 后分析（仍需网络）
      - fetch_project_from_path() → 直接读本地目录（完全离线）

    适用场景：
      - 企业私有项目（不在 GitHub 上）
      - 本地开发中的项目
      - OTA 下载后的软件包
      - 任何已经存在于文件系统上的代码

    Args:
        path: 本地目录路径（绝对或相对路径）

    Returns:
        RepoInfo 包含 README、依赖文件、项目类型等
    """
    # 展开 ~ 和解析为绝对路径
    root = Path(path).expanduser().resolve()

    if not root.is_dir():
        raise FileNotFoundError(f"本地路径不存在或不是目录：{root}")

    logger.info(f"📂 分析本地项目：{root}")

    # 从目录名推导 repo 名
    repo_name = root.name
    # 尝试从 .git/config 获取远程 URL → 推导 owner
    owner = "_local"
    clone_url = str(root)
    try:
        git_config = root / ".git" / "config"
        if git_config.is_file():
            content = git_config.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'url\s*=\s*\S+[:/]([^/\s]+)/([^/\s.]+?)(?:\.git)?\s*$',
                          content, re.MULTILINE)
            if m:
                owner = m.group(1)
                repo_name = m.group(2)
                clone_url = re.search(r'url\s*=\s*(\S+)', content).group(1)
    except Exception:
        pass

    # 本地分析
    readme = _find_readme(root)
    dep_files = _extract_local_dep_files(root)
    language = _detect_language_from_files(root)

    # 检测许可证
    license_id = "Unknown"
    for lname in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"):
        lp = root / lname
        if lp.is_file():
            license_id = "Detected"
            break

    repo_data = {"language": language}
    project_types = detect_project_types(repo_data, readme, dep_files)

    return RepoInfo(
        owner=owner,
        repo=repo_name,
        full_name=f"{owner}/{repo_name}" if owner != "_local" else repo_name,
        description="",
        stars=0,
        language=language,
        license=license_id,
        default_branch="",
        readme=readme,
        project_type=project_types,
        dependency_files=dep_files,
        clone_url=clone_url,
        homepage="",
    )
