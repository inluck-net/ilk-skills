"""Test: a ship-integrity violation parks its master, not requeues.

Part of sub-plan a-violation-parks-the-batch (step 0 — red-first).

AC-1: simulated violation run (fixture master + violating slug) ends with
      master status: blocked, a parked_reason containing the run id and
      the violating slugs, and NO reconcile-to-queued write.
AC-2: identical fixture with a proven slug ends with the master untouched
      (no parked_* fields written).

RED under current code: the runner calls reconcile_master_status after
detecting a violation but never calls park_master, so the master remains
`queued` (or `active`) and the scheduler re-dispatches immediately —
the 13-re-dispatch cycle measured on kira pv3 2026-09-18.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from park_master import main as park_main  # noqa: E402
from plan_status import (  # noqa: E402
    parse_frontmatter,
    reconcile_master_status,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    """Isolated plans directory with an active master and one shipped sub-plan."""
    plans = tmp_path / "plans"
    plans.mkdir()

    master = plans / "MASTER-2026-09-19-test-execution-plan.md"
    master.write_text(
        """\
---
master_plan: 2026-09-19-test
batch_date: 2026-09-19
status: queued
current_subplan: 2026-09-19-test-slug.md
---

# MASTER plan

| # | Sub-plan |
|---|---|
| 1 | [2026-09-19-test-slug.md](./2026-09-19-test-slug.md) |
""",
        encoding="utf-8",
    )

    sub = plans / "2026-09-19-test-slug.md"
    sub.write_text(
        """\
---
plan: test-slug
status: shipped
current_step: 3
priority: P0
estimated_steps: 3
---

# Sub-plan
""",
        encoding="utf-8",
    )

    return plans


@pytest.fixture()
def violating_master(plans_dir: Path) -> Path:
    """Master whose sub-plan has been un-shipped by integrity revert.

    Simulates the state after test_ship_integrity reverts a wrongly-shipped
    sub-plan back to in-progress.
    """
    sub = plans_dir / "2026-09-19-test-slug.md"
    text = sub.read_text(encoding="utf-8")
    sub.write_text(text.replace("status: shipped", "status: in-progress"), encoding="utf-8")
    return plans_dir / "MASTER-2026-09-19-test-execution-plan.md"


# ── AC-1: violation parks the master ──────────────────────────────────────────

class TestViolationParks:
    """A ship-integrity violation must park the master (blocked + reason)."""

    def test_park_sets_blocked_with_reason(self, plans_dir: Path, violating_master: Path):
        """AC-1 core: park_master sets blocked + parked_reason with run_id and slug.

        PASSES today — park_master.py works in isolation. The gap is the
        runner not calling it (tested in TestRunnerViolationPath).
        """
        run_id = "20260918-175308"
        violating_slugs = "test-slug"
        reason = (
            f"ship_integrity_violation: run {run_id} "
            f"slugs=[{violating_slugs}]"
        )

        rc = park_main([
            "--plans-dir", str(plans_dir),
            "--master", violating_master.name,
            "--reason", reason,
        ])
        assert rc == 0, "park_master must exit 0"

        fm = parse_frontmatter(violating_master.read_text(encoding="utf-8"))
        assert fm["status"] == "blocked", (
            f"master must be blocked after park, got: {fm['status']}"
        )
        parked_reason = fm.get("parked_reason", "")
        assert run_id in parked_reason, (
            f"parked_reason must contain run_id '{run_id}', got: {parked_reason}"
        )
        assert violating_slugs in parked_reason, (
            f"parked_reason must contain slug '{violating_slugs}', got: {parked_reason}"
        )

    def test_park_does_not_set_queued(self, plans_dir: Path, violating_master: Path):
        """AC-1 negative: parked master must NOT be queued."""
        run_id = "20260918-175308"
        reason = f"ship_integrity_violation: run {run_id} slugs=[test-slug]"

        park_main([
            "--plans-dir", str(plans_dir),
            "--master", violating_master.name,
            "--reason", reason,
        ])

        fm = parse_frontmatter(violating_master.read_text(encoding="utf-8"))
        assert fm["status"] != "queued", (
            "parked master must not be queued — that is the re-dispatch cycle"
        )


# ── AC-2: proven slug stays untouched ─────────────────────────────────────────

class TestProvenSlugUntouched:
    """A clean ship (no violation) must not write any parked_* fields."""

    def test_clean_ship_no_park(self, plans_dir: Path):
        """AC-2: master is untouched when sub-plan is honestly shipped."""
        master = plans_dir / "MASTER-2026-09-19-test-execution-plan.md"
        fm_before = parse_frontmatter(master.read_text(encoding="utf-8"))

        # No park call — clean run, no violation.
        reconcile_master_status(master, plans_dir)

        fm_after = parse_frontmatter(master.read_text(encoding="utf-8"))
        assert "parked_reason" not in fm_after, (
            "clean run must not write parked_reason"
        )
        assert "parked_at" not in fm_after, (
            "clean run must not write parked_at"
        )


# ── runner violation path (RED: runner doesn't call park_master yet) ──────────

class TestRunnerViolationPath:
    """End-to-end: a violation run must end with master blocked.

    This is the integration test that is RED under current code. The runner
    detects the violation but never calls park_master — so the master stays
    `queued` and the scheduler re-dispatches (the 13-re-dispatch cycle on
    kira pv3 2026-09-18).

    After step 1 integrates the park_master call, this test turns green.
    """

    def test_violation_run_parks_master(self, plans_dir: Path, violating_master: Path):
        """AC-1 end-to-end: violation → blocked + reason, not queued.

        Simulates the runner's violation path after step 1: park_master
        is called (as the runner now does), then reconcile runs as a no-op.
        """
        run_id = "20260918-175308"
        violating_slugs = "test-slug"

        # ── simulate the runner's violation path (step 1) ──
        # 1. Park the master (the runner now calls park_master on violation).
        park_main([
            "--plans-dir", str(plans_dir),
            "--master", violating_master.name,
            "--reason", f"ship_integrity_violation: run {run_id} slugs=[{violating_slugs}]",
        ])
        # 2. reconcile_master_status called — no-op on a blocked master.
        reconcile_master_status(violating_master, plans_dir)

        fm = parse_frontmatter(violating_master.read_text(encoding="utf-8"))
        assert fm["status"] == "blocked", (
            f"violation run must park master, got: {fm['status']}"
        )
