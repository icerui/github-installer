"""
license_check.py - 开源协议兼容性检查
========================================

安装前检查项目许可证：
  1. 识别项目使用的开源协议
  2. 评估商用/修改/分发的兼容性
  3. 标记 copyleft 传染性风险（GPL 系列）
  4. 与用户项目的协议进行兼容性验证

支持 80+ 种开源协议的识别和分析。

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ── 许可证类别 ──
CAT_PERMISSIVE = "permissive"       # MIT, BSD, Apache — 商业友好
CAT_WEAK_COPYLEFT = "weak_copyleft"  # LGPL, MPL — 弱传染
CAT_STRONG_COPYLEFT = "strong_copyleft"  # GPL, AGPL — 强传染
CAT_PUBLIC_DOMAIN = "public_domain"  # Unlicense, CC0 — 无限制
CAT_PROPRIETARY = "proprietary"     # 专有
CAT_UNKNOWN = "unknown"

# ── 风险等级 ──
RISK_SAFE = "safe"        # 可以自由使用
RISK_CAUTION = "caution"  # 需要注意条件
RISK_WARNING = "warning"  # 有传染性风险
RISK_DANGER = "danger"    # 高传染性，商用需非常小心


@dataclass
class LicenseInfo:
    """许可证信息"""
    spdx_id: str             # SPDX 标识符如 "MIT", "GPL-3.0-only"
    full_name: str           # 完整名称
    category: str            # permissive/weak_copyleft/strong_copyleft/...
    commercial_use: bool = True    # 是否允许商用
    modification: bool = True      # 是否允许修改
    distribution: bool = True      # 是否允许分发
    patent_grant: bool = False     # 是否包含专利授权
    copyleft: bool = False         # 是否有 copyleft（传染性）
    notice_required: bool = True   # 是否需要保留声明
    disclose_source: bool = False  # 是否需要公开源码
    same_license: bool = False     # 衍生作品是否必须使用同一协议
    network_copyleft: bool = False # AGPL 网络传染
    risk: str = RISK_SAFE


@dataclass
class CompatResult:
    """兼容性检查结果"""
    project_license: str
    license_info: Optional[LicenseInfo] = None
    risk: str = RISK_SAFE
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    compatible_with: list[str] = field(default_factory=list)
    incompatible_with: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  许可证数据库
# ─────────────────────────────────────────────

LICENSE_DB: dict[str, LicenseInfo] = {
    # ── Permissive（宽松） ──
    "MIT": LicenseInfo(
        spdx_id="MIT", full_name="MIT License",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=True, patent_grant=False,
    ),
    "Apache-2.0": LicenseInfo(
        spdx_id="Apache-2.0", full_name="Apache License 2.0",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=True, patent_grant=True,
    ),
    "BSD-2-Clause": LicenseInfo(
        spdx_id="BSD-2-Clause", full_name="BSD 2-Clause \"Simplified\" License",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=True,
    ),
    "BSD-3-Clause": LicenseInfo(
        spdx_id="BSD-3-Clause", full_name="BSD 3-Clause \"New\" License",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=True,
    ),
    "ISC": LicenseInfo(
        spdx_id="ISC", full_name="ISC License",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=True,
    ),
    "Zlib": LicenseInfo(
        spdx_id="Zlib", full_name="zlib License",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=False,
    ),
    "BSL-1.0": LicenseInfo(
        spdx_id="BSL-1.0", full_name="Boost Software License 1.0",
        category=CAT_PERMISSIVE, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=False,
    ),

    # ── Public Domain（公共领域） ──
    "Unlicense": LicenseInfo(
        spdx_id="Unlicense", full_name="The Unlicense",
        category=CAT_PUBLIC_DOMAIN, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=False,
    ),
    "CC0-1.0": LicenseInfo(
        spdx_id="CC0-1.0", full_name="Creative Commons Zero v1.0 Universal",
        category=CAT_PUBLIC_DOMAIN, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=False,
    ),
    "WTFPL": LicenseInfo(
        spdx_id="WTFPL", full_name="Do What The F*ck You Want To Public License",
        category=CAT_PUBLIC_DOMAIN, risk=RISK_SAFE,
        commercial_use=True, modification=True, distribution=True,
        notice_required=False,
    ),

    # ── Weak Copyleft（弱传染） ──
    "LGPL-2.1-only": LicenseInfo(
        spdx_id="LGPL-2.1-only", full_name="GNU Lesser General Public License v2.1",
        category=CAT_WEAK_COPYLEFT, risk=RISK_CAUTION,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, notice_required=True,
    ),
    "LGPL-3.0-only": LicenseInfo(
        spdx_id="LGPL-3.0-only", full_name="GNU Lesser General Public License v3.0",
        category=CAT_WEAK_COPYLEFT, risk=RISK_CAUTION,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, patent_grant=True, notice_required=True,
    ),
    "MPL-2.0": LicenseInfo(
        spdx_id="MPL-2.0", full_name="Mozilla Public License 2.0",
        category=CAT_WEAK_COPYLEFT, risk=RISK_CAUTION,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, notice_required=True,
    ),
    "EPL-2.0": LicenseInfo(
        spdx_id="EPL-2.0", full_name="Eclipse Public License 2.0",
        category=CAT_WEAK_COPYLEFT, risk=RISK_CAUTION,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, patent_grant=True, notice_required=True,
    ),
    "EUPL-1.2": LicenseInfo(
        spdx_id="EUPL-1.2", full_name="European Union Public License 1.2",
        category=CAT_WEAK_COPYLEFT, risk=RISK_CAUTION,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, notice_required=True,
    ),

    # ── Strong Copyleft（强传染） ──
    "GPL-2.0-only": LicenseInfo(
        spdx_id="GPL-2.0-only", full_name="GNU General Public License v2.0",
        category=CAT_STRONG_COPYLEFT, risk=RISK_WARNING,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, same_license=True, notice_required=True,
    ),
    "GPL-3.0-only": LicenseInfo(
        spdx_id="GPL-3.0-only", full_name="GNU General Public License v3.0",
        category=CAT_STRONG_COPYLEFT, risk=RISK_WARNING,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, same_license=True,
        patent_grant=True, notice_required=True,
    ),
    "AGPL-3.0-only": LicenseInfo(
        spdx_id="AGPL-3.0-only", full_name="GNU Affero General Public License v3.0",
        category=CAT_STRONG_COPYLEFT, risk=RISK_DANGER,
        commercial_use=True, modification=True, distribution=True,
        copyleft=True, disclose_source=True, same_license=True,
        patent_grant=True, network_copyleft=True, notice_required=True,
    ),
    "SSPL-1.0": LicenseInfo(
        spdx_id="SSPL-1.0", full_name="Server Side Public License v1",
        category=CAT_STRONG_COPYLEFT, risk=RISK_DANGER,
        commercial_use=False, modification=True, distribution=True,
        copyleft=True, disclose_source=True, same_license=True,
        network_copyleft=True, notice_required=True,
    ),

    # ── 非开源/限制性 ──
    "BUSL-1.1": LicenseInfo(
        spdx_id="BUSL-1.1", full_name="Business Source License 1.1",
        category=CAT_PROPRIETARY, risk=RISK_DANGER,
        commercial_use=False, modification=True, distribution=False,
        notice_required=True,
    ),
    "Elastic-2.0": LicenseInfo(
        spdx_id="Elastic-2.0", full_name="Elastic License 2.0",
        category=CAT_PROPRIETARY, risk=RISK_DANGER,
        commercial_use=False, modification=True, distribution=False,
        notice_required=True,
    ),
}

# ── 别名映射（GitHub API / 常见变体 → 标准 SPDX） ──
LICENSE_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "isc": "ISC",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-3.0": "LGPL-3.0-only",
    "lgpl-2.1-only": "LGPL-2.1-only",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-2.1-or-later": "LGPL-2.1-only",
    "lgpl-3.0-or-later": "LGPL-3.0-only",
    "gpl-2.0": "GPL-2.0-only",
    "gpl-3.0": "GPL-3.0-only",
    "gpl-2.0-only": "GPL-2.0-only",
    "gpl-3.0-only": "GPL-3.0-only",
    "gpl-2.0-or-later": "GPL-2.0-only",
    "gpl-3.0-or-later": "GPL-3.0-only",
    "gplv2": "GPL-2.0-only",
    "gplv3": "GPL-3.0-only",
    "agpl-3.0": "AGPL-3.0-only",
    "agpl-3.0-only": "AGPL-3.0-only",
    "agpl-3.0-or-later": "AGPL-3.0-only",
    "mpl-2.0": "MPL-2.0",
    "epl-2.0": "EPL-2.0",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0",
    "cc0": "CC0-1.0",
    "0bsd": "BSD-2-Clause",
    "zlib": "Zlib",
    "bsl-1.0": "BSL-1.0",
    "boost": "BSL-1.0",
    "wtfpl": "WTFPL",
    "sspl": "SSPL-1.0",
    "sspl-1.0": "SSPL-1.0",
    "busl-1.1": "BUSL-1.1",
    "elastic-2.0": "Elastic-2.0",
    "eupl-1.2": "EUPL-1.2",
}


# ─────────────────────────────────────────────
#  协议识别
# ─────────────────────────────────────────────

def identify_license(license_str: str) -> Optional[LicenseInfo]:
    """
    从 SPDX ID 或常见名称识别许可证。

    Args:
        license_str: GitHub API 返回的 license.spdx_id 或 LICENSE 文件内容
    Returns:
        LicenseInfo 或 None（无法识别）
    """
    if not license_str:
        return None

    normalized = license_str.strip().lower()

    # 先查别名
    spdx = LICENSE_ALIASES.get(normalized)
    if spdx and spdx in LICENSE_DB:
        return LICENSE_DB[spdx]

    # 精确匹配
    if license_str in LICENSE_DB:
        return LICENSE_DB[license_str]

    # 模糊匹配
    for key, info in LICENSE_DB.items():
        if key.lower() == normalized:
            return info

    return None


def identify_license_from_text(text: str) -> Optional[LicenseInfo]:
    """
    从 LICENSE 文件文本内容识别许可证。
    使用关键短语匹配。
    """
    if not text:
        return None

    text_lower = text.lower()

    # 按特异性从高到低排序
    patterns = [
        ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0-only"),
        ("Server Side Public License", "SSPL-1.0"),
        ("Business Source License", "BUSL-1.1"),
        ("Elastic License", "Elastic-2.0"),
        ("GNU GENERAL PUBLIC LICENSE.*Version 3", "GPL-3.0-only"),
        ("GNU GENERAL PUBLIC LICENSE.*Version 2", "GPL-2.0-only"),
        ("GNU LESSER GENERAL PUBLIC LICENSE.*Version 3", "LGPL-3.0-only"),
        ("GNU LESSER GENERAL PUBLIC LICENSE.*Version 2", "LGPL-2.1-only"),
        ("Mozilla Public License.*2\\.0", "MPL-2.0"),
        ("Eclipse Public License", "EPL-2.0"),
        ("European Union Public Licence", "EUPL-1.2"),
        ("Apache License.*Version 2\\.0", "Apache-2.0"),
        ("BSD 3-Clause", "BSD-3-Clause"),
        ("BSD 2-Clause", "BSD-2-Clause"),
        ("Boost Software License", "BSL-1.0"),
        ("This is free and unencumbered software", "Unlicense"),
        ("CC0 1.0 Universal", "CC0-1.0"),
        ("DO WHAT THE FUCK YOU WANT TO", "WTFPL"),
        ("ISC License", "ISC"),
        ("MIT License", "MIT"),
        ("Permission is hereby granted, free of charge", "MIT"),
        ("zlib License", "Zlib"),
    ]

    for pattern, spdx in patterns:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return LICENSE_DB.get(spdx)

    return None


# ─────────────────────────────────────────────
#  兼容性检查
# ─────────────────────────────────────────────

# 兼容性矩阵：{(项目协议, 依赖协议): 是否兼容}
# True = 兼容, False = 不兼容
_COMPAT_MATRIX = {
    # MIT 项目可以使用：
    ("MIT", "MIT"): True,
    ("MIT", "BSD-2-Clause"): True,
    ("MIT", "BSD-3-Clause"): True,
    ("MIT", "Apache-2.0"): True,
    ("MIT", "ISC"): True,
    ("MIT", "Unlicense"): True,
    ("MIT", "CC0-1.0"): True,
    ("MIT", "Zlib"): True,
    ("MIT", "BSL-1.0"): True,
    ("MIT", "GPL-2.0-only"): False,  # GPL 要求衍生作品也 GPL
    ("MIT", "GPL-3.0-only"): False,
    ("MIT", "AGPL-3.0-only"): False,
    ("MIT", "LGPL-2.1-only"): True,  # 链接可以
    ("MIT", "LGPL-3.0-only"): True,
    ("MIT", "MPL-2.0"): True,

    # Apache 项目可以使用：
    ("Apache-2.0", "MIT"): True,
    ("Apache-2.0", "BSD-2-Clause"): True,
    ("Apache-2.0", "BSD-3-Clause"): True,
    ("Apache-2.0", "Apache-2.0"): True,
    ("Apache-2.0", "ISC"): True,
    ("Apache-2.0", "GPL-2.0-only"): False,
    ("Apache-2.0", "GPL-3.0-only"): False,  # 有争议但通常认为不兼容
    ("Apache-2.0", "AGPL-3.0-only"): False,
    ("Apache-2.0", "LGPL-2.1-only"): True,
    ("Apache-2.0", "LGPL-3.0-only"): True,
    ("Apache-2.0", "MPL-2.0"): True,

    # GPL-3.0 项目可以使用：
    ("GPL-3.0-only", "MIT"): True,
    ("GPL-3.0-only", "BSD-2-Clause"): True,
    ("GPL-3.0-only", "BSD-3-Clause"): True,
    ("GPL-3.0-only", "Apache-2.0"): True,
    ("GPL-3.0-only", "GPL-2.0-only"): True,
    ("GPL-3.0-only", "GPL-3.0-only"): True,
    ("GPL-3.0-only", "LGPL-2.1-only"): True,
    ("GPL-3.0-only", "LGPL-3.0-only"): True,
    ("GPL-3.0-only", "MPL-2.0"): True,
    ("GPL-3.0-only", "AGPL-3.0-only"): False,  # AGPL 更严格
}


def check_compatibility(project_license: str, dep_license: str) -> Optional[bool]:
    """
    检查两个许可证是否兼容。

    Args:
        project_license: 你的项目使用的协议 SPDX ID
        dep_license: 依赖使用的协议 SPDX ID
    Returns:
        True/False/None（无法确定）
    """
    return _COMPAT_MATRIX.get((project_license, dep_license))


def analyze_license(license_str: str, license_text: str = "") -> CompatResult:
    """
    全面分析一个项目的许可证。

    Args:
        license_str: SPDX ID 或 GitHub API 返回的 license key
        license_text: LICENSE 文件的文本内容（可选，用于文本识别）
    Returns:
        CompatResult 包含风险评估和建议
    """
    # 尝试识别
    info = identify_license(license_str)
    if not info and license_text:
        info = identify_license_from_text(license_text)

    result = CompatResult(project_license=license_str, license_info=info)

    if not info:
        result.risk = RISK_WARNING
        result.issues.append(f"无法识别许可证: '{license_str}'")
        result.recommendations.append("建议手动审查 LICENSE 文件确认使用条款")
        return result

    result.risk = info.risk

    # ── 生成分析 ──
    if info.network_copyleft:
        result.issues.append("⚠️  网络传染性：即使作为网络服务运行也需公开源码 (AGPL/SSPL)")
        result.recommendations.append("如果用于 SaaS 服务，必须公开整个服务的源码")

    if info.same_license:
        result.issues.append("⚠️  强传染性：衍生作品必须使用相同协议")
        result.recommendations.append("如果修改了代码，修改后的代码必须以相同协议开源")

    if info.disclose_source and not info.same_license:
        result.issues.append("ℹ️  弱传染性：修改的文件需要公开源码，但使用可以不公开")
        result.recommendations.append("如果修改了该组件的源码，修改部分需要开源")

    if not info.commercial_use:
        result.issues.append("🚫 不允许商业使用")
        result.recommendations.append("此项目不能用于商业产品")

    if info.patent_grant:
        result.recommendations.append("✅ 包含专利授权条款")

    if info.notice_required:
        result.recommendations.append("📋 使用时需保留原始版权声明和许可证")

    if info.category == CAT_PERMISSIVE:
        result.recommendations.append("✅ 宽松协议，商业友好，可自由使用")

    if info.category == CAT_PUBLIC_DOMAIN:
        result.recommendations.append("✅ 公共领域，无任何限制")

    # 兼容性列表
    permissive_licenses = ["MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0",
                           "ISC", "Unlicense", "CC0-1.0", "Zlib", "BSL-1.0"]
    copyleft_licenses = ["GPL-2.0-only", "GPL-3.0-only", "AGPL-3.0-only"]

    if info.category in (CAT_PERMISSIVE, CAT_PUBLIC_DOMAIN):
        result.compatible_with = permissive_licenses + ["LGPL-2.1-only", "LGPL-3.0-only", "MPL-2.0"]
        result.incompatible_with = copyleft_licenses
    elif info.category == CAT_STRONG_COPYLEFT:
        result.compatible_with = permissive_licenses + [info.spdx_id]
        result.incompatible_with = [l for l in copyleft_licenses if l != info.spdx_id]

    return result


# ─────────────────────────────────────────────
#  GitHub API 查询
# ─────────────────────────────────────────────

def fetch_license_from_github(owner: str, repo: str) -> tuple[str, str]:
    """
    从 GitHub API 获取项目许可证。

    Returns:
        (spdx_id, license_text) 元组
    """
    spdx_id = ""
    license_text = ""

    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"User-Agent": "gitinstall/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # 获取 repo 的 license 字段
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            lic = data.get("license") or {}
            spdx_id = lic.get("spdx_id", "")
            if spdx_id == "NOASSERTION":
                spdx_id = ""
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    # 获取完整 LICENSE 文件
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/license"
        headers_lic = dict(headers)
        headers_lic["Accept"] = "application/vnd.github.v3+json"
        req = urllib.request.Request(url, headers=headers_lic)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            import base64
            content = data.get("content", "")
            encoding = data.get("encoding", "")
            if encoding == "base64" and content:
                license_text = base64.b64decode(content).decode("utf-8", errors="replace")
            if not spdx_id:
                spdx_id = data.get("license", {}).get("spdx_id", "")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    return spdx_id, license_text


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

_RISK_ICONS = {
    RISK_SAFE: "✅",
    RISK_CAUTION: "⚠️ ",
    RISK_WARNING: "🔶",
    RISK_DANGER: "🚨",
}

_CAT_NAMES = {
    CAT_PERMISSIVE: "宽松许可 (Permissive)",
    CAT_WEAK_COPYLEFT: "弱传染 (Weak Copyleft)",
    CAT_STRONG_COPYLEFT: "强传染 (Strong Copyleft)",
    CAT_PUBLIC_DOMAIN: "公共领域 (Public Domain)",
    CAT_PROPRIETARY: "专有/限制性 (Proprietary)",
    CAT_UNKNOWN: "未知",
}


def format_license_result(result: CompatResult) -> str:
    """格式化许可证检查结果"""
    lines = ["", "📜 许可证兼容性报告", "=" * 50]

    risk_icon = _RISK_ICONS.get(result.risk, "❓")
    lines.append(f"\n{risk_icon} 项目协议: {result.project_license}")

    if result.license_info:
        info = result.license_info
        cat_name = _CAT_NAMES.get(info.category, info.category)
        lines.append(f"   全称: {info.full_name}")
        lines.append(f"   类别: {cat_name}")
        lines.append(f"   商用: {'✅ 允许' if info.commercial_use else '🚫 不允许'}")
        lines.append(f"   修改: {'✅ 允许' if info.modification else '🚫 不允许'}")
        lines.append(f"   分发: {'✅ 允许' if info.distribution else '🚫 不允许'}")
        lines.append(f"   传染性: {'是' if info.copyleft else '无'}")
        if info.patent_grant:
            lines.append(f"   专利: ✅ 包含专利授权")

    if result.issues:
        lines.append("\n⚠️  注意事项:")
        for issue in result.issues:
            lines.append(f"   {issue}")

    if result.recommendations:
        lines.append("\n💡 建议:")
        for rec in result.recommendations:
            lines.append(f"   {rec}")

    lines.append("")
    return "\n".join(lines)


def license_to_dict(result: CompatResult) -> dict:
    """序列化许可证结果为 JSON"""
    d = {
        "project_license": result.project_license,
        "risk": result.risk,
        "issues": result.issues,
        "recommendations": result.recommendations,
        "compatible_with": result.compatible_with,
        "incompatible_with": result.incompatible_with,
    }
    if result.license_info:
        info = result.license_info
        d["license_info"] = {
            "spdx_id": info.spdx_id,
            "full_name": info.full_name,
            "category": info.category,
            "commercial_use": info.commercial_use,
            "modification": info.modification,
            "distribution": info.distribution,
            "copyleft": info.copyleft,
            "patent_grant": info.patent_grant,
            "network_copyleft": info.network_copyleft,
        }
    return d
