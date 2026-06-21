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
