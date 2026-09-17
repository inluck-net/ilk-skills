"""Red-first tests: an isolated batch lands or reports.

Five tests, one per acceptance criterion:

  AC-1  clean merge: exit 0, every worktree commit arrives in the clone with
        identical SHAs, and the worktree is removed.
  AC-2  live loop detected: exit 2, nothing merges, worktree survives, reason
        names the blocking pids.
  AC-3  clone HEAD moved since creation: exit 3, nothing merges, reason names
        both SHAs.
  AC-4  broken liveness probe: exit 4, treated as blocked (fail-closed).
  AC-5  lock contention: exit non-zero, merge does not interleave.

Gate: ``5 failed``.  All five assert on the exit-code mapping that step 1
adds to the merge CLI.  The current CLI lets Python exceptions propagate
(exit 1 for every failure, traceback on stderr), so every assertion on a
distinct exit code or a human-readable stderr line fails.
"""
from __future__ import annotations

import fcntl
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


# ── Expected exit codes (step 1 contract) ────────────────────────────────────

_EXIT_OK = 0
_EXIT_BLOCKED = 2          # MergeBlockedError — live loop or broken probe
_EXIT_BRANCH_MOVED = 3     # BranchMovedError
_EXIT_PROBE_BROKEN = 4     # broken pgrep → fail-closed
_EXIT_LOCK_HELD = 5        # lock contention (RuntimeError from fcntl)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _merge_cli(
    repo: Path,
    worktree: Path,
    *,
    lock: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Call ``selfmod_worktree.py merge`` and capture the result."""
    cmd = [sys.executable, str(_SELFMOD), "merge", str(repo), str(worktree)]
    if lock is not None:
        cmd.extend(["--lock", str(lock)])
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env=env)


def _commit_in(path: Path, filename: str, content: str, msg: str) -> str:
    """Create a file, add, commit, and return the full SHA."""
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path,
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path,
                   check=True, capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _head_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _make_blocker_script(tmp_path: Path) -> Path:
    """Create a long-running script whose filename matches ``pgrep -f``."""
    script = tmp_path / "run_ilk_loop_test_blocker.py"
    script.write_text("import time; time.sleep(3600)\n", encoding="utf-8")
    return script


# ── AC-1: clean merge lands every commit, worktree removed ──────────────────


class TestLandingHappyPath:
    """AC-1: on a clean finish, every commit lands with identical SHAs."""

    def test_landed_commits_match_and_worktree_removed(
        self, tmp_path: Path
    ) -> None:
        """Multiple worktree commits all arrive in the clone, SHAs unchanged,
        and the worktree is removed after the merge.

        The merge CLI must exit 0 and print a success message that names
        the merge outcome (step 1's contract).  The current CLI exits 0 and
        prints "Merge complete." but does NOT verify the post-merge state
        or provide the structured output the driver needs.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )

        base_sha = _head_sha(repo)

        sha1 = _commit_in(worktree_path, "step1.txt", "one", "step 1")
        sha2 = _commit_in(worktree_path, "step2.txt", "two", "step 2")
        sha3 = _commit_in(worktree_path, "step3.txt", "three", "step 3")

        result = _merge_cli(repo, worktree_path)

        # Step 1 contract: exit 0 and a structured success message.
        assert result.returncode == _EXIT_OK, (
            f"merge should exit {_EXIT_OK}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The success message must name the merged SHA so the driver can
        # record it.  Current CLI prints "Merge complete." — no SHA.
        assert "merged" in result.stdout.lower() or sha3[:8] in result.stdout, (
            f"stdout should name the merged commit, got: {result.stdout!r}"
        )

        # Every worktree SHA must be reachable from the clone's HEAD.
        clone_log = subprocess.run(
            ["git", "rev-list", base_sha + "..HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        )
        clone_shas = set(clone_log.stdout.strip().splitlines())
        for expected in (sha1, sha2, sha3):
            assert expected in clone_shas, (
                f"worktree commit {expected[:8]} not in clone history"
            )

        assert (repo / "step1.txt").read_text() == "one"
        assert (repo / "step2.txt").read_text() == "two"
        assert (repo / "step3.txt").read_text() == "three"

        # Worktree can be cleanly removed after merge.
        subprocess.run(
            [sys.executable, str(_SELFMOD), "remove",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        assert not worktree_path.exists()


# ── AC-2: live loop blocks the merge ────────────────────────────────────────


class TestLandingBlockedByLiveLoop:
    """AC-2: a live loop detected → exit 2, worktree survives, PID named."""

    def test_merge_blocked_by_live_loop(self, tmp_path: Path) -> None:
        """With a live process matching ``pgrep -f run_ilk_loop``,
        the merge CLI must exit ``_EXIT_BLOCKED`` and name the blocking PID.

        The worktree is left intact — it holds the only copy of the work.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )

        _commit_in(worktree_path, "batch.txt", "work", "batch commit")
        wt_sha = _head_sha(worktree_path)

        # Spawn a blocker whose filename matches pgrep -f.
        script = _make_blocker_script(tmp_path)
        blocker = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            result = _merge_cli(repo, worktree_path)

            # Step 1 contract: distinct exit code for blocked merges.
            assert result.returncode == _EXIT_BLOCKED, (
                f"merge should exit {_EXIT_BLOCKED} when live loop detected, "
                f"got {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # The error names the blocking PID.
            assert str(blocker.pid) in result.stderr, (
                f"stderr should name the blocking PID {blocker.pid}\n"
                f"stderr: {result.stderr}"
            )
        finally:
            blocker.terminate()
            try:
                blocker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                blocker.kill()
                blocker.wait(timeout=5)

        # Nothing merged — clone HEAD unchanged.
        assert _head_sha(repo) != wt_sha

        # The worktree survives.
        assert worktree_path.exists()
        assert (worktree_path / "batch.txt").exists()


# ── AC-3: clone HEAD moved since creation ────────────────────────────────────


class TestLandingRefusedOnBranchMoved:
    """AC-3: clone HEAD moved → exit 3, reason names both SHAs."""

    def test_merge_refused_on_branch_moved(self, tmp_path: Path) -> None:
        """If the clone's HEAD moved between worktree creation and merge,
        the CLI must exit ``_EXIT_BRANCH_MOVED`` and name both SHAs.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )

        expected_sha = _head_sha(repo)

        _commit_in(worktree_path, "wt.txt", "work", "worktree commit")

        # Simulate another process committing into the clone.
        _commit_in(repo, "other.txt", "other process", "other commit")
        current_sha = _head_sha(repo)

        result = _merge_cli(repo, worktree_path)

        # Step 1 contract: distinct exit code for branch-moved.
        assert result.returncode == _EXIT_BRANCH_MOVED, (
            f"merge should exit {_EXIT_BRANCH_MOVED} on branch moved, "
            f"got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Both SHAs are in the stderr.
        assert expected_sha[:8] in result.stderr, (
            f"stderr should contain expected SHA {expected_sha[:8]}\n"
            f"stderr: {result.stderr}"
        )
        assert current_sha[:8] in result.stderr, (
            f"stderr should contain current SHA {current_sha[:8]}\n"
            f"stderr: {result.stderr}"
        )

        # Nothing merged.
        assert not (repo / "wt.txt").exists()

        # Worktree survives.
        assert worktree_path.exists()


# ── AC-4: broken probe is treated as blocked ─────────────────────────────────


class TestLandingFailClosedOnBrokenProbe:
    """AC-4: a broken liveness probe → exit 4, not clear."""

    def test_broken_probe_is_blocked(self, tmp_path: Path) -> None:
        """If pgrep is not on PATH, the merge CLI must exit
        ``_EXIT_PROBE_BROKEN`` — not proceed as if no loops were live.

        This is the fail-closed invariant: a broken probe is indistinguishable
        from "there are live loops but we can't see them", so the safe default
        is to refuse.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )

        _commit_in(worktree_path, "batch.txt", "work", "batch commit")
        wt_sha = _head_sha(worktree_path)

        # Remove pgrep from PATH so _find_live_ilk_pids raises.
        path_without_grep = "/usr/bin:/bin"
        result = _merge_cli(
            repo, worktree_path,
            env_extra={"PATH": path_without_grep},
        )

        # Step 1 contract: distinct exit code for broken probe.
        assert result.returncode == _EXIT_PROBE_BROKEN, (
            f"merge should exit {_EXIT_PROBE_BROKEN} on broken probe, "
            f"got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Nothing merged.
        assert _head_sha(repo) != wt_sha

        # Worktree survives.
        assert worktree_path.exists()


# ── AC-5: the merge acquires the exclusive lock ──────────────────────────────


class TestLandingMergeLock:
    """AC-5: the merge acquires the exclusive lock; contention exits non-zero."""

    def test_merge_acquires_exclusive_lock(self, tmp_path: Path) -> None:
        """If the lock is held, the merge CLI must exit ``_EXIT_LOCK_HELD``.

        The first merge acquires the lock; a second attempt while the lock
        is held must fail rather than block or interleave.
        """
        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        lock_path = tmp_path / "merge.lock"

        subprocess.run(
            [sys.executable, str(_SELFMOD), "create",
             str(repo), str(worktree_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )

        _commit_in(worktree_path, "locked.txt", "data", "locked commit")

        # Hold the lock from a "second process".
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        held_fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            result = _merge_cli(repo, worktree_path, lock=lock_path)

            # Step 1 contract: distinct exit code for lock contention.
            assert result.returncode == _EXIT_LOCK_HELD, (
                f"merge should exit {_EXIT_LOCK_HELD} when lock is held, "
                f"got {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        finally:
            held_fd.close()

        # The lock was released by the holder (us).
        fd = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # If we get here, the lock is free.
        finally:
            fd.close()
