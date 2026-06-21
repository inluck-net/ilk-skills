"""Tests for the per-project unified tracker store.

Covers:
  AC-1  tracker_dir resolves correctly from path and from key
  AC-2  add writes to per-project dir, (source, source_id) upserts
  AC-3  list_open returns only that project's entries; two projects isolated
  AC-4  set_status flips and persists
  AC-5  global backlog untouched by per-project writes

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_LOOP_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
)

# Ensure scripts dirs are importable
for _d in (_SCRIPTS_DIR, _LOOP_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def data_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated ILK_DATA_HOME pointing at a fresh tmp dir."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    return data_home


@pytest.fixture()
def fake_git_project(tmp_path: Path) -> Path:
    """Create a minimal git project so ilk_paths can resolve a key."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / ".git").mkdir()
    return proj


# ── AC-1: tracker_dir resolution ─────────────────────────────────────────────


class TestTrackerDir:
    """AC-1: tracker_dir(project_path) and tracker_dir(key=...) resolve correctly."""

    def test_from_project_path(self, data_env, fake_git_project):
        """tracker_dir(project=P) returns <data_root>/projects/<key>/."""
        import importlib
        import ilk_paths
        import project_tracker as mod

        importlib.reload(ilk_paths)
        importlib.reload(mod)

        result = mod.tracker_dir(project=fake_git_project)

        expected_key = ilk_paths.project_key(fake_git_project)
        assert result == data_env / "projects" / expected_key
        assert result.name  # not empty

    def test_from_key_directly(self, data_env):
        """tracker_dir(key='my-key') returns <data_root>/projects/my-key/."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        result = mod.tracker_dir(key="my-key")
        assert result == data_env / "projects" / "my-key"

    def test_from_path_matches_ilk_paths_key(self, data_env, fake_git_project):
        """The key used by tracker_dir matches ilk_paths.project_key for the same path."""
        import importlib
        import ilk_paths
        import project_tracker as mod

        importlib.reload(ilk_paths)
        importlib.reload(mod)

        result = mod.tracker_dir(project=fake_git_project)
        expected_key = ilk_paths.project_key(fake_git_project)
        # The path should end with projects/<key>
        assert result.parent == data_env / "projects"
        assert result.name == expected_key

    def test_raises_when_no_args(self, data_env):
        """tracker_dir() with no arguments raises ValueError."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        with pytest.raises(ValueError, match="provide either"):
            mod.tracker_dir()

    def test_raises_when_path_not_a_project(self, data_env, tmp_path):
        """tracker_dir(project=<non-git-dir>) raises ValueError."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        bogus = tmp_path / "not-a-project"
        bogus.mkdir()
        with pytest.raises(ValueError, match="cannot resolve"):
            mod.tracker_dir(project=bogus)


# ── AC-2: add writes to per-project dir, (source, source_id) upserts ─────────


class TestAdd:
    """AC-2: add() delegates to improvement_backlog with per-project backlog_dir."""

    def test_add_writes_to_project_tracker_dir(self, data_env, fake_git_project):
        """add(title=..., project=P) writes to <data_root>/projects/<key>/."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        entry = mod.add(
            title="test gap",
            gap="missing feature X",
            source="lark",
            source_id="rec1",
            project=fake_git_project,
        )
        td = mod.tracker_dir(project=fake_git_project)
        # The tracker dir should now exist with candidates.json
        tracker_file = td / "candidates.json"
        assert tracker_file.exists(), f"tracker file should exist at {tracker_file}"

    def test_add_upserts_on_source_source_id(self, data_env, fake_git_project):
        """Two adds with same (source, source_id) upsert (seen_count bumps)."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        e1 = mod.add(
            title="first title",
            gap="first gap",
            source="lark",
            source_id="rec-upsert",
            project=fake_git_project,
        )
        e2 = mod.add(
            title="updated title",
            gap="updated gap",
            source="lark",
            source_id="rec-upsert",
            project=fake_git_project,
        )
        # Should be the same entry (upserted), seen_count bumped
        assert e2.seen_count == 2
        # Title should be refreshed from the latest add
        assert e2.title == "updated title"


# ── AC-3: two projects' trackers are fully isolated ──────────────────────────


class TestIsolation:
    """AC-3: Two different projects' trackers are fully isolated."""

    def test_two_projects_isolated(self, data_env, tmp_path):
        """Entries from project A don't appear in project B's list."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        proj_a = tmp_path / "project-a"
        proj_a.mkdir()
        (proj_a / ".git").mkdir()
        proj_b = tmp_path / "project-b"
        proj_b.mkdir()
        (proj_b / ".git").mkdir()

        mod.add(
            title="gap in A",
            gap="only in project A",
            source="lark",
            source_id="rec-a1",
            project=proj_a,
        )
        mod.add(
            title="gap in B",
            gap="only in project B",
            source="lark",
            source_id="rec-b1",
            project=proj_b,
        )

        # Load entries from each project's tracker
        import improvement_backlog

        entries_a = improvement_backlog.load(
            backlog_dir=mod.tracker_dir(project=proj_a)
        )
        entries_b = improvement_backlog.load(
            backlog_dir=mod.tracker_dir(project=proj_b)
        )

        assert len(entries_a) == 1
        assert entries_a[0].source_id == "rec-a1"
        assert len(entries_b) == 1
        assert entries_b[0].source_id == "rec-b1"


# ── AC-5: global backlog untouched by per-project writes ─────────────────────


class TestGlobalBacklogUntouched:
    """AC-5: per-project add() does NOT appear in the global backlog."""

    def test_global_backlog_not_polluted(self, data_env, fake_git_project):
        """A project_tracker.add does not touch the global improvement_backlog."""
        import importlib
        import improvement_backlog
        import project_tracker as mod

        importlib.reload(mod)

        mod.add(
            title="project-only gap",
            gap="should not leak",
            source="lark",
            source_id="rec-global-test",
            project=fake_git_project,
        )

        # The global backlog dir is <data_env>/ilk-skills-improvements/
        global_entries = improvement_backlog.load(
            backlog_dir=data_env / "ilk-skills-improvements"
        )
        # Should be empty — nothing was added to the global backlog
        assert len(global_entries) == 0


# ── AC-4: list_open / load / set_status ───────────────────────────────────────


class TestListOpenLoadSetStatus:
    """AC-4: list_open, load, and set_status work on the per-project tracker."""

    def test_load_returns_all_entries(self, data_env, fake_git_project):
        """load(project=P) returns all entries from that project's tracker."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        mod.add(
            title="gap one",
            gap="first",
            source="lark",
            source_id="rec-load-1",
            project=fake_git_project,
        )
        mod.add(
            title="gap two",
            gap="second",
            source="lark",
            source_id="rec-load-2",
            project=fake_git_project,
        )

        entries = mod.load(project=fake_git_project)
        assert len(entries) == 2

    def test_list_open_returns_only_open(self, data_env, fake_git_project):
        """list_open(project=P) returns only entries with status='open'."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        e1 = mod.add(
            title="open gap",
            gap="still open",
            source="lark",
            source_id="rec-open-1",
            project=fake_git_project,
        )
        e2 = mod.add(
            title="will close",
            gap="to be shipped",
            source="lark",
            source_id="rec-open-2",
            project=fake_git_project,
        )
        # Ship the second entry
        mod.set_status(e2.id, "shipped", project=fake_git_project)

        open_entries = mod.list_open(project=fake_git_project)
        assert len(open_entries) == 1
        assert open_entries[0].id == e1.id

    def test_set_status_flips_and_persists(self, data_env, fake_git_project):
        """set_status(id, 'shipped', project=P) flips status and persists."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        entry = mod.add(
            title="to ship",
            gap="will be shipped",
            source="lark",
            source_id="rec-ship-1",
            project=fake_git_project,
        )
        assert entry.status == "open"

        mod.set_status(entry.id, "shipped", project=fake_git_project)

        # Reload from disk and verify
        reloaded = mod.load(project=fake_git_project)
        matching = [e for e in reloaded if e.id == entry.id]
        assert len(matching) == 1
        assert matching[0].status == "shipped"

    def test_set_status_raises_for_missing_id(self, data_env, fake_git_project):
        """set_status with unknown id raises KeyError."""
        import importlib
        import project_tracker as mod

        importlib.reload(mod)

        with pytest.raises(KeyError, match="no entry with id"):
            mod.set_status("nonexistent-id", "shipped", project=fake_git_project)
