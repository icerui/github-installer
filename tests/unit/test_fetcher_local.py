from __future__ import annotations

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from fetcher import _extract_local_dep_files, detect_project_types


def test_extract_local_dep_files_recurses_into_modules(tmp_path: Path):
    (tmp_path / "service-a").mkdir()
    (tmp_path / "service-a" / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (tmp_path / "service-b").mkdir()
    (tmp_path / "service-b" / "build.gradle.kts").write_text("plugins { java }", encoding="utf-8")

    dep_files = _extract_local_dep_files(tmp_path)

    assert "service-a/pom.xml" in dep_files
    assert "service-b/build.gradle.kts" in dep_files


def test_detect_project_types_uses_nested_dependency_filenames():
    dep_files = {
        "backend/pom.xml": "<project></project>",
        "worker/Cargo.toml": "[package]\nname='worker'\n",
    }

    types = detect_project_types({"language": "Java"}, "", dep_files)

    assert "java" in types
    assert "rust" in types


def test_extract_local_dep_files_keeps_supported_zig_version_files(tmp_path: Path):
    (tmp_path / "build.zig").write_text('const std = @import("std");\n', encoding="utf-8")
    (tmp_path / "build.zig.zon").write_text('.{ .minimum_zig_version = "0.15.2" }\n', encoding="utf-8")
    (tmp_path / ".zig-version").write_text("0.15.2\n", encoding="utf-8")

    dep_files = _extract_local_dep_files(tmp_path)

    assert dep_files["build.zig.zon"].startswith('.{ .minimum_zig_version')
    assert dep_files[".zig-version"] == "0.15.2\n"