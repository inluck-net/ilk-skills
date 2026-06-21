#!/usr/bin/env python3
"""Auto-close tracker/backlog entries when sub-plans ship.

Given a plans directory, scans for shipped sub-plans whose ``tickets:``
reference an OPEN entry in the per-project tracker or the global backlog,
and flips those entries to ``shipped``.  Idempotent; best-effort (never
raises into the caller).

Designed to be called from ``collect.py`` on loop stop so closing happens
automatically without any runner/``.ps1`` change.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ── Imports from sibling modules ─────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _SCRIPTS_DIR.parent.parent / "ilk-loop" / "scripts"

for _d in (_SCRIPTS_DIR, _LOOP_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import improvement_backlog  # noqa: E402
import project_tracker  # noqa: E402


# ── Frontmatter parsing ──────────────────────────────────────────────────────

def _parse_shipped_tickets(plan_path: Path) -> list[str]:
    """Extract ticket IDs from a shipped sub-plan's frontmatter.

    Returns an empty list if the plan is not shipped or has no tickets.
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError:
        return []

    if not text.startswith("---\n"):
        return []

    end = text.find("\n---\n", 4)
    if end == -1:
        return []

    fm_block = text[4:end]
    lines = fm_block.split("\n")

    # Check status: shipped
    is_shipped = False
    for line in lines:
        key_val = line.strip()
        if key_val.startswith("status:"):
            val = key_val.split(":", 1)[1].strip()
            if val == "shipped":
                is_shipped = True
            break
    if not is_shipped:
        return []

    # Parse tickets list (YAML block sequence under tickets:)
    tickets: list[str] = []
    in_tickets = False
    for line in lines:
        stripped = line.strip()
        if stripped == "tickets:":
            in_tickets = True
            continue
        if in_tickets:
            if stripped.startswith("- "):
                tickets.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                # New key — end of tickets list
                break

    return tickets


# ── Core autoclose logic ─────────────────────────────────────────────────────


def autoclose(
    plans_dir: Path,
    *,
    project: str | Path | None = None,
    key: str | None = None,
) -> int:
    """Auto-close tracker/backlog entries resolved by shipped sub-plans.

    Scans *plans_dir* for sub-plans with ``status: shipped`` whose
    ``tickets:`` list references an OPEN entry in the per-project tracker
    or the global improvement backlog.  Matching entries are flipped to
    ``shipped``.

    Idempotent — already-shipped entries are untouched.  Best-effort —
    missing/garbled trackers, unknown ticket ids, or I/O errors are
    silently skipped (never raises).

    Returns the number of entries closed.
    """
    closed = 0

    try:
        # Collect ticket ids from all shipped sub-plans
        ticket_ids: set[str] = set()
        for plan_path in sorted(plans_dir.glob("*.md")):
            if plan_path.name.startswith("MASTER-"):
                continue
            tickets = _parse_shipped_tickets(plan_path)
            ticket_ids.update(tickets)

        if not ticket_ids:
            return 0

        # Close matching entries in per-project tracker
        closed += _close_in_tracker(ticket_ids, project=project, key=key)

        # Close matching entries in global backlog
        closed += _close_in_backlog(ticket_ids)

    except Exception:
        # Best-effort — never raise into the caller
        pass

    return closed


def _close_in_tracker(
    ticket_ids: set[str],
    *,
    project: str | Path | None = None,
    key: str | None = None,
) -> int:
    """Close matching OPEN entries in the per-project tracker."""
    closed = 0
    try:
        entries = project_tracker.load(project=project, key=key)
    except Exception:
        return 0

    for entry in entries:
        if entry.id not in ticket_ids:
            continue
        if entry.status != "open":
            continue
        try:
            project_tracker.set_status(
                entry.id, "shipped", project=project, key=key
            )
            closed += 1
        except Exception:
            # Best-effort — skip on error
            pass

    return closed


def _close_in_backlog(ticket_ids: set[str]) -> int:
    """Close matching OPEN entries in the global improvement backlog."""
    closed = 0
    try:
        entries = improvement_backlog.load()
    except Exception:
        return 0

    for entry in entries:
        if entry.id not in ticket_ids:
            continue
        if entry.status != "open":
            continue
        try:
            # Directly update the entry status via the raw save path
            all_entries = improvement_backlog.load()
            for e in all_entries:
                if e.id == entry.id:
                    e.status = "shipped"
                    break
            improvement_backlog._save_raw(
                improvement_backlog._backlog_dir(),
                [e.to_dict() for e in all_entries],
            )
            closed += 1
        except Exception:
            # Best-effort — skip on error
            pass

    return closed
