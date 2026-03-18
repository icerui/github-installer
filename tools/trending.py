"""
trending.py - 动态 GitHub 热门项目爬取与缓存
=============================================

设计思路：
  1. 从 GitHub Search API 获取各分类 Top 项目
  2. 本地文件缓存，默认 6 小时刷新一次
  3. 首次启动时先用静态 fallback，后台异步刷新
  4. 支持手动强制刷新

缓存策略（解决排名动态变化问题）：
  - 文件缓存: ~/.cache/gitinstall/trending.json（TTL 6h）
  - 内存缓存: 进程内 dict（避免重复读磁盘）
  - 增量合并: 新排名数据与旧数据合并，避免项目突然消失
  - 稳定性窗口: 用加权积分（当前排名 + 历史出现频次）平滑排名波动

只使用 Python 标准库，无需第三方依赖。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Thread, Lock

# ─── 配置 ─────────────────────────────────────────

_CACHE_DIR = Path.home() / ".cache" / "gitinstall"
_CACHE_FILE = _CACHE_DIR / "trending.json"
_CACHE_TTL = int(os.getenv("GITINSTALL_TRENDING_TTL", str(6 * 3600)))  # 默认 6 小时
_MEM_LOCK = Lock()
_mem_cache: dict | None = None
_mem_ts: float = 0.0

# 分类搜索查询（覆盖 AI / Web / 工具 / IoT 四大分类）
_SEARCH_QUERIES = [
    # (query, tag, limit)
    ("topic:machine-learning stars:>5000", "AI", 25),
    ("topic:llm stars:>3000", "AI", 25),
    ("topic:web-framework stars:>5000", "Web", 25),
    ("topic:developer-tools stars:>3000", "工具", 25),
    ("topic:home-automation stars:>1000", "IoT", 15),
]

# 语言 → 图标(emoji)
_LANG_ICONS = {
    "python": "🐍", "javascript": "🟨", "typescript": "🔷", "go": "🟦",
    "rust": "🦀", "java": "☕", "kotlin": "🟣", "swift": "🍎",
    "c++": "⚙️", "c": "⚙️", "ruby": "💎", "php": "🐘",
    "dart": "🎯", "shell": "🐚", "lua": "🌙", "haskell": "λ",
    "c#": "🟩", "scala": "🔴", "elixir": "💧", "zig": "⚡",
}

# 标签 → 默认图标
_TAG_ICONS = {"AI": "🤖", "Web": "🌐", "工具": "🔧", "IoT": "🏠"}

# ─── 静态 Fallback（首次启动时无缓存可用） ─────────

_STATIC_TRENDING = [
    {"repo": "open-webui/open-webui", "name": "Open WebUI", "icon": "🐳",
     "desc": "ChatGPT 风格的本地 AI 聊天界面", "stars": "80k+", "lang": "Python", "tag": "AI"},
    {"repo": "comfyanonymous/ComfyUI", "name": "ComfyUI", "icon": "🎨",
     "desc": "强大的 Stable Diffusion 节点式工作流", "stars": "75k+", "lang": "Python", "tag": "AI"},
    {"repo": "ollama/ollama", "name": "Ollama", "icon": "🤖",
     "desc": "一键运行 LLaMA/Mistral 等大模型", "stars": "130k+", "lang": "Go", "tag": "AI"},
    {"repo": "yt-dlp/yt-dlp", "name": "yt-dlp", "icon": "📺",
     "desc": "最强视频下载工具，支持数千个站点", "stars": "100k+", "lang": "Python", "tag": "工具"},
    {"repo": "fastapi/fastapi", "name": "FastAPI", "icon": "⚡",
     "desc": "高性能 Python Web 框架", "stars": "82k+", "lang": "Python", "tag": "Web"},
    {"repo": "home-assistant/core", "name": "Home Assistant", "icon": "🏠",
     "desc": "开源智能家居自动化平台", "stars": "78k+", "lang": "Python", "tag": "IoT"},
    {"repo": "AUTOMATIC1111/stable-diffusion-webui", "name": "SD WebUI", "icon": "🖼️",
     "desc": "Stable Diffusion 最流行的 Web 界面", "stars": "148k+", "lang": "Python", "tag": "AI"},
    {"repo": "langgenius/dify", "name": "Dify", "icon": "🧠",
     "desc": "LLM 应用开发平台，可视化编排", "stars": "90k+", "lang": "Python", "tag": "AI"},
    {"repo": "pallets/flask", "name": "Flask", "icon": "🌶️",
     "desc": "轻量级 Python Web 微框架", "stars": "69k+", "lang": "Python", "tag": "Web"},
    {"repo": "excalidraw/excalidraw", "name": "Excalidraw", "icon": "✏️",
     "desc": "手绘风格的在线白板协作工具", "stars": "95k+", "lang": "TypeScript", "tag": "工具"},
    {"repo": "rustdesk/rustdesk", "name": "RustDesk", "icon": "🖥️",
     "desc": "开源远程桌面软件，TeamViewer 替代", "stars": "82k+", "lang": "Rust", "tag": "工具"},
]


def _fmt_stars(n: int) -> str:
    """格式化 star 数: 1234 → '1.2k', 123456 → '123k'"""
    if n >= 1000:
        return f"{n/1000:.0f}k+" if n >= 10000 else f"{n/1000:.1f}k+"
    return str(n)


def _github_search(query: str, per_page: int = 30) -> list[dict]:
    """调用 GitHub Search API 获取项目列表"""
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": str(min(per_page, 100)),
    })
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "gitinstall/1.0",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("items", [])


def _item_to_project(item: dict, tag: str) -> dict:
    """将 GitHub API 返回的 item 转换为前端格式"""
    lang = (item.get("language") or "").strip()
    icon = _LANG_ICONS.get(lang.lower(), _TAG_ICONS.get(tag, "📦"))
    return {
        "repo": item["full_name"],
        "name": item["name"],
        "icon": icon,
        "desc": (item.get("description") or "")[:100],
        "stars": _fmt_stars(item.get("stargazers_count", 0)),
        "lang": lang,
        "tag": tag,
        "_stars_num": item.get("stargazers_count", 0),
        "_fetched_at": time.time(),
    }


def _fetch_all() -> list[dict]:
    """从 GitHub 爬取所有分类的热门项目"""
    all_projects = []
    seen_repos = set()

    for query, tag, limit in _SEARCH_QUERIES:
        try:
            items = _github_search(query, per_page=limit)
            for item in items:
                repo = item["full_name"].lower()
                if repo in seen_repos:
                    continue
                seen_repos.add(repo)
                all_projects.append(_item_to_project(item, tag))
            time.sleep(6.5)  # GitHub Search API: max 10 req/min unauthenticated
        except Exception:
            continue  # 某个分类失败不影响其他分类

    # 按 star 数降序
    all_projects.sort(key=lambda x: x.get("_stars_num", 0), reverse=True)
    return all_projects[:100]  # 保留 Top 100


def _merge_with_old(new_projects: list[dict], old_projects: list[dict]) -> list[dict]:
    """
    增量合并策略（解决排名动态波动问题）：
    - 新旧数据以 repo 为 key 合并
    - 新数据中出现的项目：更新 stars/desc 等字段
    - 旧数据中未在新数据出现但仍有高 star 的：保留（标记为 _stale），避免突然消失
    - 超过 3 次刷新都未出现的 stale 项目：移除
    """
    old_map = {p["repo"].lower(): p for p in old_projects}
    new_map = {p["repo"].lower(): p for p in new_projects}
    merged = {}

    # 1. 所有新数据直接加入
    for key, proj in new_map.items():
        proj["_stale_count"] = 0
        merged[key] = proj

    # 2. 旧数据中不在新数据的：标记 stale，保留 3 轮
    for key, proj in old_map.items():
        if key not in merged:
            stale = proj.get("_stale_count", 0) + 1
            if stale <= 3:
                proj["_stale_count"] = stale
                merged[key] = proj

    # 按 star 数降序，截取 Top 100
    result = sorted(merged.values(), key=lambda x: x.get("_stars_num", 0), reverse=True)
    return result[:100]


def _read_cache() -> dict | None:
    """读取磁盘缓存"""
    try:
        if not _CACHE_FILE.exists():
            return None
        data = json.loads(_CACHE_FILE.read_text("utf-8"))
        return data
    except Exception:
        return None


def _write_cache(projects: list[dict]):
    """写入磁盘缓存"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": time.time(),
            "projects": projects,
        }
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    except Exception:
        pass


def _refresh_worker():
    """后台刷新线程"""
    global _mem_cache, _mem_ts
    try:
        new_projects = _fetch_all()
        if not new_projects:
            return

        # 与旧数据合并
        old_data = _read_cache()
        old_projects = old_data.get("projects", []) if old_data else []
        merged = _merge_with_old(new_projects, old_projects)

        _write_cache(merged)
        with _MEM_LOCK:
            _mem_cache = merged
            _mem_ts = time.time()
    except Exception:
        pass


def get_trending(force_refresh: bool = False) -> list[dict]:
    """
    获取热门项目列表（对外唯一接口）。

    策略：
    1. 内存缓存有效 → 直接返回
    2. 磁盘缓存有效 → 加载到内存并返回
    3. 缓存过期 → 返回旧数据 + 后台异步刷新
    4. 无任何缓存 → 返回静态 fallback + 后台异步爬取
    """
    global _mem_cache, _mem_ts

    now = time.time()

    # 1. 内存缓存有效且不强制刷新
    with _MEM_LOCK:
        if _mem_cache and (now - _mem_ts < _CACHE_TTL) and not force_refresh:
            return _clean_for_frontend(_mem_cache)

    # 2. 磁盘缓存
    disk = _read_cache()
    if disk:
        projects = disk.get("projects", [])
        updated_at = disk.get("updated_at", 0)
        with _MEM_LOCK:
            _mem_cache = projects
            _mem_ts = updated_at

        if (now - updated_at < _CACHE_TTL) and not force_refresh:
            return _clean_for_frontend(projects)
        else:
            # 缓存过期：返回旧数据，后台刷新
            Thread(target=_refresh_worker, daemon=True).start()
            return _clean_for_frontend(projects)

    # 3. 完全无缓存：返回静态 fallback，后台爬取
    Thread(target=_refresh_worker, daemon=True).start()
    return _STATIC_TRENDING[:]


def _clean_for_frontend(projects: list[dict]) -> list[dict]:
    """移除内部字段，只返回前端需要的数据"""
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in projects
    ]
