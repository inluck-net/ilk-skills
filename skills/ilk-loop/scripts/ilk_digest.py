"""One-page daily digest of audit rows, releases, and triage actions.

``render --day YYYY-MM-DD`` writes ``<data_root>/digest/<day>.md``.
``today`` prints today's page so far to stdout (no file written).

Stdlib only, plus ``ilk_paths`` and ``ilk_audit`` (imported via sys.path
from the scripts dir).
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the scripts dir is importable so we can reach ilk_paths / ilk_audit.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from ilk_audit import AuditReadError, audit_day, read_audit  # noqa: E402
from ilk_paths import ilk_data_root  # noqa: E402


# ── path helpers ─────────────────────────────────────────────────────────────


def digest_path(day: str, *, root: Path | None = None) -> Path:
    """Return ``<root or ilk_data_root()>/digest/<day>.md``."""
    base = root if root is not None else ilk_data_root()
    return base / "digest" / f"{day}.md"


# ── collect ──────────────────────────────────────────────────────────────────


def collect(day: str, *, root: Path | None = None) -> dict[str, Any]:
    """Read audit rows for *day* and group by kind.

    Returns ``{"rows": {kind: [row, ...]}, "audit_error": str | None}``.
    On ``AuditReadError`` or ``OSError`` the error is recorded and no rows
    are returned.
    """
    result: dict[str, Any] = {"rows": {}, "audit_error": None}
    try:
        raw_rows = read_audit(day, root=root)
    except AuditReadError as exc:
        result["audit_error"] = f"{exc.path}:{exc.line_no}: {exc}"
        return result
    except OSError as exc:
        # Resolve the path the same way read_audit does
        base = root if root is not None else ilk_data_root()
        day_file = base / "audit" / f"{day}.jsonl"
        result["audit_error"] = f"{day_file}: {exc}"
        return result

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        kind = row.get("kind", "unknown")
        # Tolerant reading: a row's decision is row["decision"] when that is
        # a dict, else the row itself.
        if "decision" in row and isinstance(row["decision"], dict):
            row = {**row, "decision": row["decision"]}
        grouped.setdefault(kind, []).append(row)
    result["rows"] = grouped
    return result


# ── render ───────────────────────────────────────────────────────────────────


def _field(row: dict[str, Any], *keys: str) -> str:
    """Return the first present field value as string, or '(not recorded)'."""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return str(v)
    return "(not recorded)"


def _escalations_section(
    rows_by_kind: dict[str, list[dict[str, Any]]],
    audit_error: str | None,
) -> str:
    """Render ``## Escalations needing Chad``."""
    escalated = rows_by_kind.get("escalated", [])
    triage_decided = rows_by_kind.get("triage-decided", [])
    released = rows_by_kind.get("released", [])
    rolled_back = rows_by_kind.get("rolled-back", [])
    T = len(triage_decided)
    R = len(released) + len(rolled_back)

    lines = ["## Escalations needing Chad", ""]

    if audit_error:
        lines.append(f"- Audit file unreadable: {audit_error} — every count on this page is unknown")

    if not escalated and not audit_error:
        lines.append(f"None. 0 of {T} triage decisions escalated; 0 of {R} release-train outcomes escalated.")
    elif escalated:
        for row in escalated:
            project = _field(row, "project")
            reason = _field(row, "reason", "detail")
            run_or_tag = _field(row, "run_id", "tag")
            ts = _field(row, "ts")
            parts = [f"- **{project}**: {reason}"]
            if run_or_tag != "(not recorded)":
                parts.append(f" ({run_or_tag})")
            parts.append(f" — {ts}")
            lines.append("".join(parts))

    return "\n".join(lines)


def _releases_section(
    rows_by_kind: dict[str, list[dict[str, Any]]],
    audit_error: str | None,
) -> str:
    """Render ``## Releases``."""
    released = rows_by_kind.get("released", [])
    rolled_back = rows_by_kind.get("rolled-back", [])
    R = len(released) + len(rolled_back)

    lines = ["## Releases", ""]

    if audit_error:
        lines.append("unknown — audit file unreadable")
        return "\n".join(lines)

    deployed = [r for r in released if r.get("outcome") != "skipped"]
    skipped_count = len(released) - len(deployed)

    if not deployed and not rolled_back:
        lines.append(f"None. 0 releases of {R} release-train outcomes.")
    else:
        for row in deployed:
            tag = _field(row, "tag")
            parts = [f"- **{tag}**"]
            # Show any extra fields the writer included
            for key in ("batches", "masters", "phase0", "phase1"):
                if key in row:
                    parts.append(f" {key}={row[key]}")
            lines.append("".join(parts))
        for row in rolled_back:
            tag = _field(row, "tag")
            rolled_back_to = _field(row, "rolled_back_to")
            lines.append(f"- **{tag}** rolled back to {rolled_back_to}")

    if skipped_count:
        lines.append(f"{skipped_count} release-train checks found nothing to release.")

    return "\n".join(lines)


def _triage_section(
    rows_by_kind: dict[str, list[dict[str, Any]]],
    audit_error: str | None,
) -> str:
    """Render ``## Triage actions``."""
    applied = rows_by_kind.get("triage-applied", [])
    refused = rows_by_kind.get("triage-refused", [])
    escalated = rows_by_kind.get("escalated", [])
    triage_decided = rows_by_kind.get("triage-decided", [])
    A = len(applied)
    F = len(refused)
    E = len(escalated)
    T = len(triage_decided)

    lines = ["## Triage actions", ""]

    if audit_error:
        lines.append("unknown — audit file unreadable")
        return "\n".join(lines)

    lines.append(f"{A} applied, {F} refused, {E} escalated, of {T} decisions.")
    lines.append("")

    for row in applied:
        project = _field(row, "project")
        slug = _field(row, "slug")
        action = _field(row, "action")
        basis = _field(row, "basis")
        falsifier = _field(row, "falsifier")
        lines.append(f"- **{project}** {slug}: {action} (basis: {basis}, falsifier: {falsifier})")

    for row in refused:
        project = _field(row, "project")
        slug = _field(row, "slug")
        action = _field(row, "action")
        reason = _field(row, "reason")
        lines.append(f"- **{project}** {slug}: {action} — {reason}")

    return "\n".join(lines)


def render(
    day: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Render one markdown page for *day*.

    *now* defaults to ``datetime.now(timezone.utc)``.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    data = collect(day, root=root)
    rows_by_kind = data["rows"]
    audit_error = data["audit_error"]

    # Source line
    base = root if root is not None else ilk_data_root()
    audit_file = base / "audit" / f"{day}.jsonl"
    if audit_error:
        source_label = f"UNREADABLE ({audit_error})"
    elif audit_file.exists():
        total_rows = sum(len(v) for v in rows_by_kind.values())
        source_label = f"{audit_file} ({total_rows} rows)"
    else:
        source_label = f"{audit_file} (missing — nothing was recorded)"

    iso_now = now.isoformat(timespec="seconds")
    hostname = socket.gethostname()

    parts = [
        f"# ilk digest — {day}",
        "",
        f"Rendered {iso_now} on {hostname}. Source: {source_label}",
        "",
        _escalations_section(rows_by_kind, audit_error),
        "",
        _releases_section(rows_by_kind, audit_error),
        "",
        _triage_section(rows_by_kind, audit_error),
        "",
    ]
    return "\n".join(parts)


# ── write ────────────────────────────────────────────────────────────────────


def write(day: str, *, root: Path | None = None) -> Path:
    """Render and write the page atomically, creating ``digest/`` as needed.

    Returns the path to the written file.
    """
    page = render(day, root=root)
    target = digest_path(day, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: temp file in the same dir, then os.replace
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(page)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ilk_digest",
        description="One-page daily digest of audit rows.",
    )
    sub = parser.add_subparsers(dest="verb")

    p_render = sub.add_parser("render", help="Write and print the path for a given day.")
    p_render.add_argument("--day", required=True, help="YYYY-MM-DD")

    sub.add_parser("today", help="Print today's page so far to stdout (no file written).")

    args = parser.parse_args(argv)

    if args.verb == "render":
        # Validate --day format
        try:
            datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"invalid day: {args.day!r}", file=sys.stderr)
            return 2
        path = write(args.day)
        print(path)
        return 0

    if args.verb == "today":
        today = audit_day()
        page = render(today)
        print(page, end="")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())