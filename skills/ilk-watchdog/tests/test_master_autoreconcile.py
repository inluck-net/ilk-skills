"""Auto-reconcile master status on ship.

Tests that when all registered sub-plans of a master are shipped, the
master's frontmatter ``status:`` is persisted to ``shipped`` — and that
this reconcile is idempotent (no rewrite churn) and preserves the rest
of the file byte-for-byte.

AC-1..AC-5 from 2026-06-08-master-autoreconcile-on-ship.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"


# ── helpers ─────────────────────────────────────────────────────────

def _write_master(plans_dir: Path, name: str, *, status: str = "active",
                  subplans: list[str] | None = None,
                  body_extra: str = "") -> None:
    """Write a minimal MASTER-*.md with sub-plan references in the body."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        f"created: 2026-06-08T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines.append("## Sub-plan registry")
        lines.append("")
        lines.append("| # | Sub-plan | Status |")
        lines.append("|---|---|---|")
        for sp in subplans:
            lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
        lines.append("")
    if body_extra:
        lines.append(body_extra)
        lines.append("")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _write_subplan(plans_dir: Path, name: str, *, status: str = "pending",
                   current_step: int = 0, estimated_steps: int = 3) -> None:
    """Write a minimal sub-plan *.md."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"plan: {name.replace('.md', '')}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        "last_updated: 2026-06-08\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _import_plan_status():
    """Import plan_status from the skill scripts, fresh each time."""
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("plan_status",):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import plan_status
    return plan_status


# ── tests ───────────────────────────────────────────────────────────

class TestReconcileMasterStatus:
    """AC-1..AC-5: auto-reconcile master status when all sub-plans ship."""

    def test_flip_on_all_shipped(self, tmp_path):
        """AC-1: last sub-plan flips to shipped → master status becomes shipped."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-06-08-b.md", status="pending",
                       current_step=0, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        # Not all shipped yet — should not flip.
        assert ps.reconcile_master_status(master_path, plans) is False
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "active"

        # Ship the last sub-plan.
        _write_subplan(plans, "2026-06-08-b.md", status="shipped",
                       current_step=3, estimated_steps=3)

        # Now reconcile should flip.
        assert ps.reconcile_master_status(master_path, plans) is True
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "shipped"

    def test_idempotent(self, tmp_path):
        """AC-2: running reconcile again on already-shipped is a no-op."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="shipped",
                      subplans=["2026-06-08-a.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        # Already shipped — should return False (no change).
        assert ps.reconcile_master_status(master_path, plans) is False

        # Verify file is unchanged.
        content_before = master_path.read_text(encoding="utf-8")
        assert ps.reconcile_master_status(master_path, plans) is False
        content_after = master_path.read_text(encoding="utf-8")
        assert content_before == content_after

    def test_body_preserved(self, tmp_path):
        """AC-3: only frontmatter status line changes; body is byte-for-byte."""
        plans = tmp_path / "plans"
        body_extra = "Some extra body content\nwith multiple lines\n"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md"], body_extra=body_extra)
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        content_before = master_path.read_text(encoding="utf-8")
        # Extract body (after closing ---)
        fm_end = content_before.find("\n---", 3)
        body_before = content_before[fm_end:]

        ps.reconcile_master_status(master_path, plans)

        content_after = master_path.read_text(encoding="utf-8")
        fm_end_after = content_after.find("\n---", 3)
        body_after = content_after[fm_end_after:]

        assert body_before == body_after, "Body should be unchanged"
        # Verify status changed.
        fm = ps.parse_frontmatter(content_after)
        assert fm["status"] == "shipped"

    def test_loop_status_reports_shipped(self, tmp_path):
        """AC-4: after reconcile, loop_status doesn't select the master."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"
        ps.reconcile_master_status(master_path, plans)

        # Verify the status is now shipped.
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "shipped"

        # Verify master_has_nonshipped returns False.
        assert ps.master_has_nonshipped(master_path, plans) is False
        assert ps.is_master_all_shipped(master_path, plans) is True

    def test_mixed_subplans_no_flip(self, tmp_path):
        """Not all shipped → no flip."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="queued",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-06-08-b.md", status="in-progress",
                       current_step=1, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        assert ps.reconcile_master_status(master_path, plans) is False
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "queued"


class TestMissingSubplanNotFalseShip:
    """AC-1..AC-4: a missing registered sub-plan file counts as non-shipped."""

    def test_missing_file_prevents_ship(self, tmp_path):
        """AC-1: registry lists 2 sub-plans, only 1 file exists and shipped
        → master_has_nonshipped is True (missing file = outstanding work)."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        # Only create one file — the other is missing on disk.
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        assert ps.master_has_nonshipped(master_path, plans) is True
        assert ps.is_master_all_shipped(master_path, plans) is False

    def test_missing_file_no_reconcile(self, tmp_path):
        """AC-2: reconcile_master_status does NOT flip when a sub-plan file
        is missing (prevents the false-ship that hit MASTER mid-authoring)."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        # 2026-06-08-b.md intentionally NOT created.

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        assert ps.reconcile_master_status(master_path, plans) is False
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "active"

    def test_all_files_present_and_shipped_flips(self, tmp_path):
        """AC-3: both files present + all shipped → reconcile flips to shipped
        (regression-safe: the legitimate path still works)."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-06-08-b.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        assert ps.reconcile_master_status(master_path, plans) is True
        fm = ps.parse_frontmatter(master_path.read_text(encoding="utf-8"))
        assert fm["status"] == "shipped"

    def test_present_but_pending_subplan_no_flip(self, tmp_path):
        """AC-4: a present-but-pending sub-plan → non-shipped (existing behavior)."""
        plans = tmp_path / "plans"
        _write_master(plans, "MASTER-test.md", status="active",
                      subplans=["2026-06-08-a.md", "2026-06-08-b.md"])
        _write_subplan(plans, "2026-06-08-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-06-08-b.md", status="pending",
                       current_step=0, estimated_steps=3)

        ps = _import_plan_status()
        master_path = plans / "MASTER-test.md"

        assert ps.master_has_nonshipped(master_path, plans) is True
        assert ps.reconcile_master_status(master_path, plans) is False
