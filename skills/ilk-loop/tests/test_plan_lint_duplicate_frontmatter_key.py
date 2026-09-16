"""Tests for lint_duplicate_frontmatter_key — duplicate-key frontmatter lint.

A sub-plan's frontmatter can carry the same key twice. The two readers in
routine use disagree about which wins:

- ``grep -m1 '^status:'`` returns the **first**;
- ``parse_frontmatter`` returns the **last**.

MEASURED 2026-09-16: of 27 ``2026-09-15*`` plan files in gh-resolve, exactly
**1** has two ``status:`` keys — ``grep -m1`` read ``pending``, the parser read
``shipped``.  A peer session read that file twice, twenty minutes apart, and
reported "fields reset between two reads" — a phantom rewrite caused entirely
by the two readers disagreeing about an unchanged file.

This lint catches the class: any top-level key appearing twice in the
frontmatter block is a HARD finding naming the key, both values, both line
numbers, and which value the runtime uses (the last).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


def _plan(fm_body: str, body: str = "") -> str:
    """Build a plan text with the given frontmatter body and optional markdown body."""
    return f"---\n{fm_body}---\n\n{body}"


# ── the measured case: two status keys ──────────────────────────────────────

class TestDuplicateKeyDetection:
    """The positive cases: a key appearing twice in frontmatter."""

    def test_two_status_keys_flagged(self) -> None:
        """Reproduce the measured shape: status at top and status six lines later."""
        text = _plan(
            "plan: x\n"
            "status: pending\n"
            "current_step: 0\n"
            "tickets: []\n"
            "priority: P1\n"
            "estimated_steps: 5\n"
            "status: shipped\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert len(findings) == 1
        f = findings[0]
        assert f.startswith("HARD")
        assert "status" in f
        assert "pending" in f
        assert "shipped" in f

    def test_finding_names_both_line_numbers(self) -> None:
        """AC-2: the finding says which lines the key appears on."""
        text = _plan(
            "plan: x\n"
            "status: pending\n"       # line 2
            "current_step: 0\n"
            "status: shipped\n"       # line 4
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert len(findings) == 1
        f = findings[0]
        # Line numbers should be present (1-indexed within frontmatter).
        assert "2" in f or "line 2" in f
        assert "4" in f or "line 4" in f

    def test_finding_says_which_value_the_runtime_uses(self) -> None:
        """AC-3: the finding states that the runtime uses the LAST value."""
        text = _plan(
            "plan: x\n"
            "status: pending\n"
            "status: shipped\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert len(findings) == 1
        f = findings[0]
        assert "last" in f.lower() or "runtime" in f.lower()

    def test_any_key_not_just_status(self) -> None:
        """AC-1: any duplicate key is flagged, not just status."""
        text = _plan(
            "plan: x\n"
            "current_step: 0\n"
            "current_step: 3\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert len(findings) == 1
        assert "current_step" in findings[0]

    def test_multiple_different_duplicates(self) -> None:
        """Two different keys each duplicated → two findings."""
        text = _plan(
            "plan: x\n"
            "status: pending\n"
            "status: shipped\n"
            "current_step: 0\n"
            "current_step: 3\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert len(findings) == 2


# ── false-positive risk: things that are NOT duplicates ─────────────────────

class TestNotDuplicates:
    """Negative cases — the rule's risk is false positives."""

    def test_key_in_frontmatter_and_body_is_not_duplicate(self) -> None:
        """The body is outside the frontmatter block."""
        text = _plan(
            "plan: x\nstatus: pending\n",
            body="## Steps\n\nstatus: shipped\n",
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []

    def test_key_in_fenced_code_block_is_not_duplicate(self) -> None:
        """A key inside a ```yaml block in the body is not a frontmatter duplicate."""
        text = _plan(
            "plan: x\nstatus: pending\n",
            body="```yaml\nstatus: shipped\n```\n",
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []

    def test_list_item_looking_like_key_value(self) -> None:
        """A list item `- key: value` is not a top-level key."""
        text = _plan(
            "plan: x\n"
            "tickets:\n"
            "  - status: open\n"
            "  - status: closed\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []

    def test_nested_mapping_key_not_top_level(self) -> None:
        """A key under a different parent block is not a duplicate of a top-level key."""
        text = _plan(
            "plan: x\n"
            "expected_entities:\n"
            "  migrations: []\n"
            "  db_tables: []\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []

    def test_single_occurrence_is_clean(self) -> None:
        """The normal case: each key appears once."""
        text = _plan(
            "plan: x\n"
            "status: pending\n"
            "current_step: 0\n"
            "tickets: []\n"
            "priority: P1\n"
            "estimated_steps: 5\n"
        )
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []

    def test_no_frontmatter_is_clean(self) -> None:
        """A file with no frontmatter block has no frontmatter duplicates."""
        text = "# Just a heading\n\nSome body text.\n"
        findings = plan_lint.lint_duplicate_frontmatter_key(text, "test-slug")
        assert findings == []


# ── registered in ALL_CHECKS ────────────────────────────────────────────────

class TestRegisteredInAllChecks:
    """AC-4: the lint fires through plan_lint.py's CLI, not only as a callable."""

    def test_in_all_checks(self) -> None:
        """The function is registered in ALL_CHECKS."""
        assert plan_lint.lint_duplicate_frontmatter_key in plan_lint.ALL_CHECKS
