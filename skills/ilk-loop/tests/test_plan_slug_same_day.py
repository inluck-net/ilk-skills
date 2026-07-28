#!/usr/bin/env python3
"""Same-day plan slugs (``YYYY-MM-DD<letter>-<slug>``) must round-trip everywhere.

More than one batch can be planned on a single day, disambiguated by a
trailing letter on the date: ``2026-07-28-wire-push-and-pr`` alongside
``2026-07-28b-doctor-is-a-gate``.

The date shape was duplicated as an inline literal across eight call sites in
four skills. When the letter suffix was introduced only ``loop_status``'s
discovery regex was updated, so the loop could *find* a ``2026-07-28b-*``
sub-plan while five other parsers failed to parse the same filename — a
partial fix that turns a clean "file not found" into a half-parsed state.
These tests pin every site to plan_slug.py so the shape cannot drift again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import plan_slug  # noqa: E402

# Same-day (letter) and plain forms must both work everywhere.
SAME_DAY_SLUG = "2026-07-28b-doctor-is-a-gate"
SAME_DAY_FILE = SAME_DAY_SLUG + ".md"
PLAIN_SLUG = "2026-07-28-wire-push-and-pr"
PLAIN_FILE = PLAIN_SLUG + ".md"


# ── the shared primitives ────────────────────────────────────────────────────

@pytest.mark.parametrize("slug,expected", [
    (SAME_DAY_SLUG, "doctor-is-a-gate"),
    (PLAIN_SLUG, "wire-push-and-pr"),
    ("2026-07-28z-x", "x"),
    ("no-date-here", "no-date-here"),          # idempotent passthrough
    ("doctor-is-a-gate", "doctor-is-a-gate"),
])
def test_strip_date_prefix(slug, expected):
    assert plan_slug.strip_date_prefix(slug) == expected


def test_strip_date_prefix_is_idempotent():
    once = plan_slug.strip_date_prefix(SAME_DAY_SLUG)
    assert plan_slug.strip_date_prefix(once) == once


def test_split_date_prefix_keeps_the_letter_with_the_date():
    assert plan_slug.split_date_prefix(SAME_DAY_SLUG) == ("2026-07-28b", "doctor-is-a-gate")
    assert plan_slug.split_date_prefix("no-date") is None


@pytest.mark.parametrize("slug,expected", [
    (SAME_DAY_SLUG, True), (PLAIN_SLUG, True), ("combat-vfx", False),
])
def test_has_date_prefix(slug, expected):
    assert plan_slug.has_date_prefix(slug) is expected


@pytest.mark.parametrize("pattern_name", ["SUBPLAN_REF_RE", "SUBPLAN_REF_OPTIONAL_MD_RE"])
def test_ref_patterns_match_same_day_filenames(pattern_name):
    rx = getattr(plan_slug, pattern_name)
    assert rx.findall(f"| {SAME_DAY_FILE} | pending |") == [SAME_DAY_FILE]
    assert rx.findall(f"| {PLAIN_FILE} | shipped |") == [PLAIN_FILE]


def test_ref_patterns_still_reject_subdirectory_paths():
    """The `/` exclusion is load-bearing — don't regress it."""
    assert plan_slug.SUBPLAN_REF_RE.findall(f"docs/plans/{SAME_DAY_FILE}") == []


# ── every consumer must agree with the shared primitives ─────────────────────

def test_loop_status_discovers_and_slugs_a_same_day_subplan(tmp_path):
    """loop_status must both FIND the file and parse its slug.

    Regression: discovery was fixed at one site while the slug-stripping a
    few hundred lines later still used the old pattern.
    """
    import loop_status
    master = (
        "---\ntitle: t\nstatus: active\nmaster_plan: 2026-07-28b-master\n---\n\n"
        "# MASTER\n\n| plan | status |\n|---|---|\n"
        f"| {SAME_DAY_FILE} | pending |\n| {PLAIN_FILE} | shipped |\n"
    )
    order = loop_status.extract_master_order(master)
    assert SAME_DAY_FILE in order, f"same-day sub-plan not discovered: {order}"
    assert PLAIN_FILE in order
    assert loop_status.strip_date_prefix(SAME_DAY_SLUG) == "doctor-is-a-gate"


def test_plan_status_extracts_and_strips_same_day(tmp_path):
    import plan_status
    master = f"# MASTER\n\n| {SAME_DAY_FILE} | pending |\n"
    assert plan_status.extract_subplan_files(master) == [SAME_DAY_FILE]
    assert plan_status._strip_date_prefix(SAME_DAY_SLUG) == "doctor-is-a-gate"


def test_promote_next_master_slug_and_prefix_detection():
    import promote_next_master as p
    assert p._slug_from_filename(SAME_DAY_FILE) == "doctor-is-a-gate"
    assert p.has_date_prefix(SAME_DAY_FILE) is True


def test_status_progress_date_prefix_matches_same_day():
    """The dashboard strips a common date prefix for compact display."""
    sys.path.insert(0, str(_SCRIPTS.parent.parent / "ilk-launcher" / "scripts"))
    import status_progress
    m = status_progress._DATE_PREFIX_RE.match(SAME_DAY_SLUG)
    assert m is not None, "dashboard cannot parse a same-day slug"
    assert m.group(1) == "2026-07-28b"
    assert m.group(2) == "doctor-is-a-gate"


def test_collect_subplan_ref_re_matches_same_day():
    """Postmortem scoping resolves the master's registry refs."""
    sys.path.insert(0, str(_SCRIPTS.parent.parent / "ilk-feedback" / "scripts"))
    import collect
    assert collect._subplan_ref_re().findall(f"| {SAME_DAY_FILE} |") == [SAME_DAY_FILE]


def test_no_stale_inline_date_literals_remain():
    """Guard the whole point: no consumer may re-inline the date shape.

    plan_slug.py is the only file allowed to spell out the date regex.
    A bare ``\\d{4}-\\d{2}-\\d{2}-`` (no optional letter) is the exact bug
    this batch fixed, so fail on it anywhere else in the shipped scripts.
    """
    import re as _re
    bad_literal = _re.compile(r"\\d\{4\}-\\d\{2\}-\\d\{2\}-")
    root = _SCRIPTS.parent.parent          # skills/
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "plan_slug.py" or "/tests/" in py.as_posix():
            continue
        if bad_literal.search(py.read_text(encoding="utf-8")):
            offenders.append(py.relative_to(root).as_posix())
    assert not offenders, (
        "these files re-inline the date-prefix regex without the optional "
        f"same-day letter; import from plan_slug instead: {offenders}"
    )
