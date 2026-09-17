"""Red-gate unships master — reconciliation is not symmetric.

A master whose sub-plan is reverted to `in-progress` by `test_ship_integrity`
should no longer read `status: shipped`.  Measured on gh-resolve run
`20260917-203304`: `MASTER-2026-09-17-execution-plan.md` remained `shipped`
while a registered sub-plan was `in-progress`.  The scheduler saw `shipped` and
stopped dispatching the batch — `idle (all-queues-empty)` every 5 minutes.

AC-1  A master whose sub-plans are all `shipped` still reconciles to `shipped`
      (positive control).
AC-2  A master marked `shipped` whose sub-plan is then reverted to
      `in-progress` no longer reads `shipped` after reconciliation.
AC-3  After AC-2, the master is in `_RUNNABLE_STATUSES`, proven by calling the
      predicate, not by asserting a literal string.
AC-4  Reconciliation is idempotent in both directions — repeated calls produce
      no rewrite churn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
from plan_status import (  # noqa: E402
    _RUNNABLE_STATUSES,
    is_master_runnable_status,
    parse_frontmatter,
    reconcile_master_status,
)


# ── fixtures ────────────────────────────────────────────────────────────────

_MASTER_FM = (
    "---\n"
    "title: demo\n"
    "created: 2026-09-17T00:00:00+08:00\n"
    "status: queued\n"
    "priority: 0\n"
    "---\n\n"
    "# demo\n\n"
    "| # | file |\n|---|---|\n"
    "| 0 | [2026-09-17-work.md](2026-09-17-work.md) |\n"
)

_SUBPLAN_SHIPPED = (
    "---\n"
    "plan: 2026-09-17-work\n"
    "status: shipped\n"
    "current_step: 4\n"
    "estimated_steps: 4\n"
    "---\n\n"
    "### Step 0\n- work\n"
)

_SUBPLAN_IN_PROGRESS = (
    "---\n"
    "plan: 2026-09-17-work\n"
    "status: in-progress\n"
    "current_step: 2\n"
    "estimated_steps: 4\n"
    "---\n\n"
    "### Step 0\n- work\n"
)


@pytest.fixture
def plans(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    (d / "MASTER-2026-09-17-demo.md").write_text(_MASTER_FM, encoding="utf-8")
    (d / "2026-09-17-work.md").write_text(_SUBPLAN_SHIPPED, encoding="utf-8")
    return d


def _master_fm(plans: Path) -> dict:
    return parse_frontmatter(
        (plans / "MASTER-2026-09-17-demo.md").read_text(encoding="utf-8-sig"))


def _reconcile_master_text(plans: Path) -> bool:
    """Return True if the master was *modified* by reconciliation."""
    return reconcile_master_status(plans / "MASTER-2026-09-17-demo.md", plans)


# ── AC-1: positive control — all shipped still reconciles ──────────────────

class TestPositiveControl:
    def test_all_shipped_flips_to_shipped(self, plans: Path) -> None:
        """The master starts as `queued`; when every sub-plan is `shipped`,
        reconciliation must flip the master to `shipped`."""
        result = _reconcile_master_text(plans)
        assert result is True, "master should have been modified"
        assert _master_fm(plans)["status"] == "shipped"


# ── AC-2: stranded master — revert breaks the shipped claim ────────────────

class TestStrandedMaster:
    def test_reverted_subplan_unships_master(self, plans: Path) -> None:
        """After a sub-plan is reverted to `in-progress`, the master must
        no longer read `shipped` after reconciliation.

        This is the defect measured on gh-resolve run 20260917-203304:
        reconcile_master_status returns early (False) when
        is_master_all_shipped() is False, so it never flips away from
        `shipped`.  This test is RED until the fix lands.
        """
        # First: reconcile to shipped.
        _reconcile_master_text(plans)
        assert _master_fm(plans)["status"] == "shipped"

        # Simulate ship_integrity reverting the sub-plan.
        (plans / "2026-09-17-work.md").write_text(
            _SUBPLAN_IN_PROGRESS, encoding="utf-8")

        # Re-reconcile.
        _reconcile_master_text(plans)

        # This assertion is the defect: the master should NOT still be shipped.
        assert _master_fm(plans)["status"] != "shipped", (
            "a master with a reverted sub-plan still reads `shipped` — "
            "reconcile_master_status is not symmetric"
        )

    def test_unshipped_master_is_runnable(self, plans: Path) -> None:
        """AC-3: after AC-2, the master must be in _RUNNABLE_STATUSES."""
        _reconcile_master_text(plans)
        (plans / "2026-09-17-work.md").write_text(
            _SUBPLAN_IN_PROGRESS, encoding="utf-8")
        _reconcile_master_text(plans)

        status = _master_fm(plans)["status"]
        assert status in _RUNNABLE_STATUSES, (
            f"master status is `{status}` which is not in "
            f"_RUNNABLE_STATUSES={_RUNNABLE_STATUSES} — the scheduler "
            f"will not dispatch it"
        )
        assert is_master_runnable_status(status), (
            "is_master_runnable_status rejects the reconciled status"
        )


# ── AC-4: idempotence — no rewrite churn ───────────────────────────────────

class TestIdempotence:
    def test_shipped_is_idempotent(self, plans: Path) -> None:
        """Consecutive reconciliations on an all-shipped master must be no-ops."""
        _reconcile_master_text(plans)  # flip once
        first = (plans / "MASTER-2026-09-17-demo.md").read_bytes()
        assert _reconcile_master_text(plans) is False
        assert (plans / "MASTER-2026-09-17-demo.md").read_bytes() == first

    def test_unshipped_is_idempotent(self, plans: Path) -> None:
        """Consecutive reconciliations on a reverted master must be no-ops."""
        _reconcile_master_text(plans)
        (plans / "2026-09-17-work.md").write_text(
            _SUBPLAN_IN_PROGRESS, encoding="utf-8")
        _reconcile_master_text(plans)  # first flip
        first = (plans / "MASTER-2026-09-17-demo.md").read_bytes()
        assert _reconcile_master_text(plans) is False
        assert (plans / "MASTER-2026-09-17-demo.md").read_bytes() == first
