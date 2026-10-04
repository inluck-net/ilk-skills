"""One-page daily digest of audit rows, releases, and triage actions.

``render --day YYYY-MM-DD`` writes ``<data_root>/digest/<day>.md``.
``today`` prints today's page so far to stdout (no file written).

Stdlib only, plus ``ilk_paths`` and ``ilk_audit`` (imported via sys.path
from the scripts dir).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Ensure the scripts dir is importable so we can reach ilk_paths / ilk_audit.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from ilk_audit import AuditReadError, audit_day, read_audit  # noqa: E402
from ilk_paths import ilk_data_root  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402
from status_all import resolve_project_status  # noqa: E402


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


def _batches_section(
    day: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Render ``## Batches`` — per-project shipped / in-flight / blocked / idle."""
    if now is None:
        now = datetime.now(timezone.utc)

    base = root if root is not None else ilk_data_root()
    projects_dir = base / "projects"

    lines = ["## Batches", ""]

    if not projects_dir.is_dir():
        lines.append(f"None. 0 projects under {projects_dir}.")
        return "\n".join(lines)

    project_keys = sorted(d.name for d in projects_dir.iterdir() if d.is_dir())

    total_shipped = 0
    total_in_flight = 0
    total_blocked = 0
    total_unreadable = 0
    idle_keys: list[str] = []

    for key in project_keys:
        proj_dir = projects_dir / key
        plans_dir = proj_dir / "plans"

        # Count sub-plans shipped on *day*
        shipped_on_day = 0
        total_subplans_on_day = 0
        if plans_dir.is_dir():
            for sp_file in sorted(plans_dir.glob("20??-??-??-*.md")):
                try:
                    text = sp_file.read_text(encoding="utf-8-sig")
                    fm = parse_frontmatter(text)
                except (OSError, ValueError):
                    continue
                if fm.get("last_updated") == day:
                    total_subplans_on_day += 1
                    if fm.get("status") == "shipped":
                        shipped_on_day += 1

        # Resolve project status for in-flight / blocked
        try:
            status = resolve_project_status(proj_dir, roles=[], providers=[])
        except Exception as exc:
            lines.append(f"- **{key}** status unreadable: {type(exc).__name__}: {exc}")
            total_unreadable += 1
            continue

        sentinel = status.get("sentinel", {})
        sentinel_alive = sentinel.get("alive", False)
        blocked = status.get("blocked", False)
        blocked_reason = status.get("blocked_reason")
        active_master = status.get("active_master", "")
        subplan_index = status.get("subplan_index", 0)
        subplan_count = status.get("subplan_count", 0)
        sentinel_state = sentinel.get("state", "")

        is_active = False
        if shipped_on_day > 0:
            lines.append(f"- **{key}** {shipped_on_day} shipped of {total_subplans_on_day} sub-plans last updated on D")
            total_shipped += shipped_on_day

        if sentinel_alive and active_master:
            lines.append(
                f"- **{key}** in flight: {active_master}, sub-plan {subplan_index} of {subplan_count}, {sentinel_state}"
            )
            total_in_flight += 1
            is_active = True
        elif blocked:
            reason_text = blocked_reason or "unknown"
            lines.append(f"- **{key}** blocked: {reason_text}")
            total_blocked += 1
            is_active = True

        if not is_active and shipped_on_day == 0:
            idle_keys.append(key)

    total_projects = len(project_keys)
    if idle_keys:
        lines.append(f"{len(idle_keys)} of {total_projects} projects idle: {', '.join(idle_keys)}")

    # Header line (prepend after building)
    header = (
        f"State at render {now.isoformat(timespec='seconds')}. "
        f"{total_projects} projects: "
        f"{total_shipped} shipped work on D, "
        f"{total_in_flight} in flight, "
        f"{total_blocked} blocked, "
        f"{total_unreadable} unreadable, "
        f"{len(idle_keys)} idle."
    )
    lines.insert(1, header)

    return "\n".join(lines)


def _judgment_calls_section(
    day: str,
    *,
    root: Path | None = None,
) -> str:
    """Render ``## Judgment calls made`` — from masters created on *day*."""
    base = root if root is not None else ilk_data_root()
    projects_dir = base / "projects"

    lines = ["## Judgment calls made", ""]

    total_calls = 0
    total_masters_on_day = 0
    total_masters_with_calls = 0

    if not projects_dir.is_dir():
        lines.append(f"None. 0 judgment calls in 0 masters created on D.")
        return "\n".join(lines)

    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue
        plans_dir = proj_dir / "plans"
        if not plans_dir.is_dir():
            continue
        key = proj_dir.name

        for master_file in sorted(plans_dir.glob("MASTER-*.md")):
            try:
                text = master_file.read_text(encoding="utf-8-sig")
                fm = parse_frontmatter(text)
            except (OSError, ValueError):
                continue

            created = fm.get("created", "")
            if not created.startswith(day):
                continue

            total_masters_on_day += 1
            master_slug = fm.get("master_plan", master_file.stem)

            # Find ## Execution rationale section
            calls = _extract_judgment_calls(text)
            if calls:
                total_masters_with_calls += 1
                total_calls += len(calls)
                for call, basis, falsifier in calls:
                    parts = [f"- **{key}** {master_slug}: {call}"]
                    parts.append(f"  Basis: {basis}")
                    parts.append(f"  Wrong if: {falsifier}")
                    lines.append("\n".join(parts))

    if total_calls:
        lines.insert(
            1,
            f"{total_calls} judgment calls in {total_masters_with_calls} of {total_masters_on_day} masters created on D."
        )
    else:
        lines.insert(
            1,
            f"None. 0 judgment calls in {total_masters_on_day} masters created on D."
        )

    return "\n".join(lines)


def _extract_judgment_calls(master_text: str) -> list[tuple[str, str, str]]:
    """Extract judgment calls from a master's ## Execution rationale section.

    Returns list of (call, basis, falsifier) tuples.
    Each part is ``(not recorded)`` when missing.
    """
    import re

    # Find the Execution rationale section
    idx = master_text.find("## Execution rationale")
    if idx < 0:
        return []

    section = master_text[idx:]
    # Cut at the next ## heading (or end of text)
    next_heading = section.find("\n## ", 1)
    if next_heading >= 0:
        section = section[:next_heading]

    results: list[tuple[str, str, str]] = []
    lines = section.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped.startswith("- Judgment call:"):
            i += 1
            continue

        # Collect continuation lines (indented by 2 spaces)
        block_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i]
            if next_line.startswith("  ") and next_line.strip():
                block_lines.append(next_line.strip())
                i += 1
            elif next_line.strip() == "":
                i += 1
                continue
            else:
                break

        block = " ".join(block_lines)

        # Remove leading "- Judgment call: "
        if block.startswith("- Judgment call:"):
            block = block[len("- Judgment call:"):].strip()

        call = block
        basis = "(not recorded)"
        falsifier = "(not recorded)"

        # Find "Basis:" and "Wrong if:" — use regex to match at a sentence
        # boundary (after . ? ! or start-of-string) so that "Basis:" embedded
        # inside the call text (e.g. "looking. Basis: the memory rule") is not
        # mistaken for the section marker.
        basis_match = re.search(r'(?<=[.?!] )Basis:|^Basis:', block)
        wrong_match = re.search(r'(?<=[.?!] )Wrong if:|^Wrong if:', block)

        if basis_match and wrong_match:
            b_start = basis_match.start()
            w_start = wrong_match.start()
            if b_start < w_start:
                call = block[:b_start].strip()
                basis = block[b_start + len("Basis:"):w_start].strip()
                falsifier = block[w_start + len("Wrong if:"):].strip()
            else:
                call = block[:w_start].strip()
                falsifier = block[w_start + len("Wrong if:"):b_start].strip()
                basis = block[b_start + len("Basis:"):].strip()
        elif basis_match:
            b_start = basis_match.start()
            call = block[:b_start].strip()
            basis = block[b_start + len("Basis:"):].strip()
        elif wrong_match:
            w_start = wrong_match.start()
            call = block[:w_start].strip()
            falsifier = block[w_start + len("Wrong if:"):].strip()

        # Strip trailing periods from parts
        call = call.rstrip(".")
        basis = basis.rstrip(".")
        falsifier = falsifier.rstrip(".")

        results.append((call, basis, falsifier))

    return results


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
        _batches_section(day, root=root, now=now),
        "",
        _judgment_calls_section(day, root=root),
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


# ── scheduled ────────────────────────────────────────────────────────────────


def scheduled(
    day: str,
    root: Path | None = None,
    *,
    notify: Callable[[str, int, Path], None] | None = None,
) -> dict[str, Any]:
    """Render and write *day*'s page once, behind an atomic marker.

    Returns ``{"day": D, "already": True}`` when the marker already exists.
    Otherwise renders the page, counts escalations (``escalated`` rows + one
    when the audit file is unreadable), calls *notify* when escalations >= 1,
    and writes the marker JSON last.

    A render exception is caught and recorded in the marker's ``error`` field;
    the marker stays so the scheduler does not retry.
    """
    base = root if root is not None else ilk_data_root()
    marker = base / "digest" / f".scheduled-{day}"

    # Atomic once-per-day guard: O_CREAT|O_EXCL fails if the file exists.
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except FileExistsError:
        return {"day": day, "already": True}

    error: str | None = None
    page_path: Path | None = None
    escalations = 0
    notified = False

    try:
        page_path = write(day, root=root)

        data = collect(day, root=root)
        rows_by_kind = data["rows"]
        audit_error = data["audit_error"]

        escalated_rows = rows_by_kind.get("escalated", [])
        escalations = len(escalated_rows)
        if audit_error:
            escalations += 1

        if escalations >= 1:
            _notify(day, escalations, page_path, root=root, notify=notify)
            notified = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # Write the marker content last.
    marker_data = {
        "day": day,
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "escalations": escalations,
        "notified": notified,
        "error": error,
    }
    try:
        marker.write_text(json.dumps(marker_data), encoding="utf-8")
    except OSError:
        pass

    if error:
        return {"day": day, "error": error}
    return {"day": day, "escalations": escalations, "notified": notified}


def _notify(
    day: str,
    count: int,
    page_path: Path,
    *,
    root: Path | None = None,
    notify: Callable[[str, int, Path], None] | None = None,
) -> None:
    """Send a digest-ready notification.

    If *notify* is provided, call it directly.  Otherwise invoke
    ``ilk_notify.py --event digest-ready``.
    """
    if notify is not None:
        notify(day, count, page_path)
        return

    notify_py = Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts" / "ilk_notify.py"
    hostname = socket.gethostname()
    detail = f"{count} escalation(s) — {page_path}"
    try:
        subprocess.run(
            [sys.executable, str(notify_py), "--event", "digest-ready",
             "--project", hostname, "--detail", detail],
            capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except Exception:
        pass


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

    p_scheduled = sub.add_parser("scheduled", help="Render yesterday's page once (behind an atomic marker).")
    p_scheduled.add_argument("--day", required=True, help="YYYY-MM-DD")

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

    if args.verb == "scheduled":
        # Validate --day format
        try:
            datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"invalid day: {args.day!r}", file=sys.stderr)
            return 2
        result = scheduled(args.day)
        if result.get("error"):
            print(f"error: {result['error']}", file=sys.stderr)
            return 1
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())