#!/usr/bin/env python3
"""Park a master whose work tree is gone, so the queue advances.

Called by ``scheduler.sh`` before the no-progress bound.  When a run's
sentinel ends with ``state: work_tree_invalid`` — a config error no restart
can fix — this script pauses the project's single ``active`` master and
writes a marker so the same ``run_id`` is never handled twice.

Usage:
  park_dead_master.py <project_data_dir>

Exit 0 always.  Prints one JSON line:
  ``{"parked": "<master-filename>", "run_id": "..."}``  when something was parked
  ``{"parked": null, "reason": "..."}``                  when nothing happened
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# ── resolve imports from sibling toolkit dir ──────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOOP_SCRIPTS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"
sys.path.insert(0, str(_LOOP_SCRIPTS))

from ilk_audit import write_audit  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402
from promote_next_master import write_status  # noqa: E402

_TARGET_STATE = "work_tree_invalid"
_PARKED_STATUS = "paused"


def _result(parked: str | None, *, run_id: str = "", reason: str = "") -> None:
    """Print the single JSON result line and exit."""
    print(json.dumps({"parked": parked, "run_id": run_id, "reason": reason}))


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        _result(None, reason="usage: park_dead_master.py <project_data_dir>")
        return 0

    project_dir = Path(argv[0])

    # ── read sentinel ─────────────────────────────────────────────────────
    sentinel_path = project_dir / "runtime" / "launcher" / "last-exit.json"
    try:
        sentinel = json.loads(sentinel_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _result(None, reason=f"unreadable sentinel: {exc}")
        return 0

    state = (sentinel.get("state") or "").strip()
    run_id = (sentinel.get("run_id") or "").strip()

    if state != _TARGET_STATE:
        _result(None, run_id=run_id, reason=f"state is {state!r}, not {_TARGET_STATE!r}")
        return 0

    if not run_id:
        _result(None, reason="sentinel has no run_id")
        return 0

    # ── idempotency marker ────────────────────────────────────────────────
    marker = project_dir / "runtime" / "launcher" / f"parked-{run_id}.json"
    if marker.exists():
        _result(None, run_id=run_id, reason=f"already handled (marker {marker.name})")
        return 0

    # ── find the single active master ─────────────────────────────────────
    plans_dir = project_dir / "plans"
    if not plans_dir.is_dir():
        _result(None, run_id=run_id, reason="no plans dir")
        return 0

    active_masters: list[tuple[Path, dict]] = []
    for p in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if (fm.get("status") or "").strip().lower() == "active":
            active_masters.append((p, fm))

    if not active_masters:
        _result(None, run_id=run_id, reason="no active master found")
        return 0

    if len(active_masters) > 1:
        _result(None, run_id=run_id,
                reason=f"{len(active_masters)} active masters; expected 1")
        return 0

    master_path, _fm = active_masters[0]

    # ── park: set status to paused ────────────────────────────────────────
    write_status(master_path, _PARKED_STATUS)

    # ── append progress-log row ───────────────────────────────────────────
    today = date.today().isoformat()
    log_row = (
        f"| {today} | Parked by the scheduler: run {run_id} ended "
        f"work_tree_invalid (no restart can fix it). |"
    )
    text = master_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_progress_section = False
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Progress log":
            in_progress_section = True
            out.append(line)
            continue
        if in_progress_section and stripped.startswith("## "):
            # End of progress-log section — insert before the next heading.
            out.append(log_row + "\n")
            inserted = True
            in_progress_section = False
            out.append(line)
            continue
        out.append(line)
    # If the section was the last thing in the file (no trailing heading).
    if in_progress_section and not inserted:
        # Ensure a trailing newline before the row.
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(log_row + "\n")
        inserted = True

    # Create the section if the master had none.
    if not inserted:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append("\n## Progress log\n\n")
        out.append("| Date | Event |\n")
        out.append("|---|---|\n")
        out.append(log_row + "\n")
        inserted = True

    if inserted:
        tmp = master_path.with_suffix(master_path.suffix + ".tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        import os
        os.replace(tmp, master_path)

    # ── audit row ─────────────────────────────────────────────────────────
    key = project_dir.name
    try:
        write_audit(
            "master-parked-dead-work-tree",
            key,
            master=master_path.name,
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001 — audit failure must not block parking
        pass

    # ── idempotency marker ────────────────────────────────────────────────
    marker.write_text(
        json.dumps({"run_id": run_id, "master": master_path.name,
                    "parked_at": today}),
        encoding="utf-8",
    )

    _result(master_path.name, run_id=run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())