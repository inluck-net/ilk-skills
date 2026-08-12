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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_slug import (  # noqa: E402
    SUBPLAN_REF_RE,
    strip_date_prefix,
)


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

# Matches sub-plan filenames like 2026-06-06-slug.md (and same-day variants
# like 2026-07-28b-slug.md) at the top level, not in subdirectories.
# Canonical pattern lives in plan_slug.py — do not re-inline it here.
_SUBPLAN_RE = SUBPLAN_REF_RE


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


# ── runnable-aware sibling predicate ─────────────────────────────────────────

_RUNNABLE_SUBPLAN_STATUSES = {"pending", "in-progress"}


def master_has_runnable(master_path: Path, plans_dir: Path) -> bool:
    """Return True if a master has >= 1 sub-plan the loop could pick up.

    Differs from master_has_nonshipped: a ``blocked`` sub-plan is
    outstanding work, but it is NOT runnable — nothing the loop does
    will advance it until a human unblocks it.  The scheduler needs
    "runnable", not "un-shipped", or it dispatches a no-op forever.

    Conservative fallbacks mirror master_has_nonshipped exactly:
    no registered sub-plans → True; a registered file missing on disk →
    True; an unreadable file → ``continue``.

    Any status not in ``_RUNNABLE_SUBPLAN_STATUSES`` and not ``shipped``
    (e.g. ``blocked``, ``skipped``) is treated as not runnable.
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
        status = fm.get("status", "pending")
        if status in _RUNNABLE_SUBPLAN_STATUSES:
            return True
    return False


# ── depends_on-aware drain predicates (L4) ───────────────────────────────────

def _parse_depends_on(raw: str) -> list[str]:
    """Parse the ``depends_on`` front-matter value into a list of slugs.

    Accepts every shape plans actually use:
      - unquoted YAML flow list:  ``[alpha, beta-gamma]``  (the common form)
      - JSON-style quoted list:   ``["alpha", "beta"]``
      - comma-separated bare:     ``alpha, beta``
      - a single bare slug:       ``alpha``
      - empty / whitespace:       ``[]`` / ``""``

    Slugs contain hyphens, so an unquoted flow list is valid YAML but NOT valid
    JSON — ``json.loads('[queue-drain-past-blocked]')`` raises. Relying on a
    JSON parse therefore collapsed the whole ``[...]`` string into one bogus
    slug, which never matched a sibling and falsely stalled the queue
    (2026-06-17, self-hosting). We strip the brackets and split on commas
    instead, which handles quoted and unquoted items uniformly.
    """
    raw = raw.strip()
    if not raw or raw == "[]":
        return []

    def _clean(items: list[str]) -> list[str]:
        out: list[str] = []
        for s in items:
            s = s.strip().strip('"').strip("'").strip()
            if s:
                out.append(s)
        return out

    if raw.startswith("["):
        inner = raw[1:-1] if raw.endswith("]") else raw[1:]
        return _clean(inner.split(","))
    if "," in raw:
        return _clean(raw.split(","))
    return _clean([raw])


def _slug_from_filename(fname: str) -> str:
    """Extract the slug from a sub-plan filename like ``2026-01-01-alpha.md``."""
    slug = fname
    if slug.endswith(".md"):
        slug = slug[:-3]
    return _strip_date_prefix(slug)


def _strip_date_prefix(slug: str) -> str:
    """Strip a leading ``YYYY-MM-DD[<letter>]-`` date prefix from a slug.

    Idempotent: a slug with no date prefix is returned unchanged.
    Thin wrapper over :func:`plan_slug.strip_date_prefix`, kept because
    several modules import this private name.
    """
    return strip_date_prefix(slug)


def subplan_is_runnable(fm: dict[str, str], sibling_statuses: dict[str, str]) -> bool:
    """Return True if a sub-plan is runnable (L4).

    A sub-plan is runnable iff:
      - ``status ∈ {pending, in-progress}``
      - every slug in ``depends_on`` maps to ``shipped`` in *sibling_statuses*

    Parameters
    ----------
    fm:
        The sub-plan's front-matter dict (keys: ``status``, ``depends_on``, …).
    sibling_statuses:
        Mapping of sibling slug → status string for every sibling in the
        same master.  Missing keys are treated as non-shipped.
    """
    status = fm.get("status", "pending").strip()
    if status not in ("pending", "in-progress"):
        return False
    deps = _parse_depends_on(fm.get("depends_on", ""))
    # Normalize sibling keys to the date-stripped form so that a dated
    # depends_on slug (e.g. "2026-06-29-alpha") matches a stripped key
    # ("alpha") — and vice-versa.
    norm_statuses = {_strip_date_prefix(k): v for k, v in sibling_statuses.items()}
    for dep in deps:
        if norm_statuses.get(_strip_date_prefix(dep)) != "shipped":
            return False
    return True


def master_is_drainable(master_path: Path, plans_dir: Path) -> bool:
    """Return True iff the master has >= 1 runnable registered sub-plan (L4).

    A master with only blocked / dep-on-blocked sub-plans is NOT drainable
    (it is *stalled*).  A master with zero registered sub-plans is
    considered drainable (nothing blocks it — legacy / empty masters
    should still be promotable).

    Missing sub-plan files count as runnable (their status can't be read,
    so treat as pending — matching ``master_has_nonshipped`` semantics).
    """
    try:
        master_text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    registered = extract_subplan_files(master_text)
    if not registered:
        return True  # empty master — nothing blocks it

    # Build slug→status map for all registered sub-plans.
    sibling_statuses: dict[str, str] = {}
    for fname in registered:
        slug = _slug_from_filename(fname)
        sub_path = plans_dir / fname
        if not sub_path.exists():
            sibling_statuses[slug] = "pending"  # missing → treat as pending
            continue
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            sibling_statuses[slug] = "pending"
            continue
        sub_fm = parse_frontmatter(sub_text)
        sibling_statuses[slug] = sub_fm.get("status", "pending").strip()

    # Check if any registered sub-plan is runnable.
    for fname in registered:
        slug = _slug_from_filename(fname)
        sub_path = plans_dir / fname
        if not sub_path.exists():
            # Missing file → pending → runnable (dep-free assumed).
            return True
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            return True
        sub_fm = parse_frontmatter(sub_text)
        if subplan_is_runnable(sub_fm, sibling_statuses):
            return True
    return False


def reconcile_master_registry(master_path: Path, plans_dir: Path) -> bool:
    """Rewrite the sub-plan registry table's Status cells from the sub-plan files.

    The registry row duplicates a fact the sub-plan front-matter already owns,
    and nothing kept the copy honest: ``reconcile_master_status`` deliberately
    leaves the table byte-for-byte alone, and the row was only ever updated by
    the agent in prose. So a completed master could carry ``status: shipped`` in
    its front-matter while its only registry row still read ``pending`` — two of
    three sources agreeing and the table dissenting (observed 2026-08-03 on
    ``MASTER-issue-2340-2026-08-03.md``). An external consumer that reads the
    registry to decide whether work is finished concludes it is not.

    Only Status cells of rows referencing a *registered* sub-plan are touched.
    The column is located by name from the table header, so both
    ``| # | Sub-plan | Status |`` and ``| # | Slug | Steps | Status |`` work. A
    registered sub-plan whose file is missing is left alone rather than guessed
    at. Every other byte of the file is preserved.

    Returns True if the file was modified, False if no change was needed.
    Idempotent: a table already in agreement is a no-op with no rewrite churn.
    """
    try:
        text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return False

    registered = extract_subplan_files(text)
    if not registered:
        return False

    # Actual status per registered sub-plan, from the sub-plan file itself.
    actual: dict[str, str] = {}
    for fname in registered:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue  # still being authored — do not invent a status
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        actual[fname] = parse_frontmatter(sub_text).get("status", "pending").strip()
    if not actual:
        return False

    lines = text.splitlines(keepends=True)
    status_idx = None
    changed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            # A table cannot span a non-table line; forget the header.
            status_idx = None
            continue

        cells = line.rstrip("\n").rstrip("\r").split("|")

        # Header row: locate the Status column by name.
        if status_idx is None:
            for idx, cell in enumerate(cells):
                if cell.strip().lower() == "status":
                    status_idx = idx
                    break
            continue

        # Separator row (|---|---|) carries no data.
        if set(stripped.replace("|", "").replace(":", "").strip()) <= {"-"}:
            continue

        if status_idx >= len(cells):
            continue

        row_fname = next((f for f in actual if f in line), None)
        if row_fname is None:
            continue

        want = actual[row_fname]
        if cells[status_idx].strip() == want:
            continue

        cells[status_idx] = f" {want} "
        rebuilt = "|".join(cells)
        # Preserve the original line ending.
        if line.endswith("\r\n"):
            rebuilt += "\r\n"
        elif line.endswith("\n"):
            rebuilt += "\n"
        lines[i] = rebuilt
        changed = True

    if not changed:
        return False

    master_path.write_text("".join(lines), encoding="utf-8")
    return True


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
