"""Python helpers for release train dispatch decisions.

Called by scheduler.sh to decide whether to start the release train.
Separated from the shell script so tests can import and verify the logic.

Part of sub-plan: a-shipped-batch-starts-the-release-train.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


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


def sentinel_all_shipped(sentinel_file: Path) -> bool:
    """Check if a sentinel indicates all-shipped (success state).

    Returns True when the sentinel state is one of the success states
    the watchdog treats as success: all-shipped, already-shipped, shipped,
    or blocked-no-runnable with all sub-plans shipped.
    """
    if not sentinel_file.exists():
        return False
    try:
        data = json.loads(sentinel_file.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return False
    state = data.get("state", "")
    return state in ("all-shipped", "already-shipped", "shipped")


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