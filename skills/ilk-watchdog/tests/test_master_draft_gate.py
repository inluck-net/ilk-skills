"""Draft-gate test: a ``draft`` master is non-runnable across all three
readers (loop_status, scheduler_scan, promote_next_master); flipping it to
``queued`` makes it runnable.

This closes the authoring race behind the 2026-06-08 self-dispatch incident:
``/ilk-plan`` authors masters as ``draft`` (non-runnable) while writing/QC'ing
them, then flips to ``queued`` once ready.

Reuses the fixture harness from ``test_master_selection_agreement``.
"""
from __future__ import annotations

from test_master_selection_agreement import (
    _write_master,
    _write_subplan,
    _read_loop_status,
    _read_scan_projects,
    _run_promote,
    _selected_from_scan,
)


class TestDraftGate:
    """A draft master is invisible to all three readers (AC-1); flipping to
    queued makes it runnable (AC-2)."""

    def test_draft_master_is_non_runnable(self, tmp_path):
        """AC-1: draft master + pending sub-plan → non-runnable everywhere."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-draft.md", status="draft",
                      subplans=["2026-06-08-work.md"])
        _write_subplan(plans, "2026-06-08-work.md", status="pending",
                       current_step=0, estimated_steps=4)

        # loop_status (manual path): nothing actionable, exit 0.
        ls = _read_loop_status(plans)
        assert ls["queue_exit"] == 0, "draft master must report nothing-to-do"
        assert ls["next"] is None, "draft master must yield no next sub-plan"

        # scheduler_scan (autonomous path): project not runnable.
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert not _selected_from_scan(scan, "test-proj"), (
            "scheduler must NOT see a draft-only project as runnable"
        )

        # promote_next_master: nothing to promote.
        promote = _run_promote(plans)
        assert promote["promoted"] is None, "draft master must not be promoted"

    def test_flip_to_queued_makes_runnable(self, tmp_path):
        """AC-2: same master flipped draft → queued becomes runnable."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-draft.md", status="queued",
                      subplans=["2026-06-08-work.md"])
        _write_subplan(plans, "2026-06-08-work.md", status="pending",
                       current_step=0, estimated_steps=4)

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan, "test-proj"), (
            "queued master with pending work must be runnable"
        )

        promote = _run_promote(plans)
        assert promote["promoted"] == "MASTER-draft.md", (
            "queued master must be the promotion target"
        )
