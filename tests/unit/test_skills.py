"""
Tests for skills.py - Skills 插件系统
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from skills import (
    SkillManager, SkillMeta, SkillInstallPlan, Skill,
    ensure_builtin_skills, format_skills_list, BUILTIN_SKILLS,
)


class TestSkillMeta:
    def test_from_dict_minimal(self):
        meta = SkillMeta.from_dict({"name": "test", "version": "1.0"})
        assert meta.name == "test"
        assert meta.version == "1.0"
        assert meta.match_repos == []
        assert meta.tags == []

    def test_from_dict_full(self):
        meta = SkillMeta.from_dict({
            "name": "pytorch-cuda",
            "version": "2.0",
            "description": "GPU优化",
            "author": "test",
            "match_repos": ["pytorch/pytorch"],
            "match_languages": ["python"],
            "match_files": ["setup.py"],
            "match_patterns": ["pytorch"],
            "tags": ["ai", "gpu"],
        })
        assert meta.name == "pytorch-cuda"
        assert meta.match_repos == ["pytorch/pytorch"]
        assert "ai" in meta.tags

    def test_from_dict_missing_fields(self):
        meta = SkillMeta.from_dict({})
        assert meta.name == ""
        assert meta.version == "0.1.0"


class TestSkillInstallPlan:
    def test_from_dict(self):
        plan = SkillInstallPlan.from_dict({
            "steps": [{"command": "pip install torch", "description": "install"}],
            "launch_command": "python main.py",
            "env_vars": {"CUDA_HOME": "/usr/local/cuda"},
        })
        assert len(plan.steps) == 1
        assert plan.launch_command == "python main.py"
        assert plan.env_vars["CUDA_HOME"] == "/usr/local/cuda"

    def test_from_dict_empty(self):
        plan = SkillInstallPlan.from_dict({})
        assert plan.steps == []
        assert plan.launch_command == ""


class TestSkillManager:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = SkillManager(skills_dir=Path(self.tmpdir))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_empty(self):
        assert self.mgr.list_skills() == []

    def test_create_and_list(self):
        self.mgr.create_skill(
            name="test-skill",
            description="A test skill",
            steps=[{"command": "echo hello", "description": "test"}],
            tags=["test"],
        )
        skills = self.mgr.list_skills()
        assert len(skills) == 1
        assert skills[0].meta.name == "test-skill"
        assert skills[0].enabled is True

    def test_create_duplicate_raises(self):
        self.mgr.create_skill(name="dup", description="first", steps=[])
        try:
            self.mgr.create_skill(name="dup", description="second", steps=[])
            assert False, "应该抛出 FileExistsError"
        except FileExistsError:
            pass

    def test_create_invalid_name(self):
        try:
            self.mgr.create_skill(name="INVALID NAME!", description="bad", steps=[])
            assert False, "应该抛出 ValueError"
        except ValueError:
            pass

    def test_remove_skill(self):
        self.mgr.create_skill(name="removeme", description="tmp", steps=[])
        assert len(self.mgr.list_skills()) == 1
        assert self.mgr.remove_skill("removeme") is True
        assert len(self.mgr.list_skills()) == 0

    def test_remove_nonexistent(self):
        assert self.mgr.remove_skill("nope") is False

    def test_toggle_skill(self):
        self.mgr.create_skill(name="toggle", description="t", steps=[])
        assert self.mgr.list_skills()[0].enabled is True

        self.mgr.toggle_skill("toggle", enabled=False)
        assert self.mgr.list_skills()[0].enabled is False

        self.mgr.toggle_skill("toggle", enabled=True)
        assert self.mgr.list_skills()[0].enabled is True

    def test_export_import(self):
        self.mgr.create_skill(
            name="exportme",
            description="Export test",
            steps=[{"command": "ls", "description": "list"}],
            match_repos=["owner/repo"],
        )
        data = self.mgr.export_skill("exportme")
        assert data is not None
        assert data["skill"]["name"] == "exportme"
        assert len(data["install"]["steps"]) == 1

        # 删除后重新导入
        self.mgr.remove_skill("exportme")
        assert len(self.mgr.list_skills()) == 0

        path = self.mgr.import_skill(data)
        assert path.exists()
        skills = self.mgr.list_skills()
        assert len(skills) == 1
        assert skills[0].meta.name == "exportme"

    def test_export_nonexistent(self):
        assert self.mgr.export_skill("nope") is None

    def test_find_matching_by_repo(self):
        self.mgr.create_skill(
            name="comfyui",
            description="ComfyUI 安装",
            steps=[],
            match_repos=["comfyanonymous/ComfyUI"],
        )
        matches = self.mgr.find_matching_skills(owner="comfyanonymous", repo="ComfyUI")
        assert len(matches) == 1
        assert matches[0].meta.name == "comfyui"

    def test_find_matching_by_language(self):
        self.mgr.create_skill(
            name="py-skill",
            description="Python skill",
            steps=[],
            match_languages=["python"],
        )
        matches = self.mgr.find_matching_skills(language="Python")
        assert len(matches) == 1

    def test_find_matching_by_files(self):
        self.mgr.create_skill(
            name="docker-skill",
            description="Docker",
            steps=[],
            match_files=["Dockerfile"],
        )
        matches = self.mgr.find_matching_skills(file_list=["Dockerfile", "README.md"])
        assert len(matches) == 1

    def test_find_matching_by_pattern(self):
        self.mgr.create_skill(
            name="ml-skill",
            description="ML",
            steps=[],
            match_patterns=["pytorch|tensorflow"],
        )
        matches = self.mgr.find_matching_skills(project_types=["pytorch"])
        assert len(matches) == 1

    def test_find_no_match(self):
        self.mgr.create_skill(
            name="specific",
            description="Specific",
            steps=[],
            match_repos=["specific/only"],
        )
        matches = self.mgr.find_matching_skills(owner="other", repo="project")
        assert len(matches) == 0

    def test_disabled_skill_not_matched(self):
        self.mgr.create_skill(
            name="disabled",
            description="D",
            steps=[],
            match_languages=["python"],
        )
        self.mgr.toggle_skill("disabled", enabled=False)
        matches = self.mgr.find_matching_skills(language="python")
        assert len(matches) == 0

    def test_create_generates_readme(self):
        path = self.mgr.create_skill(
            name="readme-test",
            description="Has README",
            steps=[{"command": "echo hi", "description": "say hi"}],
        )
        readme = (path / "README.md").read_text()
        assert "readme-test" in readme
        assert "echo hi" in readme


class TestBuiltinSkills:
    def test_builtin_skills_defined(self):
        assert len(BUILTIN_SKILLS) >= 7

    def test_ensure_builtin_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("skills.SKILLS_DIR", Path(tmpdir)):
                ensure_builtin_skills()
                mgr = SkillManager(Path(tmpdir))
                skills = mgr.list_skills()
                assert len(skills) == len(BUILTIN_SKILLS)

    def test_ensure_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("skills.SKILLS_DIR", Path(tmpdir)):
                ensure_builtin_skills()
                ensure_builtin_skills()  # 第二次不应报错
                mgr = SkillManager(Path(tmpdir))
                assert len(mgr.list_skills()) == len(BUILTIN_SKILLS)


class TestFormatSkillsList:
    def test_empty_list(self):
        text = format_skills_list([])
        assert "未安装" in text

    def test_with_skills(self):
        skill = Skill(
            meta=SkillMeta(
                name="test", version="1.0",
                description="Test skill", tags=["ai"],
            ),
            plan=SkillInstallPlan(),
            path=Path("/tmp/test"),
            enabled=True,
        )
        text = format_skills_list([skill])
        assert "test" in text
        assert "v1.0" in text
        assert "ai" in text
