#!/usr/bin/env python3
"""Generate a synthetic ILK_DATA_HOME fixture for drain-past-blocked testing.

Creates a project under ``<out>/projects/<key>/plans/`` with masters and
sub-plans described by a JSON spec.  Idempotent and deterministic (dates
are passed in, not generated).

Spec format (JSON)::

    {
      "project_key": "test-proj",
      "masters": [
        {
          "filename": "MASTER-2026-06-01-alpha.md",
          "status": "active",
          "created": "2026-06-01T00:00:00+08:00",
          "priority": 1,
          "sub_plans": [
            {
              "filename": "2026-06-01-alpha-a.md",
              "slug": "alpha-a",
              "status": "pending",
              "estimated_steps": 3,
              "current_step": 0,
              "depends_on": []
            }
          ]
        }
      ]
    }

Usage::

    python gen_mock_masters.py --spec '{"project_key": "...", "masters": [...]}'
    python gen_mock_masters.py --spec-file fixture.json --out /tmp/fixture

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_slug import strip_date_prefix as _strip_date_prefix  # noqa: E402


def _master_template(m: dict) -> str:
    """Build a MASTER-*.md file from a spec entry."""
    # Derive a human-readable plan name.  If not provided, use the slug
    # portion of the filename (strip MASTER- prefix and .md suffix) but
    # replace date-prefixed slugs with just the slug part to avoid the
    # title matching the sub-plan regex (which looks for YYYY-MM-DD-*.md).
    default_plan = m["filename"].replace("MASTER-", "").replace(".md", "")
    # Strip the date prefix if present: 2026-06-01-m1 → m1
    default_plan = _strip_date_prefix(default_plan)
    plan = m.get("plan", default_plan)
    created = m.get("created", "2026-06-01T00:00:00+08:00")
    status = m.get("status", "active")
    priority = m.get("priority", "")

    # Build sub-plan registry rows.
    rows: list[str] = []
    for sp in m.get("sub_plans", []):
        fname = sp["filename"]
        steps = sp.get("estimated_steps", 1)
        sp_status = sp.get("status", "pending")
        rows.append(f"| {len(rows)+1} | [{fname}](./{fname}) | {steps} | {sp_status} |")

    registry = "\n".join(rows) if rows else "| | (none) | | |"

    # For the master_plan frontmatter value, use the original slug (with date)
    # since that's what the convention expects.  The title uses the cleaned
    # name to avoid matching the sub-plan regex.
    raw_plan = m.get("plan", m["filename"].replace("MASTER-", "").replace(".md", ""))
    lines = [
        "---",
        f"master_plan: {raw_plan}",
        f"batch_date: {created[:10]}",
        f"status: {status}",
    ]
    if priority:
        lines.append(f"priority: {priority}")
    lines.append(f"created: {created}")
    lines += [
        "---",
        "",
        f"# MASTER plan: {plan}",
        "",
        "## Sub-plan registry",
        "",
        "| # | Slug | Steps | Status |",
        "|---|---|---|---|",
        registry,
        "",
    ]
    return "\n".join(lines)


def _subplan_template(sp: dict) -> str:
    """Build a sub-plan .md file from a spec entry."""
    slug = sp.get("slug", sp["filename"].replace(".md", ""))
    status = sp.get("status", "pending")
    current_step = sp.get("current_step", 0)
    estimated_steps = sp.get("estimated_steps", 1)
    last_updated = sp.get("last_updated", "2026-06-01")
    depends_on = sp.get("depends_on", [])
    dep_str = json.dumps(depends_on) if depends_on else "[]"

    lines = [
        "---",
        f"plan: {slug}",
        f"status: {status}",
        f"current_step: {current_step}",
        f"estimated_steps: {estimated_steps}",
        f"last_updated: {last_updated}",
        f"depends_on: {dep_str}",
        "---",
        "",
        f"# Sub-plan: {slug}",
        "",
        f"Status: {status}.  Depends on: {', '.join(depends_on) if depends_on else '(none)'}.",
        "",
    ]
    return "\n".join(lines)


def generate_fixture(spec: dict, out: Path) -> Path:
    """Write the fixture under *out* and return the plans dir path.

    Writes to ``<out>/docs/plans/`` and creates a ``<out>/.git`` marker
    so ``find_plans_dir`` can discover the plans via the walk-up
    fallback.  Also sets ``ILK_DATA_HOME`` to *out* so the external-
    resolution path works if a project root is found.

    Returns ``<out>/docs/plans/``.
    """
    project_key = spec.get("project_key", "test-proj")
    plans_dir = out / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Create a .git marker so find_project_root(out) returns out as root.
    (out / ".git").mkdir(exist_ok=True)

    for m in spec.get("masters", []):
        master_path = plans_dir / m["filename"]
        master_path.write_text(_master_template(m), encoding="utf-8")

        for sp in m.get("sub_plans", []):
            sp_path = plans_dir / sp["filename"]
            sp_path.write_text(_subplan_template(sp), encoding="utf-8")

    return plans_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", type=str, default=None,
                    help="JSON spec string (inline)")
    ap.add_argument("--spec-file", type=Path, default=None,
                    help="Path to JSON spec file")
    ap.add_argument("--out", type=Path, default=Path.cwd(),
                    help="Output root directory (default: cwd)")
    args = ap.parse_args(argv)

    if args.spec_file:
        spec = json.loads(args.spec_file.read_text(encoding="utf-8"))
    elif args.spec:
        spec = json.loads(args.spec)
    else:
        print("ERROR: provide --spec or --spec-file", file=sys.stderr)
        return 2

    plans_dir = generate_fixture(spec, args.out)
    print(json.dumps({"plans_dir": str(plans_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
