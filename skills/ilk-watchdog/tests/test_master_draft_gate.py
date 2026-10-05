"""Draft-gate test: a ``draft`` master is non-runnable across all three
readers (loop_status, scheduler_scan, promote_next_master); flipping it to
``queued`` makes it runnable.

This closes the authoring race behind the 2026-06-08 self-dispatch incident:
``/ilk-plan`` authors masters as ``draft`` (non-runnable) while writing/QC'ing
them, then flips to ``queued`` once ready.

Reuses the fixture harness from ``test_master_selection_agreement``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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


# ── draft-only dry period ─────────────────────────────────────────────────


def _write_master_with_auto_planned(plans_dir: Path, name: str, *,
                                     status: str = "draft",
                                     subplans: list[str] | None = None,
                                     auto_planned: bool = False) -> None:
    """Write a MASTER with optional auto_planned frontmatter."""
    subplans = subplans or []
    rows = "\n".join(
        f"| {i} | [{s}](./{s}) | 1 | 2 | pending |"
        for i, s in enumerate(subplans)
    )
    auto_planned_line = f"\nauto_planned: true" if auto_planned else ""
    body = f"""---
master_plan: test-batch
batch_date: 2026-06-08
source_status: 可执行
total_tickets: {len(subplans)}
status: {status}{auto_planned_line}
current_subplan: {subplans[0] if subplans else ""}
---

# MASTER plan: test batch

## Sub-plan registry

| # | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|
{rows}
"""
    (plans_dir / name).write_text(body, encoding="utf-8")


def _setup_project_with_config(tmp_path: Path, *, autoplan_config: dict,
                                plans_status: str = "draft",
                                auto_planned: bool = True) -> Path:
    """Set up a full project structure with .ilk-launch.json config.

    Returns the plans directory.
    """
    # Create toolkit repo with config
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir(parents=True, exist_ok=True)
    (toolkit / ".ilk-launch.json").write_text(
        json.dumps({"autoplan": autoplan_config}) + "\n",
        encoding="utf-8",
    )

    # Create project data dir
    project_dir = tmp_path / "projects" / "test-proj"
    (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
    (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
        json.dumps({"project_path": str(toolkit)}) + "\n",
        encoding="utf-8",
    )

    # Create plans
    plans = project_dir / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    _write_master_with_auto_planned(
        plans, "MASTER-draft.md", status=plans_status,
        subplans=["2026-06-08-work.md"], auto_planned=auto_planned,
    )
    _write_subplan(plans, "2026-06-08-work.md", status="pending",
                   current_step=0, estimated_steps=4)
    return plans


class TestDraftOnlyDryPeriod:
    """Auto-planned draft master with draft_only config stays non-runnable."""

    def test_draft_only_auto_planned_master_non_runnable(self, tmp_path):
        """AC-1: auto-planned draft master + draft_only: true → non-runnable."""
        _setup_project_with_config(
            tmp_path,
            autoplan_config={"enabled": True, "draft_only": True},
            plans_status="draft",
            auto_planned=True,
        )

        # scheduler_scan: project not runnable
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert not _selected_from_scan(scan, "test-proj"), (
            "scheduler must NOT see a draft-only auto-planned master as runnable"
        )

        # loop_status: nothing actionable
        plans = tmp_path / "projects" / "test-proj" / "plans"
        ls = _read_loop_status(plans)
        assert ls["queue_exit"] == 0, "draft-only master must report nothing-to-do"
        assert ls["next"] is None, "draft-only master must yield no next sub-plan"

        # promote_next_master: nothing to promote
        promote = _run_promote(plans)
        assert promote["promoted"] is None, "draft-only master must not be promoted"

    def test_draft_only_false_auto_planned_master_runnable(self, tmp_path):
        """AC-3: auto-planned draft master + draft_only: false → can be promoted."""
        _setup_project_with_config(
            tmp_path,
            autoplan_config={"enabled": True, "draft_only": False},
            plans_status="queued",
            auto_planned=True,
        )

        # scheduler_scan: project is runnable when queued
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan, "test-proj"), (
            "queued auto-planned master must be runnable when draft_only is false"
        )

    def test_draft_only_absent_auto_planned_master_runnable(self, tmp_path):
        """AC-3: no draft_only key → default false, queued master is runnable."""
        _setup_project_with_config(
            tmp_path,
            autoplan_config={"enabled": True},  # no draft_only key
            plans_status="queued",
            auto_planned=True,
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan, "test-proj"), (
            "absent draft_only must default to false; queued master is runnable"
        )
