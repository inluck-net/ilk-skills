#!/usr/bin/env python3
"""park_master — take a master plan out of the scheduler's queue, or put it back.

The inverse of this already existed (`/ilk-resume` writes a resolve-ack) but
nothing performed the park, so the operation was hand-editing frontmatter
under the external plans dir with no record of why.

WHAT A PARK IS, and what it is NOT.  The scheduler dispatches a project only
when one of its masters has status `queued` or `active`
(``plan_status._RUNNABLE_STATUSES``, applied at ``scheduler_scan`` pass 1).
Setting the status to `blocked` removes the master from the scan entirely.

That is DURABLE.  The postmortem blacklist is not: it is a 60-minute backoff
(``blacklist_status.is_blacklisted`` -> ``within-backoff``) that expires and
lets dispatch resume.  Measured 2026-09-05 on a duplicate resolver run —
killing the loop bought exactly one hour each time, three times over, while
the park held across the expiry.  If you want a project to stop until a human
says otherwise, this is the lever; the blacklist is not.

Parking records WHY, in `parked_at` / `parked_reason`, because a bare
`status: blocked` is indistinguishable from a stall the loop caused itself.

Usage:
  park_master.py --project <project-or-data-dir> --reason "..."   [--master NAME]
  park_master.py --project <project-or-data-dir> --unpark          [--master NAME]
  park_master.py --project <project-or-data-dir> --status
Add --dry-run to see the decision without writing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import find_plans_dir as _resolve_plans_dir  # noqa: E402
from plan_status import (  # noqa: E402
    normalize_master_status,
    parse_frontmatter,
)
from promote_next_master import write_status  # noqa: E402

PARKED = "blocked"
# Statuses a park can act on. `shipped` is done, not parked; `draft` is
# already invisible to the scheduler.
PARKABLE = {"queued", "active"}


def _yaml_scalar(value: str) -> str:
    """Quote a reason so it survives parse_frontmatter round-trip.

    The parser strips an inline ``# comment`` from UNQUOTED scalars, so an
    unquoted ``duplicate of PR #4622`` reads back as ``duplicate of PR`` --
    silently, and ``#`` is exactly what a real reason contains (issue and PR
    numbers).  Quoted values are returned unchanged by the comment stripper
    and then unwrapped, so quoting is the fix.

    It does not unescape, so inner double quotes are folded to single ones
    rather than backslashed, and newlines are flattened -- frontmatter is
    line-oriented.
    """
    flat = " ".join(str(value).split())
    return '"' + flat.replace('"', "'") + '"'


def _stamp(path: Path, reason: str | None, when: str) -> None:
    """Record why and when, next to the status line, atomically.

    A bare `status: blocked` cannot be told apart from a stall the loop
    produced on its own, which is exactly the ambiguity that made the
    resolver-4546 incident take an investigation instead of a glance.

    *reason* of None REMOVES the stamp — an unparked master must not keep
    carrying `parked_at`, which would read as still parked.
    """
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    out, in_fm, done = [], False, False
    for line in lines:
        if line.rstrip("\n") == "---":
            if not in_fm:
                in_fm = True
            elif not done:
                if reason is not None:
                    out.append(f"parked_at: {when}\n")
                    out.append(f"parked_reason: {_yaml_scalar(reason)}\n")
                done = True
            out.append(line)
            continue
        # Drop any previous stamp so re-parking does not accumulate them.
        if in_fm and not done and line.startswith(("parked_at:", "parked_reason:")):
            continue
        out.append(line)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(out), encoding="utf-8")
    import os
    os.replace(tmp, path)


def _masters(plans_dir: Path) -> list[tuple[Path, dict]]:
    rows = []
    for p in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            rows.append((p, parse_frontmatter(p.read_text(encoding="utf-8-sig"))))
        except OSError:
            continue
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="project root or its data dir; plans dir is resolved from it")
    ap.add_argument("--plans-dir", type=Path, default=None,
                    help="explicit plans directory (skips resolution)")
    ap.add_argument("--master", default=None,
                    help="master filename; default = the single parkable one")
    ap.add_argument("--reason", default=None, help="why this is parked (recorded)")
    ap.add_argument("--unpark", action="store_true",
                    help="return a parked master to `queued`")
    ap.add_argument("--status", action="store_true",
                    help="report master statuses and exit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.plans_dir:
        plans_dir, source = Path(a.plans_dir), "explicit"
        if not plans_dir.is_dir():
            print(json.dumps({"error": "explicit plans dir not found",
                              "plans_dir": str(plans_dir)}))
            return 2
    else:
        plans_dir, source = _resolve_plans_dir(a.project)
        if not plans_dir:
            print(json.dumps({"error": "no plans dir resolved",
                              "project": str(a.project)}))
            return 2
        plans_dir = Path(plans_dir)

    rows = _masters(plans_dir)
    if a.status:
        print(json.dumps({
            "plans_dir": str(plans_dir), "plans_source": source,
            "masters": [{"master": p.name,
                         "status": normalize_master_status(fm.get("status") or ""),
                         "parked_at": fm.get("parked_at"),
                         "parked_reason": fm.get("parked_reason")} for p, fm in rows],
        }, indent=2))
        return 0

    want = PARKED if a.unpark else None  # unpark looks for blocked; park for parkable
    if a.unpark:
        cands = [(p, fm) for p, fm in rows
                 if normalize_master_status(fm.get("status") or "") == want]
    else:
        cands = [(p, fm) for p, fm in rows
                 if normalize_master_status(fm.get("status") or "") in PARKABLE]

    if a.master:
        cands = [(p, fm) for p, fm in cands if p.name == a.master]

    if not cands:
        # A negative names its search space -- "nothing to park" and "wrong
        # directory" must not look identical.
        print(json.dumps({
            "error": "no matching master",
            "wanted": "blocked" if a.unpark else sorted(PARKABLE),
            "plans_dir": str(plans_dir),
            "masters_searched": [
                {"master": p.name,
                 "status": normalize_master_status(fm.get("status") or "")}
                for p, fm in rows],
        }, indent=2))
        return 1
    if len(cands) > 1 and not a.master:
        print(json.dumps({
            "error": "several masters match - pass --master to choose",
            "candidates": [p.name for p, _ in cands],
        }, indent=2))
        return 1

    target, fm = cands[0]
    new_status = "queued" if a.unpark else PARKED
    when = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    reason = a.reason or ("unparked" if a.unpark else "parked by operator")

    plan = {
        "plans_dir": str(plans_dir),
        "master": target.name,
        "from": normalize_master_status(fm.get("status") or ""),
        "to": new_status,
        "reason": reason,
        "dry_run": a.dry_run,
    }
    if not a.dry_run:
        try:
            write_status(target, new_status)
            # Unpark strips the stamp rather than rewriting it: a `queued`
            # master carrying parked_at reads as still parked.
            _stamp(target, None if a.unpark else reason, when)
        except Exception as e:  # noqa: BLE001
            plan["error"] = f"{type(e).__name__}: {e}"
            print(json.dumps(plan, indent=2))
            return 2
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
