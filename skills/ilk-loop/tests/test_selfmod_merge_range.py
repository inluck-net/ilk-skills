"""Red-first test: merge_back must preserve every commit, not just the tip.

AC-1: A worktree carrying N commits (N >= 3) merges back with all N present
      in the clone, verified by git rev-list count.
AC-2: Commit SHAs are unchanged by the merge (fast-forward, not rewrite).

This file is deliberately created as a failing test (red-first, step-0
convention from decomposition-principles §8).  The current _do_merge()
cherry-picks exactly one commit and will fail this test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Reuse the throwaway-repo helper already established in test_selfmod_worktree.
from test_selfmod_worktree import _create_throwaway_repo


def _collect_shas(repo_path: Path, count: int) -> list[str]:
    """Return the *count* most-recent commit SHAs (oldest-first)."""
    result = subprocess.run(
        ["git", "log", "--format=%H", f"-{count}", "--reverse"],
        cwd=repo_path,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


class TestMergePreservesEveryCommit:
    """merge_back must bring back ALL commits, not just the tip."""

    def test_multi_commit_merge_preserves_all(
        self,
        tmp_path: Path,
    ) -> None:
        """Three commits in the worktree must all land in the clone.

        The current _do_merge cherry-picks only the tip SHA, so this test
        should FAIL until step-1 replaces the cherry-pick with --ff-only.
        """
        from selfmod_worktree import SelfmodWorktree

        repo = _create_throwaway_repo(tmp_path)
        worktree_path = tmp_path / "selfmod-worktree"

        sw = SelfmodWorktree(repo, worktree_path)
        sw.create()

        # Record HEAD before any worktree commits.
        before_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            text=True,
            check=True,
        ).stdout.strip()

        # Make three distinct commits in the worktree.
        worktree_shas: list[str] = []
        for i in range(3, 0, -1):  # 3, 2, 1  — oldest-first when reversed
            file_name = f"commit-{i}.txt"
            (worktree_path / file_name).write_text(
                f"content-{i}", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", file_name],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            encoding="utf-8", errors="replace",
            )
            subprocess.run(
                ["git", "commit", "-m", f"commit {i}"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            encoding="utf-8", errors="replace",
            )

        # Collect the three SHAs from the worktree (oldest-first).
        worktree_shas = _collect_shas(worktree_path, 3)
        assert len(worktree_shas) == 3, f"expected 3 commits, got {len(worktree_shas)}"

        # Merge back (no live loops).
        with patch("selfmod_worktree._find_live_ilk_pids", return_value=[]):
            sw.merge_back()

        # AC-1: All three commits present in the clone.
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{before_sha}..HEAD"],
            cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            text=True,
            check=True,
        )
        merged_count = int(result.stdout.strip())
        assert merged_count == 3, (
            f"expected 3 commits merged, got {merged_count}"
        )

        # AC-2: SHAs unchanged (fast-forward preserves SHAs).
        clone_shas = _collect_shas(repo, 3)
        assert clone_shas == worktree_shas, (
            f"SHAs differ after merge:\n"
            f"  worktree: {worktree_shas}\n"
            f"  clone:    {clone_shas}"
        )
