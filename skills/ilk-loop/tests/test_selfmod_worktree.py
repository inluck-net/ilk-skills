"""Red-first tests for selfmod_worktree — worktree isolation for self-modifying batches.

Step 0: A live consumer loop blocks the merge.
The selfmod worktree module must detect live ilk loops and refuse to merge
when one is running, preventing the cross-project self-modification hazard.

These tests define the interface that selfmod_worktree.py must implement.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Resolve paths relative to this test file.
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
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("test repo", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True,
                   capture_output=True)
    return repo


# ── Step 0: Red-first — a live consumer loop blocks the merge ────────────────

class TestLiveLoopBlocksMerge:
    """With a live loop present, the merge must not proceed."""

    def test_merge_blocked_when_live_loop_detected(
        self, tmp_path: Path, live_ilk_pid: int
    ) -> None:
        """A live consumer loop must block the merge.

        This is the decisive test for blocker 1: the selfmod worktree
        module must refuse to merge when an active ilk loop is running.
        Uses live_ilk_pid fixture to get a PID that passes command-verified
        liveness checks (not os.getpid(), which is the bug the check
        exists to catch).
        """
        from selfmod_worktree import SelfmodWorktree, MergeBlockedError

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        # Create a worktree for the self-modifying batch
        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make a change in the worktree so there's something to merge
        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        # Patch _find_live_ilk_pids to return our live PID
        with patch(
            "selfmod_worktree._find_live_ilk_pids",
            return_value=[live_ilk_pid],
        ):
            # Attempt merge — must be blocked
            with pytest.raises(MergeBlockedError) as exc_info:
                sw.merge_back()

            # Verify the error names the blocking PID(s)
            assert live_ilk_pid in exc_info.value.blocking_pids
            assert len(exc_info.value.blocking_pids) >= 1

    def test_merge_proceeds_when_no_live_loop(self, tmp_path: Path) -> None:
        """Merge must proceed when no live loops are detected.

        This is the positive control: when _find_live_ilk_pids returns
        empty, the merge should complete successfully.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make a change in the worktree
        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        # Patch _find_live_ilk_pids to return empty (no live loops)
        with patch(
            "selfmod_worktree._find_live_ilk_pids",
            return_value=[],
        ):
            # Merge should succeed
            sw.merge_back()

        # Verify the change landed in the main repo
        assert (repo / "new-file.txt").exists()
        assert (repo / "new-file.txt").read_text(encoding="utf-8") == "selfmod change"

    def test_merge_blocked_error_carries_metadata(self, tmp_path: Path) -> None:
        """MergeBlockedError must carry the blocking PIDs and a human-readable message."""
        from selfmod_worktree import MergeBlockedError

        pids = [12345, 67890]
        err = MergeBlockedError(blocking_pids=pids)

        assert err.blocking_pids == pids
        assert "12345" in str(err)
        assert "67890" in str(err)

    def test_liveness_check_fails_closed_on_probe_error(
        self, tmp_path: Path, live_ilk_pid: int
    ) -> None:
        """A broken probe must NOT collapse to zero live loops.

        If _find_live_ilk_pids raises (broken pgrep, permission error),
        the merge must be blocked — not proceed as if no loops were live.
        This is the fail-closed invariant from the design doc.
        """
        from selfmod_worktree import SelfmodWorktree, MergeBlockedError

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make a change so there's something to merge
        (worktree_path / "new-file.txt").write_text(
            "selfmod change", encoding="utf-8"
        )
        subprocess.run(["git", "add", "new-file.txt"], cwd=worktree_path,
                      check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "selfmod change"],
            cwd=worktree_path, check=True, capture_output=True
        )

        # Patch _find_live_ilk_pids to raise (simulating a broken probe)
        with patch(
            "selfmod_worktree._find_live_ilk_pids",
            side_effect=RuntimeError("pgrep: command not found"),
        ):
            # Merge must be blocked (fail closed)
            with pytest.raises(MergeBlockedError):
                sw.merge_back()


# ── Step 1: Worktree lifecycle ──────────────────────────────────────────────

class TestWorktreeLifecycle:
    """Create, reuse, and remove worktrees cleanly."""

    def test_create_worktree(self, tmp_path: Path) -> None:
        """Creating a worktree produces a valid git worktree directory."""
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        assert worktree_path.exists()
        assert (worktree_path / ".git").exists()  # worktree marker

    def test_create_idempotent(self, tmp_path: Path) -> None:
        """Creating a worktree twice is a no-op (idempotent)."""
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make a change so we can verify it survives the second create.
        (worktree_path / "marker.txt").write_text("survives", encoding="utf-8")

        sw.create()

        assert (worktree_path / "marker.txt").read_text(encoding="utf-8") == "survives"

    def test_remove_clean_worktree(self, tmp_path: Path) -> None:
        """Removing a clean worktree succeeds without force."""
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        sw.remove()

        assert not worktree_path.exists()

    def test_remove_dirty_worktree_without_force_raises(
        self, tmp_path: Path
    ) -> None:
        """Removing a dirty worktree without force raises WorktreeDirtyError."""
        from selfmod_worktree import SelfmodWorktree, WorktreeDirtyError

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make an uncommitted change.
        (worktree_path / "dirty.txt").write_text("uncommitted", encoding="utf-8")

        with pytest.raises(WorktreeDirtyError) as exc_info:
            sw.remove()

        assert worktree_path in [exc_info.value.worktree_path]
        assert "dirty.txt" in exc_info.value.dirty_files

    def test_remove_dirty_worktree_with_force(self, tmp_path: Path) -> None:
        """Force-removing a dirty worktree succeeds and discards changes."""
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Make an uncommitted change.
        (worktree_path / "dirty.txt").write_text("uncommitted", encoding="utf-8")

        sw.remove(force=True)

        assert not worktree_path.exists()

    def test_remove_nonexistent_worktree_is_noop(self, tmp_path: Path) -> None:
        """Removing a worktree that doesn't exist is a no-op."""
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        # Don't create — just remove.
        sw.remove()  # Should not raise.
