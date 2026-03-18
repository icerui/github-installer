"""
dependency_audit.py - 依赖安全审计系统
========================================

安装前扫描项目依赖，识别：
  1. 已知 CVE 漏洞的包版本
  2. 恶意/误植攻击包名（typosquatting）
  3. 过时且不再维护的依赖
  4. 可疑的依赖模式（如 postinstall 脚本）

支持：Python (pip), Node.js (npm), Rust (cargo), Go (go mod)

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ── 风险等级 ──
RISK_CRITICAL = "critical"
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"
RISK_INFO = "info"


@dataclass
class VulnReport:
    """单条漏洞/风险 报告"""
    package: str
    version: str = ""
    risk: str = RISK_INFO
    category: str = ""        # cve, typosquat, unmaintained, suspicious
    description: str = ""
    cve_id: str = ""
    advisory_url: str = ""
    fix_version: str = ""
    ecosystem: str = ""       # python, npm, cargo, go


@dataclass
class AuditResult:
    """完整审计结果"""
    ecosystem: str
    total_packages: int = 0
    vulnerabilities: list[VulnReport] = field(default_factory=list)
    warnings: list[VulnReport] = field(default_factory=list)
    scan_time: float = 0.0
    error: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.risk == RISK_CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.risk == RISK_HIGH)

    @property
    def is_safe(self) -> bool:
        return self.critical_count == 0 and self.high_count == 0


# ─────────────────────────────────────────────
#  已知恶意/危险包（typosquatting 检测）
# ─────────────────────────────────────────────

# 常见被误植攻击的包名映射 {恶意包名: 真实包名}
KNOWN_TYPOSQUATS_PYTHON = {
    "python-dateutil": None,  # 这个是正规的
    "python3-dateutil": "python-dateutil",
    "reqeusts": "requests",
    "requsets": "requests",
    "reque5ts": "requests",
    "request": "requests",
    "python-nmap": None,  # 正规
    "nmap-python": "python-nmap",
    "djang0": "django",
    "djnago": "django",
    "colourama": "colorama",
    "openvc": "opencv-python",
    "python-opencv": "opencv-python",
    "numppy": "numpy",
    "sciipy": "scipy",
    "pand4s": "pandas",
    "matplotilib": "matplotlib",
    "tesorflow": "tensorflow",
    "pytoroch": "pytorch",
    "flassk": "flask",
    "crytography": "cryptography",
    "cyptography": "cryptography",
    "beautiflsoup4": "beautifulsoup4",
    "beautifulsoup": "beautifulsoup4",
    "sqlalcheni": "sqlalchemy",
    "cereals": None,  # 不是 serial，但可疑
    "setup-tools": "setuptools",
    "set-up-tools": "setuptools",
}

KNOWN_TYPOSQUATS_NPM = {
    "crossenv": "cross-env",
    "cross-env.js": "cross-env",
    "d3.js": "d3",
    "fabric-js": "fabric",
    "ffmpegs": "ffmpeg",
    "gruntcli": "grunt-cli",
    "http-proxy.js": "http-proxy",
    "mariadb": None,  # 正规
    "mongose": "mongoose",
    "mssql.js": "mssql",
    "mssql-node": "mssql",
    "nodecaffe": "node-caffe",
    "nodefabric": "node-fabric",
    "nodeffmpeg": "node-ffmpeg",
    "nodemailer-js": "nodemailer",
    "noderequest": "request",
    "nodesass": "node-sass",
    "opencv.js": "opencv",
    "openssl.js": "openssl",
    "proxy.js": "proxy",
    "shadowsock": "shadowsocks",
    "smb": None,  # 正规
    "sqlite.js": "sqlite3",
    "sqliter": "sqlite3",
    "sulern": None,
    "tkinter": None,
}

# ── 已知废弃/危险的 Python 包 ──
DEPRECATED_PYTHON = {
    "pycrypto": "已被 pycryptodome 取代，存在已知漏洞",
    "pyopenssl": "考虑使用 ssl 标准库模块",
    "nose": "已停止维护，建议迁移到 pytest",
    "imp": "Python 3.12 已移除，使用 importlib",
    "distutils": "Python 3.12 已移除，使用 setuptools",
    "optparse": "已被 argparse 取代",
    "cgi": "Python 3.13 已移除",
    "cgitb": "Python 3.13 已移除",
    "imghdr": "Python 3.13 已移除",
    "mailcap": "Python 3.13 已移除",
    "msilib": "Python 3.13 已移除",
    "nis": "Python 3.13 已移除",
    "nntplib": "Python 3.13 已移除",
    "ossaudiodev": "Python 3.13 已移除",
    "pipes": "Python 3.13 已移除",
    "sndhdr": "Python 3.13 已移除",
    "spwd": "Python 3.13 已移除",
    "sunau": "Python 3.13 已移除",
    "telnetlib": "Python 3.13 已移除",
    "uu": "Python 3.13 已移除",
    "xdrlib": "Python 3.13 已移除",
}


# ─────────────────────────────────────────────
#  解析依赖文件
# ─────────────────────────────────────────────

def parse_requirements_txt(content: str) -> list[tuple[str, str]]:
    """解析 requirements.txt → [(包名, 版本约束)]"""
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # 处理 name==version, name>=version, name~=version 等
        m = re.match(r'^([a-zA-Z0-9_.-]+)\s*([><=!~]+\s*[\d.a-zA-Z*]+(?:\s*,\s*[><=!~]+\s*[\d.a-zA-Z*]+)*)?', line)
        if m:
            name = m.group(1).lower().replace("-", "_").replace(".", "_")
            version = m.group(2).strip() if m.group(2) else ""
            deps.append((name, version))
    return deps


def parse_package_json(content: str) -> list[tuple[str, str]]:
    """解析 package.json → [(包名, 版本)]"""
    deps = []
    try:
        data = json.loads(content)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for name, version in data.get(section, {}).items():
                deps.append((name.lower(), str(version)))
    except (json.JSONDecodeError, TypeError):
        pass
    return deps


def parse_cargo_toml(content: str) -> list[tuple[str, str]]:
    """解析 Cargo.toml 的 [dependencies] → [(包名, 版本)]"""
    deps = []
    in_deps = False
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("[dependencies]"):
            in_deps = True
            continue
        if line.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps and "=" in line:
            parts = line.split("=", 1)
            name = parts[0].strip().lower()
            version = parts[1].strip().strip('"').strip("'")
            if not version.startswith("{"):
                deps.append((name, version))
    return deps


def parse_go_mod(content: str) -> list[tuple[str, str]]:
    """解析 go.mod 的 require 块 → [(模块路径, 版本)]"""
    deps = []
    in_require = False
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("require ("):
            in_require = True
            continue
        if line == ")" and in_require:
            in_require = False
            continue
        if in_require:
            parts = line.split()
            if len(parts) >= 2:
                deps.append((parts[0], parts[1]))
        elif line.startswith("require "):
            parts = line[8:].split()
            if len(parts) >= 2:
                deps.append((parts[0], parts[1]))
    return deps


# ─────────────────────────────────────────────
#  审计引擎
# ─────────────────────────────────────────────

def _check_typosquats_python(deps: list[tuple[str, str]]) -> list[VulnReport]:
    """检查 Python 包名是否为已知误植攻击包"""
    results = []
    for name, version in deps:
        normalized = name.lower().replace("-", "_").replace(".", "_")
        # 检查我们的已知列表
        for bad_name, real_name in KNOWN_TYPOSQUATS_PYTHON.items():
            bad_normalized = bad_name.lower().replace("-", "_").replace(".", "_")
            if normalized == bad_normalized and real_name is not None:
                results.append(VulnReport(
                    package=name,
                    version=version,
                    risk=RISK_CRITICAL,
                    category="typosquat",
                    description=f"疑似误植攻击！'{name}' 可能是 '{real_name}' 的恶意仿冒",
                    ecosystem="python",
                ))
    return results


def _check_typosquats_npm(deps: list[tuple[str, str]]) -> list[VulnReport]:
    """检查 npm 包名是否为已知误植攻击包"""
    results = []
    for name, version in deps:
        lower_name = name.lower()
        for bad_name, real_name in KNOWN_TYPOSQUATS_NPM.items():
            if lower_name == bad_name.lower() and real_name is not None:
                results.append(VulnReport(
                    package=name,
                    version=version,
                    risk=RISK_CRITICAL,
                    category="typosquat",
                    description=f"疑似误植攻击！'{name}' 可能是 '{real_name}' 的恶意仿冒",
                    ecosystem="npm",
                ))
    return results


def _check_deprecated_python(deps: list[tuple[str, str]]) -> list[VulnReport]:
    """检查 Python 废弃/危险包"""
    results = []
    for name, version in deps:
        normalized = name.lower().replace("-", "_").replace(".", "_")
        if normalized in DEPRECATED_PYTHON:
            results.append(VulnReport(
                package=name,
                version=version,
                risk=RISK_MEDIUM,
                category="deprecated",
                description=DEPRECATED_PYTHON[normalized],
                ecosystem="python",
            ))
    return results


def _check_version_patterns(deps: list[tuple[str, str]], ecosystem: str) -> list[VulnReport]:
    """检查可疑的版本模式"""
    results = []
    for name, version in deps:
        # 无版本约束 → 警告
        if not version or version == "*" or version == "latest":
            results.append(VulnReport(
                package=name,
                version=version or "(无版本约束)",
                risk=RISK_LOW,
                category="unpinned",
                description="未锁定版本，可能导致不可复现的安装",
                ecosystem=ecosystem,
            ))
    return results


def _check_pypi_advisory(name: str, version: str) -> list[VulnReport]:
    """查询 PyPI JSON API 检查包信息（不依赖外部数据库）"""
    results = []
    try:
        url = f"https://pypi.org/pypi/{name}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "gitinstall/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            info = data.get("info", {})

            # 检查是否被标记为废弃
            classifiers = info.get("classifiers", [])
            for c in classifiers:
                if "Inactive" in c or "Obsolete" in c:
                    results.append(VulnReport(
                        package=name,
                        version=version,
                        risk=RISK_MEDIUM,
                        category="unmaintained",
                        description=f"包已被标记为不活跃: {c}",
                        ecosystem="python",
                    ))

            # 检查 vulnerabilities 字段 (PEP 691)
            vulns = data.get("vulnerabilities", [])
            for v in vulns:
                cve_id = ""
                for alias in v.get("aliases", []):
                    if alias.startswith("CVE-"):
                        cve_id = alias
                        break
                results.append(VulnReport(
                    package=name,
                    version=version,
                    risk=RISK_HIGH if cve_id else RISK_MEDIUM,
                    category="cve",
                    description=v.get("summary", v.get("details", "已知漏洞")[:200]),
                    cve_id=cve_id,
                    advisory_url=v.get("link", ""),
                    fix_version=", ".join(v.get("fixed_in", [])),
                    ecosystem="python",
                ))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass  # 网络不可用时静默跳过
    return results


def _check_npm_advisory(name: str, version: str) -> list[VulnReport]:
    """查询 npm registry 检查包信息"""
    results = []
    try:
        url = f"https://registry.npmjs.org/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "gitinstall/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

            # 检查是否被废弃
            if data.get("deprecated"):
                results.append(VulnReport(
                    package=name,
                    version=version,
                    risk=RISK_MEDIUM,
                    category="deprecated",
                    description=f"包已被废弃: {str(data['deprecated'])[:200]}",
                    ecosystem="npm",
                ))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass
    return results


# ─────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────

def audit_python_deps(content: str, online: bool = False) -> AuditResult:
    """
    审计 Python 依赖（requirements.txt 内容）

    Args:
        content: requirements.txt 文件内容
        online: 是否查询在线漏洞数据库（较慢）
    """
    start = time.time()
    deps = parse_requirements_txt(content)
    result = AuditResult(ecosystem="python", total_packages=len(deps))

    # 离线检查（快速）
    result.vulnerabilities.extend(_check_typosquats_python(deps))
    result.vulnerabilities.extend(_check_deprecated_python(deps))
    result.warnings.extend(_check_version_patterns(deps, "python"))

    # 在线检查（如请求）
    if online:
        for name, version in deps[:20]:  # 限制请求数
            result.vulnerabilities.extend(_check_pypi_advisory(name, version))

    result.scan_time = time.time() - start
    return result


def audit_npm_deps(content: str, online: bool = False) -> AuditResult:
    """审计 npm 依赖（package.json 内容）"""
    start = time.time()
    deps = parse_package_json(content)
    result = AuditResult(ecosystem="npm", total_packages=len(deps))

    result.vulnerabilities.extend(_check_typosquats_npm(deps))
    result.warnings.extend(_check_version_patterns(deps, "npm"))

    if online:
        for name, version in deps[:20]:
            result.vulnerabilities.extend(_check_npm_advisory(name, version))

    result.scan_time = time.time() - start
    return result


def audit_cargo_deps(content: str) -> AuditResult:
    """审计 Cargo 依赖"""
    start = time.time()
    deps = parse_cargo_toml(content)
    result = AuditResult(ecosystem="cargo", total_packages=len(deps))
    result.warnings.extend(_check_version_patterns(deps, "cargo"))
    result.scan_time = time.time() - start
    return result


def audit_go_deps(content: str) -> AuditResult:
    """审计 Go 依赖"""
    start = time.time()
    deps = parse_go_mod(content)
    result = AuditResult(ecosystem="go", total_packages=len(deps))
    result.warnings.extend(_check_version_patterns(deps, "go"))
    result.scan_time = time.time() - start
    return result


def audit_project(dependency_files: dict, online: bool = False) -> list[AuditResult]:
    """
    审计项目所有依赖文件。

    Args:
        dependency_files: {文件名: 内容} 如 fetcher.py 返回的 dependency_files
        online: 是否在线查询漏洞
    Returns:
        各生态系统的审计结果列表
    """
    results = []

    for filename, content in dependency_files.items():
        lower = filename.lower()
        if lower in ("requirements.txt", "requirements-dev.txt",
                     "requirements_dev.txt", "requirements-test.txt"):
            results.append(audit_python_deps(content, online))
        elif "setup.py" in lower or "pyproject.toml" in lower:
            # setup.py/pyproject.toml 中的依赖格式不同，
            # 但可以尝试提取 install_requires 行
            extracted = _extract_setup_py_deps(content)
            if extracted:
                results.append(audit_python_deps(extracted, online))
        elif lower == "package.json":
            results.append(audit_npm_deps(content, online))
        elif lower == "cargo.toml":
            results.append(audit_cargo_deps(content))
        elif lower == "go.mod":
            results.append(audit_go_deps(content))

    return results


def _extract_setup_py_deps(content: str) -> str:
    """从 setup.py 或 pyproject.toml 提取依赖列表为 requirements.txt 格式"""
    deps = []
    # 匹配 install_requires=[...] 或 dependencies=[...]
    pattern = r'(?:install_requires|dependencies)\s*=\s*\[(.*?)\]'
    m = re.search(pattern, content, re.DOTALL)
    if m:
        block = m.group(1)
        for item in re.findall(r'["\']([^"\']+)["\']', block):
            deps.append(item)
    return "\n".join(deps)


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

_RISK_ICONS = {
    RISK_CRITICAL: "🚨",
    RISK_HIGH: "❌",
    RISK_MEDIUM: "⚠️ ",
    RISK_LOW: "ℹ️ ",
    RISK_INFO: "💡",
}

_RISK_COLORS = {
    RISK_CRITICAL: "\033[91m",
    RISK_HIGH: "\033[91m",
    RISK_MEDIUM: "\033[93m",
    RISK_LOW: "\033[94m",
    RISK_INFO: "\033[90m",
}
_RESET = "\033[0m"


def format_audit_results(results: list[AuditResult]) -> str:
    """格式化审计结果为终端输出"""
    lines = ["", "🔍 依赖安全审计报告", "=" * 50]

    all_safe = True
    total_vulns = 0
    total_warns = 0

    for r in results:
        lines.append(f"\n📦 {r.ecosystem.upper()} ({r.total_packages} 个包, {r.scan_time:.1f}s)")
        lines.append("-" * 40)

        if r.error:
            lines.append(f"  ❌ 审计出错: {r.error}")
            continue

        if r.vulnerabilities:
            all_safe = False
            total_vulns += len(r.vulnerabilities)
            for v in r.vulnerabilities:
                icon = _RISK_ICONS.get(v.risk, "?")
                color = _RISK_COLORS.get(v.risk, "")
                lines.append(f"  {icon} {color}[{v.risk.upper()}]{_RESET} {v.package} {v.version}")
                lines.append(f"     {v.description}")
                if v.cve_id:
                    lines.append(f"     CVE: {v.cve_id}")
                if v.fix_version:
                    lines.append(f"     修复版本: {v.fix_version}")

        if r.warnings:
            total_warns += len(r.warnings)
            for w in r.warnings:
                icon = _RISK_ICONS.get(w.risk, "?")
                lines.append(f"  {icon} {w.package}: {w.description}")

        if not r.vulnerabilities and not r.warnings:
            lines.append("  ✅ 未发现安全问题")

    lines.append("\n" + "=" * 50)
    if all_safe and total_warns == 0:
        lines.append("✅ 审计通过：未发现安全风险")
    else:
        lines.append(f"📊 总计: {total_vulns} 个漏洞, {total_warns} 个警告")
        if total_vulns > 0:
            lines.append("⚠️  建议修复所有漏洞后再安装")

    return "\n".join(lines)


def audit_to_dict(results: list[AuditResult]) -> dict:
    """序列化审计结果为 JSON"""
    return {
        "results": [
            {
                "ecosystem": r.ecosystem,
                "total_packages": r.total_packages,
                "scan_time": round(r.scan_time, 3),
                "is_safe": r.is_safe,
                "vulnerabilities": [
                    {
                        "package": v.package,
                        "version": v.version,
                        "risk": v.risk,
                        "category": v.category,
                        "description": v.description,
                        "cve_id": v.cve_id,
                        "fix_version": v.fix_version,
                    }
                    for v in r.vulnerabilities
                ],
                "warnings": [
                    {
                        "package": w.package,
                        "description": w.description,
                        "risk": w.risk,
                    }
                    for w in r.warnings
                ],
            }
            for r in results
        ],
        "overall_safe": all(r.is_safe for r in results),
    }


# ─────────────────────────────────────────────
#  SBOM (Software Bill of Materials) 生成
#  支持 CycloneDX 1.5 JSON 和 SPDX 2.3 JSON
# ─────────────────────────────────────────────

import uuid
from datetime import datetime, timezone


def _collect_all_deps(project_dir: str) -> list[dict]:
    """从项目目录中收集所有依赖信息"""
    deps = []
    req_path = os.path.join(project_dir, "requirements.txt")
    if os.path.isfile(req_path):
        with open(req_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for name, version in parse_requirements_txt(content):
            deps.append({"name": name, "version": version, "ecosystem": "python"})
    pkg_path = os.path.join(project_dir, "package.json")
    if os.path.isfile(pkg_path):
        with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for name, version in parse_package_json(content):
            deps.append({"name": name, "version": version, "ecosystem": "npm"})
    cargo_path = os.path.join(project_dir, "Cargo.toml")
    if os.path.isfile(cargo_path):
        with open(cargo_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for name, version in parse_cargo_toml(content):
            deps.append({"name": name, "version": version, "ecosystem": "cargo"})
    gomod_path = os.path.join(project_dir, "go.mod")
    if os.path.isfile(gomod_path):
        with open(gomod_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for name, version in parse_go_mod(content):
            deps.append({"name": name, "version": version, "ecosystem": "go"})
    return deps


def generate_sbom_cyclonedx(
    project_dir: str,
    project_name: str = "",
    project_version: str = "0.0.0",
) -> dict:
    """
    生成 CycloneDX 1.5 JSON 格式的 SBOM。
    CycloneDX 是 OWASP 标准，被 GitHub、GitLab、NIST 广泛认可。
    """
    deps = _collect_all_deps(project_dir)
    if not project_name:
        project_name = os.path.basename(os.path.abspath(project_dir))

    serial = f"urn:uuid:{uuid.uuid4()}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    purl_prefix = {
        "python": "pkg:pypi", "npm": "pkg:npm",
        "cargo": "pkg:cargo", "go": "pkg:golang",
    }

    components = []
    for dep in deps:
        name = dep.get("name", "")
        version = dep.get("version", "")
        eco = dep.get("ecosystem", "")
        purl = f"{purl_prefix.get(eco, 'pkg:generic')}/{name}"
        if version:
            purl += f"@{version}"
        components.append({
            "type": "library",
            "name": name,
            "version": version or "unknown",
            "purl": purl,
            "bom-ref": f"{eco}/{name}@{version or 'unknown'}",
        })

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": {"components": [{"type": "application", "name": "gitinstall", "version": "1.1.0"}]},
            "component": {
                "type": "application", "name": project_name,
                "version": project_version, "bom-ref": f"root/{project_name}",
            },
        },
        "components": components,
        "dependencies": [{"ref": f"root/{project_name}", "dependsOn": [c["bom-ref"] for c in components]}],
    }


def generate_sbom_spdx(
    project_dir: str,
    project_name: str = "",
    project_version: str = "0.0.0",
) -> dict:
    """
    生成 SPDX 2.3 JSON 格式的 SBOM。
    SPDX 是 Linux Foundation 和 ISO/IEC 5962:2021 标准。
    """
    deps = _collect_all_deps(project_dir)
    if not project_name:
        project_name = os.path.basename(os.path.abspath(project_dir))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_ns = f"https://spdx.org/spdxdocs/{project_name}-{uuid.uuid4()}"

    purl_prefix = {
        "python": "pkg:pypi", "npm": "pkg:npm",
        "cargo": "pkg:cargo", "go": "pkg:golang",
    }

    packages = [{
        "SPDXID": "SPDXRef-RootPackage", "name": project_name,
        "versionInfo": project_version, "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False, "supplier": "NOASSERTION",
    }]
    relationships = []

    for i, dep in enumerate(deps):
        name = dep.get("name", "")
        version = dep.get("version", "")
        eco = dep.get("ecosystem", "")
        spdx_id = f"SPDXRef-Package-{i + 1}"
        purl = f"{purl_prefix.get(eco, 'pkg:generic')}/{name}"
        if version:
            purl += f"@{version}"
        packages.append({
            "SPDXID": spdx_id, "name": name,
            "versionInfo": version or "NOASSERTION",
            "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": purl}],
        })
        relationships.append({
            "spdxElementId": "SPDXRef-RootPackage",
            "relatedSpdxElement": spdx_id,
            "relationshipType": "DEPENDS_ON",
        })

    relationships.append({
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relatedSpdxElement": "SPDXRef-RootPackage",
        "relationshipType": "DESCRIBES",
    })

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": project_name,
        "documentNamespace": doc_ns,
        "creationInfo": {
            "created": now,
            "creators": ["Tool: gitinstall-1.1.0"],
            "licenseListVersion": "3.22",
        },
        "packages": packages,
        "relationships": relationships,
    }


def export_sbom(
    project_dir: str,
    output_path: Optional[str] = None,
    fmt: str = "cyclonedx",
    project_name: str = "",
    project_version: str = "0.0.0",
) -> str:
    """导出 SBOM 到文件。返回输出文件路径。"""
    if fmt == "spdx":
        sbom = generate_sbom_spdx(project_dir, project_name, project_version)
        suffix = "spdx.json"
    else:
        sbom = generate_sbom_cyclonedx(project_dir, project_name, project_version)
        suffix = "cdx.json"

    if not output_path:
        name = project_name or os.path.basename(os.path.abspath(project_dir))
        output_path = os.path.join(project_dir, f"{name}.{suffix}")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sbom, f, indent=2, ensure_ascii=False)
    return output_path


# ─────────────────────────────────────────────
#  DevSecOps 安全左移  (Market Opportunity #5)
# ─────────────────────────────────────────────

@dataclass
class SecurityPolicy:
    """安全策略定义"""
    name: str = ""
    description: str = ""
    rules: list[dict] = field(default_factory=list)
    severity: str = "high"      # critical | high | medium | low
    action: str = "block"       # block | warn | audit
    enabled: bool = True


@dataclass
class PolicyResult:
    """策略检查结果"""
    policy_name: str = ""
    passed: bool = True
    violations: list[dict] = field(default_factory=list)
    action: str = "block"
    message: str = ""


@dataclass
class ComplianceReport:
    """合规性报告"""
    framework: str = ""         # soc2 | iso27001 | nist | pci_dss
    project_name: str = ""
    timestamp: str = ""
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    findings: list[dict] = field(default_factory=list)
    score: float = 0.0


# ── 预置安全策略 ──

_DEFAULT_POLICIES: list[dict] = [
    {
        "name": "no-critical-vulns",
        "description": "禁止存在 CRITICAL 级漏洞",
        "check": "max_risk",
        "threshold": RISK_CRITICAL,
        "action": "block",
        "severity": "critical",
    },
    {
        "name": "no-typosquats",
        "description": "禁止使用已知 typosquatting 包",
        "check": "category",
        "category": "typosquatting",
        "action": "block",
        "severity": "critical",
    },
    {
        "name": "no-deprecated",
        "description": "禁止使用已废弃包",
        "check": "category",
        "category": "deprecated",
        "action": "warn",
        "severity": "medium",
    },
    {
        "name": "version-pinning",
        "description": "所有依赖必须固定版本",
        "check": "category",
        "category": "unpinned_version",
        "action": "warn",
        "severity": "low",
    },
    {
        "name": "max-deps-limit",
        "description": "依赖数量不超过阈值",
        "check": "dep_count",
        "threshold": 200,
        "action": "warn",
        "severity": "medium",
    },
]


def create_security_policy(
    name: str,
    rules: list[dict],
    action: str = "block",
    severity: str = "high",
    description: str = "",
) -> SecurityPolicy:
    """创建自定义安全策略"""
    return SecurityPolicy(
        name=name,
        description=description or name,
        rules=rules,
        severity=severity,
        action=action,
    )


def evaluate_policies(
    audit_results: list[AuditResult],
    policies: list[dict] | None = None,
) -> list[PolicyResult]:
    """
    策略即代码（Policy-as-Code）：按策略检查审计结果。

    返回每条策略的通过/违反结果。
    """
    if policies is None:
        policies = _DEFAULT_POLICIES

    results = []

    for policy in policies:
        if not policy.get("enabled", True):
            continue

        check_type = policy.get("check", "")
        violations: list[dict] = []

        if check_type == "max_risk":
            threshold = policy.get("threshold", RISK_HIGH)
            for ar in audit_results:
                for v in ar.vulnerabilities:
                    if v.risk >= threshold:
                        violations.append({
                            "package": v.package,
                            "risk": v.risk,
                            "description": v.description,
                            "cve": v.cve_id,
                        })

        elif check_type == "category":
            target_cat = policy.get("category", "")
            for ar in audit_results:
                for v in ar.vulnerabilities:
                    if target_cat.lower() in v.category.lower():
                        violations.append({
                            "package": v.package,
                            "category": v.category,
                            "description": v.description,
                        })

        elif check_type == "dep_count":
            threshold = policy.get("threshold", 200)
            for ar in audit_results:
                if ar.total_packages > threshold:
                    violations.append({
                        "ecosystem": ar.ecosystem,
                        "count": ar.total_packages,
                        "threshold": threshold,
                    })

        elif check_type == "license":
            # 禁止使用特定许可证
            blocked = policy.get("blocked_licenses", [])
            # 此检查需要外部许可证数据
            pass

        results.append(PolicyResult(
            policy_name=policy.get("name", ""),
            passed=len(violations) == 0,
            violations=violations,
            action=policy.get("action", "block"),
            message=policy.get("description", ""),
        ))

    return results


def security_gate(
    audit_results: list[AuditResult],
    policies: list[dict] | None = None,
) -> dict:
    """
    安全门禁：在安装前执行安全检查，决定是否放行。

    返回:
      {
        "allowed": True/False,
        "blockers": [...],       # 阻止安装的违规
        "warnings": [...],       # 告警但不阻止
        "summary": "..."
      }
    """
    policy_results = evaluate_policies(audit_results, policies)

    blockers = [pr for pr in policy_results if not pr.passed and pr.action == "block"]
    warnings = [pr for pr in policy_results if not pr.passed and pr.action == "warn"]

    return {
        "allowed": len(blockers) == 0,
        "blockers": [
            {"policy": b.policy_name, "violations": b.violations, "message": b.message}
            for b in blockers
        ],
        "warnings": [
            {"policy": w.policy_name, "violations": w.violations, "message": w.message}
            for w in warnings
        ],
        "summary": _gate_summary(blockers, warnings),
    }


def _gate_summary(blockers: list, warnings: list) -> str:
    if blockers:
        names = ", ".join(b.policy_name for b in blockers)
        return f"🚫 安装被阻止: 违反策略 [{names}]"
    if warnings:
        names = ", ".join(w.policy_name for w in warnings)
        return f"⚠️ 安装允许但有警告: [{names}]"
    return "✅ 所有安全策略检查通过"


# ── 合规性报告 ──

_COMPLIANCE_FRAMEWORKS: dict[str, list[dict]] = {
    "soc2": [
        {"id": "CC6.1", "name": "逻辑/物理访问控制", "check": "no_critical_vulns"},
        {"id": "CC6.6", "name": "系统边界安全", "check": "no_typosquats"},
        {"id": "CC6.8", "name": "恶意软件防护", "check": "no_typosquats"},
        {"id": "CC7.1", "name": "漏洞管理", "check": "vuln_scan_performed"},
        {"id": "CC7.2", "name": "安全事件监控", "check": "audit_logging"},
        {"id": "CC8.1", "name": "变更管理", "check": "version_pinning"},
    ],
    "iso27001": [
        {"id": "A.12.6.1", "name": "技术漏洞管理", "check": "no_critical_vulns"},
        {"id": "A.14.1.2", "name": "安全应用服务", "check": "no_typosquats"},
        {"id": "A.14.1.3", "name": "应用事务保护", "check": "version_pinning"},
        {"id": "A.14.2.5", "name": "系统安全工程", "check": "sbom_generated"},
        {"id": "A.15.1.3", "name": "ICT 供应链", "check": "supply_chain_audit"},
    ],
    "nist": [
        {"id": "RA-5", "name": "漏洞监控和扫描", "check": "vuln_scan_performed"},
        {"id": "SA-11", "name": "开发安全测试", "check": "no_critical_vulns"},
        {"id": "SA-12", "name": "供应链保护", "check": "no_typosquats"},
        {"id": "CM-7", "name": "最小功能原则", "check": "dep_count_limit"},
        {"id": "SI-2", "name": "缺陷修补", "check": "no_deprecated"},
    ],
    "pci_dss": [
        {"id": "6.3.2", "name": "漏洞识别和管理", "check": "no_critical_vulns"},
        {"id": "6.5", "name": "安全编码实践", "check": "no_typosquats"},
        {"id": "11.3", "name": "渗透测试", "check": "vuln_scan_performed"},
    ],
}


def generate_compliance_report(
    audit_results: list[AuditResult],
    framework: str = "soc2",
    project_name: str = "",
    sbom_generated: bool = False,
) -> ComplianceReport:
    """
    根据审计结果生成合规性报告。

    支持: soc2, iso27001, nist, pci_dss
    """
    checks = _COMPLIANCE_FRAMEWORKS.get(framework.lower(), _COMPLIANCE_FRAMEWORKS["soc2"])

    # 预计算审计状态
    has_critical = any(
        v.risk >= RISK_CRITICAL for ar in audit_results for v in ar.vulnerabilities
    )
    has_typosquats = any(
        "typosquat" in v.category.lower() for ar in audit_results for v in ar.vulnerabilities
    )
    has_pinning_issues = any(
        "unpin" in v.category.lower() for ar in audit_results for v in ar.vulnerabilities
    )
    has_deprecated = any(
        "deprecat" in v.category.lower() for ar in audit_results for v in ar.vulnerabilities
    )
    total_deps = sum(ar.total_packages for ar in audit_results)

    check_map = {
        "no_critical_vulns": not has_critical,
        "no_typosquats": not has_typosquats,
        "version_pinning": not has_pinning_issues,
        "vuln_scan_performed": len(audit_results) > 0,
        "audit_logging": True,  # 审计日志被执行即为通过
        "sbom_generated": sbom_generated,
        "supply_chain_audit": not has_typosquats and not has_critical,
        "dep_count_limit": total_deps <= 200,
        "no_deprecated": not has_deprecated,
    }

    findings = []
    passed = 0
    for check in checks:
        check_id = check["check"]
        result = check_map.get(check_id, False)
        if result:
            passed += 1
        findings.append({
            "control_id": check["id"],
            "name": check["name"],
            "passed": result,
            "details": f"{'✅ 通过' if result else '❌ 未通过'}: {check['name']}",
        })

    total = len(checks)
    score = (passed / total * 100) if total > 0 else 0

    now = __import__("datetime").datetime.now().isoformat()

    return ComplianceReport(
        framework=framework.upper(),
        project_name=project_name,
        timestamp=now,
        total_checks=total,
        passed_checks=passed,
        failed_checks=total - passed,
        findings=findings,
        score=round(score, 1),
    )


def format_compliance_report(report: ComplianceReport) -> str:
    """格式化合规性报告"""
    grade = "A+" if report.score >= 95 else "A" if report.score >= 90 else \
            "B" if report.score >= 80 else "C" if report.score >= 70 else \
            "D" if report.score >= 60 else "F"

    lines = [
        f"📋 {report.framework} 合规性报告",
        f"   项目: {report.project_name or 'N/A'}",
        f"   时间: {report.timestamp}",
        f"   得分: {report.score}% ({grade})",
        f"   检查: {report.passed_checks}/{report.total_checks} 通过",
        "",
    ]

    for f in report.findings:
        icon = "✅" if f["passed"] else "❌"
        lines.append(f"   {icon} [{f['control_id']}] {f['name']}")

    return "\n".join(lines)


def format_security_gate(gate_result: dict) -> str:
    """格式化安全门禁结果"""
    lines = [gate_result.get("summary", "")]

    blockers = gate_result.get("blockers", [])
    for b in blockers:
        lines.append(f"   🚫 {b['policy']}: {b['message']}")
        for v in b.get("violations", [])[:3]:
            pkg = v.get("package", v.get("ecosystem", ""))
            lines.append(f"      - {pkg}: {v.get('description', v.get('count', ''))}")

    warnings = gate_result.get("warnings", [])
    for w in warnings:
        lines.append(f"   ⚠️ {w['policy']}: {w['message']}")

    return "\n".join(lines)
