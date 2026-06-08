"""supervised_only guard test: a master flagged ``supervised_only: true`` is
excluded from the autonomous scheduler (``scheduler_scan``) and from
``promote_next_master``, but is STILL selectable by ``loop_status`` (manual
``/ilk``). The flag blocks *autonomy*, not *execution*.

Reuses the fixture harness from ``test_master_selection_agreement``.
"""
from __future__ import annotations

from test_master_selection_agreement import (
    _write_subplan,
    _read_loop_status,
    _read_scan_projects,
    _run_promote,
    _selected_from_scan,
)


def _write_master_supervised(plans_dir, name, *, status, subplans,
                             supervised_only=True):
    """Write a MASTER with a ``supervised_only`` frontmatter flag."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        "created: 2026-06-08T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        f"supervised_only: {'true' if supervised_only else 'false'}",
        "---",
        "",
        f"# {name}",
        "",
        "## Sub-plan registry",
        "",
        "| # | Sub-plan | Status |",
        "|---|---|---|",
    ]
    for sp in subplans:
        lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


class TestSupervisedOnlyGuard:
    """Blocks the autonomous scheduler/promote (AC-1); manual path intact (AC-2)."""

    def test_excluded_from_scheduler_and_promote(self, tmp_path):
        """AC-1: supervised_only master is invisible to scan + promote."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master_supervised(plans, "MASTER-sup.md", status="queued",
                                 subplans=["2026-06-08-infra.md"])
        _write_subplan(plans, "2026-06-08-infra.md", status="pending",
                       current_step=0, estimated_steps=4)

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert not _selected_from_scan(scan, "test-proj"), (
            "scheduler must NOT dispatch a supervised_only master"
        )

        promote = _run_promote(plans)
        assert promote["promoted"] is None, (
            "promote must NOT auto-promote a supervised_only master"
        )

    def test_still_runnable_manually(self, tmp_path):
        """AC-2: an active supervised_only master is still selected by loop_status."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master_supervised(plans, "MASTER-sup.md", status="active",
                                 subplans=["2026-06-08-infra.md"])
        _write_subplan(plans, "2026-06-08-infra.md", status="pending",
                       current_step=0, estimated_steps=4)

        ls = _read_loop_status(plans)
        assert ls["queue_exit"] == 1, (
            "manual /ilk must still run a supervised_only master"
        )
        assert ls["next"] is not None
        assert "infra" in ls["next"]["fname"]
