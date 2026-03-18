"""
multi_source.py - 多源代码托管平台支持
========================================

灵感来源：OpenClaw 22+ 渠道集成模式

支持从多个代码托管平台安装项目：
  1. GitHub     (github.com)        — 主力支持
  2. GitLab     (gitlab.com)        — 完整支持
  3. Bitbucket  (bitbucket.org)     — 完整支持
  4. Gitee      (gitee.com)         — 国内镜像
  5. Codeberg   (codeberg.org)      — 开源替代
  6. 自定义 Git URL                  — 通用 git clone

架构：
  统一的 SourceProvider 接口，每个平台一个实现。
  自动从 URL 识别平台 → 路由到对应 Provider。

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RepoMetadata:
    """统一的仓库元数据"""
    platform: str           # github, gitlab, bitbucket, gitee, codeberg
    owner: str
    repo: str
    full_name: str          # owner/repo
    description: str = ""
    stars: int = 0
    language: str = ""
    clone_url: str = ""
    homepage: str = ""
    license: str = ""
    default_branch: str = "main"
    topics: list[str] = field(default_factory=list)
    is_fork: bool = False
    is_archived: bool = False
    last_push: str = ""


class SourceProvider(ABC):
    """代码托管平台统一接口"""

    platform_name: str = ""
    api_base: str = ""
    raw_base: str = ""

    @abstractmethod
    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        """获取仓库元数据"""

    @abstractmethod
    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        """获取 README 内容"""

    @abstractmethod
    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        """获取指定文件内容"""

    def get_clone_url(self, owner: str, repo: str) -> str:
        """获取 clone URL"""
        return f"https://{self.platform_name}/{owner}/{repo}.git"

    def _api_get(self, url: str, headers: dict = None) -> dict:
        """通用 API GET 请求"""
        hdrs = {"User-Agent": "gitinstall/1.0", "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(f"仓库不存在: {url}")
            raise ConnectionError(f"API 请求失败 ({e.code}): {url}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"网络错误: {e}")

    def _raw_get(self, url: str) -> str:
        """获取原始文本内容"""
        hdrs = {"User-Agent": "gitinstall/1.0"}
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode(errors="replace")
        except Exception:
            return ""


# ─────────────────────────────────────────────
#  GitHub Provider
# ─────────────────────────────────────────────

class GitHubProvider(SourceProvider):
    platform_name = "github.com"
    api_base = "https://api.github.com"
    raw_base = "https://raw.githubusercontent.com"

    def __init__(self):
        self._token = os.getenv("GITHUB_TOKEN", "").strip()

    def _auth_headers(self) -> dict:
        h = {}
        if self._token:
            h["Authorization"] = f"token {self._token}"
        return h

    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        url = f"{self.api_base}/repos/{owner}/{repo}"
        data = self._api_get(url, self._auth_headers())
        return RepoMetadata(
            platform="github",
            owner=data.get("owner", {}).get("login", owner),
            repo=data.get("name", repo),
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description", "") or "",
            stars=data.get("stargazers_count", 0),
            language=data.get("language", "") or "",
            clone_url=data.get("clone_url", self.get_clone_url(owner, repo)),
            homepage=data.get("homepage", "") or "",
            license=(data.get("license") or {}).get("spdx_id", ""),
            default_branch=data.get("default_branch", "main"),
            topics=data.get("topics", []),
            is_fork=data.get("fork", False),
            is_archived=data.get("archived", False),
            last_push=data.get("pushed_at", ""),
        )

    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        branch = branch or "main"
        for fname in ["README.md", "README.rst", "README.txt", "README", "readme.md"]:
            content = self._raw_get(f"{self.raw_base}/{owner}/{repo}/{branch}/{fname}")
            if content:
                return content[:50000]
        return ""

    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        branch = branch or "main"
        return self._raw_get(f"{self.raw_base}/{owner}/{repo}/{branch}/{path}")


# ─────────────────────────────────────────────
#  GitLab Provider
# ─────────────────────────────────────────────

class GitLabProvider(SourceProvider):
    platform_name = "gitlab.com"
    api_base = "https://gitlab.com/api/v4"

    def __init__(self):
        self._token = os.getenv("GITLAB_TOKEN", "").strip()

    def _auth_headers(self) -> dict:
        h = {}
        if self._token:
            h["PRIVATE-TOKEN"] = self._token
        return h

    def _project_id(self, owner: str, repo: str) -> str:
        return urllib.parse.quote(f"{owner}/{repo}", safe="")

    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        pid = self._project_id(owner, repo)
        url = f"{self.api_base}/projects/{pid}"
        data = self._api_get(url, self._auth_headers())
        ns = data.get("namespace", {})
        return RepoMetadata(
            platform="gitlab",
            owner=ns.get("path", owner),
            repo=data.get("path", repo),
            full_name=data.get("path_with_namespace", f"{owner}/{repo}"),
            description=data.get("description", "") or "",
            stars=data.get("star_count", 0),
            language="",  # GitLab API 需要额外请求获取语言
            clone_url=data.get("http_url_to_repo", self.get_clone_url(owner, repo)),
            homepage=data.get("web_url", ""),
            license="",
            default_branch=data.get("default_branch", "main"),
            topics=data.get("topics", []) or data.get("tag_list", []),
            is_fork=data.get("forked_from_project") is not None,
            is_archived=data.get("archived", False),
            last_push=data.get("last_activity_at", ""),
        )

    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        pid = self._project_id(owner, repo)
        branch = branch or "main"
        for fname in ["README.md", "README.rst", "README.txt", "README"]:
            encoded = urllib.parse.quote(fname, safe="")
            url = f"{self.api_base}/projects/{pid}/repository/files/{encoded}/raw?ref={branch}"
            content = self._raw_get(url)
            if content:
                return content[:50000]
        return ""

    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        pid = self._project_id(owner, repo)
        branch = branch or "main"
        encoded = urllib.parse.quote(path, safe="")
        url = f"{self.api_base}/projects/{pid}/repository/files/{encoded}/raw?ref={branch}"
        return self._raw_get(url)


# ─────────────────────────────────────────────
#  Bitbucket Provider
# ─────────────────────────────────────────────

class BitbucketProvider(SourceProvider):
    platform_name = "bitbucket.org"
    api_base = "https://api.bitbucket.org/2.0"

    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        url = f"{self.api_base}/repositories/{owner}/{repo}"
        data = self._api_get(url)
        return RepoMetadata(
            platform="bitbucket",
            owner=owner,
            repo=repo,
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description", "") or "",
            stars=0,  # Bitbucket 不公开 star 数
            language=data.get("language", "") or "",
            clone_url=f"https://bitbucket.org/{owner}/{repo}.git",
            homepage=data.get("website", "") or "",
            license="",
            default_branch=data.get("mainbranch", {}).get("name", "main"),
            is_fork=data.get("parent") is not None,
        )

    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        branch = branch or "main"
        for fname in ["README.md", "README.rst", "README.txt", "README"]:
            url = f"https://bitbucket.org/{owner}/{repo}/raw/{branch}/{fname}"
            content = self._raw_get(url)
            if content:
                return content[:50000]
        return ""

    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        branch = branch or "main"
        url = f"https://bitbucket.org/{owner}/{repo}/raw/{branch}/{path}"
        return self._raw_get(url)


# ─────────────────────────────────────────────
#  Gitee Provider (国内镜像)
# ─────────────────────────────────────────────

class GiteeProvider(SourceProvider):
    platform_name = "gitee.com"
    api_base = "https://gitee.com/api/v5"

    def __init__(self):
        self._token = os.getenv("GITEE_TOKEN", "").strip()

    def _auth_params(self) -> str:
        if self._token:
            return f"?access_token={self._token}"
        return ""

    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        url = f"{self.api_base}/repos/{owner}/{repo}{self._auth_params()}"
        data = self._api_get(url)
        return RepoMetadata(
            platform="gitee",
            owner=data.get("owner", {}).get("login", owner),
            repo=data.get("path", repo),
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description", "") or "",
            stars=data.get("stargazers_count", 0),
            language=data.get("language", "") or "",
            clone_url=data.get("html_url", "") + ".git" if data.get("html_url") else self.get_clone_url(owner, repo),
            homepage=data.get("homepage", "") or "",
            license=(data.get("license") or ""),
            default_branch=data.get("default_branch", "master"),
        )

    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        branch = branch or "master"
        for fname in ["README.md", "README.rst", "README.txt", "README"]:
            url = f"https://gitee.com/{owner}/{repo}/raw/{branch}/{fname}"
            content = self._raw_get(url)
            if content:
                return content[:50000]
        return ""

    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        branch = branch or "master"
        url = f"https://gitee.com/{owner}/{repo}/raw/{branch}/{path}"
        return self._raw_get(url)


# ─────────────────────────────────────────────
#  Codeberg Provider
# ─────────────────────────────────────────────

class CodebergProvider(SourceProvider):
    platform_name = "codeberg.org"
    api_base = "https://codeberg.org/api/v1"

    def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        url = f"{self.api_base}/repos/{owner}/{repo}"
        data = self._api_get(url)
        return RepoMetadata(
            platform="codeberg",
            owner=data.get("owner", {}).get("login", owner),
            repo=data.get("name", repo),
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description", "") or "",
            stars=data.get("stars_count", 0),
            language=data.get("language", "") or "",
            clone_url=data.get("clone_url", self.get_clone_url(owner, repo)),
            homepage=data.get("website", "") or "",
            default_branch=data.get("default_branch", "main"),
            is_fork=data.get("fork", False),
            is_archived=data.get("archived", False),
        )

    def get_readme(self, owner: str, repo: str, branch: str = None) -> str:
        branch = branch or "main"
        url = f"https://codeberg.org/{owner}/{repo}/raw/branch/{branch}/README.md"
        content = self._raw_get(url)
        if content:
            return content[:50000]
        return ""

    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        branch = branch or "main"
        url = f"https://codeberg.org/{owner}/{repo}/raw/branch/{branch}/{path}"
        return self._raw_get(url)


# ─────────────────────────────────────────────
#  平台自动识别 + 路由
# ─────────────────────────────────────────────

# 平台 URL 模式 → Provider 映射
_PLATFORM_PATTERNS = [
    (r'github\.com[:/]([^/]+)/([^/\s\.]+)', "github"),
    (r'gitlab\.com[:/]([^/]+)/([^/\s\.]+)', "gitlab"),
    (r'bitbucket\.org[:/]([^/]+)/([^/\s\.]+)', "bitbucket"),
    (r'gitee\.com[:/]([^/]+)/([^/\s\.]+)', "gitee"),
    (r'codeberg\.org[:/]([^/]+)/([^/\s\.]+)', "codeberg"),
]

_PROVIDERS = {
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
    "bitbucket": BitbucketProvider,
    "gitee": GiteeProvider,
    "codeberg": CodebergProvider,
}


def detect_platform(identifier: str) -> tuple[str, str, str]:
    """
    从 URL 或标识符自动检测平台。

    返回：(platform, owner, repo)
    platform 为 "github", "gitlab", "bitbucket", "gitee", "codeberg" 之一，
    未识别时默认 "github"。
    """
    identifier = identifier.strip().rstrip("/")

    for pattern, platform in _PLATFORM_PATTERNS:
        match = re.search(pattern, identifier, re.IGNORECASE)
        if match:
            owner = match.group(1)
            repo = match.group(2).removesuffix(".git")
            return platform, owner, repo

    # 没有匹配任何平台 URL → 假设是 GitHub 的 owner/repo
    if "/" in identifier and not identifier.startswith("http"):
        parts = identifier.split("/")
        if len(parts) >= 2:
            return "github", parts[0], parts[1]

    # 仅项目名
    return "github", "", identifier


def get_provider(platform: str) -> SourceProvider:
    """获取对应平台的 Provider 实例"""
    cls = _PROVIDERS.get(platform)
    if not cls:
        raise ValueError(f"不支持的平台: {platform}（支持: {', '.join(_PROVIDERS.keys())}）")
    return cls()


def fetch_from_any_source(identifier: str) -> RepoMetadata:
    """
    从任意源获取仓库元数据。

    自动检测平台并路由到对应 Provider。
    支持: GitHub, GitLab, Bitbucket, Gitee, Codeberg URL 或 owner/repo 格式。
    """
    platform, owner, repo = detect_platform(identifier)
    provider = get_provider(platform)
    return provider.get_repo_metadata(owner, repo)


def get_supported_platforms() -> list[dict]:
    """返回支持的平台列表"""
    return [
        {"name": "GitHub", "domain": "github.com", "key": "github", "env_token": "GITHUB_TOKEN"},
        {"name": "GitLab", "domain": "gitlab.com", "key": "gitlab", "env_token": "GITLAB_TOKEN"},
        {"name": "Bitbucket", "domain": "bitbucket.org", "key": "bitbucket", "env_token": ""},
        {"name": "Gitee", "domain": "gitee.com", "key": "gitee", "env_token": "GITEE_TOKEN"},
        {"name": "Codeberg", "domain": "codeberg.org", "key": "codeberg", "env_token": ""},
    ]
