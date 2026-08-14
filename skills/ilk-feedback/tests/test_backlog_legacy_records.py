"""Tests for improvement_backlog.py's handling of legacy record shapes.

The backlog store at ``~/.ilk-data/ilk-skills-improvements/candidates.json``
is cross-project and cross-machine.  Records written before ``leverage``,
``severity``, ``first_seen``, ``last_seen``, and ``seen_count`` became
required cannot be constructed by ``Entry.from_dict``, and one bad record
aborts the entire listing (list comprehension at ``:309``).

These tests use a fixture store under ``tmp_path`` — never the real store.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add the scripts dir so we can import improvement_backlog
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import improvement_backlog  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture records
# ---------------------------------------------------------------------------

# Pre-required fields only (written before leverage/severity/first_seen/
# last_seen/seen_count became required).
_LEGACY_RECORD = {
    "id": "legacy-001",
    "title": "Legacy entry missing five fields",
    "kind": "toolkit",
    "gap": "This record predates the five required fields.",
    "evidence": {"file": "foo.py", "line": 10},
    "proposed_fix": "Add defaults.",
    "status": "open",
}

# Current shape — all fields present.
_CURRENT_RECORD = {
    "id": "current-001",
    "title": "Current entry with all fields",
    "kind": "toolkit",
    "gap": "This record has the full schema.",
    "evidence": {"file": "bar.py", "line": 20},
    "proposed_fix": "Nothing to fix.",
    "leverage": "medium",
    "severity": "low",
    "status": "open",
    "first_seen": "2026-08-13T10:00:00+08:00",
    "last_seen": "2026-08-13T12:00:00+08:00",
    "seen_count": 3,
}

# Missing even the mandatory ``id`` — genuinely unconstructible.
_UNCONSTRUCTIBLE_RECORD = {
    "title": "No id field",
    "kind": "toolkit",
    "gap": "Cannot be constructed without an id.",
    "evidence": {},
    "proposed_fix": "Skip with warning.",
    "leverage": "low",
    "severity": "low",
    "status": "open",
    "first_seen": "2026-08-13T10:00:00+08:00",
    "last_seen": "2026-08-13T12:00:00+08:00",
    "seen_count": 1,
}


@pytest.fixture
def backlog_dir(tmp_path: Path) -> Path:
    """Create a fixture backlog store with legacy, current, and bad records."""
    d = tmp_path / "backlog"
    d.mkdir()
    (d / "candidates.json").write_text(
        json.dumps([_LEGACY_RECORD, _CURRENT_RECORD, _UNCONSTRUCTIBLE_RECORD]),
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# from_dict unit tests
# ---------------------------------------------------------------------------


class TestFromDict:
    """Entry.from_dict behaviour against each record shape."""

    def test_legacy_record_constructs_after_fix(self) -> None:
        """A legacy record missing five required fields now constructs with defaults."""
        entry = improvement_backlog.Entry.from_dict(_LEGACY_RECORD)
        assert entry.id == "legacy-001"
        assert entry.leverage == "low"

    def test_legacy_record_constructs_with_defaults(self) -> None:
        """A legacy record constructs with defaults for the five newer fields."""
        entry = improvement_backlog.Entry.from_dict(_LEGACY_RECORD)
        assert entry.id == "legacy-001"
        # The five defaulted fields should have safe values, not be absent.
        assert entry.leverage == "low"
        assert entry.severity == "low"
        assert entry.first_seen == ""
        assert entry.last_seen == ""
        assert entry.seen_count == 0

    def test_current_record_constructs(self) -> None:
        """A current record with all fields constructs fine."""
        entry = improvement_backlog.Entry.from_dict(_CURRENT_RECORD)
        assert entry.id == "current-001"
        assert entry.leverage == "medium"
        assert entry.seen_count == 3

    def test_unconstructible_record_raises(self) -> None:
        """A record missing ``id`` (a positional field) raises TypeError."""
        with pytest.raises(TypeError):
            improvement_backlog.Entry.from_dict(_UNCONSTRUCTIBLE_RECORD)


# ---------------------------------------------------------------------------
# Listing integration tests
# ---------------------------------------------------------------------------


class TestListing:
    """improvement_backlog.load / list_entries against a mixed store."""

    def test_load_aborts_on_unconstructible_record(self, backlog_dir: Path) -> None:
        """One unconstructible record still aborts the entire listing.

        The list comprehension at :309 propagates the first TypeError.
        After step 1, the legacy record constructs fine, but the
        unconstructible one (missing ``id``) still raises.
        """
        with pytest.raises(TypeError):
            improvement_backlog.load(backlog_dir)

    @pytest.mark.xfail(
        strict=True,
        reason="Step 2 will skip unconstructible records; until then load still aborts.",
    )
    def test_load_succeeds_with_legacy_record(self, backlog_dir: Path) -> None:
        """After step 2, load should succeed and return all constructible records."""
        entries = improvement_backlog.load(backlog_dir)
        # Legacy + current = 2 (unconstructible is skipped in step 2).
        assert len(entries) >= 2

    @pytest.mark.xfail(
        strict=True,
        reason="Step 2 will skip unconstructible records; until then this fails.",
    )
    def test_load_skips_unconstructible_with_warning(
        self, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """After step 2, an unconstructible record is skipped with a warning."""
        entries = improvement_backlog.load(backlog_dir)
        # Legacy + current = 2; unconstructible is skipped.
        assert len(entries) == 2
        captured = capsys.readouterr()
        assert "No id field" in captured.err or "No id field" in captured.out
