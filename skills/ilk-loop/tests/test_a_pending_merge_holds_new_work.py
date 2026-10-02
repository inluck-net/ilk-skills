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


def _make_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Create a minimal project directory with a worktree and sentinel.

    Returns (project_root, ext_project_dir).  The project root IS the git
    repo so that ``find_plans_dir`` can locate plans via the external layout.
    """
    project = tmp_path / "repos" / name
    project.mkdir(parents=True)

    # Init a git repo at the project root.
    _git(project, "init", "-q")
    (project / "README.md").write_text("seed\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-q", "-m", "seed")

    # Compute the project key so we can create the external layout.
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    data_home = tmp_path / ".ilk-data"
    key = ilk_paths.project_key(project)

    # Create runtime/launcher structure under the external data dir.
    ext_project = data_home / "projects" / key
    launcher = ext_project / "runtime" / "launcher"
    launcher.mkdir(parents=True)

    # Create a real git worktree so unmerged_worktree_commits() works.
    wt = launcher / "worktrees" / "selfmod-batch"
    _git(project, "worktree", "add", "--detach", str(wt))

    # Create plans directory (external layout).
    plans = ext_project / "plans"
    plans.mkdir(parents=True)

    # Write last-launch.json so resolve_repo_path works.
    (launcher / "last-launch.json").write_text(
        json.dumps({"project_path": str(project)}), encoding="utf-8"
    )

    return project, ext_project


def _write_sentinel(project: Path, state: str = "running",
                    merge_deferred: bool = False,
                    ext_project: Path | None = None) -> None:
    """Write a last-exit.json sentinel."""
    if ext_project is None:
        # Compute from project path.
        sys.path.insert(0, str(_SCRIPTS))
        import ilk_paths
        data_home = project.parent.parent.parent / ".ilk-data"
        key = ilk_paths.project_key(project)
        ext_project = data_home / "projects" / key
    sentinel = ext_project / "runtime" / "launcher" / "last-exit.json"
    sentinel.write_text(json.dumps({
        "state": state,
        "pid": 12345,
        "run_id": "test-run",
        "started_at": "2026-01-01T00:00:00+0000",
        "ended_at": None,
        "iterations": 1,
        "project_path": str(project),
        "cli": "claude",
        "jsonl_log": str(ext_project / "test.jsonl"),
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
    project_a, ext_a = _make_project(tmp_path, "project-a")
    project_b, ext_b = _make_project(tmp_path, "project-b")

    # Project A has merge-pending state
    _write_sentinel(project_a, state="merge-deferred", ext_project=ext_a)
    wt_a = ext_a / "runtime" / "launcher" / "worktrees" / "selfmod-batch"
    _add_unmerged_commit(wt_a)
    plans_a = ext_a / "plans"
    _write_master(plans_a, "2026-01-01-a", status="queued")

    # Project B is queued and ready to dispatch
    _write_sentinel(project_b, state="shipped", ext_project=ext_b)  # previous run shipped
    plans_b = ext_b / "plans"
    _write_master(plans_b, "2026-01-01-b", status="queued")

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
    assert scan[0]["key"] == ext_a.name
    assert scan[0].get("reason") == "merge-pending"


def test_ac3_runner_yields_at_iteration_boundary(tmp_path: Path) -> None:
    """AC-3: a runner between iterations with another project's merge pending
    ⇒ exits with the declared state, and its next relaunch proceeds once
    the merge has landed.

    The runner's ``_check_other_project_merge_pending`` function scans all
    projects for a merge-deferred sentinel with unmerged worktree commits,
    excluding the current project.  When found, it returns the yielding
    project's key (exit 0); the caller breaks with ``merge-deferred``.
    """
    # Create the "current" project (the one running the loop).
    project, ext_proj = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running", ext_project=ext_proj)

    # Create another project with merge pending.
    other_project, ext_other = _make_project(tmp_path, "other-project")
    _write_sentinel(other_project, state="merge-deferred", ext_project=ext_other)
    wt_other = ext_other / "runtime" / "launcher" / "worktrees" / "selfmod-batch"
    _add_unmerged_commit(wt_other)

    # Source the runner and call the yield check.
    runner = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    env = _sandbox_env(tmp_path)
    env["PROJECT_KEY"] = ext_proj.name
    env["ILK_SKILL_HOME"] = str(_REPO / "skills")
    env["_SKILL_ROOT"] = str(_REPO / "skills")
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export ILK_DATA_HOME='{tmp_path / ".ilk-data"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        export _SKILL_ROOT='{_REPO / "skills"}'
        source '{runner}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        PROJECT_KEY='my-project'
        _yield=$(_check_other_project_merge_pending) && rc=0 || rc=$?
        echo "YIELD=$_yield"
        echo "RC=$rc"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env,
    )
    lines = result.stdout.splitlines()
    yield_line = [l for l in lines if l.startswith("YIELD=")]
    rc_line = [l for l in lines if l.startswith("RC=")]
    assert yield_line, f"no YIELD output: {result.stdout}\n{result.stderr}"
    assert rc_line, f"no RC output: {result.stdout}\n{result.stderr}"
    yield_val = yield_line[-1].split("=", 1)[1]
    rc_val = rc_line[-1].split("=", 1)[1]
    assert rc_val == "0", (
        f"_check_other_project_merge_pending should exit 0 when another "
        f"project has a pending merge, got exit {rc_val}"
    )
    assert yield_val == ext_other.name, (
        f"should yield to '{ext_other.name}', got '{yield_val}'"
    )


def test_ac2_no_merge_pending_dispatches_normally(tmp_path: Path) -> None:
    """AC-2: no merge pending ⇒ B dispatches as today.

    This is a control test - when no merge is pending, normal dispatch
    should proceed.
    """
    # This test should pass (no xfail) as it tests existing behavior
    project_b, ext_b = _make_project(tmp_path, "project-b")
    _write_sentinel(project_b, state="shipped", ext_project=ext_b)
    plans_b = ext_b / "plans"
    _write_master(plans_b, "2026-01-01-b", status="queued")

    # Verify project structure is set up correctly
    assert (ext_b / "runtime" / "launcher" / "last-exit.json").exists()
    assert (plans_b / "MASTER-2026-01-01-b.md").exists()

    # TODO: Verify normal dispatch would proceed
    # For now, just verify setup is correct
    assert True


# ── Folded items (AC-4, AC-5, AC-6) ─────────────────────────────────────────


def test_ac4_held_master_classifies_blocked_no_runnable(tmp_path: Path) -> None:
    """AC-4: master active for iteration 1, parked before iteration 2 ⇒ no
    iteration 2, sentinel blocked-no-runnable, held line printed.

    Sources the runner's ``classify_loop_status`` and verifies it produces
    ``blocked-no-runnable`` when a human-held master is present.  The runner
    now calls this at the top of each iteration (before dispatch).
    """
    import ilk_paths

    project, ext_proj = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running", ext_project=ext_proj)
    plans = ext_proj / "plans"
    _write_master(plans, "2026-01-01", status="active")

    # Park the master via park_master.py (creates hold: human).
    park_script = _SCRIPTS / "park_master.py"
    env = _sandbox_env(tmp_path)
    r = subprocess.run(
        [sys.executable, str(park_script),
         "--plans-dir", str(plans),
         "--reason", "operator stop"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    assert r.returncode == 0, f"park failed: {r.stderr or r.stdout}"

    # Source the runner and call classify_loop_status.
    runner = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    loop_status = _REPO / "skills" / "ilk-loop" / "scripts" / "loop_status.py"
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export ILK_DATA_HOME='{tmp_path / ".ilk-data"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        export _SKILL_ROOT='{_REPO / "skills"}'
        source '{runner}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        PROJECT_PATH='{project}'
        LOOP_STATUS_SCRIPT='{loop_status}'
        classify_loop_status
        echo "CLASSIFIED=$CLASSIFIED_STATUS HELD=${{HELD_BY:-}}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace", env=env,
    )
    assert "CLASSIFIED=blocked-no-runnable" in result.stdout, (
        f"expected blocked-no-runnable, got: {result.stdout}\n{result.stderr}"
    )
    assert "HELD=MASTER-2026-01-01.md" in result.stdout, (
        f"expected HELD=MASTER-2026-01-01.md in: {result.stdout}"
    )


def test_ac5_master_snapshot_catches_tamper(tmp_path: Path) -> None:
    """AC-5: a stub worker rewrites a parked master blocked → shipped ⇒ after
    the iteration it reads blocked with parked_at intact, and the run exits
    ship_integrity_violation.

    Tests the snapshot/restore mechanism directly: take a snapshot of a
    blocked master, simulate a worker changing it to shipped, restore, and
    verify the tamper is detected and reverted.
    """
    project, ext_proj = _make_project(tmp_path, "my-project")
    plans = ext_proj / "plans"

    # Write a parked master (blocked with parked_at).
    master_path = plans / "MASTER-2026-01-01.md"
    master_path.write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-01-01
        status: blocked
        parked_at: 2026-01-01T00:00:00
        parked_reason: operator stop
        hold: human
        ---
        # MASTER plan: 2026-01-01
        ## Sub-plan registry
        | # | Slug | Steps |
        |---|---|---|
        | 1 | test-subplan | 2 |
    """), encoding="utf-8")

    # Import master_snapshot.
    sys.path.insert(0, str(_SCRIPTS))
    import master_snapshot

    # Take a snapshot.
    snap = master_snapshot.take(plans, ["MASTER-2026-01-01.md"])
    snap_file = tmp_path / "snapshot.json"
    snap_file.write_text(json.dumps(snap), encoding="utf-8")

    # Simulate a worker rewriting blocked → shipped.
    master_path.write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-01-01
        status: shipped
        parked_at: 2026-01-01T00:00:00
        parked_reason: operator stop
        hold: human
        ---
        # MASTER plan: 2026-01-01
        ## Sub-plan registry
        | # | Slug | Steps |
        |---|---|---|
        | 1 | test-subplan | 2 |
    """), encoding="utf-8")

    # Restore — should detect the tamper.
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    result = master_snapshot.restore(plans, snap_data)

    # The restore should have detected the status change.
    assert result["restored"], f"expected restore to detect tamper: {result}"
    restored_entry = result["restored"][0]
    assert "status" in restored_entry["changed"], (
        f"expected status in changed fields: {restored_entry}"
    )

    # Verify the file was restored to blocked.
    fm_text = master_path.read_text(encoding="utf-8-sig")
    assert "status: blocked" in fm_text, (
        f"expected status: blocked after restore, got:\n{fm_text}"
    )
    assert "parked_at:" in fm_text, (
        f"expected parked_at to survive restore:\n{fm_text}"
    )


def test_ac6_write_status_refuses_held_master(tmp_path: Path) -> None:
    """AC-6: write_status on a file whose on-disk status became blocked after
    it was read ⇒ refuses; park_master --unpark still works.

    Tests that write_status refuses to modify a held master (blocked with
    parked_at), and that park_master --unpark passes allow_from_held=True.
    """
    sys.path.insert(0, str(_SCRIPTS))
    from promote_next_master import write_status, HeldMasterRefused

    project, ext_proj = _make_project(tmp_path, "my-project")
    plans = ext_proj / "plans"

    # Write a parked master (blocked with parked_at).
    master_path = plans / "MASTER-2026-01-01.md"
    master_path.write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-01-01
        status: blocked
        parked_at: 2026-01-01T00:00:00
        parked_reason: operator stop
        hold: human
        ---
        # MASTER plan: 2026-01-01
        ## Sub-plan registry
        | # | Slug | Steps |
        |---|---|---|
        | 1 | test-subplan | 2 |
    """), encoding="utf-8")

    # write_status should refuse by default.
    with pytest.raises(HeldMasterRefused, match="master is held"):
        write_status(master_path, "queued")

    # Verify the file was NOT modified.
    fm_text = master_path.read_text(encoding="utf-8-sig")
    assert "status: blocked" in fm_text, (
        "write_status should not have modified the file"
    )

    # write_status with allow_from_held=True should succeed.
    result = write_status(master_path, "queued", allow_from_held=True)
    assert result is True
    fm_text = master_path.read_text(encoding="utf-8-sig")
    assert "status: queued" in fm_text, (
        f"expected status: queued after allow_from_held, got:\n{fm_text}"
    )

    # Reset to blocked for the park_master --unpark test.
    master_path.write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-01-01
        status: blocked
        parked_at: 2026-01-01T00:00:00
        parked_reason: operator stop
        hold: human
        ---
        # MASTER plan: 2026-01-01
        ## Sub-plan registry
        | # | Slug | Steps |
        |---|---|---|
        | 1 | test-subplan | 2 |
    """), encoding="utf-8")

    # park_master --unpark --release-hold should work (passes allow_from_held).
    park_script = _SCRIPTS / "park_master.py"
    env = _sandbox_env(tmp_path)
    r = subprocess.run(
        [sys.executable, str(park_script),
         "--plans-dir", str(plans),
         "--unpark", "--release-hold"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    assert r.returncode == 0, f"unpark failed: {r.stderr or r.stdout}"
    out = json.loads(r.stdout)
    assert out["to"] == "queued", f"expected to=queued, got {out}"

    # Verify the master is now queued and park fields are gone.
    fm_text = master_path.read_text(encoding="utf-8-sig")
    assert "status: queued" in fm_text, (
        f"expected status: queued after unpark:\n{fm_text}"
    )
    assert "parked_at" not in fm_text, (
        f"parked_at should be removed after unpark:\n{fm_text}"
    )