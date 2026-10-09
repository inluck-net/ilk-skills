#!/usr/bin/env python3
"""subplan_status_snapshot — undo a sub-plan status no reader understands.

A worker owns ``current_step`` (+1 per turn) and nothing else in a sub-plan's
frontmatter.  Workers still write ``status:`` by hand, and some of the values
they pick are outside the state machine: gh-resolve run 20261009-133350 set
``status: complete`` after its last step, and another set a sub-plan to
``queued`` (a MASTER status).  Readers branch on a fixed set
(``loop_status.py:418-431,656``), so such a sub-plan is neither runnable nor
terminal: the dependent never becomes runnable, the master goes
``blocked-no-runnable`` and the scheduler moves on (backlog aba1019c).

The runner takes a snapshot of the ``status`` line of every sub-plan the run's
master registers, right before it dispatches the agent, and restores right
after the agent returns.  Only a status that CHANGED during the turn and is
now OUTSIDE ``KNOWN_STATUSES`` is put back.  An in-vocabulary change is left
alone: ``shipped`` via ship_transition and ``blocked`` via quarantine are
legitimate, and a worker's direct ``shipped`` is one-ship enforcement's call.

Usage:
  subplan_status_snapshot.py take    --plans-dir D --master NAME --out F
  subplan_status_snapshot.py restore --plans-dir D --snapshot F

``restore`` prints one ``[status-guard]`` line per restored sub-plan.
Exit 0 = nothing restored; 3 = at least one restored; 2 = the snapshot could
not be read or a restore could not be written.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_status import extract_subplan_files, parse_frontmatter  # noqa: E402

#: Every sub-plan status a reader branches on (loop_status.py:418-431,656).
KNOWN_STATUSES = frozenset(
    {"pending", "ready", "in-progress", "shipped", "blocked", "skipped-by-operator"}
)

_STATUS_RE = re.compile(r"^status[ \t]*:.*$", re.MULTILINE)


def _frontmatter_span(text: str) -> tuple[int, int] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    return (3, end) if end >= 0 else None


def _status_line(path: Path) -> str | None:
    """The raw ``status:`` value of *path*'s frontmatter; None when absent."""
    text = path.read_text(encoding="utf-8-sig")
    span = _frontmatter_span(text)
    if span is None:
        return None
    m = _STATUS_RE.search(text, span[0], span[1])
    return m.group(0).partition(":")[2].strip() if m else None


def _norm(raw: str | None) -> str:
    if raw is None:
        return ""
    return (parse_frontmatter(f"---\nstatus: {raw}\n---\n").get("status") or "").strip().lower()


def take(plans_dir: Path, master: str) -> dict[str, str | None]:
    """Snapshot the raw status of every sub-plan *master* registers.

    A missing master is an error, not an empty snapshot: an empty snapshot
    would verify nothing and read as clean.  A registered sub-plan with no
    file yet is skipped (there is nothing to restore it to).
    """
    mp = plans_dir / master
    if not mp.is_file():
        raise FileNotFoundError(f"master not found: {mp}")
    snap: dict[str, str | None] = {}
    for name in extract_subplan_files(mp.read_text(encoding="utf-8-sig")):
        p = plans_dir / name
        if p.is_file():
            snap[name] = _status_line(p)
    return snap


def _write_status(path: Path, raw: str | None) -> None:
    """Set *path*'s status line to *raw* (None removes it); every other byte kept."""
    text = path.read_text(encoding="utf-8-sig")
    span = _frontmatter_span(text)
    if span is None:
        raise ValueError(f"{path.name}: no frontmatter block")
    m = _STATUS_RE.search(text, span[0], span[1])
    if raw is None:
        if m is None:
            return
        cut_end = m.end() + 1 if text[m.end():m.end() + 1] == "\n" else m.end()
        new = text[:m.start()] + text[cut_end:]
    elif m is None:
        new = text[:span[1]] + f"\nstatus: {raw}" + text[span[1]:]
    else:
        new = text[:m.start()] + f"status: {raw}" + text[m.end():]
    tmp = path.with_name(path.name + ".status-guard.tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(path)


def restore(plans_dir: Path, snap: dict[str, str | None]) -> tuple[list[str], int]:
    """Restore each sub-plan whose status changed to an unknown value.

    Returns (log lines, number restored).  A snapshot value that
    is itself unknown is not written back (it would not help); that sub-plan
    is reported as unrestorable instead.
    """
    lines: list[str] = []
    restored = 0
    for name, before in snap.items():
        p = plans_dir / name
        if not p.is_file():
            continue
        now = _status_line(p)
        if _norm(now) == _norm(before) or _norm(now) in KNOWN_STATUSES:
            continue
        shown = now if now is not None else "<absent>"
        if _norm(before) not in KNOWN_STATUSES:
            lines.append(
                f"[status-guard] {name}: status {shown!s} is outside the state machine, "
                f"and the pre-dispatch value {before!s} is too; left for the operator"
            )
            continue
        _write_status(p, before)
        restored += 1
        lines.append(
            f"[status-guard] {name}: status {shown} -> {before} "
            f"(the worker wrote a status outside the state machine; restored the pre-dispatch value)"
        )
    return lines, restored


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    t.add_argument("--plans-dir", required=True, type=Path)
    t.add_argument("--master", required=True)
    t.add_argument("--out", required=True, type=Path)
    r = sub.add_parser("restore")
    r.add_argument("--plans-dir", required=True, type=Path)
    r.add_argument("--snapshot", required=True, type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "take":
        try:
            snap = take(a.plans_dir, a.master)
        except (OSError, ValueError) as e:
            print(f"subplan_status_snapshot: {e}", file=sys.stderr)
            return 2
        a.out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
        return 0

    try:
        snap = json.loads(a.snapshot.read_text(encoding="utf-8"))
        if not isinstance(snap, dict):
            raise ValueError("snapshot is not an object")
        lines, restored = restore(a.plans_dir, snap)
    except (OSError, ValueError) as e:
        print(f"subplan_status_snapshot: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 3 if restored else 0


if __name__ == "__main__":
    sys.exit(main())
