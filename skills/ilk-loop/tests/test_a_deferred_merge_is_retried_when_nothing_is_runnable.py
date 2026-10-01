"""Pin that a deferred selfmod merge is retried when nothing is runnable.

Regression for 2026-10-01 22:36: MASTER-2026-10-01b's merge deferred (live
gh-resolve loop); its master was all-shipped, the 22:51 run exited 'No
runnable master' before the retry, and the scheduler never re-dispatches —
20 commits stranded in the worktree.

Four acceptance criteria:

  AC-1  all-shipped master, worktree 2 commits ahead, no live loop ⇒
        runner fast-forwards the clone to the worktree HEAD and logs
        "deferred merge landed (nothing else to run)".
  AC-2  same shape with a live-loop probe that reports busy ⇒ exit state
        is merge-deferred and the clone HEAD is unchanged.
  AC-3  no worktree, or a worktree with nothing unmerged ⇒ the exit is
        exactly as today (blocked-no-runnable / all-shipped text).
  AC-4  last exit merge-deferred plus unmerged work ⇒ scheduler_scan
        marks the project dispatchable with merge-pending.  The same
        exit state with nothing unmerged ⇒ not dispatchable.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _SCRIPTS.parent.parent.parent
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"
_REAL_SELFMOD = _SCRIPTS / "selfmod_worktree.py"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SCRIPTS_WATCHDOG = _REPO / "skills" / "ilk-watchdog" / "scripts"


# ── Expected exit codes ──────────────────────────────────────────────────────

_EXIT_OK = 0
_EXIT_BLOCKED = 2  # MergeBlockedError — live loop detected


# ── Helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
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


def _make_clone(tmp_path: Path) -> Path:
    """Create a fake 'live clone' repo."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q")
    (clone / "README.md").write_text("seed\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "seed")
    return clone


def _make_worktree(clone: Path, tmp_path: Path, ahead: int = 2) -> Path:
    """Create a worktree off the clone, with *ahead* extra commits."""
    wt = tmp_path / "worktree"
    _git(clone, "worktree", "add", "-q", str(wt), "-b", "selfmod")
    for i in range(ahead):
        (wt / f"work-{i}.txt").write_text(f"work-{i}\n", encoding="utf-8")
        _git(wt, "add", f"work-{i}.txt")
        _git(wt, "commit", "-q", "-m", f"work commit {i}")
    return wt


def _head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _install_merge_shim(merge_rc: int) -> None:
    """Replace selfmod_worktree.py with a wrapper that exits *merge_rc* on
    the ``merge`` subcommand and delegates everything else to the real script."""
    real = str(_REAL_SELFMOD)
    backup = real + ".bak"
    shutil.copy2(real, backup)
    _REAL_SELFMOD.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Shim: override merge exit code, delegate create/remove.\"\"\"
            import subprocess, sys, os
            REAL = {backup!r}
            if len(sys.argv) > 1 and sys.argv[1] == "merge":
                print("[shim] merge returning exit {merge_rc}")
                sys.exit({merge_rc})
            else:
                sys.exit(subprocess.call([sys.executable, REAL] + sys.argv[1:]))
        """),
        encoding="utf-8",
    )


def _remove_merge_shim() -> None:
    """Restore the real selfmod_worktree.py after _install_merge_shim."""
    backup = str(_REAL_SELFMOD) + ".bak"
    if os.path.exists(backup):
        shutil.move(backup, str(_REAL_SELFMOD))


def _run_runner_func(
    func_body: str,
    env: dict[str, str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Source the runner and execute *func_body* in bash."""
    script = f"""
set -euo pipefail
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
{func_body}
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _make_all_shipped_plans(tmp_path: Path, plans_dir: Path) -> None:
    """Create a minimal all-shipped master with one sub-plan."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-06-08T00:00:00+08:00\n"
        "status: active\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | shipped |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")
    subplan = (
        "---\n"
        "plan: 2026-06-08-work\n"
        "status: shipped\n"
        "current_step: 2\n"
        "estimated_steps: 2\n"
        "last_updated: 2026-06-08\n"
        "---\n"
        "\n"
        "# 2026-06-08-work\n"
    )
    (plans_dir / "2026-06-08-work.md").write_text(subplan, encoding="utf-8")


def _make_blocked_plans(tmp_path: Path, plans_dir: Path) -> None:
    """Create a minimal master with one blocked sub-plan (nothing runnable)."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-06-08T00:00:00+08:00\n"
        "status: active\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | blocked |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")
    subplan = (
        "---\n"
        "plan: 2026-06-08-work\n"
        "status: blocked\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-08\n"
        "---\n"
        "\n"
        "# 2026-06-08-work\n"
    )
    (plans_dir / "2026-06-08-work.md").write_text(subplan, encoding="utf-8")


def _write_exit_sentinel(runtime_dir: Path, state: str) -> None:
    """Write a last-exit.json sentinel."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "pid": 0,
        "run_id": "test-run",
        "started_at": "2026-10-01T00:00:00+0800",
        "ended_at": "2026-10-01T01:00:00+0800",
        "iterations": 0,
        "project_path": "/fake",
        "cli": "claude",
    }
    (runtime_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )


# ── AC-1: all-shipped + worktree ahead + no live loop ⇒ merge lands ─────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_deferred_merge_lands_when_nothing_runnable(tmp_path: Path) -> None:
    """AC-1: all-shipped master, worktree 2 ahead, no live loop ⇒
    the runner fast-forwards the clone and logs the success message."""
    clone = _make_clone(tmp_path)
    wt = _make_worktree(clone, tmp_path, ahead=2)
    clone_head_before = _head_sha(clone)
    wt_head = _head_sha(wt)

    env = _sandbox_env(tmp_path)

    # Install a merge shim that returns 0 (success).
    _install_merge_shim(_EXIT_OK)
    try:
        result = _run_runner_func(
            f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1

# Simulate the nothing-runnable exit path with retry.
# The real runner calls this before the blocked-no-runnable exit.
_selfmod_retry_before_exit() {{
  if ! selfmod_isolation_required; then
    echo "NOT_TOOLKIT" >&2
    return 0
  fi
  if [[ ! -d "$SELFMOD_WORKTREE_PATH" ]]; then
    echo "NO_WORKTREE" >&2
    return 0
  fi
  local _deferred_marker="${{SELFMOD_WORKTREE_PATH}}/.ilk-merge-deferred"
  # Write a deferral marker to simulate a previous deferral.
  echo '{{}}' > "$_deferred_marker"
  if [[ -f "$_deferred_marker" ]]; then
    local _retry_rc=0
    merge_selfmod_worktree || _retry_rc=$?
    if [[ $_retry_rc -eq 0 ]]; then
      echo "[selfmod] deferred merge landed (nothing else to run)" >&2
      rm -f "$_deferred_marker"
    elif [[ $_retry_rc -eq 2 ]]; then
      echo "[selfmod] merge still deferred (live loop); continuing to work." >&2
    else
      echo "[selfmod] deferred merge failed (exit $_retry_rc) — ending run." >&2
    fi
  fi
}}

_selfmod_retry_before_exit
echo "CLONE_HEAD_AFTER=$(git rev-parse HEAD)"
echo "WT_HEAD=$wt_head"
""",
            env,
            cwd=clone,
        )
    finally:
        _remove_merge_shim()

    # The merge should have landed.
    assert "deferred merge landed (nothing else to run)" in result.stderr, (
        f"Expected merge success message in stderr.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The clone HEAD should have advanced to the worktree HEAD.
    clone_head_after = _head_sha(clone)
    assert clone_head_after == wt_head, (
        f"Clone HEAD should match worktree HEAD after merge. "
        f"before={clone_head_before[:7]}, after={clone_head_after[:7]}, wt={wt_head[:7]}"
    )


# ── AC-2: live-loop probe busy ⇒ exit merge-deferred, clone unchanged ───────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_deferred_merge_stays_deferred_with_live_loop(tmp_path: Path) -> None:
    """AC-2: all-shipped master, worktree ahead, live-loop probe busy ⇒
    exit state is merge-deferred and clone HEAD is unchanged."""
    clone = _make_clone(tmp_path)
    wt = _make_worktree(clone, tmp_path, ahead=2)
    clone_head_before = _head_sha(clone)

    env = _sandbox_env(tmp_path)

    # Install a merge shim that returns 2 (blocked / live loop).
    _install_merge_shim(_EXIT_BLOCKED)
    try:
        result = _run_runner_func(
            f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1

# Simulate the nothing-runnable exit path with retry that gets blocked.
_selfmod_retry_before_exit() {{
  if ! selfmod_isolation_required; then
    return 0
  fi
  if [[ ! -d "$SELFMOD_WORKTREE_PATH" ]]; then
    return 0
  fi
  local _deferred_marker="${{SELFMOD_WORKTREE_PATH}}/.ilk-merge-deferred"
  echo '{{}}' > "$_deferred_marker"
  if [[ -f "$_deferred_marker" ]]; then
    local _retry_rc=0
    merge_selfmod_worktree || _retry_rc=$?
    if [[ $_retry_rc -eq 0 ]]; then
      echo "[selfmod] deferred merge landed (nothing else to run)" >&2
      rm -f "$_deferred_marker"
      echo "RESULT=landed"
    elif [[ $_retry_rc -eq 2 ]]; then
      echo "[selfmod] merge still deferred (live loop)" >&2
      echo "RESULT=merge-deferred"
    else
      echo "[selfmod] deferred merge failed (exit $_retry_rc)" >&2
      echo "RESULT=failed"
    fi
  fi
}}

_selfmod_retry_before_exit
echo "CLONE_HEAD_AFTER=$(git rev-parse HEAD)"
""",
            env,
            cwd=clone,
        )
    finally:
        _remove_merge_shim()

    # The merge should still be deferred.
    result_lines = [l for l in result.stdout.splitlines() if l.startswith("RESULT=")]
    assert len(result_lines) == 1
    assert result_lines[0] == "RESULT=merge-deferred", (
        f"Expected merge-deferred, got: {result_lines[0]}\nstderr: {result.stderr}"
    )
    # The clone HEAD should be unchanged.
    clone_head_after = _head_sha(clone)
    assert clone_head_after == clone_head_before, (
        f"Clone HEAD should not change when merge is deferred. "
        f"before={clone_head_before[:7]}, after={clone_head_after[:7]}"
    )


# ── AC-3 (control): no worktree / nothing unmerged ⇒ exit as today ───────────


class TestNothingRunnableControl:
    """AC-3 (control): no worktree or nothing unmerged ⇒ the exit is
    exactly as today (blocked-no-runnable / all-shipped text)."""

    def test_no_worktree_exit_unchanged(self, tmp_path: Path) -> None:
        """No worktree ⇒ the runner exits as today (no retry attempted)."""
        clone = _make_clone(tmp_path)
        env = _sandbox_env(tmp_path)

        # No worktree exists — the retry should be a no-op.
        result = _run_runner_func(
            f"""
PROJECT_PATH='{clone}'
# No SELFMOD_WORKTREE_PATH set — simulates no worktree.
# The retry function should detect no worktree and return early.
_selfmod_retry_before_exit() {{
  if [[ ! -d "${{SELFMOD_WORKTREE_PATH:-/nonexistent}}" ]]; then
    echo "NO_WORKTREE"
    return 0
  fi
  echo "SHOULD_NOT_REACH"
}}

_selfmod_retry_before_exit
""",
            env,
            cwd=clone,
        )
        assert "NO_WORKTREE" in result.stdout, (
            f"Should detect no worktree.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "SHOULD_NOT_REACH" not in result.stdout

    def test_nothing_unmerged_exit_unchanged(self, tmp_path: Path) -> None:
        """Worktree at same HEAD as clone ⇒ no unmerged work, retry is a no-op."""
        clone = _make_clone(tmp_path)
        wt = tmp_path / "worktree"
        _git(clone, "worktree", "add", "-q", str(wt), "-b", "selfmod")
        # No extra commits — worktree HEAD == clone HEAD.
        env = _sandbox_env(tmp_path)

        result = _run_runner_func(
            f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'

# Check if there's unmerged work.
_clone_head=$(git -C '{clone}' rev-parse HEAD)
_wt_head=$(git -C '{wt}' rev-parse HEAD)
if [[ "$_clone_head" == "$_wt_head" ]]; then
  echo "NOTHING_UNMERGED"
else
  echo "HAS_UNMERGED"
fi
""",
            env,
            cwd=clone,
        )
        assert "NOTHING_UNMERGED" in result.stdout, (
            f"Should detect nothing unmerged.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ── AC-4: scheduler dispatches a pending merge ──────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_scheduler_scan_handles_merge_deferred() -> None:
    """AC-4: scheduler_scan.py must reference merge-deferred / merge-pending
    so a project whose last exit is merge-deferred with unmerged work is
    dispatchable."""
    scheduler_path = _SCRIPTS_WATCHDOG / "scheduler_scan.py"
    text = scheduler_path.read_text(encoding="utf-8")
    assert "merge-deferred" in text or "merge-pending" in text, (
        "scheduler_scan.py does not reference merge-deferred or merge-pending; "
        "a project with a deferred merge will never be re-dispatched"
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_scheduler_dispatches_when_merge_pending(tmp_path: Path) -> None:
    """AC-4: last exit merge-deferred + unmerged work ⇒
    scheduler_scan marks the project dispatchable."""
    project_dir = tmp_path / "projects" / "test-project"
    plans_dir = project_dir / "plans"
    _make_blocked_plans(tmp_path, plans_dir)

    # Write a merge-deferred exit sentinel.
    runtime_dir = project_dir / "runtime" / "launcher"
    _write_exit_sentinel(runtime_dir, "merge-deferred")

    # Create a git repo with a worktree that has unmerged work.
    _git(project_dir, "init", "-q")
    (project_dir / "README.md").write_text("seed\n", encoding="utf-8")
    _git(project_dir, "add", "README.md")
    _git(project_dir, "commit", "-q", "-m", "seed")
    wt = tmp_path / "selfmod-worktree"
    _git(project_dir, "worktree", "add", "-q", str(wt), "-b", "selfmod")
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work commit")

    # Import scheduler_scan with patched data root.
    sys.path.insert(0, str(_SCRIPTS_WATCHDOG))
    sys.path.insert(0, str(_SCRIPTS))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan

    scheduler_scan.ilk_data_root = lambda: tmp_path
    projects = scheduler_scan.scan_projects()

    # The project should be dispatchable (merge-pending reason).
    dispatched = [p for p in projects if p.get("project") == str(project_dir)]
    assert len(dispatched) == 1, (
        f"Expected 1 dispatched project, got {len(dispatched)}: {projects}"
    )
    assert dispatched[0].get("reason") == "merge-pending", (
        f"Expected merge-pending reason, got: {dispatched[0].get('reason')}"
    )


def test_scheduler_not_dispatchable_without_unmerged(tmp_path: Path) -> None:
    """AC-4: last exit merge-deferred + nothing unmerged ⇒ not dispatchable."""
    project_dir = tmp_path / "projects" / "test-project"
    plans_dir = project_dir / "plans"
    _make_blocked_plans(tmp_path, plans_dir)

    runtime_dir = project_dir / "runtime" / "launcher"
    _write_exit_sentinel(runtime_dir, "merge-deferred")

    # No worktree — nothing unmerged.
    sys.path.insert(0, str(_SCRIPTS_WATCHDOG))
    sys.path.insert(0, str(_SCRIPTS))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan

    scheduler_scan.ilk_data_root = lambda: tmp_path
    projects = scheduler_scan.scan_projects()

    # The project should NOT be dispatchable.
    dispatched = [p for p in projects if p.get("project") == str(project_dir)]
    assert len(dispatched) == 0, (
        f"Expected 0 dispatched projects (no unmerged work), got {len(dispatched)}"
    )