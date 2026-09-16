"""Red-first tests: count_authored_steps must not scan past ## Steps.

AC-1: only step headings under ``## Steps`` are returned.
AC-2: the gh-resolve fixture shape yields ``[0, 1, 2, 3, 4]``.
AC-3: a plan with no ``## Steps`` heading falls back to scanning the whole
      body (legacy layout) rather than returning ``[]``.
AC-4: duplicate ``### Step N`` headings *within* ``## Steps`` are still
      reported (dedup is not this sub-plan's job).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable when running this file directly
# under --import-mode=importlib (conftest adds sibling test dirs, but
# ship_audit lives one level up in scripts/).
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ship_audit  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

# The exact shape measured on gh-resolve's
# 2026-09-15d-a-dead-run-must-not-hold-a-claim.md — five authored steps,
# then three ``### Step N`` subheadings under ``## Findings``.
_GH_RESOLVE_BODY = """\
## Steps

### Step 0
test pinning

### Step 1
fix the slug extractor

### Step 2
verify slug extraction

### Step 3
live dry-run sweep on rezmac (2026-09-16)

### Step 4
removal experiment

## Findings

### Step 0
test pinning

### Step 3
live dry-run sweep on rezmac (2026-09-16)

### Step 4
removal experiment
"""


# ── Tests ───────────────────────────────────────────────────────────────────


def test_findings_headings_not_counted_as_authored_steps():
    """AC-1 + AC-2: step headings under ## Findings must not appear.

    A superset/contains assertion would pass with the phantoms still present
    (``[0,0,1,2,3,3,4,4] ⊇ [0,1,2,3,4]`` is true), so exact equality is
    required here.  The FM-0002 lint recommends superset to survive a growing
    accessor — that advice is wrong for this specific predicate because the
    *defect* is a superset.  Recorded here so the next reader does not "fix" it.
    """
    result = ship_audit.count_authored_steps(_GH_RESOLVE_BODY)
    assert result == [0, 1, 2, 3, 4]


def test_legacy_no_steps_heading_falls_back_to_full_body():
    """AC-3: a plan with no ## Steps heading still returns step numbers.

    ``ship_integrity.py:203-204`` treats an empty return as "nothing to
    check" — silently disabling the gate.  The fallback must scan the whole
    body, exactly as the original implementation did.
    """
    legacy_body = """\
### Step 0
first

### Step 1
second

## Findings

### Step 0
note
"""
    result = ship_audit.count_authored_steps(legacy_body)
    # Without the legacy fallback the result is [] and the gate is disabled.
    assert result == [0, 0, 1]


def test_duplicate_headings_within_steps_still_reported():
    """AC-4: duplicate ### Step N under ## Steps is an authoring defect,
    not something to silently deduplicate.

    A plan with two ``### Step 3`` sections under Steps is already broken
    and the doubled entry makes that visible.
    """
    body = """\
## Steps

### Step 0
first

### Step 3
a

### Step 3
b

### Step 4
c
"""
    result = ship_audit.count_authored_steps(body)
    assert result == [0, 3, 3, 4]
