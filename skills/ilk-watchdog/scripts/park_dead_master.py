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
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

# ── resolve imports from sibling toolkit dir ──────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOOP_SCRIPTS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"
sys.path.insert(0, str(_LOOP_SCRIPTS))

from ilk_audit import write_audit  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402
from plan_state import transition, TransitionError  # noqa: E402

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

    if not run_id or not re.fullmatch(r'[A-Za-z0-9_.-]+',run_id) or run_id in {'.','..'}:
        _result(None, reason="sentinel has no run_id")
        return 0

    # ── idempotency marker ────────────────────────────────────────────────
    marker = project_dir / "runtime" / "launcher" / f"parked-{run_id}.json"
    if marker.exists():
        _result(None, run_id=run_id, reason=f"already handled (marker {marker.name})")
        return 0

    # A stale sentinel cannot park another live or launching batch.
    from run_registry import project_status, RegistryError
    try:
        if project_status(project_dir/'runtime/launcher')['busy']:
            _result(None,run_id=run_id,reason='project has a live or launching run')
            return 0
    except (RegistryError,OSError,ValueError) as exc:
        _result(None,run_id=run_id,reason=f'project ownership unverified: {exc}')
        return 0

    # ── find the single active master ─────────────────────────────────────
    plans_dir = project_dir / "plans"
    if not plans_dir.is_dir():
        _result(None, run_id=run_id, reason="no plans dir")
        return 0

    active_masters = []
    for p in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            if p.resolve().parent!=plans_dir.resolve():
                _result(None,run_id=run_id,reason="master escapes plans root")
                return 0
            raw=p.read_bytes()
            fm = parse_frontmatter(raw.decode("utf-8-sig"))
        except OSError:
            continue
        if (fm.get("status") or "").strip().lower() == "active":
            active_masters.append((p, fm, raw))

    if not active_masters:
        _result(None, run_id=run_id, reason="no active master found")
        return 0

    if len(active_masters) > 1:
        _result(None, run_id=run_id,
                reason=f"{len(active_masters)} active masters; expected 1")
        return 0

    master_path, _fm, raw = active_masters[0]
    recorded_master=sentinel.get('master_path')
    if recorded_master is not None and (not isinstance(recorded_master,str)
            or not Path(recorded_master).is_absolute() or Path(recorded_master).resolve()!=master_path.resolve()):
        _result(None,run_id=run_id,reason='sentinel master contradicts active master')
        return 0

    # Status and explanatory body bytes share the same CAS/journal replacement.
    today = date.today().isoformat()
    log_row = (
        f"| {today} | Parked by the scheduler: run {run_id} ended "
        f"work_tree_invalid (no restart can fix it). |"
    )
    try:
        transition(master_path,{'status':_fm['status']},{'status':_PARKED_STATUS},
                   actor='scheduler',reason='stopped run has an invalid work tree',
                   progress_note=log_row,expected_digest=hashlib.sha256(raw).hexdigest())
    except (TransitionError,OSError) as exc:
        _result(None,run_id=run_id,reason=f'state transition refused: {exc}')
        return 0

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