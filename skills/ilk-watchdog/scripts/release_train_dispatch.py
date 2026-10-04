"""Python helpers for release train dispatch decisions.

Called by scheduler.sh to decide whether to start the release train.
Separated from the shell script so tests can import and verify the logic.

Part of sub-plan: a-shipped-batch-starts-the-release-train.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# plan_status readers — same import pattern as scheduler_scan.py:77-83
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))

from plan_status import (  # noqa: E402
    is_master_all_shipped,
    normalize_master_status,
    parse_frontmatter,
)


def is_release_train_enabled() -> bool:
    """Check if the release train is enabled via environment variable.

    Returns False when ILK_RELEASE_TRAIN=0; True otherwise.
    """
    return os.environ.get("ILK_RELEASE_TRAIN", "1") != "0"


def is_release_lock_held(data_dir: Path) -> bool:
    """Check if a release train lock exists and names a live pid.

    A stale lock (dead pid) is treated as not-held and can be replaced.
    """
    lock_file = data_dir / "runtime" / "release" / "train.lock"
    if not lock_file.exists():
        return False
    try:
        pid = int(lock_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    return _is_pid_alive(pid)


def sentinel_all_shipped(sentinel_file: Path, plans_dir: Path | None = None) -> bool:
    """Check if a sentinel indicates all sub-plans shipped.

    Returns True when:
    - the sentinel state is ``all-shipped``, ``already-shipped``, or ``shipped``
      (legacy success states — no plans_dir needed); or
    - the sentinel state is ``blocked-no-runnable`` AND ALL of:
      * the sentinel has no ``held_by`` (a human park is not a finished batch);
      * ``int(sentinel["iterations"]) >= 1`` (a pre-loop exit shipped nothing
        this run; a missing or unparseable ``iterations`` → False);
      * ``plans_dir`` holds at least one ``MASTER-*.md``;
      * every master whose ``normalize_master_status(fm["status"])`` is
        ``active`` satisfies ``is_master_all_shipped(master, plans_dir)``.

    Any OSError or parse failure on the way → False (the train does not
    start on an unreadable plans dir).

    ``plans_dir`` defaults to the sentinel's grand-grand-parent / ``plans``
    (citing ``ilk_paths.py:419`` and ``:455``).
    """
    if not sentinel_file.exists():
        return False
    try:
        data = json.loads(sentinel_file.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return False
    state = data.get("state", "")

    # Legacy success states — no plans_dir needed
    if state in ("all-shipped", "already-shipped", "shipped"):
        return True

    # blocked-no-runnable: check the batch, not just the state word
    if state != "blocked-no-runnable":
        return False

    # A human park is not a finished batch
    if data.get("held_by"):
        return False

    # A pre-loop exit shipped nothing this run
    try:
        iterations = int(data.get("iterations", 0))
    except (ValueError, TypeError):
        return False
    if iterations < 1:
        return False

    # Resolve plans_dir from sentinel path if not provided
    if plans_dir is None:
        plans_dir = sentinel_file.parent.parent.parent / "plans"  # ilk_paths.py:419, :455

    try:
        masters = sorted(plans_dir.glob("MASTER-*.md"))
    except OSError:
        return False

    if not masters:
        return False

    # Every active master must be all-shipped
    for master_path in masters:
        try:
            fm = parse_frontmatter(master_path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if normalize_master_status(fm.get("status", "")) != "active":
            continue
        if not is_master_all_shipped(master_path, plans_dir):
            return False

    return True


def sentinel_is_failure(sentinel_file: Path) -> bool:
    """Check if a sentinel indicates a failure state that should not start the train."""
    if not sentinel_file.exists():
        return False
    try:
        data = json.loads(sentinel_file.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return False
    state = data.get("state", "")
    return state in (
        "local_checks_failed",
        "local_checks_failed_no_commits",
        "error",
        "interrupted",
        "no-progress",
        "budget-exhausted",
        "quota-exhausted",
        "startup-hang",
        "timeout",
        "max-iterations",
    )


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given pid is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False