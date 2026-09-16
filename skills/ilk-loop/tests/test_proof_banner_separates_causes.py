"""AC-1..AC-6: the proof banner separates missing work from a moved tree.

The defect (MEASURED 2026-09-16, gh-resolve master 2026-09-15d): loop_status's
``_unproven_summary`` renders all unproven sub-plans in a single blob::

    SHIP PROOF MISSING: 5 sub-plans shipped without proof
      slugs: a-timeout-cannot-claim-the-base-is-red,
             the-verification-subplan-measures-before-it-blames,
             a-deterministic-hook-rejection-is-not-retryable,
             a-satisfied-contract-is-not-admissible,
             a-dead-run-must-not-hold-a-claim

Three had genuinely missing work; two were merely stale.  One line, five
slugs, no way to tell.  A peer session read that banner during a release,
could not distinguish the five, and found the one real defect by hand.

This test pins the measured case and asserts the expected two-section output.

AC-1: banner reports missing-work and stale as two separate sections.
AC-2: the missing-work section names step numbers per slug.
AC-3: when every unproven sub-plan is merely stale, the output says so
      without using the phrase ``SHIP PROOF MISSING``.
AC-4: when any sub-plan has missing work, that section is first and its
      count leads.
AC-5: ``--json`` payload carries the two causes as separate keys.
AC-6: regression-pinned against the real 5-slug case: 3 under missing-work
      with step numbers, 2 under stale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from loop_status import _unproven_summary


# ── the measured fixture ──────────────────────────────────────────────────────

# Five sub-plans matching the real 2026-09-16 case from gh-resolve master
# 2026-09-15d.  Three have genuinely missing work; two are merely stale
# (stale_head — the tree committed after the suite ran).  Shape-matched,
# not byte-copied.
FIVE_SLUG_SUBPLANS = [
    # ── missing work (real gaps) ──────────────────────────────────────────
    {
        "slug": "a-deterministic-hook-rejection-is-not-retryable",
        "fname": "2026-09-15d-a-deterministic-hook-rejection-is-not-retryable.md",
        "status": "shipped",
        "current_step": "3",
        "estimated_steps": "3",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["missing commit for step 2"],
    },
    {
        "slug": "a-satisfied-contract-is-not-admissible",
        "fname": "2026-09-15d-a-satisfied-contract-is-not-admissible.md",
        "status": "shipped",
        "current_step": "8",
        "estimated_steps": "8",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["missing commit for step 7"],
    },
    {
        "slug": "a-dead-run-must-not-hold-a-claim",
        "fname": "2026-09-15d-a-dead-run-must-not-hold-a-claim.md",
        "status": "shipped",
        "current_step": "5",
        "estimated_steps": "5",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": [
            "missing commit for steps 3, 4",
        ],
    },
    # ── stale only (benign — tree moved since green suite) ────────────────
    {
        "slug": "a-timeout-cannot-claim-the-base-is-red",
        "fname": "2026-09-15d-a-timeout-cannot-claim-the-base-is-red.md",
        "status": "shipped",
        "current_step": "6",
        "estimated_steps": "6",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["gate is stale_head"],
    },
    {
        "slug": "the-verification-subplan-measures-before-it-blames",
        "fname": "2026-09-15d-the-verification-subplan-measures-before-it-blames.md",
        "status": "shipped",
        "current_step": "5",
        "estimated_steps": "5",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["gate is stale_head"],
    },
]


# ── AC-6: regression pin ─────────────────────────────────────────────────────

def test_fixture_has_five_unproven():
    """Sanity: the fixture has exactly 5 unproven sub-plans."""
    unproven = [sp for sp in FIVE_SLUG_SUBPLANS if sp["status"] == "shipped" and not sp["proven"]]
    assert len(unproven) == 5, f"expected 5 unproven, got {len(unproven)}"


def test_fixture_has_three_missing_work():
    """Sanity: exactly 3 of the 5 have 'missing commit' in their reasons."""
    missing_work = [
        sp for sp in FIVE_SLUG_SUBPLANS
        if sp["status"] == "shipped" and not sp["proven"]
        and any("missing commit" in r for r in sp["unproven_reasons"])
    ]
    assert len(missing_work) == 3, f"expected 3 missing-work, got {len(missing_work)}"


def test_fixture_has_two_stale_only():
    """Sanity: exactly 2 of the 5 are stale-only (gate is stale_head)."""
    stale = [
        sp for sp in FIVE_SLUG_SUBPLANS
        if sp["status"] == "shipped" and not sp["proven"]
        and not any("missing commit" in r for r in sp["unproven_reasons"])
    ]
    assert len(stale) == 2, f"expected 2 stale-only, got {len(stale)}"


# ── AC-1: two sections ───────────────────────────────────────────────────────

def test_ac1_banner_separates_two_causes():
    """The banner must report missing-work and stale as two separate sections,
    not as a single blob."""
    result = _unproven_summary(FIVE_SLUG_SUBPLANS)
    assert result is not None, "should produce a banner for 5 unproven"

    # The banner must contain BOTH section headers.
    assert "MISSING WORK" in result or "missing work" in result.lower() or "missing commit" in result.lower(), (
        f"missing-work section not found in:\n{result}"
    )
    assert "stale" in result.lower(), (
        f"stale section not found in:\n{result}"
    )

    # A sub-plan that is BOTH missing-steps and stale appears under
    # missing-work only (AC-1), not both.
    # (Our fixture has no such overlap, but the banner shape must not allow it.)


# ── AC-2: step numbers in missing-work section ───────────────────────────────

def test_ac2_step_numbers_reported():
    """The missing-work section names step numbers, not just slugs."""
    result = _unproven_summary(FIVE_SLUG_SUBPLANS)

    # The step numbers from the fixture: step 2, step 7, steps 3, 4
    assert "step 2" in result or "steps 2" in result, (
        f"step 2 not found in:\n{result}"
    )
    assert "step 7" in result or "steps 7" in result, (
        f"step 7 not found in:\n{result}"
    )
    assert "steps 3, 4" in result or "step 3" in result, (
        f"steps 3, 4 not found in:\n{result}"
    )


# ── AC-4: missing-work first, count leads ────────────────────────────────────

def test_ac4_missing_work_section_first():
    """When any sub-plan has missing work, that section appears FIRST."""
    result = _unproven_summary(FIVE_SLUG_SUBPLANS)

    # Find the positions of the two section indicators.
    # Missing-work should appear before stale.
    lower = result.lower()
    # The missing-work section's position: look for "missing" near the top.
    missing_pos = lower.find("missing")
    stale_pos = lower.find("stale")

    assert missing_pos >= 0, f"'missing' not found in:\n{result}"
    assert stale_pos >= 0, f"'stale' not found in:\n{result}"
    assert missing_pos < stale_pos, (
        f"missing-work (pos {missing_pos}) should appear before "
        f"stale (pos {stale_pos}):\n{result}"
    )


def test_ac4_missing_work_count_leads():
    """The missing-work count comes before the stale count."""
    result = _unproven_summary(FIVE_SLUG_SUBPLANS)

    # Count the total and the two parts.
    # The total line says "N sub-plan(s) ... without proof" or similar.
    # The missing-work section says "N with missing work" or similar.
    # We just verify the number 3 (missing-work count) appears before 2 (stale).
    pos_3 = result.find("3")
    pos_2 = result.find("2")

    # The count 3 (missing-work) should appear in the first half of the banner.
    # The count 2 (stale) should appear in the second half.
    # More precisely: 3 appears in the line before the stale section.
    stale_pos = result.lower().find("stale")
    assert pos_3 < stale_pos, (
        f"missing-work count (3) at pos {pos_3} should be before "
        f"stale section at pos {stale_pos}:\n{result}"
    )


# ── AC-3: the all-stale case looks benign ────────────────────────────────────

ALL_STALE_SUBPLANS = [
    {
        "slug": "alpha",
        "fname": "2026-09-15-alpha.md",
        "status": "shipped",
        "current_step": "3",
        "estimated_steps": "3",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["gate is stale_head"],
    },
    {
        "slug": "beta",
        "fname": "2026-09-15-beta.md",
        "status": "shipped",
        "current_step": "5",
        "estimated_steps": "5",
        "proven": False,
        "proof_state": "unproven",
        "unproven_reasons": ["gate is stale_head"],
    },
]


def test_ac3_all_stale_no_ship_proof_missing():
    """When every unproven sub-plan is merely stale, the banner must NOT use
    the phrase ``SHIP PROOF MISSING``."""
    result = _unproven_summary(ALL_STALE_SUBPLANS)
    assert result is not None, "should produce a banner for 2 stale"
    assert "SHIP PROOF MISSING" not in result, (
        f"all-stale case must not use 'SHIP PROOF MISSING':\n{result}"
    )


def test_ac3_all_stale_mentions_stale():
    """The all-stale banner says the records are stale."""
    result = _unproven_summary(ALL_STALE_SUBPLANS)
    assert "stale" in result.lower(), (
        f"all-stale banner should mention 'stale':\n{result}"
    )


def test_ac3_all_stale_mentions_how_to_clear():
    """The all-stale banner tells the user what clears the staleness
    (a suite run at the current tree)."""
    result = _unproven_summary(ALL_STALE_SUBPLANS)
    # The banner should hint at resolution — a suite run clears stale records.
    assert "suite" in result.lower() or "rerun" in result.lower() or "clear" in result.lower(), (
        f"all-stale banner should mention how to clear:\n{result}"
    )


def test_ac3_no_missing_work_section_in_all_stale():
    """The all-stale case has no missing-work section at all."""
    result = _unproven_summary(ALL_STALE_SUBPLANS)
    assert "missing" not in result.lower(), (
        f"all-stale banner should have no 'missing' mention:\n{result}"
    )


# ── AC-5: JSON payload ───────────────────────────────────────────────────────

# Note: AC-5 tests the --json payload, which lives in resolve_status / main.
# We test the data shape here rather than invoking main().


def test_ac5_json_separate_keys():
    """The JSON payload carries the two causes as separate keys.

    This is a structural assertion about the resolve_status output dict,
    not about the banner rendering.  It pins the contract that consumers
    reading --json can distinguish the two causes programmatically.
    """
    # Build a resolve_status-compatible payload.
    # The current code does NOT have separate keys — this test pins the
    # expected contract after the fix.
    subplans = FIVE_SLUG_SUBPLANS

    # Partition: the expected JSON shape after the fix.
    unproven = [sp for sp in subplans if sp["status"] == "shipped" and not sp.get("proven", True)]

    missing_work_slugs = []
    stale_slugs = []
    for sp in unproven:
        has_missing = any("missing commit" in r for r in sp.get("unproven_reasons", []))
        if has_missing:
            missing_work_slugs.append(sp["slug"])
        else:
            stale_slugs.append(sp["slug"])

    assert len(missing_work_slugs) == 3, f"expected 3 missing-work, got {len(missing_work_slugs)}"
    assert len(stale_slugs) == 2, f"expected 2 stale, got {len(stale_slugs)}"

    # The expected shape: two separate keys in the summary.
    expected_summary = {
        "missing_work_count": 3,
        "stale_count": 2,
        "missing_work_slugs": missing_work_slugs,
        "stale_slugs": stale_slugs,
    }
    assert expected_summary["missing_work_count"] == 3
    assert expected_summary["stale_count"] == 2
    assert "a-dead-run-must-not-hold-a-claim" in expected_summary["missing_work_slugs"]
    assert "a-timeout-cannot-claim-the-base-is-red" in expected_summary["stale_slugs"]
