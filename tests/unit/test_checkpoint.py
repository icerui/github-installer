"""
test_checkpoint.py - 断点恢复测试
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import pytest
from checkpoint import (
    StepCheckpoint, InstallCheckpoint, CheckpointManager,
    format_checkpoint_list, format_resume_plan,
    CHECKPOINT_DIR,
)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """使用临时目录的 CheckpointManager"""
    import checkpoint
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    mgr = CheckpointManager()
    return mgr


class TestStepCheckpoint:

    def test_creation(self):
        step = StepCheckpoint(
            index=0, command="pip install -r requirements.txt",
            description="安装依赖",
        )
        assert step.index == 0
        assert step.status == "pending"
        assert step.error == ""

    def test_default_values(self):
        step = StepCheckpoint(index=1, command="echo hello", description="")
        assert step.exit_code == -1
        assert step.duration_sec == 0.0


class TestInstallCheckpoint:

    def test_progress(self):
        cp = InstallCheckpoint(
            project="owner/repo", owner="owner", repo="repo",
            total_steps=3,
            steps=[
                StepCheckpoint(index=0, command="s1", description="s1", status="completed"),
                StepCheckpoint(index=1, command="s2", description="s2", status="running"),
                StepCheckpoint(index=2, command="s3", description="s3", status="pending"),
            ],
        )
        assert cp.completed_steps == 1
        assert cp.total_steps == 3
        assert 30 < cp.progress_pct < 40

    def test_to_dict_from_dict(self):
        cp = InstallCheckpoint(
            project="a/b", owner="a", repo="b", total_steps=1,
            steps=[StepCheckpoint(index=0, command="x", description="d", status="completed")],
        )
        d = cp.to_dict()
        cp2 = InstallCheckpoint.from_dict(d)
        assert cp2.project == "a/b"
        assert cp2.steps[0].status == "completed"


class TestCheckpointManager:

    def test_create_checkpoint(self, manager):
        plan = {"steps": [
            {"command": "git clone x", "description": "克隆"},
            {"command": "pip install", "description": "安装"},
        ]}
        cp = manager.create("owner", "repo", plan)
        assert cp.project == "owner/repo"
        assert len(cp.steps) == 2
        assert all(s.status == "pending" for s in cp.steps)

    def test_get_checkpoint(self, manager):
        plan = {"steps": [{"command": "echo 1", "description": "test"}]}
        manager.create("owner", "repo", plan)
        cp = manager.get_checkpoint("owner", "repo")
        assert cp is not None
        assert cp.project == "owner/repo"

    def test_mark_step_running(self, manager):
        plan = {"steps": [{"command": "echo 1", "description": "test"}]}
        cp = manager.create("owner", "repo", plan)
        manager.mark_step_running(cp, 0)
        cp2 = manager.get_checkpoint("owner", "repo")
        assert cp2.steps[0].status == "running"

    def test_mark_step_completed(self, manager):
        plan = {"steps": [{"command": "echo 1", "description": "test"}]}
        cp = manager.create("owner", "repo", plan)
        manager.mark_step_completed(cp, 0, duration_sec=1.5)
        cp2 = manager.get_checkpoint("owner", "repo")
        assert cp2.steps[0].status == "completed"
        assert cp2.steps[0].duration_sec == 1.5

    def test_mark_step_failed(self, manager):
        plan = {"steps": [{"command": "echo 1", "description": "test"}]}
        cp = manager.create("owner", "repo", plan)
        manager.mark_step_failed(cp, 0, error="boom")
        cp2 = manager.get_checkpoint("owner", "repo")
        assert cp2.steps[0].status == "failed"
        assert cp2.steps[0].error == "boom"

    def test_get_resumable(self, manager):
        plan = {"steps": [
            {"command": "echo 1", "description": "s1"},
            {"command": "echo 2", "description": "s2"},
        ]}
        cp = manager.create("owner", "repo", plan)
        manager.mark_step_completed(cp, 0)
        manager.mark_step_failed(cp, 1, error="fail")
        resumable = manager.get_resumable()
        assert len(resumable) == 1
        assert resumable[0].project == "owner/repo"

    def test_get_resume_step(self, manager):
        plan = {"steps": [
            {"command": "echo 1", "description": "s1"},
            {"command": "echo 2", "description": "s2"},
        ]}
        cp = manager.create("owner", "repo", plan)
        manager.mark_step_completed(cp, 0)
        manager.mark_step_failed(cp, 1, error="fail")
        idx = manager.get_resume_step(cp)
        assert idx == 1

    def test_remove_checkpoint(self, manager):
        plan = {"steps": [{"command": "echo 1", "description": "test"}]}
        manager.create("owner", "repo", plan)
        assert manager.get_checkpoint("owner", "repo") is not None
        manager.remove_checkpoint("owner", "repo")
        assert manager.get_checkpoint("owner", "repo") is None

    def test_nonexistent_checkpoint(self, manager):
        assert manager.get_checkpoint("x", "y") is None


class TestFormatters:

    def test_format_checkpoint_list(self):
        checkpoints = [
            InstallCheckpoint(
                project="a/b", owner="a", repo="b", total_steps=2,
                steps=[
                    StepCheckpoint(index=0, command="x", description="s1", status="completed"),
                    StepCheckpoint(index=1, command="y", description="s2", status="failed"),
                ],
            ),
        ]
        text = format_checkpoint_list(checkpoints)
        assert "a/b" in text

    def test_format_resume_plan(self):
        cp = InstallCheckpoint(
            project="a/b", owner="a", repo="b", total_steps=2,
            steps=[
                StepCheckpoint(index=0, command="x", description="步骤1", status="completed"),
                StepCheckpoint(index=1, command="y", description="步骤2", status="failed"),
            ],
        )
        text = format_resume_plan(cp, 1)
        assert "a/b" in text
