"""Red-first tests for reused worktree refresh on create().

Step 0: A reused worktree with no unique work must follow main.
When a worktree is reused and its HEAD is a strict ancestor of the clone's
HEAD (no unique commits), it should be refreshed to the clone's HEAD.

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
                   capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("test repo", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True,
                   capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    return repo


def _head(path: Path) -> str:
    """Return the SHA of HEAD in the given repo."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _add_commit(repo_path: Path, filename: str, content: str, message: str) -> None:
    """Add a file and commit it."""
    (repo_path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo_path,
                  check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "commit", "-m", message], cwd=repo_path,
                  check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace")


# ── AC-1: The #53 shape — worktree with no unique work follows main ──────────

class TestReusedWorktreeFollowsMain:
    """A reused worktree with no unique work must be refreshed to main."""

    def test_worktree_follows_main_after_clone_moves_ahead(self, tmp_path: Path) -> None:
        """AC-1: throwaway repo; create worktree; add 2 commits to clone's main;
        run create() again. The worktree HEAD equals the clone HEAD, the marker
        holds the clone HEAD, and a following merge-back with 1 new worktree
        commit fast-forwards (exit 0).

        This is the decisive test for #53: the reused worktree must be
        refreshed when the clone has moved ahead and the worktree has no
        unique work.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Record the initial HEAD
        initial_head = _head(repo)
        assert _head(worktree_path) == initial_head

        # Add 2 commits to the clone's main (simulating other work)
        _add_commit(repo, "clone-file-1.txt", "clone work 1", "clone commit 1")
        _add_commit(repo, "clone-file-2.txt", "clone work 2", "clone commit 2")

        clone_head_after = _head(repo)
        assert clone_head_after != initial_head

        # Reuse the worktree (as a later run would)
        sw_reused = SelfmodWorktree(repo, worktree_path)
        sw_reused.create()

        # AC-1: worktree HEAD must now equal clone HEAD
        assert _head(worktree_path) == clone_head_after, (
            "worktree HEAD should have been refreshed to clone HEAD"
        )

        # AC-1: marker must hold the clone HEAD
        marker_path = worktree_path.parent / f"{worktree_path.name}.head-at-creation"
        assert marker_path.exists(), "marker file must exist"
        assert marker_path.read_text(encoding="utf-8").strip() == clone_head_after

        # AC-1: a following merge-back with 1 new worktree commit fast-forwards
        _add_commit(worktree_path, "worktree-change.txt", "new work", "worktree commit")

        with patch("selfmod_worktree._find_live_ilk_pids", return_value=[]):
            with patch("selfmod_worktree._find_live_ilk_pids_hostwide", return_value=[]):
                sw_reused.merge_back()

        # Verify the change landed in the main repo
        assert (repo / "worktree-change.txt").exists()
        assert (repo / "worktree-change.txt").read_text(encoding="utf-8") == "new work"


# ── AC-2: Worktree with unique commit, main unchanged ────────────────────────

class TestWorktreeWithUniqueCommitUnchanged:
    """A worktree with unique commits and main unchanged must not be refreshed."""

    def test_worktree_with_unique_commit_not_refreshed(self, tmp_path: Path) -> None:
        """AC-2: the worktree has 1 unique commit and main has not moved ⇒ it is
        unchanged after create().

        This is a control: when the worktree has unique work, it must not be
        refreshed even if main hasn't moved.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        initial_head = _head(repo)

        # Add a unique commit to the worktree (not in clone)
        _add_commit(worktree_path, "unique-file.txt", "unique work", "unique commit")
        worktree_head = _head(worktree_path)
        assert worktree_head != initial_head

        # Reuse the worktree
        sw_reused = SelfmodWorktree(repo, worktree_path)
        sw_reused.create()

        # AC-2: worktree HEAD must be unchanged (still has the unique commit)
        assert _head(worktree_path) == worktree_head, (
            "worktree with unique commits should not be refreshed"
        )
        assert (worktree_path / "unique-file.txt").exists(), (
            "unique file must still exist"
        )


# ── AC-3: Diverged worktree ──────────────────────────────────────────────────

class TestDivergedWorktreeNotRefreshed:
    """A diverged worktree must not be refreshed and must log a warning."""

    def test_diverged_worktree_not_refreshed(self, tmp_path: Path) -> None:
        """AC-3: diverged ⇒ unchanged, the warning is logged, and the unique
        commit is still reachable.

        When the worktree and clone have diverged (neither is ancestor of the
        other), the worktree must not be refreshed. The unique commit must
        remain reachable.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        initial_head = _head(repo)

        # Add a commit to the worktree (unique work)
        _add_commit(worktree_path, "wt-unique.txt", "worktree work", "wt commit")
        worktree_head = _head(worktree_path)

        # Add a different commit to the clone (creating divergence)
        _add_commit(repo, "clone-unique.txt", "clone work", "clone commit")
        clone_head = _head(repo)

        # Reuse the worktree
        sw_reused = SelfmodWorktree(repo, worktree_path)
        sw_reused.create()

        # AC-3: worktree HEAD must be unchanged (diverged, not refreshed)
        assert _head(worktree_path) == worktree_head, (
            "diverged worktree should not be refreshed"
        )

        # AC-3: the unique commit must still be reachable
        assert (worktree_path / "wt-unique.txt").exists(), (
            "unique file must still exist in worktree"
        )

        # AC-3: verify the worktree and clone have diverged
        # (neither is ancestor of the other)
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", worktree_head, clone_head],
            cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        # Exit code 1 means "not an ancestor" (diverged)
        assert result.returncode == 1, "worktree HEAD should not be ancestor of clone HEAD"


# ── AC-4: Dirty worktree and main moved ──────────────────────────────────────

class TestDirtyWorktreeNotRefreshed:
    """A dirty worktree with main moved must not be refreshed."""

    def test_dirty_worktree_not_refreshed(self, tmp_path: Path) -> None:
        """AC-4: dirty (an untracked file) and main moved ⇒ not refreshed, and
        the file is still present.

        When the worktree has uncommitted changes (dirty), it must not be
        refreshed even if main has moved ahead. The dirty file must remain.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        initial_head = _head(repo)

        # Make the worktree dirty (untracked file)
        (worktree_path / "dirty-file.txt").write_text("uncommitted", encoding="utf-8")

        # Add commits to the clone's main (main moved)
        _add_commit(repo, "clone-file.txt", "clone work", "clone commit")
        clone_head = _head(repo)
        assert clone_head != initial_head

        # Reuse the worktree
        sw_reused = SelfmodWorktree(repo, worktree_path)
        sw_reused.create()

        # AC-4: worktree HEAD must be unchanged (dirty, not refreshed)
        assert _head(worktree_path) == initial_head, (
            "dirty worktree should not be refreshed"
        )

        # AC-4: the dirty file must still be present
        assert (worktree_path / "dirty-file.txt").exists(), (
            "dirty file must still exist"
        )
        assert (worktree_path / "dirty-file.txt").read_text(encoding="utf-8") == "uncommitted"


# ── AC-5: Marker missing but worktree valid ──────────────────────────────────

class TestMarkerMissingButWorktreeValid:
    """A valid worktree with missing marker must re-save the marker."""

    def test_marker_missing_but_worktree_valid(self, tmp_path: Path) -> None:
        """AC-5: marker file missing but the worktree is valid ⇒ create()
        re-saves the marker.

        When the marker file is deleted but the worktree is still valid,
        create() must re-save the marker with the current clone HEAD.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        initial_head = _head(repo)

        # Delete the marker file
        marker_path = worktree_path.parent / f"{worktree_path.name}.head-at-creation"
        assert marker_path.exists(), "marker must exist after first create"
        marker_path.unlink()
        assert not marker_path.exists(), "marker must be deleted"

        # Reuse the worktree
        sw_reused = SelfmodWorktree(repo, worktree_path)
        sw_reused.create()

        # AC-5: marker must be re-saved with the current clone HEAD
        assert marker_path.exists(), "marker must be re-created"
        saved_head = marker_path.read_text(encoding="utf-8").strip()
        assert saved_head == initial_head, (
            f"marker should hold clone HEAD {initial_head[:8]}, got {saved_head[:8]}"
        )

        # AC-5: worktree HEAD must be unchanged (marker missing doesn't trigger refresh)
        assert _head(worktree_path) == initial_head, (
            "worktree with missing marker should not be refreshed"
        )