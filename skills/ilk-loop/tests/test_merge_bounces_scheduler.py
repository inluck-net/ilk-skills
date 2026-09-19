"""Red-first tests: a merge must bounce a live scheduler daemon.

AC-1: daemon alive → bounce called → merge proceeds.
AC-2: loop alive → still refuses (unchanged path — existing tests cover this,
      included here as a behavioral freeze).
AC-3: bounce failure → merge does not proceed (fail closed).

These tests define the interface that selfmod_worktree.py must implement.
Under the current code they are RED: the daemon is invisible to the merge
probe and no bounce path exists.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_throwaway_repo(tmp_path: Path) -> Path:
    """Create a minimal throwaway git repo for testing worktree operations."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True,
                   capture_output=True)
    (repo / "README.md").write_text("test repo", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True,
                   capture_output=True)
    return repo


def _make_bounce_script(path: Path, exit_code: int = 0) -> Path:
    """Write a stub bounce script that logs invocations and exits with *exit_code*."""
    path.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "bounced $$" >> "{path}.log"
            exit {exit_code}
        """),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


# ── AC-1: daemon alive → bounce → proceed ────────────────────────────────────

class TestMergeBouncesScheduler:
    """Merge must bounce a live scheduler daemon and then proceed."""

    def test_daemon_alive_bounces_and_proceeds(
        self, tmp_path: Path, live_daemon_pid: int
    ) -> None:
        """A live scheduler daemon triggers a bounce; merge proceeds on success.

        Verifies: AC-1 (bounce called, merge proceeds).
        RED under current code — no daemon probe exists.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        bounce_script = _make_bounce_script(tmp_path / "bounce.sh")

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        with (
            patch(
                "selfmod_worktree._find_live_daemon_pids",
                return_value=[live_daemon_pid],
            ),
            patch(
                "selfmod_worktree._find_live_ilk_pids",
                return_value=[],
            ),
        ):
            sw.merge_back(bounce_daemons_path=bounce_script)

        # Change landed in main repo.
        assert (repo / "new-file.txt").exists()
        assert (repo / "new-file.txt").read_text(encoding="utf-8") == "selfmod change"

        # Bounce script was invoked.
        log = bounce_script.parent / "bounce.sh.log"
        assert log.exists(), "bounce script was not called"
        assert "bounced" in log.read_text(encoding="utf-8")


# ── AC-3: bounce failure → fail closed ───────────────────────────────────────

class TestMergeBlockedOnBounceFailure:
    """Merge must not proceed when the daemon bounce fails."""

    def test_daemon_alive_bounce_fails_blocks_merge(
        self, tmp_path: Path, live_daemon_pid: int
    ) -> None:
        """A failed bounce must not silently pass — merge is blocked.

        Verifies: AC-3 (bounce failure → fail closed).
        """
        from selfmod_worktree import SelfmodWorktree, MergeBlockedError

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        bounce_script = _make_bounce_script(tmp_path / "bounce.sh", exit_code=2)

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        with (
            patch(
                "selfmod_worktree._find_live_daemon_pids",
                return_value=[live_daemon_pid],
            ),
            patch(
                "selfmod_worktree._find_live_ilk_pids",
                return_value=[],
            ),
        ):
            with pytest.raises(MergeBlockedError) as exc_info:
                sw.merge_back(bounce_daemons_path=bounce_script)

        msg = str(exc_info.value)
        assert "bounce" in msg.lower() or "daemon" in msg.lower()
        assert live_daemon_pid in exc_info.value.blocking_pids

        # Bounce script was still invoked (attempt before refusing).
        log = bounce_script.parent / "bounce.sh.log"
        assert log.exists(), "bounce script was not called"


# ── AC-2: live loop still refuses (behavioral freeze) ────────────────────────

class TestMergeStillRefusesOnLiveLoop:
    """A live loop must still be refused — the loop path is unchanged."""

    def test_loop_alive_still_refuses(
        self, tmp_path: Path, live_daemon_pid: int, live_ilk_pid: int
    ) -> None:
        """Even with a daemon present, a live loop takes precedence and refuses.

        Verifies: AC-2 (loop refusal path unchanged).
        """
        from selfmod_worktree import SelfmodWorktree, MergeBlockedError

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"
        bounce_script = _make_bounce_script(tmp_path / "bounce.sh")

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        with (
            patch(
                "selfmod_worktree._find_live_daemon_pids",
                return_value=[live_daemon_pid],
            ),
            patch(
                "selfmod_worktree._find_live_ilk_pids",
                return_value=[live_ilk_pid],
            ),
        ):
            with pytest.raises(MergeBlockedError) as exc_info:
                sw.merge_back(bounce_daemons_path=bounce_script)

        # The error must reference the LOOP PID (the blocking cause).
        assert live_ilk_pid in exc_info.value.blocking_pids