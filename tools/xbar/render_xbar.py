#!/usr/bin/env python3
"""Render status_all --json into xbar/SwiftBar menu-bar format.

Pure renderer: reads JSON from a file (``--json-from``) or stdin, writes
xbar text to stdout.  No network, no side-effects.

xbar format:
  - First line  → menu-bar title (what you see in the bar)
  - Second line → ``---`` separator
  - Subsequent lines → menu rows; may carry ``| href=`` / ``| bash=`` params
"""
from __future__ import annotations

import argparse
import json
import sys


def render_xbar(entries: list[dict]) -> str:
    """Convert a status_all JSON array into xbar text.

    Parameters
    ----------
    entries:
        Each dict matches the ``resolve_project_status`` schema from
        ``status_all.py``: ``project_key``, ``sentinel.alive``, ``step``, etc.

    Returns
    -------
    str
        Multi-line xbar text ready for stdout.
    """
    if not entries:
        return "ilk !\n---\nNo projects found"

    alive_count = sum(1 for e in entries if e.get("sentinel", {}).get("alive"))
    total = len(entries)

    # Title
    if alive_count > 0:
        title = f"ilk {alive_count}*"
    else:
        title = "ilk ok"

    lines = [title, "---"]

    for e in entries:
        key = e.get("project_key", "?")
        sent = e.get("sentinel", {})
        is_alive = sent.get("alive", False)
        state = sent.get("state", "none")
        step = e.get("step", "")
        next_sp = e.get("next_subplan", "")

        # Status icon
        if is_alive:
            icon = "*"
        else:
            icon = "-"

        # Row text: key + icon + step info
        row = f"{icon} {key}"
        if step:
            row += f"  {step}"
        if next_sp:
            row += f"  {next_sp}"

        # Add state suffix for non-obvious states
        if state not in ("running", "none"):
            row += f"  ({state})"

        lines.append(row)

    # Separator + actions
    lines.append("---")
    lines.append("Open log | bash=ilk-status")
    lines.append("Refresh | refresh=true")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render status_all JSON as xbar text")
    ap.add_argument("--json-from", type=str, default=None,
                    help="Path to JSON file (default: read stdin)")
    args = ap.parse_args()

    if args.json_from:
        with open(args.json_from, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    sys.stdout.write(render_xbar(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
