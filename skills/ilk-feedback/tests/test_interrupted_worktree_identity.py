"""Interrupted ordinary-worktree runs remain selectable and diagnosable."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
COLLECT = ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"
LOOP_SCRIPTS = ROOT / "skills" / "ilk-loop" / "scripts"
sys.path.insert(0, str(LOOP_SCRIPTS))
from ilk_paths import project_key  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def interrupted_worktree(tmp_path: Path):
    clone = tmp_path / "project"
    worktree = tmp_path / "project-worktree"
    data_home = tmp_path / "ilk-data"
    clone.mkdir()
    _git(clone, "init")
    _git(clone, "config", "user.email", "fixture@example.invalid")
    _git(clone, "config", "user.name", "Fixture")
    (clone / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "fixture")
    _git(clone, "worktree", "add", "-b", "fixture-worktree", str(worktree))

    key = project_key(clone)
    root = data_home / "projects" / key
    logs = root / "logs"
    launcher = root / "runtime" / "launcher"
    run_dir = logs / "runs" / "20261005-202912"
    run_dir.mkdir(parents=True)
    launcher.mkdir(parents=True)

    records = [
        {
            "run_id": "20261005-161027",
            "iteration": 0,
            "project": str(clone),
            "stop_reason": "blocked-no-runnable",
            "timestamp": "2026-10-05T16:10:34+0800",
        },
        {
            "run_id": "20261005-202912",
            "iteration": 1,
            "project": str(worktree),
            "status": "started",
            "timestamp": "2026-10-05T20:29:12+0800",
        },
    ]
    (logs / ".ilk-loop.log").write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    tail_marker = "LOCAL_CHECK_FAILED_OPT_IN_ABSENT"
    (run_dir / "iter-01.log").write_text(tail_marker + "\n", encoding="utf-8")
    (launcher / "last-launch.json").write_text(
        json.dumps(
            {
                "run_id": "20261005-202912",
                "project_path": str(worktree),
                "started_at": "2026-10-05T20:29:12+0800",
                "max_iterations": 30,
                "iteration_timeout_min": 90,
                "jsonl_log": str(logs / ".ilk-loop.log"),
                "log_dir": str(run_dir),
            }
        ),
        encoding="utf-8",
    )
    (launcher / "last-exit.json").write_text(
        json.dumps(
            {
                "state": "interrupted",
                "run_id": "20261005-202912",
                "iterations": 1,
                "project_path": str(worktree),
                "stopped_reason": "runner exited without a terminal state",
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "ILK_DATA_HOME": str(data_home), "PYTHONIOENCODING": "utf-8"}
    return clone, worktree, launcher, env, tail_marker


@pytest.mark.parametrize("use_worktree", [False, True])
@pytest.mark.xfail(strict=True, reason="feedback keys and filters ordinary worktrees as separate projects")
def test_default_feedback_selects_interrupted_worktree_run(interrupted_worktree, use_worktree: bool) -> None:
    clone, worktree, launcher, env, tail_marker = interrupted_worktree
    query = worktree if use_worktree else clone
    result = subprocess.run(
        [sys.executable, str(COLLECT), "--project-path", str(query), "--quiet"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = launcher / "postmortems" / "20261005-202912.md"
    assert report.exists()
    body = report.read_text(encoding="utf-8")
    assert "classification: interrupted" in body
    assert tail_marker in body


@pytest.mark.xfail(strict=True, reason="explicit lookup rejects the worktree-path started record")
def test_explicit_run_id_uses_matching_sentinel_and_iteration_tail(interrupted_worktree) -> None:
    clone, _worktree, launcher, env, tail_marker = interrupted_worktree
    result = subprocess.run(
        [
            sys.executable,
            str(COLLECT),
            "--project-path",
            str(clone),
            "--run-id",
            "20261005-202912",
            "--quiet",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    body = (launcher / "postmortems" / "20261005-202912.md").read_text(encoding="utf-8")
    assert "classification: interrupted" in body
    assert tail_marker in body

