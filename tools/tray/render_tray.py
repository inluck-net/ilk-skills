#!/usr/bin/env python3
"""Render status_all --json into a Windows tray view-spec dict.

Pure renderer: reads JSON from a file (``--json-from``) or stdin, writes
the view-spec as JSON to stdout.  No network, no side-effects.

The view-spec is consumed by ``ilk-tray.ps1`` (the NotifyIcon host) which
paints the tray icon, tooltip, and context-menu rows from it.
"""
from __future__ import annotations

import argparse
import json
import sys

# NotifyIcon tooltip hard-limit (Windows).
_MAX_TOOLTIP = 127


def render_tray(entries: list[dict]) -> dict:
    """Convert a status_all JSON array into a tray view-spec dict.

    Parameters
    ----------
    entries:
        Each dict matches the ``resolve_project_status`` schema from
        ``status_all.py``: ``project_key``, ``sentinel.alive``, ``step``, etc.

    Returns
    -------
    dict
        Keys: ``icon_state``, ``tooltip``, ``rows``.
    """
    if not entries:
        return {
            "icon_state": "idle",
            "tooltip": "ilk: no projects",
            "rows": [],
        }

    alive_count = 0
    blocked_count = 0
    stale_count = 0
    error_count = 0
    idle_count = 0

    rows: list[dict] = []

    for e in entries:
        # Orphan filter — mirrors render_xbar.py.  status_all marks a project
        # orphaned when its resolved repo_path is gone from disk; the entry can
        # only report history and no action here can reach a repo.  Dropped
        # before icon assignment so a leftover state=running sentinel cannot
        # raise `attention` (which the idle filter below would not catch).
        if e.get("orphaned"):
            continue

        key = e.get("project_key", "?")
        sent = e.get("sentinel", {})
        is_alive = sent.get("alive", False)
        state = sent.get("state", "none")
        step = e.get("step", "")
        next_sp = e.get("next_subplan", "")

        # Determine per-project icon_state.
        # Blocked (needs-human) is the highest-priority category.
        if e.get("blocked"):
            icon = "attention"
            blocked_count += 1
        elif is_alive:
            icon = "running"
            alive_count += 1
        elif state == "running" and not is_alive:
            # Stale: sentinel says running but process is dead.
            icon = "attention"
            stale_count += 1
        elif state in ("error", "errored"):
            icon = "attention"
            error_count += 1
        else:
            icon = "idle"
            idle_count += 1

        # Row label: mirror render_xbar's text convention.
        label = key
        model = e.get("model") or ""
        if e.get("blocked"):
            classification = e.get("classification") or "unknown"
            label += f"  BLOCKED: {classification}  -> /ilk-resume"
            if e.get("blocked_reason") == "within-backoff" and e.get("blocked_expiry"):
                # Append local expiry time if available.
                try:
                    from datetime import datetime, timezone
                    expiry = datetime.fromisoformat(e["blocked_expiry"])
                    label += f"  (until {expiry.strftime('%H:%M')})"
                except Exception:
                    pass
        else:
            if step:
                label += f"  {step}"
            if next_sp:
                label += f"  {next_sp}"
            # Run-state suffix, driven by the computed icon (not the raw sentinel
            # state), so an idle/stale project is unambiguous: its step/next_subplan
            # is the NEXT pending work, which otherwise reads like a running task
            # (the tooltip-says-idle vs popup-looks-running mismatch). A 'running'
            # row needs no suffix — the icon conveys it.
            if icon == "running" and model:
                label += f"  running on {model}"
            elif icon == "idle":
                label += "  (idle)"
            elif icon == "attention":
                label += "  (error)" if state in ("error", "errored") else "  (stale)"

        # ── Idle filter: skip pure-idle entries (no row, no action) ──
        # Hide iff idle AND not manually_runnable AND not blocked.
        # Idle entries are still counted above for the tooltip summary.
        if icon == "idle" and not e.get("manually_runnable") and not e.get("blocked"):
            continue

        action: dict = {"kind": "status", "project_key": key}
        if e.get("blocked") and e.get("report_path"):
            action["report_path"] = e["report_path"]

        rows.append({
            "label": label,
            "icon_state": icon,
            "project_key": key,
            "action": action,
        })

        # ── Action rows: Start now / Resume ──────────────────────────
        # Start now (kind:"run"): manually_runnable & not running — dispatchable work exists.
        # Resume (kind:"resume"): parked/blacklisted — needs /ilk-resume.
        # The two carry DIFFERENT paths (see render_xbar for the rationale):
        #  - run → ilk-run.ps1 -Start, which resolves a project root from the
        #    SOURCE repo path (repo_path); data dir is only a fallback.
        #  - resume → blacklist_status.py --project, which expects the data dir.
        data_path = e.get("path", "")
        run_path = e.get("repo_path") or data_path
        if e.get("manually_runnable"):
            rows.append({
                "label": "Start now",
                "icon_state": icon,
                "project_key": key,
                "action": {"kind": "run", "project_key": key, "path": run_path},
            })
        if e.get("parked"):
            rows.append({
                "label": "Resume",
                "icon_state": icon,
                "project_key": key,
                "action": {"kind": "resume", "project_key": key, "path": data_path},
            })

    # Global icon_state: blocked > running > attention > idle.
    if blocked_count > 0:
        global_state = "attention"
    elif alive_count > 0:
        global_state = "running"
    elif stale_count + error_count > 0:
        global_state = "attention"
    else:
        global_state = "idle"

    # Build tooltip summary, truncate if needed.
    parts: list[str] = []
    if blocked_count:
        parts.append(f"{blocked_count} blocked")
    if alive_count:
        parts.append(f"{alive_count} running")
    if stale_count:
        parts.append(f"{stale_count} stale")
    if error_count:
        parts.append(f"{error_count} error")
    if idle_count:
        parts.append(f"{idle_count} idle")
    summary = ", ".join(parts) if parts else "no projects"
    tooltip = f"ilk: {summary}"
    if len(tooltip) > _MAX_TOOLTIP:
        tooltip = tooltip[:_MAX_TOOLTIP - 3] + "..."

    return {
        "icon_state": global_state,
        "tooltip": tooltip,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render status_all JSON as a tray view-spec")
    ap.add_argument("--json-from", type=str, default=None,
                    help="Path to JSON file (default: read stdin)")
    args = ap.parse_args()

    if args.json_from:
        with open(args.json_from, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    view = render_tray(data)
    json.dump(view, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
