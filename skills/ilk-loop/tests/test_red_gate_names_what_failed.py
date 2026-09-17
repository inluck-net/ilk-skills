"""Tests for ship_integrity.py — pinning the two states that collapse into one sentence.

Covers AC-1 through AC-5 for sub-plan a-red-gate-names-what-failed:
  - AC-1: all_passed=false + failing entries → names those commands (positive control)
  - AC-2: all_passed=false + empty results → verdict names the record as unreadable
  - AC-3: neither key present → same unreadable-record verdict
  - AC-4: "(unknown)" never appears in any reason string
  - AC-5: AC-2 and AC-3 remain ok=False
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ship_integrity import evaluate_ship


class TestRedGateNamesWhatFailed:
    """AC-1 through AC-5: the two states that currently collapse."""

    # -- AC-1: positive control — failing entries are named -------------------

    def test_ac1_failing_entries_are_named(self):
        """AC-1: all_passed=false with failing entries names those commands."""
        gate = {
            "all_passed": False,
            "results": [
                {"command": "pytest -q", "passed": False, "exit_code": 1},
                {"command": "mypy skills/", "passed": True, "exit_code": 0},
            ],
        }
        verdict = evaluate_ship(
            "shipped",
            [{"command": "pytest -q"}, {"command": "mypy skills/"}],
            gate,
        )
        assert verdict.ok is False
        assert "pytest -q" in verdict.reason
        # mypy passed, should not appear as a failing check.
        assert "mypy skills/" not in verdict.reason

    # -- AC-2: empty results → unreadable record, not "(unknown)" -------------

    def test_ac2_empty_results_names_record_not_unknown(self):
        """AC-2: all_passed=false with empty results names the record, not (unknown)."""
        gate = {"all_passed": False, "results": []}
        verdict = evaluate_ship(
            "shipped",
            [{"command": "pytest -q"}],
            gate,
        )
        assert verdict.ok is False  # AC-5
        assert "(unknown)" not in verdict.reason  # AC-4
        # Must reference the record shape, not pretend checks are unnamed.
        assert "record" in verdict.reason.lower() or "results" in verdict.reason.lower()

    # -- AC-3: neither key → same unreadable-record verdict ------------------

    def test_ac3_neither_key_same_unreadable_verdict(self):
        """AC-3: a record with neither all_passed nor results → unreadable record."""
        gate: dict = {}
        verdict = evaluate_ship(
            "shipped",
            [{"command": "pytest -q"}],
            gate,
        )
        assert verdict.ok is False  # AC-5
        assert "(unknown)" not in verdict.reason  # AC-4
        # Same class of reason as AC-2.
        assert "record" in verdict.reason.lower() or "results" in verdict.reason.lower()

    # -- AC-4: "(unknown)" never appears — exhaustive check -------------------

    def test_ac4_unknown_never_in_any_reason(self):
        """AC-4: the string (unknown) never appears in any branch's reason."""
        # Every gate shape that the verdict function can receive:
        cases = [
            # Case A: positive control (failing checks named)
            {
                "all_passed": False,
                "results": [
                    {"command": "pytest -q", "passed": False, "exit_code": 1},
                ],
            },
            # Case B: empty results
            {"all_passed": False, "results": []},
            # Case C: results absent
            {"all_passed": False},
            # Case D: neither key
            {},
            # Case E: results present but all passing (green gate — sanity)
            {
                "all_passed": True,
                "results": [
                    {"command": "pytest -q", "passed": True, "exit_code": 0},
                ],
            },
        ]
        checks = [{"command": "pytest -q"}]
        for gate in cases:
            verdict = evaluate_ship("shipped", checks, gate)
            assert "(unknown)" not in verdict.reason, (
                f"(unknown) found in reason for gate={gate!r}: {verdict.reason!r}"
            )

    # -- AC-5: AC-2 and AC-3 remain ok=False ---------------------------------

    def test_ac5_empty_results_remains_blocking(self):
        """AC-5 (explicit): empty results still blocks the ship."""
        gate = {"all_passed": False, "results": []}
        verdict = evaluate_ship("shipped", [{"command": "pytest -q"}], gate)
        assert verdict.ok is False

    def test_ac5_neither_key_remains_blocking(self):
        """AC-5 (explicit): neither key still blocks the ship."""
        gate: dict = {}
        verdict = evaluate_ship("shipped", [{"command": "pytest -q"}], gate)
        assert verdict.ok is False