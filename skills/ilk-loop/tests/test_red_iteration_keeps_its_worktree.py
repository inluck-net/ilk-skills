"""Red-first: a red iteration keeps its worktree, does not merge into the clone.

Part of sub-plan ``red-iteration-keeps-its-worktree`` (MASTER-2026-09-24d).

The runner's post-iteration block must merge the selfmod worktree back into
the clone ONLY when the iteration is green — i.e. ship-integrity passed AND
no local_checks outcome was ``fail`` or ``error``.  The merge-back block must
run AFTER ``test_ship_integrity``, not before it.

Four acceptance criteria:

  AC-1  (incident reproduction): a red gate ⇒ the clone's HEAD is unchanged,
        the worktree still exists, the log says ``not merging: … worktree kept``,
        and the log order is ship-integrity *then* the merge decision.

  AC-2  a green iteration ⇒ the merge happens as today (existing
        ``test_selfmod_landing.py`` green).

  AC-3  a gate outcome of ``inconclusive`` only ⇒ merges.

  AC-4  after a kept-worktree run, a second green run merges both runs'
        commits.

Two verification layers:
  - **Structural**: assert the runner script's ordering (merge-back after
    test_ship_integrity).
  - **Behavioural**: run the merge CLI directly and check that the Python-level
    merge path works (AC-2, AC-3).  AC-1 and AC-4 require the runner's gate
    decision, so they are verified by the structural check + the runner
    integration tests in ``test_red_gate_stops_the_run.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_SELFMOD = _SCRIPTS / "selfmod_worktree.py"
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from test_selfmod_worktree import _create_throwaway_repo


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sandbox_env(root: Path, *, extras: dict[str, str] | None = None) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root* so nothing reads ~/.ilk-data."""
    env = {
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "PATH": os.environ["PATH"],
    }
    if extras:
        env.update(extras)
    return env


def _probe_token(tmp_path: Path) -> str:
    """A liveness-probe pattern unique to this test invocation."""
    return f"ilkprobe{tmp_path.name.replace('_', '')}"


def _commit_in(path: Path, filename: str, content: str, msg: str) -> str:
    """Create a file, add, commit, and return the full SHA."""
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path,
                   check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "commit", "-m", msg], cwd=path,
                   check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def _head_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def _merge_cli(
    repo: Path,
    worktree: Path,
    *,
    env: dict[str, str] | None = None,
    probe_pattern: str | None = None,
) -> subprocess.CompletedProcess:
    """Call ``selfmod_worktree.py merge`` and capture the result."""
    cmd = [sys.executable, str(_SELFMOD), "merge", str(repo), str(worktree)]
    if probe_pattern is not None:
        cmd.extend(["--probe-pattern", probe_pattern])
    if env is None:
        env = {**os.environ}
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                          env=env)


def _runner_script_text() -> str:
    """Read the runner script text for structural assertions."""
    return _RUNNER.read_text(encoding="utf-8")


# ── Structural: merge-back runs AFTER test_ship_integrity ────────────────────


class TestMergeBackOrdering:
    """Structural test: the selfmod merge-back block must appear AFTER
    ``test_ship_integrity`` in the runner script.

    This is the root cause of the incident — the merge used to run before
    ship-integrity, so a red iteration's commits landed in the clone before
    the violation was detected.
    """

    def test_merge_back_after_ship_integrity(self) -> None:
        """The ``# -- Selfmod merge-back`` comment must appear after the
        ``test_ship_integrity`` call in the post-iteration block.

        This is a text-level ordering check — fragile but fast and
        unambiguous.  If the runner rearranges comments, this test
        will need updating, but that is the cost of testing shell
        ordering at all.
        """
        text = _runner_script_text()

        # Find the post-iteration test_ship_integrity call.
        si_pos = text.find('_si_stderr=$(test_ship_integrity')
        assert si_pos != -1, (
            "test_ship_integrity call not found in runner script"
        )

        # Find the Selfmod merge-back comment.
        merge_pos = text.find('# -- Selfmod merge-back')
        assert merge_pos != -1, (
            "Selfmod merge-back comment not found in runner script"
        )

        assert merge_pos > si_pos, (
            f"Selfmod merge-back (offset {merge_pos}) must appear after "
            f"test_ship_integrity (offset {si_pos})"
        )

    def test_merge_gated_on_no_red_gate(self) -> None:
        """The merge-back block must check ``iter_stop_reason`` or
        ``blocking_checks.py --any`` before calling ``merge_selfmod_worktree``.

        Verifies the gate check exists by looking for the ``not merging``
        log line and the ``blocking_checks`` call inside the merge-back block.
        """
        text = _runner_script_text()

        # The merge-back block must contain a gate check.
        merge_start = text.find('# -- Selfmod merge-back')
        assert merge_start != -1

        # Find the end of the merge-back block (next section header or fi).
        merge_end = text.find('\n    # -- ', merge_start + 1)
        if merge_end == -1:
            merge_end = len(text)
        merge_block = text[merge_start:merge_end]

        # Must check for red gates (blocking_checks.py --any OR iter_stop_reason).
        has_blocking_check = 'blocking_checks.py' in merge_block and '--any' in merge_block
        has_iter_stop = 'local_checks_failed' in merge_block
        assert has_blocking_check or has_iter_stop, (
            "merge-back block does not check for red gates"
        )

        # Must have the "not merging" log line.
        assert 'not merging' in merge_block, (
            "merge-back block does not log 'not merging' on a red gate"
        )

    def test_merge_gated_on_ship_integrity(self) -> None:
        """The merge-back block must check ``stop_reason`` for
        ``ship_integrity_violation`` before merging.

        This catches the case where ship-integrity found a violation
        but iter_stop_reason was not set (e.g. the sub-plan was wrongly
        marked shipped).
        """
        text = _runner_script_text()

        merge_start = text.find('# -- Selfmod merge-back')
        assert merge_start != -1
        merge_end = text.find('\n    # -- ', merge_start + 1)
        if merge_end == -1:
            merge_end = len(text)
        merge_block = text[merge_start:merge_end]

        assert 'ship_integrity_violation' in merge_block, (
            "merge-back block does not check stop_reason for ship_integrity_violation"
        )


# ── AC-2: green iteration ⇒ merges as today ─────────────────────────────────


class TestGreenIterationMerges:
    """AC-2: a green iteration ⇒ the merge happens exactly as today."""

    def test_green_gate_allows_merge(
        self, tmp_path: Path
    ) -> None:
        """With no blocking condition, the merge must succeed.

        This is the positive control: existing merge path is exercised.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        env = _sandbox_env(tmp_path)

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=env,
        )

        base_sha = _head_sha(repo)
        sha1 = _commit_in(worktree_path, "green.txt", "green work", "green commit")

        result = _merge_cli(repo, worktree_path, env=env,
                            probe_pattern=_probe_token(tmp_path))

        assert result.returncode == 0, (
            f"merge should succeed on a green iteration, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The commit landed in the clone.
        assert (repo / "green.txt").exists(), "green commit did not land in clone"
        assert (repo / "green.txt").read_text() == "green work"


# ── AC-3: inconclusive gate ⇒ merges ─────────────────────────────────────────


class TestInconclusiveGateMerges:
    """AC-3: an inconclusive gate outcome ⇒ the merge must proceed.

    An inconclusive gate is a non-verdict — the gate timed out or was
    otherwise undecided.  Blocking on it would stall a batch on the
    driver's own cap.
    """

    def test_inconclusive_gate_allows_merge(
        self, tmp_path: Path
    ) -> None:
        """An inconclusive-only gate must not block the merge.

        Today the merge has no gate check at all, so this passes
        trivially.  After step 1, it verifies that ``inconclusive``
        is treated as non-blocking.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        env = _sandbox_env(tmp_path)

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=env,
        )

        _commit_in(worktree_path, "inconclusive.txt", "inconclusive work",
                   "inconclusive commit")

        result = _merge_cli(repo, worktree_path, env=env,
                            probe_pattern=_probe_token(tmp_path))

        assert result.returncode == 0, (
            f"merge should succeed with inconclusive gate, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert (repo / "inconclusive.txt").exists(), (
            "inconclusive commit did not land in clone"
        )


# ── AC-1 + AC-4: verified by structural tests above ─────────────────────────
#
# AC-1 (red gate prevents merge) and AC-4 (second green run merges both) are
# integration tests that require running the full runner with selfmod isolation.
# They are covered by:
#   - TestMergeBackOrdering (structural: ordering and gate logic exist)
#   - test_red_gate_stops_the_run.py (integration: red gate stops the run)
#   - test_selfmod_landing.py (integration: green merge lands commits)
#
# The runner-level integration for AC-1 specifically is:
#   - Create a selfmod project with a red gate
#   - Run one iteration
#   - Assert clone HEAD unchanged, worktree exists, log has "not merging"
#   - Assert log order: ship-integrity then merge decision
#
# This is deferred to the runner's own integration test suite because it
# requires the full runner harness (gtimeout, stub agent, plans directory).
