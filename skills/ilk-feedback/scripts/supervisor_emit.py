#!/usr/bin/env python3
"""
Emit a supervisor finding into the improvement backlog.

Thin CLI wrapper around ``improvement_backlog.add_candidate`` that
produces a ``source="supervisor"`` entry with the supervisor's
structured finding (title / gap / proposed_fix / severity / relations
like ``project`` and ``run_id``).

Usage:
    python supervisor_emit.py --title "X" --gap "Y" --severity high --project P

See ``references/supervisor-emit.md`` for the contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Resolve sibling import
_HERE = Path(__file__).resolve()
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import improvement_backlog  # noqa: E402


def emit(
    *,
    title: str,
    gap: str,
    proposed_fix: str = "",
    severity: str = "medium",
    leverage: str = "medium",
    kind: str = "bug",
    project: str = "",
    run_id: str = "",
    source_id: str = "",
    backlog_dir: Path | str | None = None,
) -> improvement_backlog.Entry:
    """Add a ``source=supervisor`` entry to the backlog.

    Returns the created or updated ``Entry``.
    """
    relations: dict[str, str] = {}
    if project:
        relations["project"] = project
    if run_id:
        relations["run_id"] = run_id

    entry = improvement_backlog.add_candidate(
        title=title,
        kind=kind,
        gap=gap,
        proposed_fix=proposed_fix,
        leverage=leverage,
        severity=severity,
        source="supervisor",
        source_id=source_id,
        relations=relations or None,
        backlog_dir=backlog_dir,
    )
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit a supervisor finding into the improvement backlog.",
    )
    parser.add_argument("--title", required=True, help="Short title for the finding")
    parser.add_argument("--gap", required=True, help="Description of the gap")
    parser.add_argument("--proposed-fix", default="", help="Suggested fix")
    parser.add_argument("--severity", default="medium", choices=["high", "medium", "low"],
                        help="Severity (default: medium)")
    parser.add_argument("--leverage", default="medium", choices=["high", "medium", "low"],
                        help="Leverage (default: medium)")
    parser.add_argument("--kind", default="bug",
                        choices=list(improvement_backlog.KINDS),
                        help="Entry kind (default: bug)")
    parser.add_argument("--project", default="", help="Project name for relations")
    parser.add_argument("--run-id", default="", help="Run ID for relations")
    parser.add_argument("--source-id", default="",
                        help="Per-source stable key for PULL-upsert dedup")
    args = parser.parse_args()

    entry = emit(
        title=args.title,
        gap=args.gap,
        proposed_fix=args.proposed_fix,
        severity=args.severity,
        leverage=args.leverage,
        kind=args.kind,
        project=args.project,
        run_id=args.run_id,
        source_id=args.source_id,
    )
    print(json.dumps(entry.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
