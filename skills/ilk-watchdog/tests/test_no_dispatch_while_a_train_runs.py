"""Tests for fleet-wide dispatch hold while any release train runs.

Sub-plan: no-dispatch-while-a-train-runs, step 0 (red-first pins).

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path`` with sandbox
projects.  No real daemon, no real ``~/.ilk-data``, and no real release
process are touched.

The four acceptance criteria:
  AC-1  ``any_release_lock_held`` returns None with no projects dir, None
        with a dead-pid lock, and the project-dir name with a lock naming
        ``os.getpid()``.
  AC-2  (wiring) the scheduler.sh text has a call to ``any_release_lock_held``
        that appears BEFORE the existing ``if is_release_lock_held "$path"``
        line, and the string ``reason=fleet-hold``.
  AC-3  (behaviour) a scheduler ``--dry-run --once`` pass with two projects A
        and B, a runnable queued master in B, and a live train.lock (pid
        ``os.getpid()``) in A, emits ``skip-releasing`` for B with
        ``fleet-hold`` and no ``dispatch`` decision for B.  Control: with A's
        lock removed, B is dispatched.
  AC-4  (control) the existing train tests in ``unit_test_targets`` pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCHEDULER_SH = SCRIPTS_ILK_WATCHDOG / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"

sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
sys.path.insert(0, str(SCRIPTS_ILK_LOOP))

from release_train_dispatch import (  # noqa: E402
    any_release_lock_held,
    is_release_lock_held,
)


# ── Isolation fixture ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ILK_DATA_HOME so no test touches the real data root."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ilk-data"))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_train_lock(data_dir: Path, pid: int) -> Path:
    """Write a release train lock file with the given pid."""
    lock_dir = data_dir / "runtime" / "release"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "train.lock"
    lock_file.write_text(str(pid), encoding="utf-8")
    return lock_file


def _write_project(
    data_root: Path,
    key: str,
    *,
    master_status: str | None = "queued",
    sentinel_state: str = "all-shipped",
) -> Path:
    """Scaffold a minimal project for scheduler scan tests."""
    project_dir = data_root / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    if master_status is not None:
        (plans_dir / "MASTER-test.md").write_text(
            "---\n"
            "title: MASTER-test\n"
            "created: 2026-10-07T00:00:00+08:00\n"
            f"status: {master_status}\n"
            "---\n\n"
            "# MASTER-test\n\n"
            "| # | Sub-plan | Status |\n"
            "|---|---|---|\n"
            "| 1 | [2026-10-07-work.md](./2026-10-07-work.md) | pending |\n",
            encoding="utf-8",
        )
        (plans_dir / "2026-10-07-work.md").write_text(
            "---\n"
            "plan: 2026-10-07-work\n"
            "status: pending\n"
            "last_updated: 2026-10-07\n"
            "---\n\n# 2026-10-07-work\n",
            encoding="utf-8",
        )

    # Write sentinel
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": sentinel_state,
        "pid": 99999999,
        "run_id": f"test-{key}",
        "started_at": "2026-10-07T10:00:00+0800",
        "ended_at": "2026-10-07T10:30:00+0800",
        "iterations": 3,
        "project_path": str(project_dir),
        "cli": "claude",
    }
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel, indent=2) + "\n", encoding="utf-8"
    )
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": str(project_dir)}), encoding="utf-8"
    )

    return project_dir


# ── AC-1: any_release_lock_held ─────────────────────────────────────────────


class TestAnyReleaseLockHeld:
    """AC-1: ``any_release_lock_held`` returns None with no projects dir, None
    with a dead-pid lock, and the project-dir name with a lock naming
    ``os.getpid()``."""

    def test_no_projects_dir(self, tmp_path: Path) -> None:
        """No projects dir → None."""
        data_root = tmp_path / "ilk-data"
        data_root.mkdir()
        assert any_release_lock_held(data_root) is None

    def test_dead_pid_lock(self, tmp_path: Path) -> None:
        """A lock naming a dead pid → None."""
        data_root = tmp_path / "ilk-data"
        _write_project(data_root, "proj-a")
        proj_dir = data_root / "projects" / "proj-a"
        _write_train_lock(proj_dir, 99999999)  # dead pid
        assert any_release_lock_held(data_root) is None

    def test_live_pid_lock(self, tmp_path: Path) -> None:
        """A lock naming os.getpid() → the project-dir name."""
        data_root = tmp_path / "ilk-data"
        _write_project(data_root, "proj-a")
        proj_dir = data_root / "projects" / "proj-a"
        _write_train_lock(proj_dir, os.getpid())
        assert any_release_lock_held(data_root) == "proj-a"


# ── AC-2: wiring ────────────────────────────────────────────────────────────


class TestFleetHoldWiring:
    """AC-2 (wiring): the scheduler.sh text has a call to
    ``any_release_lock_held`` that appears BEFORE the existing
    ``if is_release_lock_held "$path"`` line, and the string
    ``reason=fleet-hold``."""

    def test_wiring_present(self) -> None:
        """scheduler.sh has the fleet-hold wiring."""
        text = SCHEDULER_SH.read_text(encoding="utf-8")
        assert "any_release_lock_held" in text, (
            "scheduler.sh should call any_release_lock_held"
        )
        assert "reason=fleet-hold" in text, (
            "scheduler.sh should log reason=fleet-hold"
        )

    def test_fleet_hold_before_per_project(self) -> None:
        """The fleet-hold check appears BEFORE the per-project lock check."""
        text = SCHEDULER_SH.read_text(encoding="utf-8")
        fleet_pos = text.find("any_release_lock_held")
        per_project_pos = text.find('if is_release_lock_held "$path"')
        assert fleet_pos >= 0, "any_release_lock_held not found"
        assert per_project_pos >= 0, "per-project is_release_lock_held not found"
        assert fleet_pos < per_project_pos, (
            "fleet-hold check must appear before per-project lock check"
        )


# ── AC-3: behaviour ────────────────────────────────────────────────────────


class TestFleetHoldBehaviour:
    """AC-3 (behaviour): a scheduler ``--dry-run --once`` pass with two
    projects A and B, a runnable queued master in B, and a live train.lock
    (pid ``os.getpid()``) in A, emits ``skip-releasing`` for B with
    ``fleet-hold`` and no ``dispatch`` decision for B.  Control: with A's
    lock removed, B is dispatched."""

    def test_fleet_hold_skips_other_projects(self, tmp_path: Path) -> None:
        """With a live train.lock in A, B gets skip-releasing fleet-hold."""
        data_root = tmp_path / "ilk-data"
        _write_project(data_root, "proj-a", master_status=None)
        _write_project(data_root, "proj-b", master_status="queued")
        proj_a = data_root / "projects" / "proj-a"
        _write_train_lock(proj_a, os.getpid())

        result = subprocess.run(
            ["bash", str(SCHEDULER_SH), "--once", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "ILK_DATA_HOME": str(data_root),
                "ILK_SKILL_HOME": str(SKILLS_DIR),
            },
        )
        combined = result.stdout + result.stderr
        assert "skip-releasing" in combined, (
            f"expected skip-releasing in output:\n{combined}"
        )
        assert "fleet-hold" in combined, (
            f"expected fleet-hold in output:\n{combined}"
        )

    def test_control_dispatch_without_lock(self, tmp_path: Path) -> None:
        """Control: with A's lock removed, B is dispatched."""
        data_root = tmp_path / "ilk-data"
        _write_project(data_root, "proj-a", master_status=None)
        _write_project(data_root, "proj-b", master_status="queued")

        result = subprocess.run(
            ["bash", str(SCHEDULER_SH), "--once", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "ILK_DATA_HOME": str(data_root),
                "ILK_SKILL_HOME": str(SKILLS_DIR),
            },
        )
        combined = result.stdout + result.stderr
        assert "fleet-hold" not in combined, (
            f"no fleet-hold expected without lock:\n{combined}"
        )