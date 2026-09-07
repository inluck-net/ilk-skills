#!/usr/bin/env python3
"""Worktree isolation for self-modifying batches.

A self-modifying improvement batch runs in a git worktree so that consumer
loops keep executing the stable clone throughout.  The merge back into the
clone is the only moment needing exclusivity.

Usage:
    from selfmod_worktree import SelfmodWorktree

    sw = SelfmodWorktree(repo_path, worktree_path)
    sw.create()           # idempotent — reuses an existing worktree
    # ... make changes in worktree_path ...
    sw.merge_back()       # blocks if live loops detected
    sw.remove()           # clean up the worktree
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────


class MergeBlockedError(RuntimeError):
    """Raised when a merge cannot proceed because live loops were detected.

    Attributes:
        blocking_pids: PIDs of the live ilk runner processes.
    """

    def __init__(self, blocking_pids: list[int]) -> None:
        self.blocking_pids = blocking_pids
        pid_list = ", ".join(str(p) for p in blocking_pids)
        super().__init__(
            f"Merge blocked: {len(blocking_pids)} live ilk loop(s) detected "
            f"(pid={pid_list}). Stop the loops before merging."
        )


class WorktreeDirtyError(RuntimeError):
    """Raised when attempting to remove a worktree that has uncommitted work.

    Attributes:
        worktree_path: Path to the dirty worktree.
        dirty_files: List of dirty file paths (relative), if detectable.
    """

    def __init__(
        self, worktree_path: Path, dirty_files: list[str] | None = None
    ) -> None:
        self.worktree_path = worktree_path
        self.dirty_files = dirty_files or []
        detail = ""
        if self.dirty_files:
            detail = f" ({', '.join(self.dirty_files[:5])})"
        super().__init__(
            f"Worktree has uncommitted work{detail}: {worktree_path}. "
            f"Commit or stash changes first, or use force=True to discard."
        )


class BranchMovedError(RuntimeError):
    """Raised when the target branch moved since the worktree was created.

    Attributes:
        branch: The branch name.
        expected_sha: SHA at worktree creation time.
        current_sha: SHA at merge time.
    """

    def __init__(
        self, branch: str, expected_sha: str, current_sha: str
    ) -> None:
        self.branch = branch
        self.expected_sha = expected_sha
        self.current_sha = current_sha
        super().__init__(
            f"Branch '{branch}' moved since worktree was created "
            f"({expected_sha[:8]}→{current_sha[:8]}). "
            f"This is a condition to report, not to overwrite."
        )


# ── Liveness detection ──────────────────────────────────────────────────────


def _find_live_ilk_pids() -> list[int]:
    """Detect live ilk runner processes.

    Uses ``pgrep -f`` to find processes whose command line matches the
    runner pattern.  ``pgrep`` exit 1 means "none found"; exit >1 is an
    error and must raise — a broken probe and a true zero must not be
    byte-identical (fail-closed invariant).

    Note: macOS ``pgrep`` has no ``-c`` flag.

    Returns:
        List of PIDs of live ilk runners.

    Raises:
        RuntimeError: If the probe itself fails (broken pgrep, permission
            error, etc.).  The caller must treat this as "unknown liveness"
            and refuse to merge.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run_ilk_loop"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pgrep not found — cannot detect live loops"
        ) from exc

    if result.returncode == 0:
        # PIDs found — one per line.
        pids = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    pids.append(int(line))
                except ValueError:
                    continue
        return pids

    if result.returncode == 1:
        # No matches — this is the "zero live loops" case.
        return []

    # Exit code >1 is an error.  Fail closed.
    raise RuntimeError(
        f"pgrep exited with code {result.returncode}: "
        f"{result.stderr.strip() or '(no stderr)'}"
    )


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_checked(*args: str, cwd: Path | None = None) -> str:
    """Run a git command, raise on failure, return stdout."""
    result = _git(*args, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _resolve_head_sha(repo_path: Path) -> str:
    """Return the SHA of HEAD in the given repo."""
    return _git_checked("rev-parse", "HEAD", cwd=repo_path)


# ── SelfmodWorktree ─────────────────────────────────────────────────────────


class SelfmodWorktree:
    """Manages a git worktree for a self-modifying batch.

    Args:
        repo_path: Path to the main repository (the clone).
        worktree_path: Path where the worktree should be created/reused.
        branch: Branch name for the worktree (default: ``selfmod-batch``).
    """

    def __init__(
        self,
        repo_path: Path,
        worktree_path: Path,
        branch: str = "selfmod-batch",
    ) -> None:
        self.repo_path = repo_path.resolve()
        self.worktree_path = worktree_path.resolve()
        self.branch = branch
        self._head_at_creation: str | None = None

    def create(self) -> None:
        """Create a detached worktree, or reuse an existing one idempotently.

        If the worktree path already exists and is a valid worktree, this
        is a no-op.  If it exists but is not a valid worktree, it is removed
        first.
        """
        if self._is_valid_worktree():
            logger.info("Reusing existing worktree at %s", self.worktree_path)
            self._head_at_creation = _resolve_head_sha(self.repo_path)
            return

        # Remove stale directory if it exists but isn't a worktree.
        if self.worktree_path.exists():
            logger.warning(
                "Removing stale non-worktree directory at %s",
                self.worktree_path,
            )
            import shutil

            shutil.rmtree(self.worktree_path)

        # Record HEAD before creating the worktree.
        self._head_at_creation = _resolve_head_sha(self.repo_path)

        # Create a detached worktree.  --detach and -b are mutually exclusive.
        _git_checked(
            "worktree",
            "add",
            "--detach",
            str(self.worktree_path),
            cwd=self.repo_path,
        )
        logger.info(
            "Created worktree at %s (branch=%s, head=%s)",
            self.worktree_path,
            self.branch,
            self._head_at_creation[:8],
        )

    def merge_back(
        self,
        lock_path: Path | None = None,
        check_branch: bool = True,
    ) -> None:
        """Merge worktree changes back into the main repo.

        Checks for live loops before merging.  If live loops are detected,
        raises ``MergeBlockedError``.  If the target branch moved since
        creation, raises ``BranchMovedError``.

        Args:
            lock_path: Optional path to an exclusive lock file.  If set,
                acquires the lock before merging.
            check_branch: Whether to check if the branch moved (default True).
        """
        # Step 1: Liveness check — fail closed.
        try:
            live_pids = _find_live_ilk_pids()
        except RuntimeError as exc:
            # Broken probe — fail closed.
            raise MergeBlockedError(blocking_pids=[-1]) from exc

        if live_pids:
            raise MergeBlockedError(blocking_pids=live_pids)

        # Step 2: Branch movement check.
        if check_branch and self._head_at_creation is not None:
            current_sha = _resolve_head_sha(self.repo_path)
            if current_sha != self._head_at_creation:
                raise BranchMovedError(
                    branch=self.branch,
                    expected_sha=self._head_at_creation,
                    current_sha=current_sha,
                )

        # Step 3: Acquire lock if requested.
        lock_fd = None
        if lock_path is not None:
            import fcntl

            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if lock_fd:
                    lock_fd.close()
                raise RuntimeError(
                    f"Could not acquire lock at {lock_path}: {exc}"
                ) from exc

            # Write holder metadata.
            metadata = json.dumps({
                "pid": __import__("os").getpid(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": "selfmod-merge",
            })
            lock_fd.write(metadata)
            lock_fd.flush()

        try:
            # Step 4: Merge.
            self._do_merge()
        finally:
            if lock_fd is not None:
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()

    def _do_merge(self) -> None:
        """Perform the actual merge of worktree changes into the main repo.

        Cherry-picks the worktree's HEAD onto the main branch.  This is the
        cleanest approach: the worktree shares the same .git directory, so
        its commits are accessible to the main repo.
        """
        # Get the worktree's HEAD — the commit(s) to bring over.
        worktree_sha = _resolve_head_sha(self.worktree_path)
        main_sha = _resolve_head_sha(self.repo_path)

        if worktree_sha == main_sha:
            logger.info("No changes to merge from worktree")
            return

        # Cherry-pick the worktree's HEAD onto the main branch.
        result = _git(
            "cherry-pick", worktree_sha,
            cwd=self.repo_path,
        )
        if result.returncode != 0:
            # Abort the failed cherry-pick before raising.
            _git("cherry-pick", "--abort", cwd=self.repo_path)
            raise RuntimeError(
                f"Cherry-pick of {worktree_sha[:8]} failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        logger.info(
            "Cherry-picked %s from worktree into main repo",
            worktree_sha[:8],
        )

    def remove(self, *, force: bool = False) -> None:
        """Remove the worktree.

        Args:
            force: If True, remove even with uncommitted work.  The discarded
                changes are logged.  If False and the worktree is dirty,
                raises ``WorktreeDirtyError``.
        """
        if not self._is_valid_worktree():
            logger.info("Worktree already removed or invalid: %s", self.worktree_path)
            return

        if not force:
            dirty = self._dirty_files()
            if dirty:
                raise WorktreeDirtyError(self.worktree_path, dirty)

        if force:
            dirty = self._dirty_files()
            if dirty:
                logger.warning(
                    "Force-removing worktree with %d dirty file(s): %s",
                    len(dirty),
                    ", ".join(dirty[:5]),
                )

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(self.worktree_path))
        _git_checked(*args, cwd=self.repo_path)
        logger.info("Removed worktree at %s", self.worktree_path)

    def _is_valid_worktree(self) -> bool:
        """Check if the worktree path is a valid git worktree."""
        if not self.worktree_path.exists():
            return False
        result = _git(
            "rev-parse",
            "--git-dir",
            cwd=self.worktree_path,
        )
        return result.returncode == 0

    def _dirty_files(self) -> list[str]:
        """List uncommitted files in the worktree."""
        result = _git(
            "status",
            "--porcelain",
            cwd=self.worktree_path,
        )
        if result.returncode != 0:
            return []
        files = []
        for line in result.stdout.strip().splitlines():
            # Porcelain format: XY filename
            if len(line) >= 3:
                files.append(line[3:].strip())
        return files


# ── CLI entry point ──────────────────────────────────────────────────────────


def main() -> None:
    """CLI interface for selfmod_worktree."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Manage a worktree for self-modifying batches."
    )
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create", help="Create or reuse a worktree.")
    create_p.add_argument("repo", type=Path, help="Path to the main repo.")
    create_p.add_argument("worktree", type=Path, help="Path for the worktree.")
    create_p.add_argument(
        "--branch", default="selfmod-batch", help="Branch name."
    )

    merge_p = sub.add_parser("merge", help="Merge worktree back into repo.")
    merge_p.add_argument("repo", type=Path, help="Path to the main repo.")
    merge_p.add_argument("worktree", type=Path, help="Path to the worktree.")
    merge_p.add_argument("--lock", type=Path, help="Lock file path.")

    remove_p = sub.add_parser("remove", help="Remove a worktree.")
    remove_p.add_argument("repo", type=Path, help="Path to the main repo.")
    remove_p.add_argument("worktree", type=Path, help="Path to the worktree.")
    remove_p.add_argument(
        "--force", action="store_true", help="Remove even if dirty."
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "create":
        sw = SelfmodWorktree(args.repo, args.worktree, args.branch)
        sw.create()
        print(f"Worktree ready at {args.worktree}")

    elif args.command == "merge":
        sw = SelfmodWorktree(args.repo, args.worktree)
        sw.merge_back(lock_path=args.lock)
        print("Merge complete.")

    elif args.command == "remove":
        sw = SelfmodWorktree(args.repo, args.worktree)
        sw.remove(force=args.force)
        print("Worktree removed.")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
