"""Tests for integrity_violation.parse — AC-1 for a-gate-edit-is-named-in-the-diagnosis.

Verifies that the parser correctly extracts kind and slug from the three
runner VIOLATION line formats and the [gates] marker line, deduplicates
on (kind, slug), and ignores non-VIOLATION lines.

Each test calls parse() inside the function so collection succeeds before
the module exists (import deferred to test body).

All tests are xfail at base because the integrity_violation module does
not yet exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# -- AC-1: parse returns the correct kind and slug for each runner format ------

class TestParseViolationFormats:
    """parse() extracts kind/slug from the three runner VIOLATION formats."""

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_gates_edited(self):
        """Line at run_ilk_loop_claude.sh:5910 — gates-edited."""
        from integrity_violation import parse

        line = "  ! [ship-integrity VIOLATION] the worker changed gates for ticket-attachment-projector; restored to the pre-dispatch snapshot (rc=3):"
        result = parse([line])
        assert len(result) == 1
        assert result[0]["kind"] == "gates-edited"
        assert result[0]["slug"] == "ticket-attachment-projector"

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_master_edited(self):
        """Line at run_ilk_loop_claude.sh:5892 — master-edited."""
        from integrity_violation import parse

        line = "  ! [ship-integrity VIOLATION] the worker changed master state; restored to the pre-dispatch snapshot (rc=3):"
        result = parse([line])
        assert len(result) == 1
        assert result[0]["kind"] == "master-edited"
        assert result[0]["slug"] is None

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_red_ship(self):
        """Line at run_ilk_loop_claude.sh:3675 — red-ship."""
        from integrity_violation import parse

        line = "  [ship-integrity VIOLATION] my-subplan: gate failed; the ship was reverted"
        result = parse([line])
        assert len(result) == 1
        assert result[0]["kind"] == "red-ship"
        assert result[0]["slug"] == "my-subplan"
        assert "gate failed" in result[0]["reason"]

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_gates_marker(self):
        """Line from gate_snapshot.py:225 — gates marker (not a VIOLATION line)."""
        from integrity_violation import parse

        line = "! [gates] my-slug: gates changed; restored to snapshot"
        result = parse([line])
        assert len(result) == 1
        assert result[0]["kind"] == "gates-edited"
        assert result[0]["slug"] == "my-slug"


# -- non-VIOLATION lines are ignored -----------------------------------------

class TestParseIgnores:
    """Lines without VIOLATION or [gates] produce no entries."""

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_ship_integrity_no_violation(self):
        """[ship-integrity] without VIOLATION is ignored."""
        from integrity_violation import parse

        line = "  [ship-integrity] cli-p1-admin: not in the active master — verdict skip recorded, not enforced"
        assert parse([line]) == []

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_empty_input(self):
        """Empty input returns empty list."""
        from integrity_violation import parse

        assert parse([]) == []


# -- deduplication ------------------------------------------------------------

class TestParseDeduplication:
    """Duplicate (kind, slug) pairs collapse to the first occurrence."""

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_duplicate_gates_edited_collapses(self):
        from integrity_violation import parse

        line = "  ! [ship-integrity VIOLATION] the worker changed gates for foo; restored to the pre-dispatch snapshot (rc=3):"
        result = parse([line, line])
        assert len(result) == 1
        assert result[0]["slug"] == "foo"

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_different_slugs_do_not_collapse(self):
        from integrity_violation import parse

        line_a = "  ! [ship-integrity VIOLATION] the worker changed gates for alpha; restored to the pre-dispatch snapshot (rc=3):"
        line_b = "  ! [ship-integrity VIOLATION] the worker changed gates for beta; restored to the pre-dispatch snapshot (rc=3):"
        result = parse([line_a, line_b])
        assert len(result) == 2
        assert result[0]["slug"] == "alpha"
        assert result[1]["slug"] == "beta"


# -- parse_file ---------------------------------------------------------------

class TestParseFile:
    """parse_file returns [] for missing or unreadable paths."""

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_missing_file_returns_empty(self, tmp_path: Path):
        from integrity_violation import parse_file

        assert parse_file(tmp_path / "nonexistent.log") == []

    @pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
    def test_existing_file_is_parsed(self, tmp_path: Path):
        from integrity_violation import parse_file

        log = tmp_path / "launcher.log"
        log.write_text(
            "  ! [ship-integrity VIOLATION] the worker changed gates for x; restored to the pre-dispatch snapshot (rc=3):\n"
            "some other line\n",
            encoding="utf-8",
        )
        result = parse_file(log)
        assert len(result) == 1
        assert result[0]["kind"] == "gates-edited"
        assert result[0]["slug"] == "x"