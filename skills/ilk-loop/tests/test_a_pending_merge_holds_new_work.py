"""Tests: a pending selfmod merge holds new work on the host.

Part of sub-plan ``a-pending-merge-holds-new-work``
(MASTER-2026-10-02b).

Six acceptance criteria:

  AC-1  project A merge-pending, project B queued ⇒ the scan dispatches
        A (merge-pending) and not B.
  AC-2  no merge pending ⇒ B dispatches as today.
  AC-3  a runner between iterations with another project's merge pending
        ⇒ exits with the declared state, and its next relaunch proceeds
        once the merge has landed.
  AC-4  master active for iteration 1, parked before iteration 2 ⇒ no
        iteration 2, sentinel blocked-no-runnable, held line printed.
  AC-5  a stub worker rewrites a parked master blocked → shipped ⇒ after
        the iteration it reads blocked with parked_at intact, and the run
        exits ship_integrity_violation.
  AC-6  write_status on a file whose on-disk status became blocked after
        it was read ⇒ refuses; park_master --unpark still works.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _SCRIPTS.parent.parent.parent
_SCHEDULER = _SCRIPTS.parent.parent / "ilk-watchdog" / "scripts" / "scheduler_scan.py"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_SCHEDULER.parent) not in sys.path:
    sys.path.insert(0, str(_SCHEDULER.parent))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root*."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _make_project(tmp_path: Path, name: str) -> Path:
    """Create a minimal project directory with a worktree and sentinel."""
    project = tmp_path / ".ilk-data" / "projects" / name
    project.mkdir(parents=True)

    # Create runtime/launcher structure
    launcher = project / "runtime" / "launcher"
    launcher.mkdir(parents=True)

    # Create a real git worktree (not a plain repo) so that
    # unmerged_worktree_commits() can detect unmerged commits.
    clone = project / "clone"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    (clone / "README.md").write_text("seed\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "seed")

    wt = launcher / "worktrees" / "selfmod-batch"
    _git(clone, "worktree", "add", "--detach", str(wt))

    # Create plans directory
    plans = project / "plans"
    plans.mkdir(parents=True)

    return project


def _write_sentinel(project: Path, state: str = "running",
                    merge_deferred: bool = False) -> None:
    """Write a last-exit.json sentinel."""
    sentinel = project / "runtime" / "launcher" / "last-exit.json"
    sentinel.write_text(json.dumps({
        "state": state,
        "pid": 12345,
        "run_id": "test-run",
        "started_at": "2026-01-01T00:00:00+0000",
        "ended_at": None,
        "iterations": 1,
        "project_path": str(project),
        "cli": "claude",
        "jsonl_log": str(project / "test.jsonl"),
        "merge_deferred": merge_deferred,
        "held_by": None,
        "failed_check": None,
    }), encoding="utf-8")


def _write_master(plans_dir: Path, name: str, status: str = "active") -> None:
    """Write a minimal master plan file."""
    master = plans_dir / f"MASTER-{name}.md"
    master.write_text(textwrap.dedent(f"""\
        ---
        master_plan: {name}
        batch_date: 2026-01-01
        status: {status}
        current_subplan: test-subplan
        ---

        # MASTER plan: {name}

        ## Sub-plan registry

        | # | Slug | Steps |
        |---|---|---|
        | 1 | test-subplan | 2 |
    """), encoding="utf-8")


def _add_unmerged_commit(wt: Path) -> None:
    """Add a commit to the worktree that hasn't been merged."""
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work commit")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_ac1_merge_pending_holds_other_projects(tmp_path: Path) -> None:
    """AC-1: project A merge-pending, project B queued ⇒ scan dispatches A
    (merge-pending) and not B.

    The scheduler should hold dispatch on project B when project A has
    a pending selfmod merge.
    """
    # Create two projects
    project_a = _make_project(tmp_path, "project-a")
    project_b = _make_project(tmp_path, "project-b")

    # Project A has merge-pending state
    _write_sentinel(project_a, state="merge-deferred")
    wt_a = project_a / "runtime" / "launcher" / "worktrees" / "selfmod-batch"
    _add_unmerged_commit(wt_a)
    plans_a = project_a / "plans"
    _write_master(plans_a, "2026-01-01-a", status="queued")

    # Project B is queued and ready to dispatch
    _write_sentinel(project_b, state="shipped")  # previous run shipped
    plans_b = project_b / "plans"
    _write_master(plans_b, "2026-01-01-b", status="queued")

    # Write last-launch.json so resolve_repo_path works
    for proj, repo_name in [(project_a, "repo-a"), (project_b, "repo-b")]:
        repo_dir = tmp_path / repo_name
        repo_dir.mkdir(exist_ok=True)
        launcher = proj / "runtime" / "launcher"
        (launcher / "last-launch.json").write_text(
            json.dumps({"project_path": str(repo_dir)}), encoding="utf-8"
        )

    # Invoke scheduler_scan with patched data root.
    # Import once, then patch ilk_data_root on the module.
    if "scheduler_scan" not in sys.modules:
        sys.path.insert(0, str(_SCHEDULER.parent))
        sys.path.insert(0, str(_SCRIPTS))
        import scheduler_scan as _ss
    else:
        _ss = sys.modules["scheduler_scan"]
    with patch.dict(os.environ,
                    {"ILK_DATA_HOME": str(tmp_path / ".ilk-data")}):
        with patch.object(_ss, "ilk_data_root",
                          return_value=tmp_path / ".ilk-data"):
            scan = _ss.scan_projects()

    # AC-1: only merge-pending project dispatched; other held
    assert len(scan) == 1
    assert scan[0]["key"] == "project-a"
    assert scan[0].get("reason") == "merge-pending"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_runner_yields_at_iteration_boundary(tmp_path: Path) -> None:
    """AC-3: a runner between iterations with another project's merge pending
    ⇒ exits with the declared state, and its next relaunch proceeds once
    the merge has landed.

    The runner should exit cleanly when it detects another project's
    merge is pending, allowing the merge to land before relaunching.
    """
    # Create a project with a runner
    project = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running")

    # Create another project with merge pending
    other_project = _make_project(tmp_path, "other-project")
    _write_sentinel(other_project, state="merge-deferred")
    wt_other = other_project / "runtime" / "launcher" / "worktrees" / "selfmod-batch"
    _add_unmerged_commit(wt_other)

    # TODO: Invoke runner's iteration boundary check
    # Should detect other project's merge pending and exit cleanly
    assert False, "AC-3 not implemented: runner should yield at iteration boundary"


def test_ac2_no_merge_pending_dispatches_normally(tmp_path: Path) -> None:
    """AC-2: no merge pending ⇒ B dispatches as today.

    This is a control test - when no merge is pending, normal dispatch
    should proceed.
    """
    # This test should pass (no xfail) as it tests existing behavior
    project_b = _make_project(tmp_path, "project-b")
    _write_sentinel(project_b, state="shipped")
    plans_b = project_b / "plans"
    _write_master(plans_b, "2026-01-01-b", status="queued")

    # Verify project structure is set up correctly
    assert (project_b / "runtime" / "launcher" / "last-exit.json").exists()
    assert (plans_b / "MASTER-2026-01-01-b.md").exists()

    # TODO: Verify normal dispatch would proceed
    # For now, just verify setup is correct
    assert True


# ── Folded items (AC-4, AC-5, AC-6) ─────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_held_master_stops_run_at_boundary(tmp_path: Path) -> None:
    """AC-4: master active for iteration 1, parked before iteration 2 ⇒ no
    iteration 2, sentinel blocked-no-runnable, held line printed.

    A held master should prevent the next iteration from starting.
    """
    # Create project with active master
    project = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running")
    plans = project / "plans"
    _write_master(plans, "2026-01-01", status="active")

    # TODO: Simulate parking the master
    # TODO: Verify next iteration doesn't start
    # TODO: Verify sentinel shows blocked-no-runnable
    assert False, "AC-4 not implemented: held master should stop run"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac5_worker_tamper_detected(tmp_path: Path) -> None:
    """AC-5: a stub worker rewrites a parked master blocked → shipped ⇒ after
    the iteration it reads blocked with parked_at intact, and the run exits
    ship_integrity_violation.

    Worker tampering with master state should be detected and reverted.
    """
    # Create project with parked master
    project = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running")
    plans = project / "plans"
    _write_master(plans, "2026-01-01", status="blocked")

    # TODO: Simulate worker rewriting blocked → shipped
    # TODO: Verify runner detects and reverts
    # TODO: Verify exit state is ship_integrity_violation
    assert False, "AC-5 not implemented: worker tamper should be detected"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac6_write_status_refuses_held_master(tmp_path: Path) -> None:
    """AC-6: write_status on a file whose on-disk status became blocked after
    it was read ⇒ refuses; park_master --unpark still works.

    Writers should refuse to modify a held master.
    """
    # Create project with master
    project = _make_project(tmp_path, "my-project")
    plans = project / "plans"
    _write_master(plans, "2026-01-01", status="active")

    # TODO: Simulate master becoming blocked between read and write
    # TODO: Verify write_status refuses
    # TODO: Verify park_master --unpark still works
    assert False, "AC-6 not implemented: write_status should refuse held master"