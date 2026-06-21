"""Tests for autoclose_tracker — auto-close tracker/backlog entries on ship.

Covers:
  AC-1  shipped sub-plan with matching OPEN entry → flipped to shipped;
        pending sub-plan's ticket left OPEN
  AC-2  idempotent — re-running makes no further change; already-shipped untouched
  AC-3  best-effort — missing/garbled tracker or unknown ticket id is a no-op (never raises)

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
def plans_dir(tmp_path: Path) -> Path:
    """Create a temporary plans directory."""
    d = tmp_path / "plans"
    d.mkdir()
    return d


@pytest.fixture()
def fake_git_project(tmp_path: Path) -> Path:
    """Create a minimal git project so ilk_paths can resolve a key."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / ".git").mkdir()
    return proj


def _write_plan(
    plans_dir: Path,
    filename: str,
    *,
    status: str,
    plan_slug: str,
    tickets: list[str] | None = None,
) -> Path:
    """Write a minimal sub-plan file with frontmatter."""
    tickets_block = ""
    if tickets:
        items = "\n".join(f"  - {t}" for t in tickets)
        tickets_block = f"tickets:\n{items}\n"

    content = f"""\
---
plan: {plan_slug}
status: {status}
current_step: 3
{tickets_block}estimated_steps: 3
last_updated: 2026-06-22
---

# Sub-plan: {plan_slug}

Body text here.
"""
    p = plans_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


# ── AC-1: shipped sub-plan closes matching OPEN entries ──────────────────────


class TestAC1ShippedClosesOpen:
    """AC-1: shipped sub-plan's tickets close matching OPEN tracker entries."""

    def test_shipped_plan_closes_open_tracker_entry(
        self, data_env, plans_dir, fake_git_project, monkeypatch
    ):
        """A shipped sub-plan with tickets: [X] closes OPEN entry X in tracker."""
        import importlib
        import ilk_paths
        import project_tracker
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(project_tracker)
        importlib.reload(autoclose_tracker)

        # Seed an OPEN entry in the per-project tracker
        entry = project_tracker.add(
            title="some gap",
            gap="missing feature",
            source="feedback",
            source_id="",
            project=fake_git_project,
        )
        assert entry.status == "open"

        # Write a shipped sub-plan referencing that entry's id
        _write_plan(
            plans_dir,
            "2026-06-22-test-plan.md",
            status="shipped",
            plan_slug="test-plan",
            tickets=[entry.id],
        )

        # Run autoclose
        closed = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )

        assert closed == 1
        # Verify the entry is now shipped
        entries = project_tracker.load(project=fake_git_project)
        matching = [e for e in entries if e.id == entry.id]
        assert len(matching) == 1
        assert matching[0].status == "shipped"

    def test_pending_plan_ticket_stays_open(
        self, data_env, plans_dir, fake_git_project
    ):
        """A pending sub-plan's ticket is NOT closed."""
        import importlib
        import ilk_paths
        import project_tracker
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(project_tracker)
        importlib.reload(autoclose_tracker)

        entry = project_tracker.add(
            title="open gap",
            gap="not yet shipped",
            source="feedback",
            source_id="",
            project=fake_git_project,
        )

        # Write a PENDING sub-plan (not shipped)
        _write_plan(
            plans_dir,
            "2026-06-22-pending-plan.md",
            status="pending",
            plan_slug="pending-plan",
            tickets=[entry.id],
        )

        closed = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )

        assert closed == 0
        entries = project_tracker.load(project=fake_git_project)
        matching = [e for e in entries if e.id == entry.id]
        assert matching[0].status == "open"


# ── AC-2: idempotent ─────────────────────────────────────────────────────────


class TestAC2Idempotent:
    """AC-2: re-running makes no further change; already-shipped untouched."""

    def test_double_run_is_idempotent(
        self, data_env, plans_dir, fake_git_project
    ):
        """Running autoclose twice produces the same result."""
        import importlib
        import ilk_paths
        import project_tracker
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(project_tracker)
        importlib.reload(autoclose_tracker)

        entry = project_tracker.add(
            title="to ship",
            gap="will be shipped",
            source="feedback",
            source_id="",
            project=fake_git_project,
        )

        _write_plan(
            plans_dir,
            "2026-06-22-shipped.md",
            status="shipped",
            plan_slug="shipped",
            tickets=[entry.id],
        )

        # First run
        closed1 = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )
        assert closed1 == 1

        # Second run — should be a no-op
        closed2 = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )
        assert closed2 == 0

    def test_already_shipped_entry_untouched(
        self, data_env, plans_dir, fake_git_project
    ):
        """An entry already at 'shipped' is not modified."""
        import importlib
        import ilk_paths
        import project_tracker
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(project_tracker)
        importlib.reload(autoclose_tracker)

        entry = project_tracker.add(
            title="already shipped",
            gap="was shipped before",
            source="feedback",
            source_id="",
            project=fake_git_project,
        )
        # Manually ship it first
        project_tracker.set_status(
            entry.id, "shipped", project=fake_git_project
        )

        _write_plan(
            plans_dir,
            "2026-06-22-also-shipped.md",
            status="shipped",
            plan_slug="also-shipped",
            tickets=[entry.id],
        )

        closed = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )

        # Should not count as closed (already shipped)
        assert closed == 0


# ── AC-3: best-effort (never raises) ─────────────────────────────────────────


class TestAC3BestEffort:
    """AC-3: missing/garbled tracker or unknown ticket id is a no-op."""

    def test_unknown_ticket_id_is_noop(
        self, data_env, plans_dir, fake_git_project
    ):
        """A ticket id that doesn't match any tracker entry is silently skipped."""
        import importlib
        import ilk_paths
        import project_tracker
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(project_tracker)
        importlib.reload(autoclose_tracker)

        # No entries in tracker at all
        _write_plan(
            plans_dir,
            "2026-06-22-unknown.md",
            status="shipped",
            plan_slug="unknown",
            tickets=["nonexistent-ticket-id"],
        )

        closed = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )

        assert closed == 0

    def test_plans_dir_with_no_master_files(
        self, data_env, tmp_path, fake_git_project
    ):
        """An empty plans directory is a no-op."""
        import importlib
        import ilk_paths
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(autoclose_tracker)

        empty_dir = tmp_path / "empty_plans"
        empty_dir.mkdir()

        closed = autoclose_tracker.autoclose(
            empty_dir, project=fake_git_project
        )

        assert closed == 0

    def test_garbled_plan_file_is_noop(
        self, data_env, plans_dir, fake_git_project
    ):
        """A plan file with no valid frontmatter is silently skipped."""
        import importlib
        import ilk_paths
        import autoclose_tracker

        importlib.reload(ilk_paths)
        importlib.reload(autoclose_tracker)

        # Write a garbled file
        garbled = plans_dir / "2026-06-22-garbled.md"
        garbled.write_text("this is not valid frontmatter\n", encoding="utf-8")

        closed = autoclose_tracker.autoclose(
            plans_dir, project=fake_git_project
        )

        assert closed == 0

    def test_no_project_key_is_noop(self, data_env, plans_dir):
        """When project cannot be resolved, autoclose still returns 0 (no raise)."""
        import importlib
        import autoclose_tracker

        importlib.reload(autoclose_tracker)

        _write_plan(
            plans_dir,
            "2026-06-22-orphan.md",
            status="shipped",
            plan_slug="orphan",
            tickets=["some-id"],
        )

        # No project/key provided — should not raise
        closed = autoclose_tracker.autoclose(plans_dir)

        assert closed == 0
