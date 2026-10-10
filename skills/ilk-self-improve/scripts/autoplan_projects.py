#!/usr/bin/env python3
"""autoplan_projects — which projects autoplan plans for, and where their rows live.

NOT kernel (track B, Chad 2026-10-10, option (b)). An improvement batch may
extend project discovery and backlog loading here, for example gh-resolve's
``autoplan.backlog`` export (mode, path, sources, rsi_host, max_age_hours in
its ``.ilk-launch.json``). The safety rails stay in the kernel files
``autoplan.py`` and ``autoplan_rails.py``:
- kernel screening, against each project's own kernel and fail closed;
- ``check_master``;
- lint and preflight;
- the forced draft;
- the attempt bounds;
- the auto-planned priority.

A project dict carries:

- ``key``: the ilk project key (the data dir name under ``projects/``).
- ``repo``: the project's repo path.
- ``toolkit``: True for the ilk-skills toolkit itself.
- ``backlog_mode``: how ``load_entries`` reads the rows. ``"dir"`` is an
  ``improvement_backlog`` directory (``backlog_path``) holding
  ``candidates.json``. A track B batch adds others, such as an export file.
- ``sources``: the admitted ``source`` values, or None for the toolkit's
  ``autoplan_rails.ELIGIBLE_SOURCES``.

The kernel reads a non-toolkit project's safety kernel from that project's
OWN ``.ilk-launch.json`` (``autoplan.kernel_file``), never from this module,
so an edit here cannot point the screening at a weaker kernel.

The toolkit itself stays in the kernel (``autoplan._find_toolkit_project``,
the ``ilk-skills-improvements`` backlog). This module only adds projects.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"
for _p in (_LOOP_SCRIPTS, _WATCHDOG_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from triage_backlog import read_backlog_strict  # noqa: E402


def discover_extra(data_root: Path) -> list[dict]:
    """Projects OTHER than the toolkit that autoplan may plan for, in order.

    The toolkit itself is found by the kernel (``autoplan._find_toolkit_project``)
    and always comes first. Each returned dict needs ``key``, ``repo``,
    ``backlog_mode``, ``sources``; ``toolkit`` is False. The kernel refuses a
    project whose own ``.ilk-launch.json`` has no readable
    ``autoplan.kernel_file`` (fail closed), so this function need not check
    it.

    Track B (gh-resolve) is not built yet: none are returned.
    """
    return []


def load_entries(project: dict) -> list[dict]:
    """Read the project's candidate rows. Raises on an unreadable backlog."""
    mode = project.get("backlog_mode")
    if mode == "dir":
        return read_backlog_strict(Path(project["backlog_path"]))
    raise ValueError(f"unsupported backlog mode: {mode!r}")
