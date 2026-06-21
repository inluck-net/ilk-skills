"""Tests for build_pull_new_filter() — the pull-new filter shape.

Regression for escaped-bug 807f35b676ae63ee: Feishu's Bitable API requires
`value` on every filter condition, including `isEmpty`.  The original inline
filter omitted `value` on the `isEmpty` condition, causing every `pull-new`
call to fail with [9499] Missing required parameter.

These tests assert the filter shape directly (Feishu-faithful), not via mocked
client returns — same lesson as test_init_project.py:test_hidden_field_omits_required_regression.
"""
import sys
from pathlib import Path

import pytest

# Ensure the scripts package is importable regardless of cwd.
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cli import build_pull_new_filter  # noqa: E402


class TestPullNewFilterShape:
    """AC-1 / AC-2 / AC-3: the filter dict satisfies Feishu's contract."""

    def test_every_condition_has_value_key(self):
        """AC-1: Feishu rejects any condition without a 'value' key.

        This test FAILS on the pre-fix code (isEmpty condition lacks value).
        """
        filt = build_pull_new_filter()
        for i, cond in enumerate(filt["conditions"]):
            assert "value" in cond, (
                f"condition[{i}] missing 'value' key — Feishu will reject: {cond}"
            )

    def test_isEmpty_condition_carries_empty_list(self):
        """AC-2: the isEmpty condition must be value: []."""
        filt = build_pull_new_filter()
        is_empty = [c for c in filt["conditions"] if c["operator"] == "isEmpty"]
        assert len(is_empty) == 1, f"expected exactly 1 isEmpty condition, got {is_empty}"
        assert is_empty[0] == {
            "field_name": "状态",
            "operator": "isEmpty",
            "value": [],
        }

    def test_xinjian_condition_unchanged(self):
        """AC-3: the 新建 condition is operator='is', value=['新建']."""
        filt = build_pull_new_filter()
        is_xinjian = [c for c in filt["conditions"] if c["operator"] == "is"]
        assert len(is_xinjian) == 1, f"expected exactly 1 'is' condition, got {is_xinjian}"
        assert is_xinjian[0] == {
            "field_name": "状态",
            "operator": "is",
            "value": ["新建"],
        }

    def test_conjunction_is_or(self):
        """The filter uses OR to combine 新建 and isEmpty."""
        filt = build_pull_new_filter()
        assert filt["conjunction"] == "or"

    def test_exactly_two_conditions(self):
        """Sanity: the filter has exactly 2 conditions."""
        filt = build_pull_new_filter()
        assert len(filt["conditions"]) == 2
