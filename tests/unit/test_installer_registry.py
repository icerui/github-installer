"""
test_installer_registry.py - 安装器注册表测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from installer_registry import (
    InstallerRegistry, BaseInstaller, InstallerInfo,
    PipInstaller, NpmInstaller, CargoInstaller, GoInstaller,
    DockerInstaller, CondaInstaller, BrewInstaller, AptInstaller,
)


class TestInstallerInfo:

    def test_pip_installer_info(self):
        inst = PipInstaller()
        assert inst.info.name == "pip"
        assert "python" in inst.info.ecosystems

    def test_npm_installer_info(self):
        inst = NpmInstaller()
        assert inst.info.name == "npm"
        assert "node" in inst.info.ecosystems

    def test_cargo_installer_info(self):
        inst = CargoInstaller()
        assert inst.info.name == "cargo"
        assert "rust" in inst.info.ecosystems

    def test_go_installer_info(self):
        inst = GoInstaller()
        assert inst.info.name == "go"
        assert "go" in inst.info.ecosystems

    def test_docker_installer_info(self):
        inst = DockerInstaller()
        assert inst.info.name == "docker"
        assert "container" in inst.info.ecosystems

    def test_info_is_installerinfo(self):
        inst = PipInstaller()
        assert isinstance(inst.info, InstallerInfo)


class TestInstallerRegistry:

    def test_singleton(self):
        # Reset singleton for isolation
        InstallerRegistry._instance = None
        r1 = InstallerRegistry()
        r2 = InstallerRegistry()
        assert r1 is r2
        # 清理
        InstallerRegistry._instance = None

    def test_list_all(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        all_inst = registry.list_all()
        assert len(all_inst) >= 8
        names = [i.info.name for i in all_inst]
        assert "pip" in names
        assert "npm" in names
        assert "cargo" in names
        InstallerRegistry._instance = None

    def test_get_by_name(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        pip = registry.get("pip")
        assert pip is not None
        assert pip.info.name == "pip"
        InstallerRegistry._instance = None

    def test_get_nonexistent(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        result = registry.get("nonexistent_installer_xyz")
        assert result is None
        InstallerRegistry._instance = None

    def test_list_available(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        available = registry.list_available()
        assert isinstance(available, list)
        InstallerRegistry._instance = None

    def test_format_registry(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        text = registry.format_registry()
        assert isinstance(text, str)
        assert "安装器" in text
        InstallerRegistry._instance = None

    def test_to_dict(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        d = registry.to_dict()
        assert isinstance(d, dict)
        assert "installers" in d
        InstallerRegistry._instance = None

    def test_find_matching(self):
        InstallerRegistry._instance = None
        registry = InstallerRegistry()
        matches = registry.find_matching(
            project_types=["python"],
            dep_files={"requirements.txt": "flask==2.0"},
        )
        match_names = [i.info.name for i in matches]
        # pip should match python projects
        assert "pip" in match_names
        InstallerRegistry._instance = None


class TestInstallerStepGeneration:

    def test_pip_generate_steps(self):
        inst = PipInstaller()
        steps = inst.generate_install_steps({
            "dependency_files": {"requirements.txt": "flask==2.0"},
        })
        assert isinstance(steps, list)
        assert len(steps) > 0
        for step in steps:
            assert "command" in step
            assert "description" in step

    def test_npm_generate_steps(self):
        inst = NpmInstaller()
        steps = inst.generate_install_steps({
            "dependency_files": {"package.json": "{}"},
        })
        assert isinstance(steps, list)
        assert len(steps) > 0

    def test_to_dict(self):
        inst = PipInstaller()
        d = inst.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "pip"
        assert "available" in d
