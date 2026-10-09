"""Track consecutive release-train permit refusals and signal the owner.

When a release train is refused for missing permits, the scheduler records
each refusal via :func:`record`.  On the third consecutive refusal for the
same ``run_id``, the function returns ``True`` — a signal to the caller that
the owner should be asked.  A fourth call (and every subsequent one) returns
``False`` to avoid re-asking.

State lives in ``<data_dir>/runtime/permit-request.json``.

CLI usage::

    python3 permit_request.py record --data-dir DIR --run-id ID --reason REASON
    python3 permit_request.py pending --data-dir DIR
    python3 permit_request.py clear  --data-dir DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE_FILE = "runtime/permit-request.json"


def _state_path(data_dir: Path) -> Path:
    return data_dir / STATE_FILE


def _read(data_dir: Path) -> dict | None:
    p = _state_path(data_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(data_dir: Path, state: dict) -> None:
    p = _state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def record(data_dir: Path, run_id: str, reason: str) -> bool:
    """Record a permit refusal.  Returns True on the 3rd consecutive refusal."""
    state = _read(data_dir)
    if state is None or state.get("run_id") != run_id:
        state = {"run_id": run_id, "passes": 0, "requested_at": None, "reason": reason}
    state["passes"] += 1
    requested = False
    if state["passes"] == 3 and state["requested_at"] is None:
        from datetime import datetime, timezone
        state["requested_at"] = datetime.now(timezone.utc).isoformat()
        requested = True
    _write(data_dir, state)
    return requested


def pending(data_dir: Path) -> str | None:
    """Return the permit-request reason if one is pending, else None."""
    state = _read(data_dir)
    if state and state.get("requested_at") is not None:
        return state.get("reason", "")
    return None


def clear(data_dir: Path) -> None:
    """Remove the permit-request state file."""
    p = _state_path(data_dir)
    if p.is_file():
        p.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description="permit-request tracker")
    sub = ap.add_subparsers(dest="cmd")

    rec = sub.add_parser("record")
    rec.add_argument("--data-dir", required=True)
    rec.add_argument("--run-id", required=True)
    rec.add_argument("--reason", required=True)

    pend = sub.add_parser("pending")
    pend.add_argument("--data-dir", required=True)

    clr = sub.add_parser("clear")
    clr.add_argument("--data-dir", required=True)

    args = ap.parse_args()
    data_dir = Path(args.data_dir)

    if args.cmd == "record":
        if record(data_dir, args.run_id, args.reason):
            print("request")
    elif args.cmd == "pending":
        r = pending(data_dir)
        if r is not None:
            print(r)
    elif args.cmd == "clear":
        clear(data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())