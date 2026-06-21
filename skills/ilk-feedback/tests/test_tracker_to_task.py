"""Tests for the tracker → task planner-feed adapter.

Covers:
  AC-1  Two open entries → markdown containing both titles + kinds + gaps
  AC-2  shipped/wontfix entries excluded (only open)
  AC-3  Empty/absent tracker → "nothing to plan", exit 0
  AC-4  Output format consistent with build_task.format_task_description

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_LOOP_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
)
_SELF_IMPROVE_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "ilk-self-improve" / "scripts"
)

for _d in (_SCRIPTS_DIR, _LOOP_SCRIPTS, _SELF_IMPROVE_SCRIPTS):
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
def fake_project(tmp_path: Path) -> Path:
    """Create a minimal git project so ilk_paths can resolve a key."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / ".git").mkdir()
    return proj


# ── AC-1: two open entries appear in output ──────────────────────────────────


class TestBuildForProject:
    """AC-1 & AC-2: build_for_project returns open entries, excludes non-open."""

    def test_two_open_entries_in_output(self, data_env, fake_project):
        """Two open tracker entries both appear in the formatted task."""
        import importlib
        import project_tracker as pt
        import tracker_to_task as mod

        importlib.reload(pt)
        importlib.reload(mod)

        pt.add(
            title="Add retry to Lark client",
            gap="Network errors crash the pull",
            kind="toolkit",
            source="lark",
            source_id="rec-ac1-a",
            project=fake_project,
        )
        pt.add(
            title="Improve error messages",
            gap="Errors are too cryptic",
            kind="bug",
            source="lark",
            source_id="rec-ac1-b",
            project=fake_project,
        )

        result = mod.build_for_project(fake_project)

        assert "Add retry to Lark client" in result
        assert "toolkit" in result
        assert "Network errors crash the pull" in result
        assert "Improve error messages" in result
        assert "bug" in result

    def test_shipped_entry_excluded(self, data_env, fake_project):
        """A shipped entry does NOT appear in the output."""
        import importlib
        import project_tracker as pt
        import tracker_to_task as mod

        importlib.reload(pt)
        importlib.reload(mod)

        e = pt.add(
            title="Shipped gap",
            gap="already done",
            source="lark",
            source_id="rec-ac2-ship",
            project=fake_project,
        )
        pt.set_status(e.id, "shipped", project=fake_project)
        # Add one open entry so the output is non-empty
        pt.add(
            title="Still open",
            gap="not done yet",
            source="lark",
            source_id="rec-ac2-open",
            project=fake_project,
        )

        result = mod.build_for_project(fake_project)

        assert "Shipped gap" not in result
        assert "Still open" in result

    def test_wontfix_entry_excluded(self, data_env, fake_project):
        """A wontfix entry does NOT appear in the output."""
        import importlib
        import project_tracker as pt
        import tracker_to_task as mod

        importlib.reload(pt)
        importlib.reload(mod)

        e = pt.add(
            title="Wontfix gap",
            gap="not fixing",
            source="lark",
            source_id="rec-ac2-wf",
            project=fake_project,
        )
        pt.set_status(e.id, "wontfix", project=fake_project)
        pt.add(
            title="Open gap",
            gap="needs work",
            source="lark",
            source_id="rec-ac2-open2",
            project=fake_project,
        )

        result = mod.build_for_project(fake_project)

        assert "Wontfix gap" not in result
        assert "Open gap" in result

    def test_empty_tracker_returns_empty_string(self, data_env, fake_project):
        """Empty tracker → empty string (no crash)."""
        import importlib
        import tracker_to_task as mod

        importlib.reload(mod)

        result = mod.build_for_project(fake_project)
        assert result == ""


# ── AC-3: CLI empty-tracker guard ────────────────────────────────────────────


class TestCLIEmptyTracker:
    """AC-3: CLI prints 'nothing to plan' and exits 0 on empty tracker."""

    def test_cli_empty_tracker_exits_zero(self, data_env, fake_project):
        """main(['--project', ...]) with empty tracker exits 0 and prints message."""
        import importlib
        import tracker_to_task as mod

        importlib.reload(mod)

        exit_code = mod.main(["--project", str(fake_project)])
        assert exit_code == 0

    def test_cli_empty_tracker_message(self, data_env, fake_project, capsys):
        """CLI prints 'Nothing to plan' when tracker is empty."""
        import importlib
        import tracker_to_task as mod

        importlib.reload(mod)

        mod.main(["--project", str(fake_project)])
        captured = capsys.readouterr()
        assert "Nothing to plan" in captured.out


# ── AC-4: format consistency with build_task ─────────────────────────────────


class TestFormatConsistency:
    """AC-4: build_for_project output shape matches build_task.format_task_description."""

    def test_output_uses_same_formatter(self, data_env, fake_project):
        """build_for_project delegates to build_task.format_task_description."""
        import importlib
        import project_tracker as pt
        import tracker_to_task as mod
        import build_task

        importlib.reload(pt)
        importlib.reload(mod)

        pt.add(
            title="Consistency check",
            gap="format must match",
            kind="toolkit",
            source="lark",
            source_id="rec-ac4-1",
            project=fake_project,
        )

        entries = pt.list_open(project=fake_project)
        expected = build_task.format_task_description(entries)
        actual = mod.build_for_project(fake_project)

        assert actual == expected
