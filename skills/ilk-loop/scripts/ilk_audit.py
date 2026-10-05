"""Append-only audit rows and typed lifecycle events.

Stdlib only. Every row is one JSON line written in a single ``write()``
call so concurrent writers interleave whole lines.

Audit rows live under ``<data root>/audit/<YYYY-MM-DD>.jsonl``.
Lifecycle events live at ``<data root>/events.jsonl``.

The data root defaults to ``~/.ilk-data`` but honours ``ILK_DATA_HOME``
(and the back-compat alias ``ILK_DATA_DIR``) via ``ilk_data_root()``.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


# ── constants ────────────────────────────────────────────────────────────

AUDIT_KINDS: tuple[str, ...] = (
    "triage-decided",
    "triage-applied",
    "triage-refused",
    "escalated",
    "released",
    "rolled-back",
    "candidate-emitted",
    "autoplan-started",
    "autoplan-queued",
    "autoplan-drafted",
    "autoplan-refused",
    "dry-period-drafted",
)

EVENT_TYPES: tuple[str, ...] = (
    "batch-dispatched",
    "run-exited",
    "triage-decided",
    "triage-applied",
    "escalated",
    "released",
    "rolled-back",
    "autoplan-started",
    "autoplan-queued",
    "dry-period-drafted",
)


# ── errors ───────────────────────────────────────────────────────────────


class AuditReadError(Exception):
    """Raised when a day file contains a line that is not a JSON object."""

    def __init__(self, path: Path, line_no: int, detail: str) -> None:
        self.path = path
        self.line_no = line_no
        super().__init__(f"{path}:{line_no}: {detail}")


# ── path helpers ─────────────────────────────────────────────────────────


def _data_root(root: Path | None = None) -> Path:
    """Resolve the ilk data root.

    If *root* is given it is used directly.  Otherwise the canonical
    resolver ``ilk_data_root()`` from ``ilk_paths`` is called (it
    honours ``ILK_DATA_HOME`` / ``ILK_DATA_DIR``).
    """
    if root is not None:
        return root
    # Import here so the module stays importable even when ilk_paths
    # is not on sys.path (e.g. during isolated unit tests that always
    # pass ``root=``).
    try:
        from skills.ilk_loop.scripts.ilk_paths import ilk_data_root

        return ilk_data_root()
    except Exception:
        # Fallback for environments where ilk_paths is unavailable.
        import os

        env = os.environ.get("ILK_DATA_HOME") or os.environ.get("ILK_DATA_DIR")
        if env:
            return Path(env)
        return Path.home() / ".ilk-data"


def audit_dir(root: Path | None = None) -> Path:
    """Return ``<data root>/audit``."""
    return _data_root(root) / "audit"


def events_path(root: Path | None = None) -> Path:
    """Return ``<data root>/events.jsonl``."""
    return _data_root(root) / "events.jsonl"


# ── writers ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """ISO 8601 with offset, second precision."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON line to *path*, creating parent dirs as needed.

    The line and trailing newline are written in a single ``write()``
    call so that concurrent writers produce whole lines, not fragments.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def write_audit(
    kind: str,
    project: str,
    *,
    root: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one audit row and return it.

    Raises ``ValueError`` if *kind* is not in ``AUDIT_KINDS``.
    """
    if kind not in AUDIT_KINDS:
        raise ValueError(
            f"unknown audit kind {kind!r}; expected one of {AUDIT_KINDS}"
        )
    row: dict[str, Any] = {
        "ts": _now_iso(),
        "host": socket.gethostname(),
        "kind": kind,
        "project": project,
    }
    row.update(fields)
    day_file = audit_dir(root) / f"{audit_day()}.jsonl"
    _append_jsonl(day_file, row)
    return row


def write_event(
    event: str,
    project: str,
    *,
    root: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one lifecycle event and return it.

    Raises ``ValueError`` if *event* is not in ``EVENT_TYPES``.
    """
    if event not in EVENT_TYPES:
        raise ValueError(
            f"unknown event type {event!r}; expected one of {EVENT_TYPES}"
        )
    row: dict[str, Any] = {
        "ts": _now_iso(),
        "host": socket.gethostname(),
        "event": event,
        "project": project,
    }
    row.update(fields)
    _append_jsonl(events_path(root), row)
    return row


# ── reader ───────────────────────────────────────────────────────────────


def audit_day() -> str:
    """The day file a row written now goes to: the host's LOCAL date.

    Every reader that computes day names must use this, not its own clock:
    triage_apply's idempotency check used the UTC date while rows were filed
    under the local date, so between local midnight and 08:00 (UTC+8) it
    looked in the wrong files and let a run be applied twice (2026-10-04).
    """
    return date.today().isoformat()


def read_audit(
    day: str | None = None,
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the audit rows for *day* (default: today).

    A missing file returns ``[]`` (nothing written is a fact).
    A line that is not a JSON object raises ``AuditReadError`` naming
    the file and line number.
    """
    if day is None:
        day = audit_day()
    day_file = audit_dir(root) / f"{day}.jsonl"
    if not day_file.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(day_file, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuditReadError(day_file, lineno, str(exc)) from exc
            if not isinstance(obj, dict):
                raise AuditReadError(
                    day_file, lineno, f"expected JSON object, got {type(obj).__name__}"
                )
            rows.append(obj)
    return rows


# ── CLI ──────────────────────────────────────────────────────────────────


def _cli_tail(args: argparse.Namespace) -> int:
    """``tail`` subcommand: print the day's rows."""
    try:
        rows = read_audit(day=args.day, root=None)
    except AuditReadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ilk_audit",
        description="Append-only audit rows and typed lifecycle events.",
    )
    sub = parser.add_subparsers(dest="cmd")

    tail_p = sub.add_parser("tail", help="print the day's audit rows")
    tail_p.add_argument("--day", default=None, help="YYYY-MM-DD (default: today)")
    tail_p.add_argument("--json", action="store_true", help="pretty-print JSON")

    args = parser.parse_args(argv)
    if args.cmd == "tail":
        return _cli_tail(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())