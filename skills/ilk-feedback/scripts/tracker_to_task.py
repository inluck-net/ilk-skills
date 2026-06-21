#!/usr/bin/env python3
"""Convert open per-project tracker entries into a markdown task for /ilk-plan.

Usage:
    python tracker_to_task.py --project <path> [--dry-run]

Loads open entries via ``project_tracker.list_open`` and formats them with
``build_task.format_task_description`` so output style is consistent with
the global improvement-backlog feed.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Sibling imports ────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve()
_SCRIPTS_DIR = _HERE.parent  # ilk-feedback/scripts
_SELF_IMPROVE_SCRIPTS = _HERE.parent.parent.parent / "ilk-self-improve" / "scripts"

for _d in (_SCRIPTS_DIR, _SELF_IMPROVE_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import project_tracker  # noqa: E402
import build_task  # noqa: E402


def build_for_project(project: str | Path) -> str:
    """Return markdown task text from open tracker entries for *project*.

    Returns an empty string when the tracker has no open entries.
    """
    entries = project_tracker.list_open(project=Path(project))
    return build_task.format_task_description(entries)
