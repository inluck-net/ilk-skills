#!/usr/bin/env python3
"""Validate SKILL.md frontmatter against the agentskills.io baseline.

Checks every `skills/*/SKILL.md` under the repo root for:

- a top-level `name:` matching the parent directory,
- a non-empty `description:`,
- absence of a top-level `model:` field (preferred model belongs under
  `metadata.preferred_model`),
- a `description` body shorter than 1024 characters.

Exits 0 if all skills pass, 1 otherwise. Uses only the Python standard
library so it can run anywhere the repo is cloned without extra installs.

See docs/standards/agentskills-io.md for the underlying standards.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DESC_LIMIT = 1024


def parse_frontmatter(text: str) -> dict[str, str] | None:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return None
    body = m.group(1)
    fields: dict[str, str] = {}
    current_key: str | None = None
    folded_lines: list[str] = []
    for line in body.splitlines():
        top = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if top and not line.startswith(" "):
            if current_key is not None:
                fields[current_key] = " ".join(s.strip() for s in folded_lines).strip()
            current_key = top.group(1)
            rest = top.group(2)
            if rest in (">-", ">", "|-", "|"):
                folded_lines = []
            else:
                fields[current_key] = rest.strip()
                current_key = None
                folded_lines = []
        elif current_key is not None and line.startswith("  "):
            folded_lines.append(line)
    if current_key is not None:
        fields[current_key] = " ".join(s.strip() for s in folded_lines).strip()
    return fields


def check_skill(skill_md: Path) -> list[str]:
    errors: list[str] = []
    fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if fm is None:
        return [f"{skill_md}: missing or malformed frontmatter"]
    name = fm.get("name")
    dir_name = skill_md.parent.name
    if not name:
        errors.append(f"{skill_md}: missing `name`")
    elif name != dir_name:
        errors.append(f"{skill_md}: `name: {name}` does not match dir `{dir_name}`")
    desc = fm.get("description", "")
    if not desc:
        errors.append(f"{skill_md}: missing `description`")
    elif len(desc) > DESC_LIMIT:
        errors.append(f"{skill_md}: description is {len(desc)} chars, limit is {DESC_LIMIT}")
    if "model" in fm:
        errors.append(
            f"{skill_md}: top-level `model:` is non-standard; "
            "move to `metadata.preferred_model`"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repo root (defaults to the parent of tools/).",
    )
    args = parser.parse_args()

    skill_dir = args.root / "skills"
    if not skill_dir.is_dir():
        print(f"no skills/ dir at {skill_dir}", file=sys.stderr)
        return 2

    skill_mds = sorted(skill_dir.glob("*/SKILL.md"))
    if not skill_mds:
        print(f"no SKILL.md files under {skill_dir}", file=sys.stderr)
        return 2

    failed = 0
    for skill_md in skill_mds:
        errors = check_skill(skill_md)
        if errors:
            failed += 1
            for err in errors:
                print(err)
        else:
            print(f"OK  {skill_md.relative_to(args.root)}")

    if failed:
        print(f"\n{failed} skill(s) failed checks", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
