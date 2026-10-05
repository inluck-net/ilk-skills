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


def check_permits_for_dispatch(data_dir: Path, project: Path) -> dict:
    """Check that every configured host has a valid permit before dispatch.

    This is the scheduler-side pre-flight check: the release train's
    ``run()`` also checks permits, but a detached train that discovers
    missing permits only at step 3 wastes a prove+cut cycle.  Checking
    here lets the scheduler skip the dispatch entirely and log a reason.

    Permits live at ``<data_dir>/runtime/permits/<host>.json``.  Each must
    contain: project, base_tag, candidate_head, host, issued_at, expires_at,
    consumed, revoked.

    Returns ``{"ok": True}`` when all permits are valid, or
    ``{"ok": False, "reason": "..."}`` on refusal.
    """
    import hashlib
    from datetime import datetime, timezone

    # Resolve hosts from config
    hosts: list[str] = []
    config_path = data_dir / "runtime" / "ship-config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            hosts = cfg.get("ship", {}).get("hosts", [])
        except (json.JSONDecodeError, KeyError):
            hosts = []
    else:
        launch_config = project / ".ilk-launch.json"
        if launch_config.exists():
            try:
                cfg = json.loads(launch_config.read_text())
                hosts = cfg.get("ship", {}).get("hosts", [])
            except (json.JSONDecodeError, KeyError):
                hosts = []

    if not hosts:
        return {"ok": True}  # no hosts configured → no permits needed

    # Resolve expected bindings from the latest tag and HEAD
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
    from ilk_paths import project_key  # noqa: E402
    project_name = project_key(project)

    # Read base_tag and candidate_head from the project's git state.
    # Best-effort: if git is unavailable, skip binding checks (the train's
    # own run() will catch binding mismatches at prove time).
    base_tag = ""
    candidate_head = ""
    try:
        import subprocess
        head_r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if head_r.returncode == 0:
            candidate_head = head_r.stdout.strip()

        tag_r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if tag_r.returncode == 0:
            base_tag = tag_r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass  # skip binding checks

    permit_dir = data_dir / "runtime" / "permits"
    now = datetime.now(timezone.utc)

    for host in hosts:
        permit_path = permit_dir / f"{host}.json"
        if not permit_path.exists():
            return {"ok": False, "reason": f"missing permit for host {host}"}

        try:
            permit = json.loads(permit_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"ok": False, "reason": f"corrupt permit for host {host}"}

        # Check required fields
        required = {"project", "base_tag", "candidate_head", "host", "expires_at", "consumed", "revoked"}
        missing = required - set(permit.keys())
        if missing:
            return {"ok": False, "reason": f"partial permit for host {host}: missing {missing}"}

        if permit.get("revoked", False):
            return {"ok": False, "reason": f"revoked permit for host {host}"}
        if permit.get("consumed", False):
            return {"ok": False, "reason": f"consumed permit for host {host}"}

        try:
            expires_at = datetime.fromisoformat(permit["expires_at"])
            if now > expires_at:
                return {"ok": False, "reason": f"stale permit for host {host}"}
        except (ValueError, TypeError):
            return {"ok": False, "reason": f"invalid expiry in permit for host {host}"}

        # Check bindings
        if permit.get("project") != project_name:
            return {"ok": False, "reason": f"crossed permit for host {host}: project mismatch"}
        if base_tag and permit.get("base_tag") != base_tag:
            return {"ok": False, "reason": f"crossed permit for host {host}: base_tag mismatch"}
        if candidate_head and permit.get("candidate_head") != candidate_head:
            return {"ok": False, "reason": f"crossed permit for host {host}: candidate_head mismatch"}

    return {"ok": True}


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given pid is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False