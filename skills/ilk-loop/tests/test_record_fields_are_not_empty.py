"""Tests for the heredoc-emptied record-field detector.

A verification record written with an unquoted heredoc command-substitutes
every backtick.  ``batch-2026-09-15d-batch.md`` lines 17-18 read::

    1.  — added  to schema expected set
    2.  — updated 18 line-number references shifted by batch changes

Every backticked value is gone.  The step-1 gate passes — it reads
``suite_failed`` and the at-base table, neither of which is affected — so the
one artifact a human reads loses its evidence **silently**.

The detector keys on the mangling signature (AC-2): a list item or
``**Field:**`` whose value is empty or begins with a separator (``—``, ``-``,
``:``) where text is required.  A field whitelist would need updating for every
new template and would silently pass the next variant.

AC-3's negatives are pinned first and in the same file: ``_(no failures)_`` and
an empty at-base table are legitimate and must stay green.  This rule's whole
risk is false positives on green records, and a green record wrongly rejected
would block every clean batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_attribution as va  # noqa: E402


# ── the measured shape ──────────────────────────────────────────────────────

MEASURED_EMPTIED_RECORD = """\
# Batch verification — 2026-09-15d

Base: abc123 · HEAD: def456
Command: `python3 -m pytest`
Date: 2026-09-15

## Suite result

**5123 passed, 0 failed, 2 skipped in 180.5s**

## At-base rerun

_(no failures)_

## Fixes landed during this step

1.  — added  to schema expected set (added by batch sub-plan 4 step 2)
2.  — updated 18 line-number references shifted by batch changes
"""


class TestEmptiedFieldDetection:
    """The mangling signature: a list item or field whose value is empty."""

    def test_measured_shape_is_rejected(self) -> None:
        """The exact text from batch-2026-09-15d, lines 17-18."""
        assert va.has_emptied_record_fields(MEASURED_EMPTIED_RECORD)

    def test_bold_field_with_empty_value(self) -> None:
        """`**verified_head:**` with nothing after the colon-on-the-line."""
        # The heredoc mangles `**verified_head:** `sha`` into just `**verified_head:**`
        text = "**verified_head:**\n\n## At-base rerun\n\n_(no failures)_\n"
        assert va.has_emptied_record_fields(text)

    def test_list_item_starting_with_separator(self) -> None:
        """A list item beginning with `— ` or `- ` where text is expected."""
        text = "1.  — added  to schema expected set\n"
        assert va.has_emptied_record_fields(text)

    def test_list_item_with_em_dash_separator(self) -> None:
        """Another measured variant: `2.  — updated 18 line-number references`."""
        text = "2.  — updated 18 line-number references shifted by batch changes\n"
        assert va.has_emptied_record_fields(text)


# ── AC-3: legitimate empty sections are NOT flagged ─────────────────────────

class TestLegitimateEmptiness:
    """Green records have legitimately empty sections."""

    def test_no_failures_marker_is_clean(self) -> None:
        """`_(no failures)_` is the standard green-suite marker."""
        text = "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n"
        assert not va.has_emptied_record_fields(text)

    def test_green_suite_with_full_fields(self) -> None:
        """A complete green record with backticked values intact."""
        text = (
            "# Batch verification\n\n"
            "Base: abc123 · HEAD: `def4567890`\n"
            "Command: `python3 -m pytest -q`\n\n"
            "## Suite result\n\n"
            "**4522 passed, 0 failed, 2 skipped in 192.06s**\n\n"
            "## At-base rerun\n\n_(no failures)_\n"
        )
        assert not va.has_emptied_record_fields(text)

    def test_empty_at_base_table_is_clean(self) -> None:
        """An at-base section with just the table header (0 failures)."""
        text = (
            "suite_failed: 0\n\n"
            "## At-base rerun\n\n"
            "| node id | at base | in baseline_red | attributed |\n"
            "|---|---|---|---|\n"
            "\n_(no failures)_\n"
        )
        assert not va.has_emptied_record_fields(text)

    def test_record_with_real_list_items(self) -> None:
        """List items that have real content (not just a separator)."""
        text = (
            "## Fixes landed during this step\n\n"
            "1. Added `check_verified_tree` to verify_attribution.py\n"
            "2. Updated `batch_gate.py` to write `tree_sha`\n"
        )
        assert not va.has_emptied_record_fields(text)
