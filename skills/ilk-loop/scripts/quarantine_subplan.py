"""Auto-quarantine helper for repeatedly-failing sub-plans (AC-5).

Called by the runner when B2 confirms a local_checks failure.  Tracks
consecutive failures per sub-plan in frontmatter (``auto_block_fails``
counter).  At >= threshold (default 2) it sets ``status: blocked``,
appends a Findings note naming the failing check, and returns
``{blocked: true}``.  Below threshold it returns
``{blocked: false, fails: N}``.

Stdlib only.  Reads/writes with ``encoding='utf-8-sig'``
([[inline-python-open-needs-utf8]]).

Usage::

    python quarantine_subplan.py --plans-dir <dir> --slug <slug> \
        --failing-check "pytest -q" [--threshold 2]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^(---\s*\n)(.*?)(\n---\s*\n)", re.DOTALL)
STATUS_LINE_RE = re.compile(r"^(\s*)status\s*:\s*(\S+)\s*$", re.MULTILINE)
FAILS_LINE_RE = re.compile(r"^(\s*)auto_block_fails\s*:\s*(\d+)\s*$", re.MULTILINE)


def _find_subplan_file(plans_dir: Path, slug: str) -> Path | None:
    """Find the sub-plan file matching a slug (``*-<slug>.md``)."""
    for p in plans_dir.glob(f"*-{slug}.md"):
        if p.name.startswith("MASTER"):
            continue
        return p
    return None


def _read_frontmatter(text: str) -> dict[str, str]:
    """Parse flat key: value frontmatter."""
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


def _set_status(text: str, new_status: str) -> str:
    """Replace ``status:`` value in frontmatter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    fm_block = m.group(2)
    if STATUS_LINE_RE.search(fm_block):
        new_fm = STATUS_LINE_RE.sub(rf"\1status: {new_status}", fm_block, count=1)
    else:
        new_fm = f"status: {new_status}\n" + fm_block
    return m.group(1) + new_fm + m.group(3) + text[m.end():]


def _set_or_bump_fails(text: str, new_count: int) -> str:
    """Set or bump ``auto_block_fails:`` in frontmatter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    fm_block = m.group(2)
    if FAILS_LINE_RE.search(fm_block):
        new_fm = FAILS_LINE_RE.sub(rf"\1auto_block_fails: {new_count}", fm_block, count=1)
    else:
        # Insert after the last line in frontmatter.
        new_fm = fm_block.rstrip() + f"\nauto_block_fails: {new_count}\n"
    return m.group(1) + new_fm + m.group(3) + text[m.end():]


def _append_findings(text: str, note: str) -> str:
    """Append a note under ``## Findings`` if the section exists."""
    marker = "## Findings"
    if marker in text:
        # Append after the section header (or after existing content).
        idx = text.find(marker)
        after_header = idx + len(marker)
        # Find the next newline after the header.
        nl = text.find("\n", after_header)
        if nl < 0:
            nl = after_header
        # Insert the note on the next line.
        return text[:nl + 1] + note + "\n" + text[nl + 1:]
    # No Findings section — append at end.
    return text.rstrip() + "\n\n## Findings\n\n" + note + "\n"


def quarantine_subplan(
    plans_dir: Path,
    slug: str,
    failing_check: str,
    threshold: int = 2,
) -> dict:
    """Check and possibly quarantine a sub-plan.

    Returns a dict with:
      - ``blocked``: True if the sub-plan was just blocked (threshold reached).
      - ``fails``: current consecutive failure count.
      - ``threshold``: the threshold.
      - ``slug``: the slug.
    """
    sub_path = _find_subplan_file(plans_dir, slug)
    if sub_path is None:
        return {"blocked": False, "fails": 0, "threshold": threshold, "slug": slug,
                "error": f"sub-plan file not found for slug '{slug}'"}

    text = sub_path.read_text(encoding="utf-8-sig")
    fm = _read_frontmatter(text)

    # If already blocked, nothing to do.
    if fm.get("status", "").strip() == "blocked":
        return {"blocked": True, "fails": int(fm.get("auto_block_fails", 0)),
                "threshold": threshold, "slug": slug, "already_blocked": True}

    current_fails = int(fm.get("auto_block_fails", "0"))
    new_fails = current_fails + 1

    # Update the counter.
    text = _set_or_bump_fails(text, new_fails)

    if new_fails >= threshold:
        # Quarantine: set blocked + append Findings note.
        text = _set_status(text, "blocked")
        today = date.today().isoformat()
        note = f"- [{today}] auto-quarantined after {new_fails} consecutive local_checks failures. Failing check: `{failing_check}`"
        text = _append_findings(text, note)

        # Atomic write.
        tmp = sub_path.with_suffix(sub_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, sub_path)

        return {"blocked": True, "fails": new_fails, "threshold": threshold, "slug": slug}
    else:
        # Below threshold — just persist the counter bump.
        tmp = sub_path.with_suffix(sub_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, sub_path)

        return {"blocked": False, "fails": new_fails, "threshold": threshold, "slug": slug}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--plans-dir", type=Path, required=True)
    ap.add_argument("--slug", required=True, help="sub-plan slug (e.g. alpha)")
    ap.add_argument("--failing-check", required=True, help="description of the failing check")
    ap.add_argument("--threshold", type=int, default=2,
                    help="consecutive failures before quarantine (default: 2)")
    args = ap.parse_args(argv)

    result = quarantine_subplan(args.plans_dir, args.slug, args.failing_check, args.threshold)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
