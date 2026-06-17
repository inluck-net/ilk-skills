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
    external_runtime_dir,
    ilk_data_root,
    project_key,
)

# Import scan_projects from ilk-watchdog (sibling skill).
_WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts"
if str(_WATCHDOG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_SCRIPTS))
from scheduler_scan import scan_projects  # noqa: E402
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


def resolve_project_status(project_dir: Path) -> dict:
    """Build a status dict for one project directory.

    Returns a dict matching the AC-2 schema.
    """
    key = project_dir.name
    plans_dir = project_dir / "plans"
    runtime_dir = external_runtime_dir(key)
    launcher_dir = external_launcher_dir(key)

    # Active master + next subplan
    active_master = ""
    next_subplan = ""
    step = ""
    if plans_dir.is_dir():
        masters = sorted(plans_dir.glob("MASTER-*.md"))
        for mp in masters:
            try:
                mtext = mp.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            mfm = parse_frontmatter(mtext)
            if (mfm.get("status") or "").strip() == "active":
                active_master = mp.name
                next_subplan, step = _resolve_next_subplan(plans_dir, mtext)
                break

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

    # Needs-human blocked classification (blacklist / stale-running / stalled).
    blocked = _blocked_info(project_dir, sentinel, active_master, next_subplan)

    return {
        "project_key": key,
        "path": str(project_dir),
        "active_master": active_master,
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": sentinel,
        "last_class": last_class,
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

    # Use scan_projects() for the project list (FIFO-sorted).
    try:
        scanned = scan_projects()
    except Exception:
        # Fallback: list project dirs directly.
        scanned = [{"key": d.name, "path": str(d)}
                   for d in sorted(data_root.iterdir()) if d.is_dir()]

    entries = []
    for proj in scanned:
        proj_path = Path(proj["path"])
        if not proj_path.is_dir():
            continue
        entries.append(resolve_project_status(proj_path))

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
