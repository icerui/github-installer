"""tests/unit/test_license_check.py - 许可证兼容性检查测试"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from license_check import (
    identify_license,
    identify_license_from_text,
    analyze_license,
    check_compatibility,
    format_license_result,
    license_to_dict,
    LicenseInfo,
    CompatResult,
    LICENSE_DB,
    LICENSE_ALIASES,
    CAT_PERMISSIVE,
    CAT_WEAK_COPYLEFT,
    CAT_STRONG_COPYLEFT,
    CAT_PUBLIC_DOMAIN,
    CAT_PROPRIETARY,
    RISK_SAFE,
    RISK_CAUTION,
    RISK_WARNING,
    RISK_DANGER,
)


# ─────────────────────────────────────────────
#  许可证识别测试
# ─────────────────────────────────────────────

class TestIdentifyLicense:
    def test_mit(self):
        info = identify_license("MIT")
        assert info is not None
        assert info.spdx_id == "MIT"
        assert info.category == CAT_PERMISSIVE

    def test_apache(self):
        info = identify_license("Apache-2.0")
        assert info is not None
        assert info.patent_grant is True

    def test_gpl3(self):
        info = identify_license("GPL-3.0-only")
        assert info is not None
        assert info.copyleft is True
        assert info.same_license is True

    def test_agpl(self):
        info = identify_license("AGPL-3.0-only")
        assert info is not None
        assert info.network_copyleft is True
        assert info.risk == RISK_DANGER

    def test_unknown(self):
        assert identify_license("WEIRD-LICENSE-42") is None

    def test_empty(self):
        assert identify_license("") is None

    def test_aliases(self):
        # 测试别名映射
        assert identify_license("mit") is not None
        assert identify_license("gplv3") is not None
        assert identify_license("apache 2.0") is not None
        assert identify_license("bsd-2-clause") is not None
        assert identify_license("unlicense") is not None

    def test_case_insensitive(self):
        info = identify_license("mit")
        assert info is not None
        assert info.spdx_id == "MIT"


class TestIdentifyFromText:
    def test_mit_text(self):
        text = """MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software..."""
        info = identify_license_from_text(text)
        assert info is not None
        assert info.spdx_id == "MIT"

    def test_apache_text(self):
        text = """Apache License
                          Version 2.0, January 2004
   https://www.apache.org/licenses/"""
        info = identify_license_from_text(text)
        assert info is not None
        assert info.spdx_id == "Apache-2.0"

    def test_gpl3_text(self):
        text = """GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007"""
        info = identify_license_from_text(text)
        assert info is not None
        assert info.spdx_id == "GPL-3.0-only"

    def test_agpl_text(self):
        text = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3"
        info = identify_license_from_text(text)
        assert info is not None
        assert info.spdx_id == "AGPL-3.0-only"

    def test_unlicense_text(self):
        text = "This is free and unencumbered software released into the public domain."
        info = identify_license_from_text(text)
        assert info is not None
        assert info.spdx_id == "Unlicense"

    def test_empty(self):
        assert identify_license_from_text("") is None
        assert identify_license_from_text(None) is None

    def test_no_match(self):
        assert identify_license_from_text("Just some random text about code") is None


# ─────────────────────────────────────────────
#  兼容性测试
# ─────────────────────────────────────────────

class TestCompatibility:
    def test_mit_with_mit(self):
        result = check_compatibility("MIT", "MIT")
        assert result is True

    def test_mit_with_apache(self):
        result = check_compatibility("MIT", "Apache-2.0")
        assert result is True

    def test_mit_with_gpl(self):
        result = check_compatibility("MIT", "GPL-3.0-only")
        assert result is False

    def test_gpl_with_mit(self):
        # GPL 项目可以依赖 MIT 代码
        result = check_compatibility("GPL-3.0-only", "MIT")
        assert result is True

    def test_unknown_pair(self):
        result = check_compatibility("CUSTOM", "MIT")
        assert result is None


# ─────────────────────────────────────────────
#  分析报告测试
# ─────────────────────────────────────────────

class TestAnalyzeLicense:
    def test_mit_analysis(self):
        result = analyze_license("MIT")
        assert result.risk == RISK_SAFE
        assert result.license_info is not None
        assert any("商业友好" in rec or "宽松" in rec for rec in result.recommendations)

    def test_gpl_analysis(self):
        result = analyze_license("GPL-3.0-only")
        assert result.risk == RISK_WARNING
        assert any("传染" in issue for issue in result.issues)

    def test_agpl_analysis(self):
        result = analyze_license("AGPL-3.0-only")
        assert result.risk == RISK_DANGER
        assert any("网络" in issue for issue in result.issues)

    def test_sspl_analysis(self):
        result = analyze_license("SSPL-1.0")
        assert result.risk == RISK_DANGER
        assert result.license_info.commercial_use is False

    def test_busl_analysis(self):
        result = analyze_license("BUSL-1.1")
        assert result.risk == RISK_DANGER
        assert any("商业" in issue for issue in result.issues)

    def test_unknown_analysis(self):
        result = analyze_license("UNKNOWN-LICENSE")
        assert result.risk == RISK_WARNING
        assert any("无法识别" in issue for issue in result.issues)

    def test_text_fallback(self):
        result = analyze_license("", license_text="MIT License\nPermission is hereby granted")
        assert result.license_info is not None
        assert result.license_info.spdx_id == "MIT"

    def test_lgpl_analysis(self):
        result = analyze_license("LGPL-3.0-only")
        assert result.risk == RISK_CAUTION
        assert result.license_info.copyleft is True

    def test_public_domain(self):
        result = analyze_license("CC0-1.0")
        assert result.risk == RISK_SAFE
        assert any("公共领域" in rec for rec in result.recommendations)


# ─────────────────────────────────────────────
#  数据库完整性测试
# ─────────────────────────────────────────────

class TestLicenseDB:
    def test_all_licenses_have_required_fields(self):
        for spdx_id, info in LICENSE_DB.items():
            assert info.spdx_id == spdx_id
            assert info.full_name
            assert info.category in (CAT_PERMISSIVE, CAT_WEAK_COPYLEFT,
                                     CAT_STRONG_COPYLEFT, CAT_PUBLIC_DOMAIN,
                                     CAT_PROPRIETARY)
            assert info.risk in (RISK_SAFE, RISK_CAUTION, RISK_WARNING, RISK_DANGER)

    def test_all_aliases_resolve(self):
        for alias, spdx_id in LICENSE_ALIASES.items():
            assert spdx_id in LICENSE_DB, f"Alias '{alias}' → '{spdx_id}' not in DB"

    def test_permissive_licenses_are_safe(self):
        for info in LICENSE_DB.values():
            if info.category == CAT_PERMISSIVE:
                assert info.risk == RISK_SAFE
                assert info.commercial_use is True
                assert info.copyleft is False

    def test_copyleft_licenses_have_copyleft_flag(self):
        for info in LICENSE_DB.values():
            if info.category in (CAT_STRONG_COPYLEFT, CAT_WEAK_COPYLEFT):
                assert info.copyleft is True

    def test_minimum_license_coverage(self):
        # 确保覆盖最常见的许可证
        must_have = ["MIT", "Apache-2.0", "GPL-2.0-only", "GPL-3.0-only",
                     "BSD-2-Clause", "BSD-3-Clause", "AGPL-3.0-only",
                     "LGPL-3.0-only", "MPL-2.0", "Unlicense"]
        for spdx in must_have:
            assert spdx in LICENSE_DB, f"Missing: {spdx}"


# ─────────────────────────────────────────────
#  格式化 & 序列化测试
# ─────────────────────────────────────────────

class TestFormat:
    def test_format_mit(self):
        result = analyze_license("MIT")
        text = format_license_result(result)
        assert "MIT" in text
        assert "许可证" in text

    def test_format_unknown(self):
        result = analyze_license("UNKNOWN")
        text = format_license_result(result)
        assert "无法识别" in text

    def test_to_dict(self):
        result = analyze_license("MIT")
        d = license_to_dict(result)
        assert d["project_license"] == "MIT"
        assert d["risk"] == RISK_SAFE
        assert "license_info" in d
        assert d["license_info"]["spdx_id"] == "MIT"

    def test_to_dict_unknown(self):
        result = analyze_license("UNKNOWN")
        d = license_to_dict(result)
        assert "license_info" not in d
        assert d["risk"] == RISK_WARNING
