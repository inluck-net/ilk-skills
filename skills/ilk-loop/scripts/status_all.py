"""All-projects status aggregator for ilk-loop.

Scans every project under ``$ILK_DATA_HOME/projects/`` (or ``~/.ilk-data``)
and emits a structured status for each: active master, next sub-plan,
sentinel liveness, and latest postmortem classification.

``--json``  — machine-readable JSON array (consumed by dashboard, xbar, etc.)
``--text``  — human-readable table (default)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── sibling imports ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import (  # noqa: E402
    external_launcher_dir,
    external_logs_dir,
    external_runtime_dir,
    ilk_data_root,
    project_key,
)

# Import blacklist_status from ilk-watchdog (sibling skill).
_WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts"
if str(_WATCHDOG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_SCRIPTS))
from blacklist_status import is_blacklisted  # noqa: E402

# Reuse loop_status helpers for frontmatter parsing and ordering.
from loop_status import extract_master_order, find_plans_dir, parse_frontmatter  # noqa: E402


# ── sentinel state vocabulary ──────────────────────────────────────────
# The runner's Finalize-Sentinel treats "running" as the ONLY live state;
# on any abnormal exit it rewrites state to a terminal value (e.g.
# local_checks_failed, shipped, interrupted).  A sentinel whose state is
# not "running" means the run is over, regardless of PID — the PID may
# have been recycled by the OS.  See master-status-vocab-and-stale-sentinel.
LIVE_SENTINEL_STATES = {"running"}


# ── pid liveness (cross-platform) ───────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Check whether *pid* is alive, cross-platform.

    POSIX: ``os.kill(pid, 0)`` — catch ``ProcessLookupError`` → dead,
    ``PermissionError`` → alive (owned by another user).
    Windows: ``tasklist /FI "PID eq <pid>"`` and check the pid appears.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            return str(pid) in out.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but we don't own it.
            return True
        except OSError:
            return False


# ── per-project resolution ──────────────────────────────────────────

def _read_sentinel(runtime_dir: Path) -> dict | None:
    """Read last-exit.json sentinel, return parsed dict or None."""
    f = runtime_dir / "last-exit.json"
    if not f.is_file():
        return None
    try:
        # utf-8-sig: the PowerShell runner writes last-exit.json with a UTF-8
        # BOM; plain utf-8 makes json.loads choke on the BOM -> None -> the tray
        # renders every running loop as "(idle)". See inline-python-open-needs-utf8.
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def _latest_postmortem_class(launcher_dir: Path) -> str | None:
    """Return the classification from the newest postmortem, or None."""
    pm_dir = launcher_dir / "postmortems"
    if not pm_dir.is_dir():
        return None
    pms = sorted(pm_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pms:
        return None
    try:
        text = pms[0].read_text(encoding="utf-8-sig")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    return fm.get("classification") or fm.get("class") or None


def _latest_jsonl_model(logs_dir: Path) -> str:
    """Return the ``model`` from the most recent JSONL summary record, or ``""``.

    The runner appends one JSON object per iteration to
    ``<logs_dir>/.ilk-loop.log``.  Each record carries a ``model`` field
    populated by ``resolve_worker_model.py``.  We read only the last
    non-empty line to keep this O(seek) rather than O(n).
    """
    jsonl_path = logs_dir / ".ilk-loop.log"
    if not jsonl_path.is_file():
        return ""
    try:
        # Seek from end: read last ~4 KiB to find the final JSONL line.
        size = jsonl_path.stat().st_size
        if size == 0:
            return ""
        read_start = max(0, size - 4096)
        with jsonl_path.open("rb") as fh:
            fh.seek(read_start)
            tail = fh.read().decode("utf-8", errors="replace")
        # Last non-empty line is the most recent record.
        lines = [l for l in tail.splitlines() if l.strip()]
        if not lines:
            return ""
        rec = json.loads(lines[-1])
        return rec.get("model") or ""
    except (json.JSONDecodeError, OSError):
        return ""


def _blocked_info(
    project_data_dir: Path,
    sentinel: dict,
    active_master: str,
    next_subplan: str,
) -> dict:
    """Derive needs-human blocked state for one project.

    Checks (in priority order):
      1. Blacklist-class postmortem within backoff → blocked (within-backoff).
      2. Sentinel state=running + dead PID → blocked (stale-running).
      3. Active master exists but no runnable sub-plan → blocked (stalled).
      4. Otherwise → not blocked.

    Errors in the blacklist check are swallowed (default to not blocked)
    so one broken project never takes down the whole status output.
    """
    blocked = False
    classification = None
    blocked_reason = None
    blocked_expiry = None
    report_path = None

    try:
        # 1. Blacklist check (source of truth: blacklist_status.py).
        bl = is_blacklisted(str(project_data_dir))
        if bl.get("blacklisted"):
            blocked = True
            classification = bl.get("classification")
            blocked_reason = bl.get("reason")  # "within-backoff"
            blocked_expiry = bl.get("expiry")
        else:
            classification = bl.get("classification")

        # 2. Stale-running: sentinel says running but PID is dead.
        if (
            not blocked
            and sentinel.get("state") in LIVE_SENTINEL_STATES
            and not sentinel.get("alive")
        ):
            blocked = True
            blocked_reason = "stale-running"

        # 3. Stalled: active master exists but no runnable sub-plan.
        if (
            not blocked
            and active_master
            and not next_subplan
        ):
            blocked = True
            blocked_reason = "stalled"

        # Latest postmortem path (for tray click-to-open).
        pm_dir = project_data_dir / "runtime" / "launcher" / "postmortems"
        if pm_dir.is_dir():
            pms = sorted(pm_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if pms:
                report_path = str(pms[0])
    except Exception:
        # Guard: never break the whole status output over a blacklist error.
        blocked = False
        classification = None
        blocked_reason = None
        blocked_expiry = None
        report_path = None

    return {
        "blocked": blocked,
        "classification": classification,
        "blocked_reason": blocked_reason,
        "blocked_expiry": blocked_expiry,
        "report_path": report_path,
    }


def _resolve_next_subplan(plans_dir: Path, master_text: str) -> tuple[str, str]:
    """Return (next_subplan_slug, step_string) from a master's sub-plans."""
    ordered = extract_master_order(master_text)
    for fname in ordered:
        path = plans_dir / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        status = fm.get("status", "pending")
        if status == "shipped":
            continue
        slug = fm.get("plan", fname.replace(".md", ""))
        cur = fm.get("current_step", "?")
        est = fm.get("estimated_steps", "?")
        return slug, f"{cur}/{est}"
    return "", ""


def _resolve_repo_path(project_dir: Path, key: str) -> str | None:
    """Resolve the SOURCE repo path for a project data dir.

    Mirrors ``scheduler_scan.resolve_repo_path`` so the xbar/tray "Start now"
    action dispatches the *same* repo the scheduler would (``path`` is the
    data dir under ~/.ilk-data, which ``ilk-run.sh`` cannot resolve a project
    root from). Resolution order:

    1. ``<data>/runtime/launcher/last-launch.json`` → ``project_path``
       (written by every launch — the reliable primary source).
    2. ``<skill-root>/ilk-launcher/projects.json`` registry — match an entry
       whose path hashes to the same key (registered but never-launched).

    Returns ``None`` if neither resolves (manual "Start now" then can't run —
    the project must be launched once or added to projects.json). Kept inline
    (not imported from scheduler_scan) so a missing host skill root never
    crashes status aggregation at import time.
    """
    last_launch = project_dir / "runtime" / "launcher" / "last-launch.json"
    if last_launch.is_file():
        try:
            data = json.loads(last_launch.read_text(encoding="utf-8-sig"))
            p = data.get("project_path")
            if p:
                return str(p)
        except (OSError, ValueError):
            pass

    registry = (
        Path(__file__).resolve().parent.parent.parent
        / "ilk-launcher"
        / "projects.json"
    )
    if registry.is_file():
        try:
            data = json.loads(registry.read_text(encoding="utf-8-sig"))
            for entry in data.get("projects", []):
                ep = entry.get("path")
                if not ep:
                    continue
                try:
                    if project_key(Path(ep)) == key:
                        return str(ep)
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError):
            pass

    return None


def resolve_project_status(project_dir: Path) -> dict:
    """Build a status dict for one project directory.

    Returns a dict matching the AC-2 schema.
    """
    key = project_dir.name
    plans_dir = project_dir / "plans"
    runtime_dir = external_runtime_dir(key)
    launcher_dir = external_launcher_dir(key)

    # Active master + next subplan (also track queued for manually_runnable).
    active_master = ""
    next_subplan = ""
    step = ""
    queued_has_work = False
    if plans_dir.is_dir():
        masters = sorted(plans_dir.glob("MASTER-*.md"))
        for mp in masters:
            try:
                mtext = mp.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            mfm = parse_frontmatter(mtext)
            m_status = (mfm.get("status") or "").strip()
            if m_status == "active":
                active_master = mp.name
                next_subplan, step = _resolve_next_subplan(plans_dir, mtext)
                break
            # Track queued masters with non-shipped sub-plans for manually_runnable.
            if m_status == "queued":
                q_slug, _ = _resolve_next_subplan(plans_dir, mtext)
                if q_slug:
                    queued_has_work = True

    # Sentinel
    sentinel_raw = _read_sentinel(runtime_dir)
    if sentinel_raw:
        spid = sentinel_raw.get("pid", 0)
        state = sentinel_raw.get("state", "unknown")
        # Only consider PID liveness when the sentinel state is live ("running").
        # A terminal state (local_checks_failed, shipped, interrupted, …) means
        # the run is over regardless of PID — the PID may have been recycled.
        # This is the read-side complement to the runner's Finalize-Sentinel,
        # which treats "running" as the only live state.
        is_live = state in LIVE_SENTINEL_STATES
        alive = is_live and pid_alive(int(spid) if isinstance(spid, (int, float)) else 0)
        sentinel = {
            "pid": spid,
            "state": state,
            "alive": alive,
        }
    else:
        sentinel = {"pid": 0, "state": "none", "alive": False}

    # Latest postmortem classification
    last_class = _latest_postmortem_class(launcher_dir)

    # Model from latest JSONL summary record (populated by runner).
    logs_dir = external_logs_dir(key)
    model = _latest_jsonl_model(logs_dir)

    # Needs-human blocked classification (blacklist / stale-running / stalled).
    blocked = _blocked_info(project_dir, sentinel, active_master, next_subplan)

    # Action flags for tray/xbar (SP1: tray-actions-render).
    # runnable: has a dispatchable master with pending/in-progress work AND not currently running AND not blocked.
    # parked: blacklisted with no valid resolve-ack (project needs /ilk-resume).
    # manually_runnable: has a queued/active master with work AND not alive AND not blocked.
    #   Includes supervised_only masters (which scan_projects() filters out).
    runnable = bool(active_master and next_subplan and not sentinel.get("alive") and not blocked.get("blocked"))
    parked = blocked.get("blocked") and blocked.get("blocked_reason") == "within-backoff"
    manually_runnable = bool(
        (active_master and next_subplan or queued_has_work)
        and not sentinel.get("alive")
        and not blocked.get("blocked")
    )

    return {
        "project_key": key,
        "path": str(project_dir),
        "repo_path": _resolve_repo_path(project_dir, key),
        "active_master": active_master,
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": sentinel,
        "last_class": last_class,
        "model": model,
        "runnable": runnable,
        "parked": parked,
        "manually_runnable": manually_runnable,
        **blocked,
    }


# ── main ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="ilk all-projects status")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON array")
    ap.add_argument("--text", action="store_true",
                    help="Emit human-readable table (default)")
    args = ap.parse_args()

    data_root = ilk_data_root() / "projects"
    if not data_root.is_dir():
        if args.json:
            print("[]")
        else:
            print("No projects found.")
        return 0

    # Enumerate ALL project dirs (not scan_projects(), which skips
    # supervised_only masters).  This ensures every project — including
    # supervised/idle ones — appears in the tray status feed.
    entries = []
    for d in sorted(data_root.iterdir()):
        if d.is_dir():
            entries.append(resolve_project_status(d))

    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        # Text table
        if not entries:
            print("No projects found.")
            return 0
        kw = max(len(e["project_key"]) for e in entries)
        kw = max(kw, len("project"))
        print(f"{'project'.ljust(kw)}  {'master'.ljust(30)}  {'next'.ljust(20)}  step   sentinel")
        print(f"{'-' * kw}  {'-' * 30}  {'-' * 20}  ------ --------")
        for e in entries:
            s = e["sentinel"]
            alive_str = "alive" if s["alive"] else "dead"
            print(
                f"{e['project_key'].ljust(kw)}  "
                f"{(e['active_master'] or '-').ljust(30)}  "
                f"{(e['next_subplan'] or '-').ljust(20)}  "
                f"{(e['step'] or '-').ljust(6)} "
                f"{s['state']}:{alive_str}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
