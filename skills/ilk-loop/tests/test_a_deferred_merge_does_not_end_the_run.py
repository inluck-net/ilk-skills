"""Red-first tests: a merge deferred by a live loop does not end the run.

Part of sub-plan ``a-deferred-merge-does-not-end-the-run``
(MASTER-2026-09-28c).

Five acceptance criteria:

  AC-1  iteration 1 green, merge returns 2 ⇒ run does NOT end.
        Iteration 2 dispatched in worktree, merge retried, returns 0, lands.
  AC-2  new run starts with deferral marker, merge returns 0 ⇒ merge lands
        before any agent is dispatched.
  AC-3  run ends with merge still deferred ⇒ sentinel selfmod_merge_failed
        plus merge_deferred field; collect.classify ⇒ merge-deferred, not
        merge-conflict.
  AC-4  merge returns 3, 5 or 6 ⇒ run ends selfmod_merge_failed without
        merge_deferred, classified merge-conflict (control).
  AC-5  no runnable work plus deferred merge ⇒ exactly one retry, then run
        ends.  No spin.

AC-1, AC-2, AC-3 and AC-5 are xfail (red-first).  AC-4 is a control.
"""
from __future__ import annotations

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

# ── Expected exit codes (selfmod_worktree.py merge contract) ─────────────────

_EXIT_OK = 0
_EXIT_BLOCKED = 2       # MergeBlockedError — live loop detected
_EXIT_BRANCH_MOVED = 3  # BranchMovedError
_EXIT_LOCK_HELD = 5     # RuntimeError (lock contention)
_EXIT_DIRTY = 6         # Live clone dirty


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


def _make_clone(tmp_path: Path) -> Path:
    """Create a fake 'live clone' repo."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q")
    (clone / "README.md").write_text("seed\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "seed")
    return clone


def _make_worktree(clone: Path, tmp_path: Path) -> Path:
    """Create a worktree off the clone, with one extra commit."""
    wt = tmp_path / "worktree"
    _git(clone, "worktree", "add", "-q", str(wt), "-b", "selfmod")
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work commit")
    return wt


def _make_plans(tmp_path: Path, slug: str = "deferred-merge") -> Path:
    """Create an external plans dir with one in-progress sub-plan."""
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / f"2026-09-28-{slug}.md").write_text(
        textwrap.dedent(f"""\
            ---
            plan: {slug}
            status: in-progress
            current_step: 0
            tickets: []
            priority: P0
            estimated_steps: 2
            last_updated: 2026-09-28
            verification_tier: loop-verified
            ---
            # Test sub-plan
            ## Steps
            ### Step 0 — test
            - placeholder
            ### Step 1 — test
            - placeholder
        """),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-28-execution-plan.md").write_text(
        textwrap.dedent("""\
            ---
            master_plan: 2026-09-28-execution
            batch_date: 2026-09-28
            status: active
            current_subplan: 2026-09-28-deferred-merge
            ---
            # MASTER
            ## Sub-plan registry
            | # | File | Status |
            |---|---|---|
            | 0 | [deferred-merge](./2026-09-28-deferred-merge.md) | in-progress |
        """),
        encoding="utf-8",
    )
    return plans


def _install_merge_shim(merge_rc: int) -> None:
    """Replace selfmod_worktree.py with a wrapper that exits *merge_rc* on
    the ``merge`` subcommand and delegates everything else to the real
    script.  Call _remove_merge_shim() afterwards."""
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
    timeout: int = 60,
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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, cwd=str(cwd) if cwd else None,
    )


# ── AC-4: control — merge returns 3/5/6 ⇒ runner sets selfmod_merge_failed ──


class TestMergeFailsControl:
    """AC-4 (control): merge exit 3, 5 or 6 ⇒ run ends selfmod_merge_failed
    without merge_deferred.  This is today's behaviour and must pass.

    We source the runner and call ``merge_selfmod_worktree()`` directly.
    The function's ``case`` statement handles non-zero exits.  In the full
    runner, the main loop then sets ``stop_reason="selfmod_merge_failed"``
    and breaks; here we verify the function's exit code and the loop's
    ``stop_reason`` variable."""

    @pytest.mark.parametrize("exit_code", [_EXIT_BRANCH_MOVED, _EXIT_LOCK_HELD, _EXIT_DIRTY])
    def test_merge_nonzero_exit_propagates(
        self, tmp_path: Path, exit_code: int,
    ) -> None:
        """merge_selfmod_worktree() returns non-zero for exit codes 3/5/6."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)
        env = _sandbox_env(tmp_path)

        _install_merge_shim(exit_code)
        try:
            result = _run_runner_func(
                f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1
merge_rc=0
merge_selfmod_worktree || merge_rc=$?
echo "MERGE_RC=$merge_rc"
""",
                env, cwd=clone,
            )
        finally:
            _remove_merge_shim()

        merge_line = [l for l in result.stdout.splitlines() if l.startswith("MERGE_RC=")]
        assert len(merge_line) == 1
        assert merge_line[0] == f"MERGE_RC={exit_code}", (
            f"merge should exit {exit_code}, got: {merge_line[0]}\n"
            f"stderr: {result.stderr}"
        )

    def test_worktree_preserved_on_failure(self, tmp_path: Path) -> None:
        """A failed merge leaves the worktree intact (not removed)."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)
        env = _sandbox_env(tmp_path)

        _install_merge_shim(_EXIT_BRANCH_MOVED)
        try:
            _run_runner_func(
                f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1
merge_selfmod_worktree || true
""",
                env, cwd=clone,
            )
        finally:
            _remove_merge_shim()

        assert wt.exists(), "worktree should survive a failed merge"


# ── AC-1: merge returns 2 ⇒ run continues ────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDeferredMergeDoesNotEndRun:
    """AC-1: merge returns 2 (live loop) ⇒ the run does NOT end.

    Today the runner's main loop (line ~5231) sets
    ``iter_stop_reason="selfmod_merge_failed"`` for ANY non-zero merge exit,
    and the loop breaks at line ~5264.  After step 1, exit 2 must NOT set
    ``stop_reason`` and ``SELFMOD_ISOLATED`` must stay 1.

    We test by sourcing the runner, calling ``merge_selfmod_worktree()``
    with exit 2, then checking the loop state variables that the main loop
    would use to decide whether to break."""

    def test_merge_exit_2_does_not_set_stop_reason(self, tmp_path: Path) -> None:
        """When merge returns 2, the main loop's stop_reason must stay empty."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)
        env = _sandbox_env(tmp_path)

        _install_merge_shim(_EXIT_BLOCKED)
        try:
            result = _run_runner_func(
                f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1

# Simulate the runner's main-loop merge block (lines 5193-5238).
# After step 1, exit 2 should NOT set stop_reason.
iter_stop_reason=""
stop_reason=""
merge_rc=0
merge_selfmod_worktree || merge_rc=$?
if [[ $merge_rc -ne 0 ]]; then
  # Current behaviour: sets iter_stop_reason for ANY non-zero exit.
  # After step 1: exit 2 should NOT set it.
  iter_stop_reason="selfmod_merge_failed"
  stop_reason="selfmod_merge_failed"
fi
SELFMOD_ISOLATED=0

echo "MERGE_RC=$merge_rc"
echo "STOP_REASON=$stop_reason"
echo "ITER_STOP_REASON=$iter_stop_reason"
echo "SELFMOD_ISOLATED=$SELFMOD_ISOLATED"
""",
                env, cwd=clone,
            )
        finally:
            _remove_merge_shim()

        assert "MERGE_RC=2" in result.stdout
        # After step 1: stop_reason must be empty (run continues).
        stop_line = [l for l in result.stdout.splitlines() if l.startswith("STOP_REASON=")]
        assert len(stop_line) == 1
        assert stop_line[0] == "STOP_REASON=", (
            f"stop_reason should be empty (run continues), got: {stop_line[0]}"
        )

    def test_selfmod_isolated_stays_1_on_deferral(self, tmp_path: Path) -> None:
        """When merge returns 2, SELFMOD_ISOLATED stays 1 for the next iteration.

        Today the runner resets SELFMOD_ISOLATED=0 unconditionally after the
        merge block (line ~5238).  After step 1, exit 2 must skip that reset."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)
        env = _sandbox_env(tmp_path)

        _install_merge_shim(_EXIT_BLOCKED)
        try:
            result = _run_runner_func(
                f"""
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=1

# Replicate the runner's merge block + reset (lines 5193-5238).
merge_rc=0
merge_selfmod_worktree || merge_rc=$?
if [[ $merge_rc -ne 0 ]]; then
  iter_stop_reason="selfmod_merge_failed"
  stop_reason="selfmod_merge_failed"
fi
# Current behaviour: unconditionally reset (line 5238).
SELFMOD_ISOLATED=0

echo "MERGE_RC=$merge_rc"
echo "SELFMOD_ISOLATED=$SELFMOD_ISOLATED"
""",
                env, cwd=clone,
            )
        finally:
            _remove_merge_shim()

        # After step 1: SELFMOD_ISOLATED should stay 1 (not reset to 0).
        iso_line = [l for l in result.stdout.splitlines() if l.startswith("SELFMOD_ISOLATED=")]
        assert len(iso_line) == 1
        assert iso_line[0] == "SELFMOD_ISOLATED=1", (
            f"SELFMOD_ISOLATED should stay 1 on deferral, got: {iso_line[0]}"
        )


# ── AC-2: deferral marker + merge 0 ⇒ merge lands before dispatch ────────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDeferralMarkerRetryOnStart:
    """AC-2: a new run starts with the deferral marker present and merge
    returns 0 ⇒ the merge lands before any agent is dispatched."""

    def test_deferral_marker_causes_pre_dispatch_merge(self, tmp_path: Path) -> None:
        """The runner must check for a deferral marker at run start and retry
        the merge before dispatching any agent.

        Today the runner has no deferral-marker logic.  After step 1, the
        runner script must contain a check for ``.ilk-merge-deferred`` in the
        reused worktree."""
        # Read the runner script and check for the deferral marker logic.
        runner_text = _RUNNER.read_text(encoding="utf-8")
        assert ".ilk-merge-deferred" in runner_text, (
            "runner should check for .ilk-merge-deferred marker at run start"
        )


# ── AC-3: run ends with deferred merge ⇒ sentinel + classify ─────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDeferredMergeSentinel:
    """AC-3: the run ends with the merge still deferred ⇒ sentinel carries
    selfmod_merge_failed plus merge_deferred field, and collect.classify
    returns merge-deferred (not merge-conflict)."""

    def test_sentinel_has_merge_deferred_field(self, tmp_path: Path) -> None:
        """When the run ends with merge deferred, the runner must write
        merge_deferred: {live_pids, since} into the sentinel.

        Today the runner's sentinel-writing code (line ~5360) does not include
        a ``merge_deferred`` field.  After step 1, the runner script must
        write this field when the merge was deferred (exit 2)."""
        runner_text = _RUNNER.read_text(encoding="utf-8")
        assert "merge_deferred" in runner_text, (
            "runner should write merge_deferred field to sentinel when merge is deferred"
        )

    def test_classify_returns_merge_deferred(self, tmp_path: Path) -> None:
        """A sentinel with merge_deferred classifies as merge-deferred,
        not merge-conflict."""
        import json
        sys.path.insert(0, str(_REPO / "skills" / "ilk-feedback" / "scripts"))
        from collect import classify

        sentinel_dir = tmp_path / ".ilk-data" / "runtime" / "launcher"
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel = sentinel_dir / "last-exit.json"
        sentinel.write_text(json.dumps({
            "state": "selfmod_merge_failed",
            "merge_deferred": {
                "live_pids": [12345],
                "since": "2026-09-28T10:00:00",
            },
        }), encoding="utf-8")

        label, _details = classify(tmp_path / ".ilk-data")
        assert label == "merge-deferred", (
            f"expected merge-deferred, got: {label}"
        )


# ── AC-5: no runnable work + deferred merge ⇒ one retry, no spin ─────────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDeferredMergeNoSpin:
    """AC-5: no runnable work plus a deferred merge ⇒ exactly one retry,
    then the run ends.  No spin."""

    def test_no_runnable_work_one_retry_then_stop(self, tmp_path: Path) -> None:
        """With no runnable work and a deferred merge, the runner must retry
        once and then end — not spin through MAX_ITERATIONS.

        Today the runner has no deferral-retry logic at all.  After step 1,
        the runner script must contain a "no runnable work" check that limits
        retries when the only pending action is a deferred merge."""
        runner_text = _RUNNER.read_text(encoding="utf-8")
        # After step 1: the runner should have logic to bound the retry
        # when there's no runnable work other than the pending merge.
        # Check for a specific pattern: a deferral-retry counter or a
        # "no work + deferred merge" guard that ends the run.
        has_retry_guard = (
            "merge_deferred_retry" in runner_text
            or "deferred_merge_retry" in runner_text
            or "no_runnable_work" in runner_text
            or ("DEFERRED" in runner_text and "retry" in runner_text.lower())
        )
        assert has_retry_guard, (
            "runner should have logic to limit retries when no work other than deferred merge"
        )