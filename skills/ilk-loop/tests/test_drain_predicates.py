"""RED tests for depends_on-aware drain predicates (AC-1 + AC-2).

The loop currently picks sub-plans by status alone — a blocked sub-plan
with an unmet `depends_on` is invisible, and a master with a single
blocked sub-plan blocks the entire queue.  L4 requires:

  AC-1: ``subplan_is_runnable(fm, sibling_statuses)`` returns False for
        blocked, False when any depends_on sibling is not shipped, True
        for pending/in-progress with all deps shipped.
  AC-2: ``master_is_drainable(master, plans_dir)`` is True iff >= 1
        registered sub-plan is runnable; a master with only blocked /
        dep-on-blocked sub-plans is NOT drainable (= stalled).

These tests build temp plans dirs and in-memory dicts — no subprocess,
no external services.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Will be implemented in step 1 — until then, every test here is RED.
from plan_status import master_is_drainable, subplan_is_runnable  # noqa: E402


# ── AC-1: subplan_is_runnable ────────────────────────────────────────────────


class TestSubplanIsRunnable:
    """subplan_is_runnable(fm: dict, sibling_statuses: dict) -> bool."""

    # --- basic status gating ---

    def test_pending_no_deps_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is True

    def test_in_progress_no_deps_is_runnable(self) -> None:
        fm = {"status": "in-progress", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is True

    def test_shipped_is_not_runnable(self) -> None:
        fm = {"status": "shipped", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is False

    def test_blocked_is_not_runnable(self) -> None:
        fm = {"status": "blocked", "depends_on": "[]"}
        assert subplan_is_runnable(fm, {}) is False

    # --- depends_on gating ---

    def test_pending_with_all_deps_shipped_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "shipped"}
        assert subplan_is_runnable(fm, siblings) is True

    def test_pending_with_unmet_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "pending"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_blocked_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "blocked"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_in_progress_dep_is_not_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha"]'}
        siblings = {"alpha": "in-progress"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_pending_with_multiple_deps_all_shipped(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha", "beta"]'}
        siblings = {"alpha": "shipped", "beta": "shipped"}
        assert subplan_is_runnable(fm, siblings) is True

    def test_pending_with_multiple_deps_one_unmet(self) -> None:
        fm = {"status": "pending", "depends_on": '["alpha", "beta"]'}
        siblings = {"alpha": "shipped", "beta": "blocked"}
        assert subplan_is_runnable(fm, siblings) is False

    def test_blocked_with_deps_shipped_is_still_not_runnable(self) -> None:
        """Blocked status overrides deps — blocked is never runnable."""
        fm = {"status": "blocked", "depends_on": '["alpha"]'}
        siblings = {"alpha": "shipped"}
        assert subplan_is_runnable(fm, siblings) is False

    # --- missing sibling (dep file missing or typo) ---

    def test_dep_not_in_siblings_is_not_runnable(self) -> None:
        """If the dep slug is missing from sibling_statuses, treat as unmet."""
        fm = {"status": "pending", "depends_on": '["nonexistent"]'}
        assert subplan_is_runnable(fm, {}) is False

    # --- empty / absent depends_on ---

    def test_empty_depends_on_string_is_runnable(self) -> None:
        fm = {"status": "pending", "depends_on": ""}
        assert subplan_is_runnable(fm, {}) is True

    def test_missing_depends_on_key_is_runnable(self) -> None:
        fm = {"status": "pending"}
        assert subplan_is_runnable(fm, {}) is True


# ── AC-2: master_is_drainable ────────────────────────────────────────────────


def _write_plan(path: Path, body: str) -> None:
    """Write a plan file with minimal frontmatter."""
    path.write_text(textwrap.dedent(body), encoding="utf-8")


class TestMasterIsDrainable:
    """master_is_drainable(master_path, plans_dir) -> bool."""

    def test_all_shipped_is_not_drainable(self, tmp_path: Path) -> None:
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: shipped
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is False

    def test_one_runnable_pending_is_drainable(self, tmp_path: Path) -> None:
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_blocked_and_dep_on_blocked_is_stalled(self, tmp_path: Path) -> None:
        """[shipped, blocked, pending-dep-on-blocked] → stalled."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
            - [gamma](2026-01-01-gamma.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: blocked
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-gamma.md", """\
            ---
            status: pending
            depends_on: ["beta"]
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is False

    def test_pending_with_unmet_dep_plus_runnable_sibling(self, tmp_path: Path) -> None:
        """[shipped, pending-dep-on-blocked, pending-no-deps] → drainable."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
            - [beta](2026-01-01-beta.md)
            - [gamma](2026-01-01-gamma.md)
        """)
        _write_plan(plans / "2026-01-01-alpha.md", """\
            ---
            status: shipped
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-beta.md", """\
            ---
            status: blocked
            depends_on: []
            ---
        """)
        _write_plan(plans / "2026-01-01-gamma.md", """\
            ---
            status: pending
            depends_on: []
            ---
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_missing_subplan_file_is_runnable(self, tmp_path: Path) -> None:
        """A registered sub-plan whose file is missing counts as outstanding
        work (matching master_has_nonshipped semantics) — and since its
        status can't be read, treat as pending (runnable)."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            - [alpha](2026-01-01-alpha.md)
        """)
        # alpha.md does NOT exist on disk
        assert master_is_drainable(plans / "MASTER.md", plans) is True

    def test_no_registered_subplans_is_not_drainable(self, tmp_path: Path) -> None:
        """A master with no sub-plan references has nothing to drain."""
        plans = tmp_path / "plans"
        plans.mkdir()
        _write_plan(plans / "MASTER.md", """\
            ---
            status: active
            ---
            No sub-plan references here.
        """)
        assert master_is_drainable(plans / "MASTER.md", plans) is False
