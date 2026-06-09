#!/usr/bin/env python3
"""
Read open candidates from the improvement backlog and format a task
description suitable for handing to /ilk-plan.

Usage:
    python build_task.py [--dry-run]

With --dry-run, prints the task description without invoking /ilk-plan.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _import_backlog():
    """Import improvement_backlog from the sibling ilk-feedback skill."""
    # Resolve relative to this script's location:
    #   skills/ilk-self-improve/scripts/build_task.py
    #   skills/ilk-feedback/scripts/improvement_backlog.py
    here = Path(__file__).resolve()
    feedback_scripts = here.parent.parent.parent / "ilk-feedback" / "scripts"
    if str(feedback_scripts) not in sys.path:
        sys.path.insert(0, str(feedback_scripts))
    import improvement_backlog
    return improvement_backlog


def load_open_candidates(backlog_dir=None):
    """Return open candidates from the backlog."""
    backlog = _import_backlog()
    return backlog.list_entries(status="open", backlog_dir=backlog_dir)


def format_task_description(candidates) -> str:
    """Format candidates into a markdown task description for /ilk-plan."""
    if not candidates:
        return ""

    lines = [
        "# Task: ilk-skills toolkit improvements",
        "",
        "Source: improvement backlog (emitted by `/ilk-feedback` postmortems).",
        "",
    ]

    for i, c in enumerate(candidates, 1):
        lines.append(f"## {i}. {c.title}")
        lines.append("")
        lines.append(f"- **Kind**: {c.kind}")
        lines.append(f"- **Gap**: {c.gap}")
        if c.evidence:
            ev_parts = []
            for k, v in c.evidence.items():
                if v:
                    ev_parts.append(f"{k}={v}")
            if ev_parts:
                lines.append(f"- **Evidence**: {', '.join(ev_parts)}")
        if c.proposed_fix:
            lines.append(f"- **Proposed fix**: {c.proposed_fix}")
        lines.append(f"- **Leverage**: {c.leverage} | **Severity**: {c.severity}")
        lines.append(f"- **Seen**: {c.seen_count}x (first: {c.first_seen})")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    candidates = load_open_candidates()

    if not candidates:
        print("Nothing to improve — backlog is empty (no open candidates).")
        return 0

    task_desc = format_task_description(candidates)

    if dry_run:
        print(task_desc)
        return 0

    # Non-dry-run: print the task description (the command body handles
    # passing it to /ilk-plan).
    print(task_desc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
