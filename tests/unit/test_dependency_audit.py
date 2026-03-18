"""tests/unit/test_dependency_audit.py - 依赖安全审计测试"""
from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from dependency_audit import (
    parse_requirements_txt,
    parse_package_json,
    parse_cargo_toml,
    parse_go_mod,
    _check_typosquats_python,
    _check_typosquats_npm,
    _check_deprecated_python,
    _check_version_patterns,
    _extract_setup_py_deps,
    audit_python_deps,
    audit_npm_deps,
    audit_cargo_deps,
    audit_go_deps,
    audit_project,
    format_audit_results,
    audit_to_dict,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_MEDIUM,
    RISK_LOW,
    VulnReport,
    AuditResult,
)


# ─────────────────────────────────────────────
#  解析器测试
# ─────────────────────────────────────────────

class TestParseRequirements:
    def test_basic(self):
        content = "requests==2.31.0\nnumpy>=1.24.0\nflask"
        deps = parse_requirements_txt(content)
        assert len(deps) == 3
        assert deps[0] == ("requests", "==2.31.0")
        assert deps[1] == ("numpy", ">=1.24.0")
        assert deps[2] == ("flask", "")

    def test_comments_and_blanks(self):
        content = "# comment\n\nrequests==2.31.0\n  \n-r base.txt\ntorch"
        deps = parse_requirements_txt(content)
        assert len(deps) == 2

    def test_complex_versions(self):
        content = "django>=4.0,<5.0\nscipy~=1.11.0"
        deps = parse_requirements_txt(content)
        assert len(deps) == 2
        assert ">=4.0" in deps[0][1]

    def test_normalize_names(self):
        content = "scikit-learn==1.3.0\nPillow==10.0.0"
        deps = parse_requirements_txt(content)
        names = [d[0] for d in deps]
        assert "scikit_learn" in names
        assert "pillow" in names


class TestParsePackageJson:
    def test_basic(self):
        content = json.dumps({
            "dependencies": {"express": "^4.18.0", "lodash": "~4.17.21"},
            "devDependencies": {"jest": "^29.0.0"},
        })
        deps = parse_package_json(content)
        assert len(deps) == 3

    def test_empty(self):
        assert parse_package_json("{}") == []

    def test_invalid(self):
        assert parse_package_json("not json") == []


class TestParseCargoToml:
    def test_basic(self):
        content = """[package]
name = "my-app"

[dependencies]
serde = "1.0"
tokio = "1.28"

[dev-dependencies]
rand = "0.8"
"""
        deps = parse_cargo_toml(content)
        assert len(deps) == 2
        assert ("serde", "1.0") in deps

    def test_complex_deps_skipped(self):
        content = """[dependencies]
serde = {version = "1.0", features = ["derive"]}
simple = "2.0"
"""
        deps = parse_cargo_toml(content)
        assert len(deps) == 1
        assert deps[0] == ("simple", "2.0")


class TestParseGoMod:
    def test_basic(self):
        content = """module example.com/mymod

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/stretchr/testify v1.8.4
)
"""
        deps = parse_go_mod(content)
        assert len(deps) == 2
        assert deps[0] == ("github.com/gin-gonic/gin", "v1.9.1")

    def test_single_require(self):
        content = "module example.com/mod\nrequire github.com/pkg/errors v0.9.1\n"
        deps = parse_go_mod(content)
        assert len(deps) == 1


# ─────────────────────────────────────────────
#  安全检查测试
# ─────────────────────────────────────────────

class TestTyposquatPython:
    def test_malicious_found(self):
        deps = [("reqeusts", "2.31.0")]
        results = _check_typosquats_python(deps)
        assert len(results) == 1
        assert results[0].risk == RISK_CRITICAL
        assert "requests" in results[0].description

    def test_legitimate_package(self):
        deps = [("requests", "2.31.0"), ("numpy", "1.24.0")]
        results = _check_typosquats_python(deps)
        assert len(results) == 0

    def test_multiple_typos(self):
        deps = [("djang0", "4.0"), ("numppy", "1.0")]
        results = _check_typosquats_python(deps)
        assert len(results) == 2
        assert all(r.risk == RISK_CRITICAL for r in results)


class TestTyposquatNpm:
    def test_malicious_found(self):
        deps = [("crossenv", "1.0.0")]
        results = _check_typosquats_npm(deps)
        assert len(results) == 1
        assert results[0].risk == RISK_CRITICAL

    def test_safe_package(self):
        deps = [("cross-env", "7.0.0"), ("express", "4.18.0")]
        results = _check_typosquats_npm(deps)
        assert len(results) == 0


class TestDeprecatedPython:
    def test_deprecated_found(self):
        deps = [("pycrypto", "2.6.1")]
        results = _check_deprecated_python(deps)
        assert len(results) == 1
        assert results[0].risk == RISK_MEDIUM
        assert "pycryptodome" in results[0].description

    def test_safe_package(self):
        deps = [("cryptography", "41.0.0")]
        results = _check_deprecated_python(deps)
        assert len(results) == 0

    def test_removed_python313(self):
        deps = [("cgi", ""), ("telnetlib", "")]
        results = _check_deprecated_python(deps)
        assert len(results) == 2


class TestVersionPatterns:
    def test_unpinned_warning(self):
        deps = [("requests", ""), ("flask", "*"), ("django", "latest")]
        results = _check_version_patterns(deps, "python")
        assert len(results) == 3
        assert all(r.risk == RISK_LOW for r in results)

    def test_pinned_ok(self):
        deps = [("requests", "==2.31.0"), ("flask", ">=2.0")]
        results = _check_version_patterns(deps, "python")
        assert len(results) == 0


# ─────────────────────────────────────────────
#  集成审计测试
# ─────────────────────────────────────────────

class TestAuditPython:
    def test_clean_deps(self):
        content = "requests==2.31.0\nnumpy==1.24.0\nflask==2.3.0"
        result = audit_python_deps(content)
        assert result.ecosystem == "python"
        assert result.total_packages == 3
        assert result.is_safe

    def test_with_typosquat(self):
        content = "reqeusts==2.31.0\nnumpy==1.24.0"
        result = audit_python_deps(content)
        assert not result.is_safe  # has critical
        assert result.critical_count == 1

    def test_with_deprecated(self):
        content = "pycrypto==2.6.1\nnose==1.3.7"
        result = audit_python_deps(content)
        assert len(result.vulnerabilities) == 2


class TestAuditNpm:
    def test_clean_deps(self):
        content = json.dumps({"dependencies": {"express": "^4.18.0", "lodash": "^4.17.21"}})
        result = audit_npm_deps(content)
        assert result.ecosystem == "npm"
        assert result.total_packages == 2
        assert result.is_safe

    def test_with_typosquat(self):
        content = json.dumps({"dependencies": {"crossenv": "1.0.0"}})
        result = audit_npm_deps(content)
        assert result.critical_count == 1


class TestAuditProject:
    def test_mixed_deps(self):
        dep_files = {
            "requirements.txt": "requests==2.31.0\nflask==2.3.0",
            "package.json": json.dumps({"dependencies": {"express": "^4.18.0"}}),
        }
        results = audit_project(dep_files)
        assert len(results) == 2
        assert results[0].ecosystem == "python"
        assert results[1].ecosystem == "npm"

    def test_cargo_deps(self):
        dep_files = {
            "Cargo.toml": "[dependencies]\nserde = \"1.0\"\ntokio = \"1.28\"",
        }
        results = audit_project(dep_files)
        assert len(results) == 1
        assert results[0].ecosystem == "cargo"

    def test_go_deps(self):
        dep_files = {
            "go.mod": "module example.com/mod\nrequire github.com/pkg/errors v0.9.1\n",
        }
        results = audit_project(dep_files)
        assert len(results) == 1
        assert results[0].ecosystem == "go"

    def test_no_dep_files(self):
        results = audit_project({})
        assert results == []


class TestExtractSetupPy:
    def test_install_requires(self):
        content = """
setup(
    name="mypackage",
    install_requires=[
        "requests>=2.0",
        "flask",
    ],
)
"""
        extracted = _extract_setup_py_deps(content)
        assert "requests>=2.0" in extracted
        assert "flask" in extracted


# ─────────────────────────────────────────────
#  格式化 & 序列化测试
# ─────────────────────────────────────────────

class TestFormatAudit:
    def test_format_safe(self):
        result = AuditResult(ecosystem="python", total_packages=5)
        text = format_audit_results([result])
        assert "审计通过" in text

    def test_format_with_vulns(self):
        result = AuditResult(
            ecosystem="python",
            total_packages=2,
            vulnerabilities=[VulnReport(
                package="bad-pkg", version="1.0", risk=RISK_CRITICAL,
                category="typosquat", description="恶意包",
            )],
        )
        text = format_audit_results([result])
        assert "bad-pkg" in text
        assert "CRITICAL" in text

    def test_to_dict(self):
        result = AuditResult(ecosystem="python", total_packages=3)
        d = audit_to_dict([result])
        assert d["overall_safe"] is True
        assert len(d["results"]) == 1
        assert d["results"][0]["ecosystem"] == "python"


class TestAuditResultProperties:
    def test_critical_count(self):
        r = AuditResult(
            ecosystem="python",
            vulnerabilities=[
                VulnReport(package="a", risk=RISK_CRITICAL),
                VulnReport(package="b", risk=RISK_HIGH),
                VulnReport(package="c", risk=RISK_CRITICAL),
            ],
        )
        assert r.critical_count == 2
        assert r.high_count == 1
        assert not r.is_safe

    def test_safe_result(self):
        r = AuditResult(ecosystem="npm", vulnerabilities=[
            VulnReport(package="x", risk=RISK_MEDIUM),
        ])
        assert r.is_safe  # Only medium, no critical/high
