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


#: Command-line substring identifying a live ilk runner.  Overridable ONLY
#: so a test can pin it: the merge CLI runs in a subprocess, so the
#: ``patch("selfmod_worktree._find_live_ilk_pids")`` the in-process tests
#: use (test_selfmod_worktree.py:80, test_selfmod_merge_range.py:96) cannot
#: reach it.  Without a pin, those tests assert on the HOST process table:
#: any unrelated process matching this string — another project's loop, or
#: a sibling test's runner subprocess — makes a merge that should succeed
#: report "blocked" instead.  Measured 2026-09-17: 3 of 5 landing tests
#: passed alone and in skills/ilk-loop/tests/ (1798 passed), and failed in
#: the full suite.  Production never passes this; the default is the
#: contract.
#:
#: That 2026-09-17 note pinned the symptom in the tests and left production
#: matching the whole world.  On 2026-09-20 the same defect landed in
#: production: kira-cloudflare running pv5-verify all day made EVERY
#: ilk-skills run end ``selfmod_merge_failed`` ("MERGE BLOCKED: live loop(s)
#: detected", merge exited 2), stranding 11 commits in the selfmod worktree.
#: The pattern is no longer the whole test — see ``_find_live_ilk_pids``,
#: which now also requires the candidate to be driving the repo being
#: merged.
DEFAULT_PROBE_PATTERN = "run_ilk_loop"

#: The runner flag whose VALUE names the project a runner is driving.  The
#: path must appear in this role, never as a bare substring: every runner of
#: every project carries the *script* path in its argv, which is what made
#: the bare-substring probe match the world.
PROJECT_PATH_FLAG = "--project-path"


def _pgrep(pattern: str) -> list[int]:
    """Return PIDs whose command line matches *pattern*, or raise.

    ``pgrep`` exit 1 means "none found"; exit >1 is an error and must raise —
    a broken probe and a true zero must not be byte-identical (fail-closed
    invariant).

    Note: macOS ``pgrep`` has no ``-c`` flag.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pgrep not found — cannot detect live loops"
        ) from exc

    if result.returncode == 0:
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


def _candidate_roots(repo_path: Path | str) -> list[str]:
    """The spellings of *repo_path* a runner's argv might carry.

    macOS hands out ``/var/folders/...`` which resolves to
    ``/private/var/folders/...``; a runner launched with one spelling must
    still be recognised when the merge is asked about the other.  Mirrors the
    ``norm``/``resolved`` pair in ``_ilk_pid.sh:ilk_project_runners``.
    """
    raw = str(repo_path).rstrip("/") or "/"
    roots = [raw]
    try:
        resolved = str(Path(repo_path).resolve()).rstrip("/") or "/"
    except OSError:  # pragma: no cover - unreadable path
        resolved = raw
    if resolved not in roots:
        roots.append(resolved)
    return roots


def _cmdline_drives_repo(cmdline: str, roots: list[str]) -> bool:
    """True when *cmdline* passes one of *roots* as the --project-path value.

    Literal ``str.find`` with an explicit end boundary, never a regex: a regex
    would treat the path as a pattern, so a project path containing ``.``
    (``tmp.EVYaXMrl92``, any dotted directory) would match a DIFFERENT
    project's runner.  Verified 2026-08-12 in ``_ilk_pid.sh``: with ``~``,
    querying ``/…/ilk.test`` matched a live runner whose real path was
    ``/…/ilkAtest``.

    The end boundary is what keeps ``--project-path /a/repo`` from matching a
    runner driving ``/a/repo-2``.  A trailing slash on the value is accepted
    as the same path.
    """
    for root in roots:
        for prefix in (PROJECT_PATH_FLAG + " ", PROJECT_PATH_FLAG + "="):
            needle = prefix + root
            start = 0
            while True:
                idx = cmdline.find(needle, start)
                if idx < 0:
                    break
                end = idx + len(needle)
                if end < len(cmdline) and cmdline[end] == "/":
                    end += 1
                if end == len(cmdline) or cmdline[end].isspace():
                    return True
                start = idx + len(needle)
    return False


def _pid_cmdline(pid: int) -> str:
    """The full command line of *pid*, or "" when it cannot be read.

    An unreadable command line yields "", which fails the role check and so
    drops the candidate.  That is the safe direction here only because the
    candidate already matched the probe pattern by a different route; the
    conservative side of this function is ``_self_and_ancestor_pids``, which
    over-blocks when it cannot see.
    """
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _self_and_ancestor_pids() -> set[int]:
    """This process and every ancestor of it.

    The merge runs *inside* the very loop it is merging for:
    ``run_ilk_loop_claude.sh:3301`` calls the merge CLI, and that runner's own
    argv carries ``--project-path <this repo>``.  Without this exclusion a
    repo-scoped probe would report the merging run itself and self-block every
    selfmod merge — strictly worse than the world-match it replaces.

    When the walk cannot complete (``ps`` fails, a PID vanishes) it returns
    what it has.  That under-excludes, which over-blocks, which is the
    fail-closed direction.
    """
    pids: set[int] = set()
    import os  # noqa: PLC0415

    pid = os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid="],
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            break
        try:
            pid = int(result.stdout.strip())
        except ValueError:
            break
    return pids


def _find_any_live_ilk_pids(pattern: str = DEFAULT_PROBE_PATTERN) -> list[int]:
    """Every live ilk runner on this box, regardless of which repo it drives.

    This is the "quiet box" probe — the question
    ``migrate_project_keys.live_loop_pids`` asks, where ANY live loop is
    disqualifying because it resolves its paths through ``project_key``
    mid-run.  It is deliberately world-scoped; the merge-back wants
    ``_find_live_ilk_pids`` instead.

    Raises:
        RuntimeError: If the probe itself fails (broken pgrep, permission
            error, etc.).  The caller must treat this as "unknown liveness"
            and refuse.
    """
    return _pgrep(pattern)


def _find_live_ilk_pids(
    repo_path: Path | str,
    pattern: str = DEFAULT_PROBE_PATTERN,
) -> list[int]:
    """Live ilk runners driving *repo_path*, excluding this process tree.

    Three filters, in order:

    1. ``pgrep -f pattern`` — the runner shape.  Production never passes
       *pattern*; tests pin it so the assertion is about their own fakes.
    2. ``--project-path <repo_path>`` in the candidate's argv — the role
       check.  This is the fix for 2026-09-20: another project's runner
       carries ``run_ilk_loop_claude.sh`` too, and used to block this repo's
       merge.
    3. Not this process or one of its ancestors — the merge runs inside the
       loop it merges for.

    A second loop on THIS repo that is not in our own ancestry still blocks;
    the narrowing is about other repos, not a blanket exemption.

    Args:
        repo_path: The repo being merged.  Required — a probe that does not
            know what it is protecting is the defect this replaced.
        pattern: Command-line substring identifying a runner.

    Returns:
        List of PIDs of live ilk runners driving *repo_path*.

    Raises:
        RuntimeError: If the probe itself fails (broken pgrep, permission
            error, etc.).  The caller must treat this as "unknown liveness"
            and refuse to merge.
    """
    candidates = _pgrep(pattern)
    if not candidates:
        return []

    roots = _candidate_roots(repo_path)
    excluded = _self_and_ancestor_pids()

    return [
        pid for pid in candidates
        if pid not in excluded and _cmdline_drives_repo(_pid_cmdline(pid), roots)
    ]


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
                encoding="utf-8", errors="replace",
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
            self._head_at_creation = self._read_saved_head()
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
        self._save_head(self._head_at_creation)
        logger.info(
            "Created worktree at %s (branch=%s, head=%s)",
            self.worktree_path,
            self.branch,
            self._head_at_creation[:8],
        )

    def _head_marker_path(self) -> Path:
        """Path to the file that persists ``_head_at_creation`` across CLI invocations.

        Stored alongside the worktree (not inside it) to avoid dirtying
        the worktree's git status.
        """
        return self.worktree_path.parent / f"{self.worktree_path.name}.head-at-creation"

    def _save_head(self, sha: str) -> None:
        """Persist the creation-time HEAD SHA alongside the worktree."""
        marker = self._head_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(sha + "\n", encoding="utf-8")

    def _read_saved_head(self) -> str | None:
        """Read the persisted creation-time HEAD SHA, or None if missing."""
        marker = self._head_marker_path()
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip()
        return None

    def merge_back(
        self,
        lock_path: Path | None = None,
        check_branch: bool = True,
        probe_pattern: str = DEFAULT_PROBE_PATTERN,
    ) -> None:
        """Merge worktree changes back into the main repo.

        Checks for live loops before merging.  If live loops are detected,
        raises ``MergeBlockedError``.  If the target branch moved since
        creation, raises ``BranchMovedError``.

        Args:
            lock_path: Optional path to an exclusive lock file.  If set,
                acquires the lock before merging.
            check_branch: Whether to check if the branch moved (default True).
            probe_pattern: Command-line substring the liveness probe matches.
                Defaults to the production contract; pinned only by tests.
                The probe is scoped to ``self.repo_path`` regardless: a
                runner driving another project never blocks this merge.
        """
        # Step 1: Liveness check — fail closed.
        try:
            live_pids = _find_live_ilk_pids(self.repo_path, probe_pattern)
        except RuntimeError as exc:
            # Broken probe — fail closed.
            raise MergeBlockedError(blocking_pids=[-1]) from exc

        if live_pids:
            raise MergeBlockedError(blocking_pids=live_pids)

        # Step 2: Branch movement check.
        head_at_creation = self._head_at_creation
        if head_at_creation is None:
            head_at_creation = self._read_saved_head()
        if check_branch and head_at_creation is not None:
            current_sha = _resolve_head_sha(self.repo_path)
            if current_sha != head_at_creation:
                raise BranchMovedError(
                    branch=self.branch,
                    expected_sha=head_at_creation,
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

        Fast-forwards the main branch to the worktree's HEAD, preserving
        every commit and its SHA.  The worktree is created with --detach
        from the clone's HEAD, so its history is always a strict descendant
        — a fast-forward is guaranteed when BranchMovedError has not fired.
        """
        worktree_sha = _resolve_head_sha(self.worktree_path)
        main_sha = _resolve_head_sha(self.repo_path)

        if worktree_sha == main_sha:
            logger.info("No changes to merge from worktree")
            return

        result = _git(
            "merge", "--ff-only", worktree_sha,
            cwd=self.repo_path,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Fast-forward merge of {worktree_sha[:8]} failed "
                f"(main={main_sha[:8]}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        logger.info(
            "Fast-forwarded main repo to %s from worktree",
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
    merge_p.add_argument(
        "--probe-pattern", default=DEFAULT_PROBE_PATTERN,
        help="Command-line substring the liveness probe matches "
             "(default: %(default)s). For tests that must not see the "
             "host's unrelated ilk processes.",
    )

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
        sw.create()  # idempotent — captures _head_at_creation for branch check
        try:
            sw.merge_back(lock_path=args.lock,
                          probe_pattern=args.probe_pattern)
        except MergeBlockedError as exc:
            # Distinguish broken probe (sentinel PID -1) from live loops.
            if exc.blocking_pids == [-1]:
                print(
                    "ERROR: Liveness probe broken — cannot detect live loops. "
                    "Refusing to merge (fail-closed).",
                    file=sys.stderr,
                )
                sys.exit(4)
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
                sys.exit(2)
        except BranchMovedError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(3)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(5)
        worktree_sha = _resolve_head_sha(args.worktree)
        print(f"Merged {worktree_sha[:8]} into {args.repo}")

    elif args.command == "remove":
        sw = SelfmodWorktree(args.repo, args.worktree)
        sw.remove(force=args.force)
        print("Worktree removed.")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
