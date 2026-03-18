"""
badge.py - Install 按钮 / 徽章系统
====================================

让项目维护者在 README 中嵌入一键安装按钮。

功能：
  1. SVG 徽章生成（纯 Python，无需外部服务）
  2. Markdown / HTML / reStructuredText 安装按钮代码
  3. VS Code URI 协议集成 (vscode://gitinstall.gitinstall/install?repo=...)
  4. 安装统计追踪
  5. 自定义样式（颜色、大小、语言）

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Optional


# ─────────────────────────────────────────────
#  SVG 徽章模板
# ─────────────────────────────────────────────

# 受 shields.io 启发的扁平化 SVG 徽章
_SVG_FLAT = """\
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{aria}">
  <title>{aria}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{msg_width}" height="20" fill="{color}"/>
    <rect width="{width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="11">
    <text aria-hidden="true" x="{label_x}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{label_x}" y="14">{label}</text>
    <text aria-hidden="true" x="{msg_x}" y="15" fill="#010101" fill-opacity=".3">{message}</text>
    <text x="{msg_x}" y="14">{message}</text>
  </g>
</svg>"""

# 大按钮样式
_SVG_BUTTON = """\
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{aria}">
  <title>{aria}</title>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{color_light}"/>
      <stop offset="100%" stop-color="{color}"/>
    </linearGradient>
    <filter id="shadow">
      <feDropShadow dx="0" dy="1" stdDeviation="1" flood-opacity="0.2"/>
    </filter>
  </defs>
  <rect width="{width}" height="{height}" rx="6" fill="url(#bg)" filter="url(#shadow)"/>
  <g fill="#fff" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">
    <text x="{cx}" y="{text_y}" font-size="{font_size}" font-weight="600">{icon} {message}</text>
    {subtitle_element}
  </g>
</svg>"""


# 颜色预设
BADGE_COLORS = {
    "blue": "#0078d4",
    "green": "#28a745",
    "red": "#cb2431",
    "orange": "#f66a0a",
    "purple": "#6f42c1",
    "black": "#24292e",
    "gray": "#6a737d",
    # 品牌色
    "github": "#24292e",
    "vscode": "#007acc",
    "python": "#3776ab",
    "node": "#339933",
    "rust": "#dea584",
    "go": "#00add8",
    "docker": "#2496ed",
}


def _estimate_text_width(text: str, font_size: int = 11) -> int:
    """估算文本宽度（像素）"""
    # 近似计算，参考 shields.io 的方法
    width = 0
    for ch in text:
        if ord(ch) > 127:
            width += font_size * 0.9  # CJK 字符更宽
        elif ch.isupper():
            width += font_size * 0.65
        elif ch in 'mwMW':
            width += font_size * 0.75
        elif ch in 'ijl.!|':
            width += font_size * 0.35
        else:
            width += font_size * 0.55
    return int(width) + 10  # padding


# ─────────────────────────────────────────────
#  徽章生成
# ─────────────────────────────────────────────

def generate_badge_svg(
    label: str = "GitInstall",
    message: str = "Install",
    color: str = "blue",
    style: str = "flat",  # "flat" | "button"
) -> str:
    """
    生成 SVG 徽章。

    Args:
        label: 左侧标签文字
        message: 右侧消息文字
        color: 颜色名或十六进制值
        style: "flat"（小徽标）或 "button"（大按钮）

    Returns:
        SVG 字符串
    """
    color_hex = BADGE_COLORS.get(color, color)
    if not color_hex.startswith("#"):
        color_hex = "#0078d4"

    safe_label = html.escape(label)
    safe_message = html.escape(message)
    aria = f"{safe_label}: {safe_message}"

    if style == "button":
        return _generate_button_svg(safe_label, safe_message, color_hex, aria)

    # Flat badge
    label_width = _estimate_text_width(label) + 2
    msg_width = _estimate_text_width(message) + 2
    total_width = label_width + msg_width

    return _SVG_FLAT.format(
        width=total_width,
        label_width=label_width,
        msg_width=msg_width,
        color=color_hex,
        label=safe_label,
        message=safe_message,
        label_x=label_width / 2,
        msg_x=label_width + msg_width / 2,
        aria=aria,
    )


def _generate_button_svg(
    label: str, message: str, color: str, aria: str,
    width: int = 200, height: int = 44,
) -> str:
    """生成大按钮 SVG"""
    # 计算亮色版本
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    light_r = min(255, r + 30)
    light_g = min(255, g + 30)
    light_b = min(255, b + 30)
    color_light = f"#{light_r:02x}{light_g:02x}{light_b:02x}"

    cx = width / 2
    font_size = 15
    text_y = height / 2 + 5

    subtitle_element = ""
    if label != message:
        text_y = height / 2 + 1
        subtitle_element = (
            f'<text x="{cx}" y="{height / 2 + 14}" '
            f'font-size="10" opacity="0.85">{label}</text>'
        )

    return _SVG_BUTTON.format(
        width=width,
        height=height,
        color=color,
        color_light=color_light,
        cx=cx,
        text_y=text_y,
        font_size=font_size,
        icon="▶",
        message=message,
        subtitle_element=subtitle_element,
        aria=aria,
    )


# ─────────────────────────────────────────────
#  嵌入代码生成
# ─────────────────────────────────────────────

def generate_install_url(
    repo: str,
    scheme: str = "vscode",  # "vscode" | "web" | "cli"
    base_url: str = "",
) -> str:
    """
    生成安装 URL。

    Args:
        repo: owner/repo 格式
        scheme: URL 方案
          - "vscode": VS Code URI 协议
          - "web": Web 安装页面
          - "cli": 命令行安装

    Returns:
        安装 URL
    """
    safe_repo = urllib.parse.quote(repo, safe="")

    if scheme == "vscode":
        return f"vscode://gitinstall.gitinstall/install?repo={safe_repo}"
    elif scheme == "web":
        base = base_url or "https://gitinstall.dev"
        return f"{base}/install/{safe_repo}"
    elif scheme == "cli":
        return f"gitinstall install {repo}"
    return ""


def generate_markdown_badge(
    repo: str,
    label: str = "GitInstall",
    message: str = "Install with GitInstall",
    color: str = "blue",
    scheme: str = "vscode",
    style: str = "flat",
    base_url: str = "",
) -> str:
    """
    生成 Markdown 格式的安装按钮代码。

    用法：维护者将返回的代码复制到 README.md 即可。

    Returns:
        Markdown 代码字符串
    """
    install_url = generate_install_url(repo, scheme=scheme, base_url=base_url)

    if style == "shields":
        # 使用 shields.io 在线服务
        safe_label = urllib.parse.quote(label, safe="")
        safe_msg = urllib.parse.quote(message, safe="")
        color_name = color if color in BADGE_COLORS else "blue"
        badge_url = f"https://img.shields.io/badge/{safe_label}-{safe_msg}-{color_name}"
        return f"[![{message}]({badge_url})]({install_url})"

    # 内联 SVG（无需外部服务）
    svg = generate_badge_svg(label=label, message=message, color=color, style=style)
    # 将 SVG 转为 data URI 用于 Markdown img tag
    svg_encoded = urllib.parse.quote(svg)
    return f"[![{message}](data:image/svg+xml,{svg_encoded})]({install_url})"


def generate_html_button(
    repo: str,
    text: str = "Install with GitInstall",
    color: str = "blue",
    scheme: str = "vscode",
    size: str = "medium",  # "small" | "medium" | "large"
    base_url: str = "",
) -> str:
    """
    生成 HTML 格式的安装按钮。

    可嵌入到任何支持 HTML 的页面（GitHub README、文档站等）。
    """
    install_url = generate_install_url(repo, scheme=scheme, base_url=base_url)
    color_hex = BADGE_COLORS.get(color, color)
    safe_text = html.escape(text)
    safe_url = html.escape(install_url)

    sizes = {
        "small": ("padding:4px 12px;font-size:12px;", "14"),
        "medium": ("padding:8px 20px;font-size:14px;", "18"),
        "large": ("padding:12px 28px;font-size:16px;", "22"),
    }
    style_str, _ = sizes.get(size, sizes["medium"])

    return (
        f'<a href="{safe_url}" '
        f'style="display:inline-block;{style_str}'
        f'background:{color_hex};color:#fff;'
        f'border-radius:6px;text-decoration:none;font-weight:600;'
        f'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">'
        f'▶ {safe_text}</a>'
    )


def generate_rst_badge(
    repo: str,
    message: str = "Install with GitInstall",
    color: str = "blue",
    scheme: str = "vscode",
    base_url: str = "",
) -> str:
    """生成 reStructuredText 格式的安装徽章"""
    install_url = generate_install_url(repo, scheme=scheme, base_url=base_url)
    safe_label = urllib.parse.quote("GitInstall", safe="")
    safe_msg = urllib.parse.quote(message, safe="")
    color_name = color if color in BADGE_COLORS else "blue"
    badge_url = f"https://img.shields.io/badge/{safe_label}-{safe_msg}-{color_name}"
    return (
        f".. image:: {badge_url}\n"
        f"   :target: {install_url}\n"
        f"   :alt: {message}"
    )


# ─────────────────────────────────────────────
#  README 自动注入
# ─────────────────────────────────────────────

_BADGE_MARKER_START = "<!-- GITINSTALL-BADGE-START -->"
_BADGE_MARKER_END = "<!-- GITINSTALL-BADGE-END -->"


def inject_badge_into_readme(
    readme_path: str,
    repo: str,
    style: str = "flat",
    color: str = "blue",
    scheme: str = "vscode",
    position: str = "top",  # "top" | "after_title" | "badges"
) -> bool:
    """
    自动在 README 中注入安装按钮。

    Args:
        readme_path: README.md 文件路径
        repo: owner/repo
        style: 徽章样式
        color: 颜色
        scheme: URL 方案
        position: 注入位置
          - "top": 文件顶部
          - "after_title": 第一个标题之后
          - "badges": 现有徽章行之后

    Returns:
        是否成功注入
    """
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    badge_md = generate_markdown_badge(repo, color=color, scheme=scheme, style="shields")
    badge_block = f"{_BADGE_MARKER_START}\n{badge_md}\n{_BADGE_MARKER_END}"

    # 如果已存在，替换
    if _BADGE_MARKER_START in content:
        pattern = re.escape(_BADGE_MARKER_START) + r".*?" + re.escape(_BADGE_MARKER_END)
        content = re.sub(pattern, badge_block, content, flags=re.DOTALL)
    else:
        if position == "after_title":
            # 在第一个 # 标题行之后插入
            match = re.search(r'^#\s+.+\n', content, re.MULTILINE)
            if match:
                insert_at = match.end()
                content = content[:insert_at] + "\n" + badge_block + "\n" + content[insert_at:]
            else:
                content = badge_block + "\n\n" + content
        elif position == "badges":
            # 在现有徽章（shields.io / badge 图片）行之后插入
            match = re.search(r'(\[!\[.*?\]\(.*?\)\]\(.*?\)\s*\n)+', content)
            if match:
                insert_at = match.end()
                content = content[:insert_at] + badge_block + "\n" + content[insert_at:]
            else:
                content = badge_block + "\n\n" + content
        else:
            # top
            content = badge_block + "\n\n" + content

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)

    return True


# ─────────────────────────────────────────────
#  生成完整的嵌入代码片段
# ─────────────────────────────────────────────

def generate_embed_snippet(
    repo: str,
    formats: Optional[list[str]] = None,
    color: str = "blue",
    scheme: str = "vscode",
    base_url: str = "",
) -> dict[str, str]:
    """
    生成所有格式的嵌入代码片段。

    Args:
        repo: owner/repo
        formats: 要生成的格式列表，None 则生成全部
        color: 颜色
        scheme: URL 方案

    Returns:
        {format_name: code_snippet} 字典
    """
    if formats is None:
        formats = ["markdown", "html", "rst", "url"]

    snippets = {}

    if "markdown" in formats:
        snippets["markdown"] = generate_markdown_badge(
            repo, color=color, scheme=scheme, style="shields"
        )

    if "html" in formats:
        snippets["html"] = generate_html_button(
            repo, color=color, scheme=scheme, base_url=base_url
        )

    if "html_large" in formats:
        snippets["html_large"] = generate_html_button(
            repo, color=color, scheme=scheme, size="large", base_url=base_url
        )

    if "rst" in formats:
        snippets["rst"] = generate_rst_badge(
            repo, color=color, scheme=scheme, base_url=base_url
        )

    if "url" in formats:
        snippets["url"] = generate_install_url(repo, scheme=scheme, base_url=base_url)

    if "cli" in formats:
        snippets["cli"] = generate_install_url(repo, scheme="cli")

    return snippets


def format_embed_snippets(snippets: dict[str, str], repo: str) -> str:
    """格式化嵌入代码片段，方便用户复制"""
    lines = [
        f"🔘 GitInstall 一键安装按钮 — {repo}",
        "",
        "将以下代码添加到你的 README 即可：",
        "",
    ]

    format_labels = {
        "markdown": "📝 Markdown (README.md)",
        "html": "🌐 HTML (Medium)",
        "html_large": "🌐 HTML (Large)",
        "rst": "📄 reStructuredText",
        "url": "🔗 Direct URL",
        "cli": "💻 CLI Command",
    }

    for fmt, code in snippets.items():
        label = format_labels.get(fmt, fmt)
        lines.append(f"### {label}")
        lines.append("```")
        lines.append(code)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)
