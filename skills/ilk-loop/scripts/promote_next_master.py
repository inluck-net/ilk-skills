#!/usr/bin/env python3
"""
promote_next_master — mark the active master as shipped and promote
the next queued master to active.

Used by ilk-watchdog after a clean ship to advance the per-project
master plan queue without operator intervention. Also runnable
manually:

  python promote_next_master.py --project <path>
  python promote_next_master.py --project <path> --dry-run

Behaviour
=========

1. Resolve the project's plans dir via ilk_paths.find_plans_dir.
2. Parse every MASTER-*.md frontmatter.
3. If there is one master with status: active -> set it to shipped
   (the 'demoted' field in the output).
4. Among status: queued masters, pick the highest priority
   (priority desc, created asc tie-break) and set it to active
   (the 'promoted' field).
5. If no master is active and no master is queued, print a JSON with
   both fields null and exit 0 (nothing to do).
6. Mutations are atomic per file (write to .tmp + rename).

Output is one JSON object on stdout describing what changed.
Exit codes: 0 on success or no-op; 2 on I/O / parse error.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import find_plans_dir as _resolve_plans_dir  # noqa: E402

FRONTMATTER_RE = re.compile(r"^(---\s*\n)(.*?)(\n---\s*\n)", re.DOTALL)
STATUS_LINE_RE = re.compile(r"^(\s*)status\s*:\s*(\S+)\s*$", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for raw in m.group(2).splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        fm[k.strip()] = v.strip()
    return fm


def write_status(path: Path, new_status: str) -> bool:
    """Replace `status:` in frontmatter atomically. Returns True on
    success (file changed), False on no-op (already matches)."""
    text = path.read_text(encoding="utf-8-sig")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path.name}: no frontmatter block")

    fm_block = m.group(2)
    if STATUS_LINE_RE.search(fm_block):
        new_fm = STATUS_LINE_RE.sub(rf"\1status: {new_status}", fm_block, count=1)
    else:
        # Insert status line at the top of the frontmatter.
        new_fm = f"status: {new_status}\n" + fm_block

    if new_fm == fm_block:
        return False

    new_text = m.group(1) + new_fm + m.group(3) + text[m.end():]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _prio(fm: dict) -> int:
    try:
        return int(fm.get("priority", 0))
    except (TypeError, ValueError):
        return 0


def _created(fm: dict) -> str:
    return str(fm.get("created", "")) or "~"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--plans-dir", type=Path, default=None,
                    help="explicit plans directory (skips auto-resolution)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan but do not modify any file")
    args = ap.parse_args(argv)

    if args.plans_dir:
        plans_dir = Path(args.plans_dir)
        source = "explicit"
        if not plans_dir.is_dir():
            print(json.dumps({"error": "explicit plans dir not found", "plans_dir": str(plans_dir)}))
            return 2
    else:
        plans_dir, source = _resolve_plans_dir(args.project)
        if not plans_dir:
            print(json.dumps({"error": "no plans dir resolved", "project": str(args.project)}))
            return 2

    masters = sorted(plans_dir.glob("MASTER-*.md"))
    parsed: list[tuple[Path, dict]] = []
    for p in masters:
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8-sig"))
        except OSError as e:
            print(json.dumps({"error": f"read fail: {p.name}: {e}"}))
            return 2
        parsed.append((p, fm))

    actives = [(p, fm) for p, fm in parsed if (fm.get("status") or "").strip() == "active"]
    queued = [(p, fm) for p, fm in parsed if (fm.get("status") or "").strip() == "queued"]

    queued.sort(key=lambda it: (-_prio(it[1]), _created(it[1])))

    demoted = actives[0][0] if actives else None
    promoted = queued[0][0] if queued else None

    plan = {
        "plans_dir": str(plans_dir),
        "plans_source": source,
        "demoted": demoted.name if demoted else None,
        "promoted": promoted.name if promoted else None,
        "queue_remaining": max(len(queued) - (1 if promoted else 0), 0),
        "active_count_before": len(actives),
        "queued_count_before": len(queued),
        "dry_run": args.dry_run,
    }

    if not args.dry_run:
        if demoted is not None:
            try:
                write_status(demoted, "shipped")
            except Exception as e:
                plan["error"] = f"demote {demoted.name}: {e}"
                print(json.dumps(plan, ensure_ascii=False))
                return 2
        if promoted is not None:
            try:
                write_status(promoted, "active")
            except Exception as e:
                plan["error"] = f"promote {promoted.name}: {e}"
                print(json.dumps(plan, ensure_ascii=False))
                return 2

    print(json.dumps(plan, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
