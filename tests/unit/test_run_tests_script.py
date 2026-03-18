"""
run_tests.py 内联执行测试 — 通过 runpy 在进程内执行以获取覆盖率
"""
import sys
import os
import runpy
import pytest


def test_run_tests_script():
    """Execute run_tests.py in-process; coverage tracks all 359 lines."""
    script = os.path.join(os.path.dirname(__file__), "../../tools/run_tests.py")
    assert os.path.exists(script), f"run_tests.py not found at {script}"

    exit_code = None

    orig_exit = sys.exit
    def mock_exit(code=0):
        nonlocal exit_code
        exit_code = code
        raise SystemExit(code)

    sys.exit = mock_exit
    try:
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit:
            pass
    finally:
        sys.exit = orig_exit

    assert exit_code == 0, f"run_tests.py exited with code {exit_code}"
