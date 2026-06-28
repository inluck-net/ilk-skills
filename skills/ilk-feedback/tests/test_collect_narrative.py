"""Tests for collect.py narrative rendering — AC-1 for detached-output-fixes.

Verifies that _label_narrative never renders the literal "None" when
per-iteration counts are missing (e.g. sentinel-derived classification
where fail_iters_in_window / pass_iters_in_window are absent from facts).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400


# -- AC-1: narrative never contains literal "None" ----------------------------


class TestNarrativeNeverNone:
    """_label_narrative must never interpolate None as a literal string."""

    def test_stuck_with_counts(self):
        """Normal path: counts present → narrative includes them."""
        facts = {
            "fail_iters_in_window": 3,
            "pass_iters_in_window": 1,
            "window_size": 5,
            "iter_at_stop": 7,
        }
        narrative = collect._label_narrative("local-checks-stuck", facts)
        assert "None" not in narrative
        assert "3" in narrative
        assert "1" in narrative

    def test_stuck_without_counts(self):
        """Sentinel path: counts absent → narrative omits the clause, no None."""
        facts = {
            "iter_at_stop": 4,
            "reason": "sentinel terminal state",
        }
        narrative = collect._label_narrative("local-checks-stuck", facts)
        assert "None" not in narrative
        assert "failed in" not in narrative
        assert "acceptance criteria" in narrative.lower() or "AC" in narrative

    def test_broken_with_counts(self):
        """Normal path: counts present → narrative includes them."""
        facts = {
            "fail_iters_in_window": 4,
            "pass_iters_in_window": 0,
            "window_size": 4,
            "iter_at_stop": 6,
        }
        narrative = collect._label_narrative("local-checks-broken", facts)
        assert "None" not in narrative
        assert "4" in narrative

    def test_broken_without_counts(self):
        """Sentinel path: counts absent → narrative omits counts, no None."""
        facts = {
            "iter_at_stop": 3,
            "reason": "sentinel terminal state",
        }
        narrative = collect._label_narrative("local-checks-broken", facts)
        assert "None" not in narrative
        assert "gate COMMAND" in narrative or "gate config" in narrative

    @pytest.mark.parametrize("label", [
        "local-checks-stuck",
        "local-checks-broken",
    ])
    def test_partial_counts_omitted(self, label):
        """When only some count keys are present, the clause is still omitted."""
        facts = {"fail_iters_in_window": 3}  # missing pass and window
        narrative = collect._label_narrative(label, facts)
        assert "None" not in narrative

    @pytest.mark.parametrize("label", [
        "local-checks-stuck",
        "local-checks-broken",
    ])
    def test_empty_facts(self, label):
        """Empty facts dict → no None in narrative."""
        narrative = collect._label_narrative(label, {})
        assert "None" not in narrative

    def test_all_classification_labels_no_none(self):
        """Every label in CLASSIFICATION_LABELS renders without literal 'None'
        when given empty facts."""
        for label in collect.CLASSIFICATION_LABELS:
            narrative = collect._label_narrative(label, {})
            assert "None" not in narrative, (
                f"_label_narrative('{label}', {{}}) contains literal 'None'"
            )
