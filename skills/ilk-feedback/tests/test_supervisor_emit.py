"""Tests for supervisor_emit.py (AC-1 + AC-3).

Covers:
  AC-1  supervisor_emit.py adds exactly one open entry with source=="supervisor"
        and the given relations (title, gap, severity, project, run_id).
  AC-3  Re-running the same emit upserts (no duplicate) via content dedup.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts"

# Ensure scripts dir is importable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def backlog_env(tmp_path: Path):
    """Isolated ILK_DATA_HOME for supervisor emit tests."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    os.environ["ILK_DATA_HOME"] = str(data_home)
    yield data_home
    os.environ.pop("ILK_DATA_HOME", None)


# ── AC-1: basic emit creates source=supervisor entry ─────────────────────────


class TestAC1SupervisorEmit:
    """AC-1: supervisor_emit.py adds exactly one open entry with source=supervisor."""

    def test_emit_creates_entry_with_correct_fields(self, backlog_env):
        """--title T --gap G --severity high --project P creates entry."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        entry = emit_mod.emit(
            title="Missing test coverage",
            gap="No tests for new CLI flag",
            severity="high",
            project="my-proj",
            backlog_dir=backlog_env,
        )

        # source is supervisor
        assert entry.source == "supervisor"
        # relations contain project
        assert entry.relations.get("project") == "my-proj"
        # defaults
        assert entry.kind == "bug"
        assert entry.status == "open"
        assert entry.seen_count == 1

    def test_emit_with_all_options(self, backlog_env):
        """All CLI options round-trip correctly."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        entry = emit_mod.emit(
            title="Crash on import",
            gap="Module fails when config missing",
            proposed_fix="Add default config fallback",
            severity="high",
            leverage="high",
            kind="toolkit",
            project="es-api",
            run_id="20260621-120000",
            backlog_dir=backlog_env,
        )

        assert entry.source == "supervisor"
        assert entry.kind == "toolkit"
        assert entry.severity == "high"
        assert entry.leverage == "high"
        assert entry.proposed_fix == "Add default config fallback"
        assert entry.relations["project"] == "es-api"
        assert entry.relations["run_id"] == "20260621-120000"

    def test_emit_appears_in_load(self, backlog_env):
        """Emitted entry survives load()."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        emit_mod.emit(
            title="Test gap",
            gap="Missing validation",
            backlog_dir=backlog_env,
        )

        entries = backlog_mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].source == "supervisor"
        assert entries[0].title == "Test gap"

    def test_emit_with_source_id(self, backlog_env):
        """source_id is stored for PULL-upsert dedup."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        entry = emit_mod.emit(
            title="External finding",
            gap="gap text",
            source_id="ext-123",
            backlog_dir=backlog_env,
        )

        assert entry.source_id == "ext-123"


# ── AC-3: upsert (no duplicate on re-emit) ──────────────────────────────────


class TestAC3Upsert:
    """AC-3: Re-running the same emit upserts (no duplicate)."""

    def test_same_emit_bumps_seen_count(self, backlog_env):
        """Same title+gap → upsert, seen_count increments."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        e1 = emit_mod.emit(
            title="Dup test",
            gap="Same gap text",
            project="proj-a",
            backlog_dir=backlog_env,
        )
        assert e1.seen_count == 1

        e2 = emit_mod.emit(
            title="Dup test",
            gap="Same gap text",
            project="proj-b",
            backlog_dir=backlog_env,
        )
        assert e2.seen_count == 2
        assert e2.id == e1.id

        entries = backlog_mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1

    def test_different_title_not_deduped(self, backlog_env):
        """Different title → separate entries."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        emit_mod.emit(title="Gap A", gap="desc", backlog_dir=backlog_env)
        emit_mod.emit(title="Gap B", gap="desc", backlog_dir=backlog_env)

        entries = backlog_mod.load(backlog_dir=backlog_env)
        assert len(entries) == 2

    def test_source_id_upsert_path(self, backlog_env):
        """Same (source, source_id) → upsert even if title changes."""
        import improvement_backlog as backlog_mod
        importlib.reload(backlog_mod)
        import supervisor_emit as emit_mod
        importlib.reload(emit_mod)

        e1 = emit_mod.emit(
            title="Original title",
            gap="gap",
            source_id="pull-001",
            backlog_dir=backlog_env,
        )
        assert e1.seen_count == 1

        e2 = emit_mod.emit(
            title="Updated title",
            gap="gap",
            source_id="pull-001",
            backlog_dir=backlog_env,
        )
        assert e2.seen_count == 2
        # source_id path refreshes title
        assert e2.title == "Updated title"

        entries = backlog_mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
