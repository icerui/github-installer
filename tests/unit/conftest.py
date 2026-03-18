"""conftest.py — 保证 error_fixer 相关测试跨平台运行

在没有任何包管理器（brew / apt / choco）的平台上，
error_fixer._install_pkg_cmd 返回空字符串，导致修复规则返回 None。
此 fixture 在这种情况下模拟 Linux + apt，确保测试可运行。
"""
import pytest


@pytest.fixture(autouse=True)
def _ensure_pkg_manager(request, monkeypatch):
    """在没有任何包管理器的平台上模拟 Linux + apt"""
    try:
        import error_fixer
    except ImportError:
        return

    # 跳过 TestPlatformDetection 类（它自己测试平台检测逻辑）
    if request.node.parent and "PlatformDetection" in (request.node.parent.name or ""):
        return

    has_any = (
        (error_fixer._is_macos() and error_fixer._has_brew())
        or (error_fixer._is_linux() and error_fixer._has_apt())
        or (error_fixer._is_windows() and error_fixer._has_choco())
    )
    if not has_any:
        monkeypatch.setattr("error_fixer._is_linux", lambda: True)
        monkeypatch.setattr("error_fixer._has_apt", lambda: True)
        monkeypatch.setattr("error_fixer._is_macos", lambda: False)
        monkeypatch.setattr("error_fixer._has_brew", lambda: False)
