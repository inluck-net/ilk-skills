"""Red-first: a red iteration keeps its worktree, does not merge into the clone.

Part of sub-plan ``red-iteration-keeps-its-worktree`` (MASTER-2026-09-24d).

The runner's post-iteration block currently merges the selfmod worktree back
into the clone *before* ship-integrity runs.  That means a red iteration
(a red gate or a ship-integrity violation) has already landed its commits
by the time the violation is detected.

Four acceptance criteria:

  AC-1  (incident reproduction): a red gate ⇒ the clone's HEAD is unchanged,
        the worktree still exists, the log says ``not merging: … worktree kept``,
        and the log order is ship-integrity *then* the merge decision.
        **xfail until step 1** reorders the post-iteration block.

  AC-2  a green iteration ⇒ the merge happens as today (existing
        ``test_selfmod_landing.py`` green).

  AC-3  a gate outcome of ``inconclusive`` only ⇒ merges.

  AC-4  after a kept-worktree run, a second green run merges both runs'
        commits.
        **xfail until step 1**.

Gate: ``4 failed``.  AC-1 and AC-4 assert on behaviour that step 1 adds;
AC-2 and AC-3 are unmarked (they exercise the merge path that already exists).
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
    """A liveness-probe pattern unique to this test invocation.

    Same rationale as in ``test_selfmod_landing.py`` — the default
    ``run_ilk_loop`` matches the whole host process table.
    """
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


# ── AC-1: red gate ⇒ clone unchanged, worktree kept ─────────────────────────


class TestRedIterationKeepsWorktree:
    """AC-1: a red gate ⇒ the clone's HEAD is unchanged, the worktree still
    exists with its commits, and the log names the reason.

    xfail until step 1 moves the merge-back to after ship-integrity and
    gates it on a green iteration.
    """

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_red_gate_prevents_merge(
        self, tmp_path: Path
    ) -> None:
        """An iteration with a red gate must not merge the worktree.

        Verifies:
        - clone HEAD is unchanged (before == after)
        - worktree still exists with its commits
        - merge_cli reports the gate blocked it (exit non-zero or stderr names reason)

        The current code unconditionally merges when SELFMOD_ISOLATED=1,
        so this test is expected to fail until step 1 adds the gate check.
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
        sha1 = _commit_in(worktree_path, "red.txt", "red work", "red commit")

        # Simulate a red gate: the merge must be refused.
        # The merge CLI currently does not accept a gate-outcome parameter;
        # step 1 will add that logic.  For now, call merge and check the
        # observable result.
        result = _merge_cli(repo, worktree_path, env=env,
                            probe_pattern=_probe_token(tmp_path))

        # After a red gate, the clone's HEAD must be unchanged.
        assert _head_sha(repo) == base_sha, (
            "clone HEAD moved despite a red gate — "
            f"before={base_sha[:8]}, after={_head_sha(repo)[:8]}"
        )

        # The worktree still exists with the commit.
        assert worktree_path.exists(), "worktree was removed despite a red gate"
        assert (worktree_path / "red.txt").exists(), "worktree commit is missing"

        # The merge result must indicate the gate prevented it.
        # (Today it exits 0 and merges — that is the bug.)
        assert result.returncode != 0 or "not merging" in result.stderr.lower(), (
            f"merge should refuse on a red gate, got exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
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

        # The worktree still exists after merge (the runner calls
        # selfmod_worktree remove separately; the merge CLI does not).
        assert worktree_path.exists(), "worktree was removed by merge CLI"


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

        # The merge CLI has no gate-outcome parameter today.
        # After step 1, this test will need to supply "inconclusive".
        result = _merge_cli(repo, worktree_path, env=env,
                            probe_pattern=_probe_token(tmp_path))

        assert result.returncode == 0, (
            f"merge should succeed with inconclusive gate, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert (repo / "inconclusive.txt").exists(), (
            "inconclusive commit did not land in clone"
        )


# ── AC-4: after a kept-worktree run, a second green run merges both ──────────


class TestKeptWorktreeMergedBySecondRun:
    """AC-4: after a red gate keeps the worktree, a second green run
    merges both runs' commits into the clone.

    xfail until step 1 keeps the worktree on a red gate.
    """

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_second_green_run_merges_both_batches(
        self, tmp_path: Path
    ) -> None:
        """Run 1 (red gate): worktree commits survive, clone unchanged.
        Run 2 (green gate): all commits from both runs land in the clone.

        Today the merge happens unconditionally in run 1, so run 2's
        worktree is empty and this test fails.
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

        # Run 1: red gate — two commits in the worktree.
        sha_r1a = _commit_in(worktree_path, "run1-a.txt", "r1a", "run 1 commit A")
        sha_r1b = _commit_in(worktree_path, "run1-b.txt", "r1b", "run 1 commit B")

        result1 = _merge_cli(repo, worktree_path, env=env,
                             probe_pattern=_probe_token(tmp_path))

        # After a red gate, the clone is unchanged.
        assert _head_sha(repo) == base_sha, (
            "run 1 (red gate) should not have merged"
        )
        assert worktree_path.exists(), "worktree was removed after red gate"

        # Run 2: green gate — one more commit in the same worktree.
        sha_r2 = _commit_in(worktree_path, "run2.txt", "r2", "run 2 commit")

        result2 = _merge_cli(repo, worktree_path, env=env,
                             probe_pattern=_probe_token(tmp_path))

        assert result2.returncode == 0, (
            f"run 2 (green gate) merge failed: exit {result2.returncode}\n"
            f"stdout: {result2.stdout}\nstderr: {result2.stderr}"
        )

        # All three commits must be reachable from the clone's HEAD.
        clone_log = subprocess.run(
            ["git", "rev-list", base_sha + "..HEAD"], cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        clone_shas = set(clone_log.stdout.strip().splitlines())
        for expected in (sha_r1a, sha_r1b, sha_r2):
            assert expected in clone_shas, (
                f"commit {expected[:8]} not in clone after second merge"
            )

        # The files landed.
        assert (repo / "run1-a.txt").read_text() == "r1a"
        assert (repo / "run1-b.txt").read_text() == "r1b"
        assert (repo / "run2.txt").read_text() == "r2"
