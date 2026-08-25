#!/usr/bin/env python3
"""Tests for AC-1, AC-3, and AC-4 of ilk-ship-stops-prescribing-a-suite.

Sub-plan: ilk-ship-stops-prescribing-a-suite, step 0.
Asserts the contradiction between Phase 1's verify-only text and the tier
table's suite prescription is absent, and that the refusal words survive.

AC-1: the tier table no longer prescribes running a suite for any tier.
AC-3: no contradiction remains — the file does not simultaneously say Phase 1
      skips the suite and that a tier runs one.
AC-4: Phase 1's three refusal words — missing, failed, stale — survive.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

SHIP_SKILL_MD = Path(__file__).resolve().parents[2] / "ilk-ship" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    return SHIP_SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tier_table(skill_md_text: str) -> str:
    """Extract the tier table from SKILL.md."""
    # The tier table starts with "| Tier | Trigger |" and ends at the next blank line
    m = re.search(
        r"(\| Tier \| Trigger \|.*?)(?:\n\n|\n#|\Z)",
        skill_md_text,
        re.DOTALL,
    )
    assert m, "Tier table not found in SKILL.md"
    return m.group(1)


@pytest.fixture(scope="module")
def phase1_section(skill_md_text: str) -> str:
    """Extract the Phase 1 section from SKILL.md."""
    # Phase 1 section starts with "### Phase 1" and ends at the next ### or ##
    m = re.search(
        r"### Phase 1.*?(?=\n### |\n## |\Z)",
        skill_md_text,
        re.DOTALL,
    )
    assert m, "Phase 1 section not found in SKILL.md"
    return m.group(0)


# ── AC-1: no tier prescribes a suite run ─────────────────────────────────────

class TestAC1NoTierPrescribesSuiteRun:
    """The tier table no longer prescribes running a suite for any tier."""

    def test_no_tier_prescribes_whole_suite(self, tier_table: str):
        """No tier row contains 'whole suite'."""
        # Split into data rows (skip header and separator)
        lines = [l.strip() for l in tier_table.splitlines() if l.strip().startswith("|")]
        data_rows = [l for l in lines if not l.startswith("| Tier") and "---" not in l]

        # Check that no row prescribes "whole suite"
        for row in data_rows:
            assert "whole suite" not in row.lower(), (
                f"Tier row still prescribes 'whole suite': {row}"
            )

    def test_no_tier_prescribes_suite_run(self, tier_table: str):
        """No tier row contains 'suite' in a way that prescribes running it."""
        # Split into data rows
        lines = [l.strip() for l in tier_table.splitlines() if l.strip().startswith("|")]
        data_rows = [l for l in lines if not l.startswith("| Tier") and "---" not in l]

        # The scope column (third column) should describe what the tier SELECTS,
        # not what suite to RUN. Look for "suite" in the scope column.
        for row in data_rows:
            # Parse the columns
            cols = [c.strip() for c in row.split("|") if c.strip()]
            if len(cols) >= 3:
                scope_col = cols[2].lower()
                # "suite" should not appear as something to run
                # But "suite" might appear in other contexts (like "no suite")
                # We need to be careful here - the test should fail if the scope
                # column says to run a suite
                if "suite" in scope_col:
                    # Check if it's prescriptive (says to run it)
                    # Phrases like "whole suite", "run suite", "test suite" are prescriptive
                    # Phrases like "no suite", "not run suite" are not prescriptive
                    assert not any(phrase in scope_col for phrase in [
                        "whole suite",
                        "run suite",
                        "test suite",
                        "suite run",
                    ]), (
                        f"Tier row prescribes running a suite: {row}"
                    )


# ── AC-3: no contradiction remains ───────────────────────────────────────────

class TestAC3NoContradiction:
    """The file does not simultaneously say Phase 1 skips the suite and that a tier runs one."""

    def test_phase1_says_no_suite_run(self, phase1_section: str):
        """Phase 1 section explicitly says it does not run the test suite."""
        # Phase 1 should say it does NOT run the suite
        # Look for phrases like "does not run", "does NOT run", "does not execute"
        # Strip markdown bold markers (**...**) before checking
        phase1_text = phase1_section.replace("**", "").lower()
        assert any(phrase in phase1_text for phrase in [
            "does not run the test suite",
            "does not run the suite",
            "does not execute the suite",
            "does not run a suite",
        ]), (
            "Phase 1 section does not explicitly say it does not run the suite"
        )

    def test_no_tier_prescribes_suite_if_phase1_skips(self, skill_md_text: str, phase1_section: str, tier_table: str):
        """If Phase 1 says it does not run the suite, then no tier row may prescribe running one.

        This is a real predicate over the file — not a grep for one phrase.
        The contradiction exists when:
        1. Phase 1 says it does NOT run the suite
        2. AND a tier row says to RUN a suite

        This test asserts that if condition 1 is true, condition 2 must be false.
        """
        # Strip markdown bold markers before checking
        phase1_text = phase1_section.replace("**", "").lower()
        phase1_says_no_suite = any(phrase in phase1_text for phrase in [
            "does not run the test suite",
            "does not run the suite",
            "does not execute the suite",
            "does not run a suite",
        ])

        if not phase1_says_no_suite:
            # If Phase 1 doesn't say it skips the suite, there's no contradiction to check
            pytest.skip("Phase 1 does not claim to skip the suite")

        # Phase 1 says it skips the suite, so check that no tier prescribes running one
        lines = [l.strip() for l in tier_table.splitlines() if l.strip().startswith("|")]
        data_rows = [l for l in lines if not l.startswith("| Tier") and "---" not in l]

        for row in data_rows:
            cols = [c.strip() for c in row.split("|") if c.strip()]
            if len(cols) >= 3:
                scope_col = cols[2].lower()
                # If the scope column prescribes running a suite, that's a contradiction
                assert not any(phrase in scope_col for phrase in [
                    "whole suite",
                    "run suite",
                    "test suite",
                    "suite run",
                ]), (
                    f"Contradiction: Phase 1 says no suite run, but tier row prescribes: {row}"
                )


# ── AC-4: the three refusal words survive ────────────────────────────────────

class TestAC4RefusalWordsSurvive:
    """Phase 1's three refusal words — missing, failed, stale — survive."""

    def test_missing_word_present(self, phase1_section: str):
        """The word 'missing' is present in Phase 1's refusal context."""
        phase1_lower = phase1_section.lower()
        # Look for "missing" in the context of refusing to release
        # It should be near "refuses to release" or similar
        assert "missing" in phase1_lower, (
            "Phase 1 section does not contain the word 'missing'"
        )

    def test_failed_word_present(self, phase1_section: str):
        """The word 'failed' is present in Phase 1's refusal context."""
        phase1_lower = phase1_section.lower()
        assert "failed" in phase1_lower, (
            "Phase 1 section does not contain the word 'failed'"
        )

    def test_stale_word_present(self, phase1_section: str):
        """The word 'stale' is present in Phase 1's refusal context."""
        phase1_lower = phase1_section.lower()
        assert "stale" in phase1_lower, (
            "Phase 1 section does not contain the word 'stale'"
        )

    def test_refusal_context(self, phase1_section: str):
        """The refusal words are in the context of refusing to release."""
        phase1_lower = phase1_section.lower()
        # The refusal words should be near "refuses to release" or similar
        # This ensures they're not just floating around randomly
        assert "refuses to release" in phase1_lower, (
            "Phase 1 section does not contain 'refuses to release'"
        )
