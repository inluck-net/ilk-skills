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

def _strip_inline_comment(value: str) -> str:
    """Strip a trailing ``# comment`` from an unquoted YAML scalar.

    Only strips when ``#`` is preceded by whitespace (the standard YAML
    inline-comment syntax).  Quoted values and values with no space before
    ``#`` (e.g. ``"a#b"``, ``http://host#frag``) are returned unchanged.
    """
    # Quoted value — leave as-is (the caller or YAML parser handles escapes).
    if value and value[0] in ('"', "'"):
        return value
    # Find first occurrence of <space># that could start a comment.
    idx = value.find(" #")
    if idx >= 0:
        return value[:idx].rstrip()
    return value


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front-matter parser (flat key: value only).

    Returns an empty dict when *text* has no valid front-matter block.

    Inline ``# comments`` are stripped from unquoted scalar values so that
    ``status: queued  # note`` parses as ``"queued"``, matching documented
    template conventions.
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
            v = v.strip()
            v = _strip_inline_comment(v)
            # Strip surrounding quotes (YAML scalar shorthand).
            if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or
                                (v[0] == "'" and v[-1] == "'")):
                v = v[1:-1]
            fm[k.strip()] = v
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


# ── status normalization ────────────────────────────────────────────────────

# The live queue model uses master status: queued | active | shipped.
# Legacy masters may use `status: pending` (the old schema).  Readers
# should treat `pending` as `queued` so old-schema masters are runnable
# rather than invisible.

_RUNNABLE_STATUSES = {"queued", "active"}


def normalize_master_status(raw_status: str) -> str:
    """Normalize a raw master front-matter status for queue-model readers.

    Maps legacy ``pending`` to ``queued``; everything else passes through
    unchanged (lowercased, stripped).  Also strips a trailing inline
    ``# comment`` as a defensive second layer (``parse_frontmatter`` should
    already have done this, but callers may pass raw values).
    """
    s = _strip_inline_comment(raw_status).strip().lower()
    if s == "pending":
        return "queued"
    return s


def is_master_runnable_status(raw_status: str) -> bool:
    """Return True if the (normalized) status is ``queued`` or ``active``."""
    return normalize_master_status(raw_status) in _RUNNABLE_STATUSES


# ── the shared predicate ────────────────────────────────────────────────────

def master_has_nonshipped(master_path: Path, plans_dir: Path) -> bool:
    """Return True if a master has >= 1 non-shipped registered sub-plan.

    A sub-plan is "registered" if its filename appears in the master body
    via ``extract_subplan_files``.  A registered sub-plan whose file is
    MISSING on disk counts as non-shipped (outstanding work) — this
    prevents autoreconcile from false-shipping a master while sub-plan
    files are still being authored.

    Masters with zero registered sub-plans (legacy / malformed) are
    treated as "has non-shipped" so they are never filtered out.
    """
    try:
        master_text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    registered = extract_subplan_files(master_text)
    if not registered:
        # No sub-plan references — treat as "has work" (legacy fallback).
        return True
    for fname in registered:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            return True
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


def reconcile_master_status(master_path: Path, plans_dir: Path) -> bool:
    """Persist ``status: shipped`` when all registered sub-plans are shipped.

    Reads *master_path*, checks via ``is_master_all_shipped``, and if the
    stored status is not already ``shipped``, rewrites **only** the
    ``status:`` line inside the front-matter block.  The rest of the file
    (registry table, body) is byte-for-byte unchanged.

    Returns True if the file was modified (status flipped), False if no
    change was needed (already shipped or not all sub-plans shipped).

    Idempotent: safe to call repeatedly — an already-``shipped`` master
    is a no-op with no rewrite churn.
    """
    if not is_master_all_shipped(master_path, plans_dir):
        return False

    text = master_path.read_text(encoding="utf-8-sig")

    # Already shipped — nothing to do.
    fm = parse_frontmatter(text)
    if fm.get("status", "").strip().lower() == "shipped":
        return False

    # Locate the front-matter block boundaries.
    if not text.startswith("---"):
        return False
    fm_end = text.find("\n---", 3)
    if fm_end < 0:
        return False

    frontmatter = text[3:fm_end]
    rest = text[fm_end:]  # includes the closing --- and everything after

    # Replace only the status: line inside frontmatter.
    new_fm_lines: list[str] = []
    replaced = False
    for line in frontmatter.splitlines(keepends=True):
        if re.match(r"^\s*status\s*:", line):
            new_fm_lines.append("status: shipped\n")
            replaced = True
        else:
            new_fm_lines.append(line)

    if not replaced:
        # No status line in frontmatter — add one at the end.
        new_fm_lines.append("status: shipped\n")

    new_text = "---" + "".join(new_fm_lines) + rest
    master_path.write_text(new_text, encoding="utf-8")
    return True
