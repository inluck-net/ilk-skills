#!/usr/bin/env python3
"""
Shared improvement backlog for ilk-skills toolkit/process candidates.

Stores structured upstream-candidate records emitted by ``collect.py``
when a postmortem finding is classified as a toolkit/process gap (not
project-local).  The backlog lives at ``~/.ilk-data/ilk-skills-improvements/``
(env-overridable via ``$ILK_DATA_HOME``).

Schema: see ``Entry`` dataclass below.  Legal kinds are listed in ``KINDS``;
``"toolkit"`` remains the default for back-compat.
Dedup: stable key derived from (kind, normalised title+gap).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_BACKLOG_DIR = Path.home() / ".ilk-data" / "ilk-skills-improvements"


def _backlog_dir() -> Path:
    """Return the backlog directory, respecting ``$ILK_DATA_HOME``."""
    override = os.environ.get("ILK_DATA_HOME")
    if override:
        return Path(override) / "ilk-skills-improvements"
    return _DEFAULT_BACKLOG_DIR


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

#: Canonical set of legal entry kinds.  ``"toolkit"`` is the historical
#: default and must always be present for back-compat.
KINDS: tuple[str, ...] = ("toolkit", "bug", "feature", "gap", "toolchain", "escaped-bug")


def _normalise_for_key(text: str) -> str:
    """Lowercase, collapse whitespace, strip non-alphanum (except spaces)."""
    t = text.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return t.strip()


def stable_key(kind: str, title: str, gap: str) -> str:
    """Derive a deterministic dedup key from kind + title + gap."""
    raw = f"{kind}|{title}|{gap}"
    normalised = _normalise_for_key(raw)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


@dataclass
class Entry:
    """One upstream-candidate record in the backlog.

    ``kind`` must be one of :data:`KINDS`; ``"toolkit"`` is the back-compat
    default.  ``source`` records where the entry originated (e.g.
    ``"feedback"`` for postmortem-emitted entries, ``"supervisor"`` for
    supervisor-emitted).  ``relations`` holds freeform structured metadata
    such as ``{"run_id": "...", "commit": "..."}``.
    """

    id: str               # stable dedup key
    title: str            # short human-readable title
    kind: str             # one of KINDS (default "toolkit")
    gap: str              # description of the gap
    evidence: dict        # {file, line, run_id, project}
    proposed_fix: str     # suggested fix
    leverage: str         # "high" / "medium" / "low"
    severity: str         # "high" / "medium" / "low"
    status: str           # "open" (default) / "planned" / "shipped" / "wontfix"
    first_seen: str       # ISO datetime
    last_seen: str        # ISO datetime
    seen_count: int       # how many times this candidate has been observed
    source: str = ""      # origin (e.g. "feedback", "supervisor", "lark", "github")
    relations: dict = field(default_factory=dict)  # structured links (run_id, commit, plan, …)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entry:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ---------------------------------------------------------------------------
# File I/O (atomic writes, best-effort file lock)
# ---------------------------------------------------------------------------

def _entries_path(backlog_dir: Path) -> Path:
    return backlog_dir / "candidates.json"


def _load_raw(backlog_dir: Path) -> list[dict[str, Any]]:
    """Load the raw JSON array from disk (empty list if missing)."""
    p = _entries_path(backlog_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_raw(backlog_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically write the JSON array to disk."""
    backlog_dir.mkdir(parents=True, exist_ok=True)
    p = _entries_path(backlog_dir)
    # Write to temp file in same dir, then rename (atomic on POSIX).
    fd, tmp = tempfile.mkstemp(dir=str(backlog_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(p))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_candidate(
    *,
    title: str,
    kind: str = "toolkit",
    gap: str,
    evidence: dict | None = None,
    proposed_fix: str = "",
    leverage: str = "medium",
    severity: str = "medium",
    backlog_dir: Path | str | None = None,
) -> Entry:
    """Add or update an upstream-candidate entry (idempotent).

    If a candidate with the same stable key already exists, bumps
    ``seen_count``/``last_seen`` and preserves ``first_seen``.

    Returns the (possibly updated) ``Entry``.
    """
    if backlog_dir is None:
        backlog_dir = _backlog_dir()
    else:
        backlog_dir = Path(backlog_dir)

    key = stable_key(kind, title, gap)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    entries_raw = _load_raw(backlog_dir)
    existing_idx: int | None = None
    for i, e in enumerate(entries_raw):
        if e.get("id") == key:
            existing_idx = i
            break

    if existing_idx is not None:
        # Update existing entry
        entry_dict = entries_raw[existing_idx]
        entry_dict["last_seen"] = now
        entry_dict["seen_count"] = entry_dict.get("seen_count", 1) + 1
        # Merge evidence if new evidence provided
        if evidence:
            old_ev = entry_dict.get("evidence", {})
            old_ev.update(evidence)
            entry_dict["evidence"] = old_ev
        entry = Entry.from_dict(entry_dict)
        entries_raw[existing_idx] = entry.to_dict()
    else:
        # Create new entry
        entry = Entry(
            id=key,
            title=title,
            kind=kind,
            gap=gap,
            evidence=evidence or {},
            proposed_fix=proposed_fix,
            leverage=leverage,
            severity=severity,
            status="open",
            first_seen=now,
            last_seen=now,
            seen_count=1,
        )
        entries_raw.append(entry.to_dict())

    _save_raw(backlog_dir, entries_raw)
    return entry


def load(backlog_dir: Path | str | None = None) -> list[Entry]:
    """Load all entries from the backlog."""
    if backlog_dir is None:
        backlog_dir = _backlog_dir()
    else:
        backlog_dir = Path(backlog_dir)

    return [Entry.from_dict(d) for d in _load_raw(backlog_dir)]


def list_entries(
    *,
    status: str | None = None,
    kind: str | None = None,
    backlog_dir: Path | str | None = None,
) -> list[Entry]:
    """List entries, optionally filtered by status/kind."""
    entries = load(backlog_dir)
    if status:
        entries = [e for e in entries if e.status == status]
    if kind:
        entries = [e for e in entries if e.kind == kind]
    return entries


# ---------------------------------------------------------------------------
# CLI (for debugging/manual inspection)
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ilk-skills improvement backlog")
    sub = parser.add_subparsers(dest="cmd")

    sub_add = sub.add_parser("add", help="Add a candidate")
    sub_add.add_argument("--title", required=True)
    sub_add.add_argument("--gap", required=True)
    sub_add.add_argument("--kind", default="toolkit")
    sub_add.add_argument("--proposed-fix", default="")
    sub_add.add_argument("--leverage", default="medium")
    sub_add.add_argument("--severity", default="medium")
    sub_add.add_argument("--project", default="")
    sub_add.add_argument("--run-id", default="")
    sub_add.add_argument("--file", default="")
    sub_add.add_argument("--line", default="")

    sub_list = sub.add_parser("list", help="List candidates")
    sub_list.add_argument("--status", default=None)
    sub_list.add_argument("--kind", default=None)
    sub_list.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.cmd == "add":
        evidence: dict[str, Any] = {}
        if args.project:
            evidence["project"] = args.project
        if args.run_id:
            evidence["run_id"] = args.run_id
        if args.file:
            evidence["file"] = args.file
        if args.line:
            evidence["line"] = args.line
        entry = add_candidate(
            title=args.title,
            kind=args.kind,
            gap=args.gap,
            evidence=evidence,
            proposed_fix=args.proposed_fix,
            leverage=args.leverage,
            severity=args.severity,
        )
        print(json.dumps(entry.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "list":
        entries = list_entries(status=args.status, kind=args.kind)
        if args.json:
            print(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False))
        else:
            if not entries:
                print("no candidates")
                return 0
            print(f"| {'id':<16} | {'status':<8} | {'seen':<4} | {'title':<40} |")
            print(f"|{'-'*18}|{'-'*10}|{'-'*6}|{'-'*42}|")
            for e in entries:
                print(f"| {e.id:<16} | {e.status:<8} | {e.seen_count:<4} | {e.title:<40} |")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
