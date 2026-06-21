"""Tests for the source_id field on backlog Entry (AC-1..AC-3).

Covers:
  AC-1  Entry has source_id field; to_dict/from_dict round-trips it; old records load with source_id=""
  AC-2  add_candidate upserts on (source, source_id) when source_id is non-empty
  AC-3  add_candidate with empty source_id still deduplicates via stable_key (existing behavior)

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# Ensure scripts dir is importable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def backlog_env(tmp_path: Path):
    """Isolated ILK_DATA_HOME for backlog tests."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    os.environ["ILK_DATA_HOME"] = str(data_home)
    yield data_home
    os.environ.pop("ILK_DATA_HOME", None)


# ── AC-1: source_id round-trip ───────────────────────────────────────────────


class TestSourceIdRoundTrip:
    """AC-1: Entry has source_id; to_dict/from_dict round-trips it; old records load."""

    def test_source_id_field_exists_with_default(self, backlog_env):
        """Entry has source_id field defaulting to empty string."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        entry = mod.add_candidate(
            title="test gap",
            gap="missing feature X",
            backlog_dir=backlog_env,
        )
        assert hasattr(entry, "source_id")
        assert entry.source_id == ""

    def test_source_id_round_trips_through_dict(self, backlog_env):
        """source_id survives to_dict() → from_dict() round-trip."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        entry = mod.add_candidate(
            title="test gap",
            gap="missing feature X",
            source="lark",
            source_id="recABC123",
            backlog_dir=backlog_env,
        )
        assert entry.source_id == "recABC123"

        d = entry.to_dict()
        assert d["source_id"] == "recABC123"

        restored = mod.Entry.from_dict(d)
        assert restored.source_id == "recABC123"
        assert restored.source == "lark"

    def test_old_record_without_source_id_loads_with_default(self, backlog_env):
        """A candidates.json written without source_id loads via load() with source_id=''."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        # Write a hand-rolled old-schema record directly to disk
        old_record = {
            "id": "old-no-source-id",
            "title": "Old Gap",
            "kind": "toolkit",
            "gap": "Missing feature Y",
            "evidence": {"project": "old-proj"},
            "proposed_fix": "add it",
            "leverage": "medium",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "seen_count": 1,
            "source": "feedback",
            # NOTE: no "source_id" key — simulates old schema
            "relations": {},
        }
        backlog_dir = backlog_env / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        candidates_path = backlog_dir / "candidates.json"
        candidates_path.write_text(
            json.dumps([old_record], indent=2),
            encoding="utf-8",
        )

        entries = mod.load(backlog_dir=backlog_dir)
        assert len(entries) == 1

        e = entries[0]
        # Original fields intact
        assert e.id == "old-no-source-id"
        assert e.source == "feedback"
        # source_id defaulted to empty string
        assert e.source_id == ""

    def test_source_id_survives_load_cycle(self, backlog_env):
        """source_id persists through add → load cycle."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(
            title="persist test",
            gap="gap desc",
            source="lark",
            source_id="recXYZ789",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].source_id == "recXYZ789"


# ── AC-2: upsert on (source, source_id) ─────────────────────────────────────


class TestSourceIdUpsert:
    """AC-2: add_candidate upserts on (source, source_id) when source_id is non-empty."""

    def test_upsert_by_source_id_despite_different_title(self, backlog_env):
        """Two calls with same (source, source_id) but different title → ONE entry.

        The second call should bump seen_count and refresh the title, not
        create a second entry.  This proves (source, source_id) is the
        dedup axis when source_id is non-empty.
        """
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(
            title="Original Title",
            gap="gap A",
            source="lark",
            source_id="rec123",
            backlog_dir=backlog_env,
        )
        mod.add_candidate(
            title="Updated Title",
            gap="gap A refreshed",
            source="lark",
            source_id="rec123",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1, f"expected 1 entry, got {len(entries)}"
        e = entries[0]
        assert e.source_id == "rec123"
        assert e.seen_count == 2
        # Title refreshed from second call
        assert e.title == "Updated Title"
        assert e.gap == "gap A refreshed"

    def test_upsert_by_source_id_preserves_original_id(self, backlog_env):
        """Upsert via source_id keeps the entry's content-based id unchanged."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(
            title="First Title",
            gap="gap X",
            source="lark",
            source_id="recABC",
            backlog_dir=backlog_env,
        )
        original_id = e1.id

        mod.add_candidate(
            title="Changed Title",
            gap="gap X changed",
            source="lark",
            source_id="recABC",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].id == original_id

    def test_different_source_ids_create_separate_entries(self, backlog_env):
        """Two calls with same source but different source_id → two entries."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(
            title="Entry A",
            gap="gap A",
            source="lark",
            source_id="recAAA",
            backlog_dir=backlog_env,
        )
        mod.add_candidate(
            title="Entry B",
            gap="gap B",
            source="lark",
            source_id="recBBB",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 2
        ids_by_source_id = {e.source_id: e for e in entries}
        assert "recAAA" in ids_by_source_id
        assert "recBBB" in ids_by_source_id


# ── AC-3: empty source_id → content dedup unchanged ─────────────────────────


class TestEmptySourceIdContentDedup:
    """AC-3: add_candidate with empty source_id still deduplicates via stable_key."""

    def test_empty_source_id_dedup_via_stable_key(self, backlog_env):
        """Two calls with empty source_id and identical (kind, title, gap) → ONE entry."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(
            title="Same Title",
            gap="Same Gap",
            kind="toolkit",
            backlog_dir=backlog_env,
        )
        mod.add_candidate(
            title="Same Title",
            gap="Same Gap",
            kind="toolkit",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].seen_count == 2
        assert entries[0].source_id == ""

    def test_empty_source_id_different_title_creates_separate(self, backlog_env):
        """Two calls with empty source_id but different title → two entries."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(
            title="Title A",
            gap="gap",
            backlog_dir=backlog_env,
        )
        mod.add_candidate(
            title="Title B",
            gap="gap",
            backlog_dir=backlog_env,
        )

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 2
