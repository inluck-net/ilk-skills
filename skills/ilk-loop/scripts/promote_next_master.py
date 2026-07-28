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
from plan_slug import has_date_prefix, strip_date_prefix  # noqa: E402
from plan_status import master_has_nonshipped, master_is_drainable  # noqa: E402

FRONTMATTER_RE = re.compile(r"^(---\s*\n)(.*?)(\n---\s*\n)", re.DOTALL)
STATUS_LINE_RE = re.compile(r"^(\s*)status\s*:\s*(\S+)\s*$", re.MULTILINE)

# Verification tiers that require a human-verify marker before downstream
# masters may build on them (decomposition-principles §12).
_VERIFY_TIERS = {"compile-only", "device-manual"}
_TRUTHY_VALUES = {"true", "yes", "1"}


def _slug_from_filename(fname: str) -> str:
    """Strip the date prefix and .md suffix from a sub-plan filename."""
    slug = fname
    if slug.endswith(".md"):
        slug = slug[:-3]
    return strip_date_prefix(slug)


def _subplan_file_for_slug(slug: str, plans_dir: Path) -> Path | None:
    """Resolve a slug to a sub-plan file on disk.

    Accepts both bare slugs (``combat-vfx``) and dated filenames
    (``2026-06-28-combat-vfx.md``).  Returns None when no matching file
    exists.
    """
    # If it already looks like a dated filename, try directly.
    if has_date_prefix(slug):
        p = plans_dir / slug
        if p.exists():
            return p
    # Try the YYYY-MM-DD-<slug>.md pattern (most common).
    for p in plans_dir.glob(f"*-{slug}.md"):
        if not p.name.startswith("MASTER"):
            return p
    # Bare filename fallback.
    p = plans_dir / slug
    return p if p.exists() else None


def _check_unverified_builds_on(
    master_fm: dict, plans_dir: Path
) -> list[str]:
    """Return slugs of unverified compile-only/device-manual dependencies.

    Reads the master's ``builds_on`` front-matter field (comma-separated
    sub-plan slugs) and checks each dependency.  A dependency is blocking
    iff:
      - its ``verification_tier`` is ``compile-only`` or ``device-manual``
      - its ``verified`` field is not truthy (absent ⇒ unverified)

    Returns an empty list when all dependencies are clear.  Degrades
    safely on missing files or malformed markers (treats them as
    unverified).
    """
    builds_on_raw = (master_fm.get("builds_on") or "").strip()
    if not builds_on_raw:
        return []

    blockers: list[str] = []
    for raw_slug in builds_on_raw.split(","):
        slug = raw_slug.strip()
        if not slug:
            continue
        sub_path = _subplan_file_for_slug(slug, plans_dir)
        if sub_path is None:
            # Missing file ⇒ can't verify ⇒ treat as blocking.
            blockers.append(slug)
            continue
        try:
            text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            blockers.append(slug)
            continue
        # Parse front-matter (same minimal parser used elsewhere).
        sub_fm: dict[str, str] = {}
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for raw_line in text[3:end].splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        k, _, v = line.partition(":")
                        sub_fm[k.strip()] = v.strip()

        tier = (sub_fm.get("verification_tier") or "").strip()
        if tier not in _VERIFY_TIERS:
            # loop-verified (or absent ⇒ loop-verified) — no block.
            continue
        verified_raw = (sub_fm.get("verified") or "").strip().lower()
        if verified_raw in _TRUTHY_VALUES:
            continue
        # Unverified compile-only/device-manual — block.
        blockers.append(slug)
    return blockers


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

    PROMOTABLE = {"queued", "pending"}

    actives = [(p, fm) for p, fm in parsed if (fm.get("status") or "").strip() == "active"]
    queued = [(p, fm) for p, fm in parsed if (fm.get("status") or "").strip() in PROMOTABLE]

    # `supervised_only` masters are never auto-promoted or auto-demoted by the
    # watchdog — they edit the loop's own infrastructure (or are otherwise
    # sensitive) and must be driven by a human via manual `/ilk`. The manual
    # path (loop_status.pick_active_master) deliberately still selects them.
    def _supervised_only(fm: dict) -> bool:
        return (fm.get("supervised_only") or "").strip().lower() in ("true", "yes", "1")
    actives = [(p, fm) for p, fm in actives if not _supervised_only(fm)]
    queued = [(p, fm) for p, fm in queued if not _supervised_only(fm)]

    # L4: drainability-aware filtering.
    # - Active: filter by master_has_nonshipped (all-shipped → no demotion
    #   needed, reconcile handles them).  A stalled active (non-shipped but
    #   non-drainable) still enters the actives list — it will be demoted
    #   to `blocked` below.
    # - Queued: filter by master_is_drainable — don't promote a queued
    #   master that is itself stalled (it would immediately stall again).
    actives = [(p, fm) for p, fm in actives if master_has_nonshipped(p, plans_dir)]
    queued = [(p, fm) for p, fm in queued if master_is_drainable(p, plans_dir)]

    # Compile-only carry-forward enforcement (decomposition-principles §12):
    # Don't promote a master that builds on an unverified compile-only or
    # device-manual sub-plan.  The human-verify marker (detached-component-
    # contracts.md, Contract 4) must be present on the dependency.
    filtered_queued: list[tuple[Path, dict]] = []
    skip_reasons: list[dict] = []
    for p, fm in queued:
        blockers = _check_unverified_builds_on(fm, plans_dir)
        if blockers:
            skip_reasons.append({
                "master": p.name,
                "reason": "unverified compile-only/device-manual dependency",
                "blockers": blockers,
            })
        else:
            filtered_queued.append((p, fm))
    queued = filtered_queued

    queued.sort(key=lambda it: (-_prio(it[1]), _created(it[1])))

    demoted = actives[0][0] if actives else None
    promoted = queued[0][0] if queued else None

    # Determine demotion target: stalled masters are parked `blocked`, not
    # `shipped`.  A master is stalled iff it has non-shipped sub-plans but
    # is non-drainable (zero runnable sub-plans).
    demote_status = "shipped"
    if demoted is not None:
        if master_has_nonshipped(demoted, plans_dir):
            demote_status = "blocked"

    plan = {
        "plans_dir": str(plans_dir),
        "plans_source": source,
        "demoted": demoted.name if demoted else None,
        "demote_status": demote_status,
        "promoted": promoted.name if promoted else None,
        "queue_remaining": max(len(queued) - (1 if promoted else 0), 0),
        "active_count_before": len(actives),
        "queued_count_before": len(queued),
        "dry_run": args.dry_run,
    }
    if skip_reasons:
        plan["skipped_unverified"] = skip_reasons

    if not args.dry_run:
        if demoted is not None:
            try:
                write_status(demoted, demote_status)
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
