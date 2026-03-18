from __future__ import annotations

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from planner import SmartPlanner


def _mac_env() -> dict:
    return {
        "os": {"type": "macos", "arch": "arm64", "is_apple_silicon": True, "chip": "M3"},
        "gpu": {"type": "mps"},
        "package_managers": {"brew": {"available": True}},
        "runtimes": {},
    }


def test_rasa_uses_cabal_project_template():
    planner = SmartPlanner()
    plan = planner.generate_plan(
        owner="ChrisPenner",
        repo="rasa",
        env=_mac_env(),
        project_types=["haskell"],
        dependency_files={"stack.yaml": "resolver: lts-16.17\n", "rasa/rasa.cabal": "name: rasa\n"},
        readme="",
    )

    commands = [step["command"] for step in plan["steps"]]
    assert plan["strategy"] == "type_template_haskell"
    assert any("cabal.project.gitinstall-core" in command for command in commands)
    assert any("allow-newer: true" in command for command in commands)
    assert any("cabal get eve-0.1.9.0" in command for command in commands)
    assert any("rasa-ext-slate" not in command and "./rasa-ext-views" in command for command in commands)


def test_yi_uses_stack_extra_deps_template():
    planner = SmartPlanner()
    plan = planner.generate_plan(
        owner="yi-editor",
        repo="yi",
        env=_mac_env(),
        project_types=["haskell"],
        dependency_files={
            "stack.yaml": "resolver: lts-21.12\nextra-deps:\n  - Hclip-3.0.0.4\n",
            "yi/yi.cabal": "name: yi\nflag pango\n  default: False\n",
        },
        readme="",
    )

    commands = [step["command"] for step in plan["steps"]]
    assert plan["strategy"] == "type_template_haskell"
    assert any("vty-crossplatform-0.5.0.0" in command for command in commands)
    assert any("stack build yi --flag yi:-pango" in command for command in commands)


def test_gifcurry_uses_cabal_shortcut_template():
    planner = SmartPlanner()
    plan = planner.generate_plan(
        owner="lettier",
        repo="gifcurry",
        env=_mac_env(),
        project_types=["haskell"],
        dependency_files={
            "Gifcurry.cabal": (
                "name: Gifcurry\n"
                "library\n"
                "  build-depends: base == 4.11.*\n"
                "executable            gifcurry_gui\n"
                "  build-depends: haskell-gi == 0.23.0\n"
                "executable            gifcurry_cli\n"
                "  build-depends: base == 4.11.*\n"
            ),
            "stack.yaml": "packages:\n  - '.'\n",
        },
        readme="",
    )

    commands = [step["command"] for step in plan["steps"]]
    assert plan["strategy"] == "type_template_haskell"
    assert any(command.endswith("cabal build all") for command in commands)
    assert all("gitinstall-headless" not in command for command in commands)
    assert plan["launch_command"].endswith("cabal run gifcurry_cli")