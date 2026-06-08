"""Shared master-status helpers for ilk-loop and ilk-watchdog.

Centralizes the "master has non-shipped sub-plans" predicate so that
``loop_status.py``, ``scheduler_scan.py``, and ``promote_next_master.py``
all agree on what "all shipped" means.

Also provides the common ``parse_frontmatter`` and ``extract_subplan_files``
utilities that were previously duplicated across those modules.

Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path


# ── front-matter parsing ────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front-matter parser (flat key: value only).

    Returns an empty dict when *text* has no valid front-matter block.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for raw in text[3:end].splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("- "):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


# ── sub-plan filename extraction ────────────────────────────────────────────

# Matches sub-plan filenames like 2026-06-06-slug.md at the top level
# (not in subdirectories).  The lookbehind ensures the preceding character
# is a non-path separator (start-of-line, whitespace, bracket, paren, or
# ``./``) — but NOT ``/``, which would indicate a subdirectory.
_SUBPLAN_RE = re.compile(
    r"(?:^|(?<=[\s(\[|]))(?:\./)?(\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*\.md)",
    re.MULTILINE,
)


def extract_subplan_files(master_text: str) -> list[str]:
    """Return ordered, deduped list of sub-plan filenames as they appear in
    the master plan body (registry table).  Excludes the master itself.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for f in _SUBPLAN_RE.findall(master_text):
        if f.startswith("MASTER"):
            continue
        if f in seen:
            continue
        seen.add(f)
        ordered.append(f)
    return ordered


# ── the shared predicate ────────────────────────────────────────────────────

def master_has_nonshipped(master_path: Path, plans_dir: Path) -> bool:
    """Return True if a master has >= 1 non-shipped registered sub-plan.

    A sub-plan is "registered" if its filename appears in the master body
    via ``extract_subplan_files``.  Missing sub-plan files are skipped
    (not counted as non-shipped).
    """
    try:
        master_text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    for fname in extract_subplan_files(master_text):
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        fm = parse_frontmatter(sub_text)
        if fm.get("status", "pending") != "shipped":
            return True
    return False


def is_master_all_shipped(master_path: Path, plans_dir: Path) -> bool:
    """Inverse of ``master_has_nonshipped``."""
    return not master_has_nonshipped(master_path, plans_dir)
