"""Test: legacy ``status: pending`` masters are treated as ``queued``.

Verifies AC-1 and AC-4 of the schema-pending-queued-doc sub-plan:
  - A master with ``status: pending`` and >=1 non-shipped sub-plan is
    treated identically to ``status: queued`` by all three readers.
  - An all-shipped ``pending`` master is still skipped.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
PROMOTE_SCRIPT = SCRIPTS_ILK_LOOP / "promote_next_master.py"


# ── helpers ─────────────────────────────────────────────────────────

def _write_master(plans_dir: Path, name: str, *, status: str = "queued",
                  priority: int = 0, created: str = "2026-06-07T00:00:00+08:00",
                  subplans: list[str] | None = None) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body_lines = [
        "---",
        f"title: {name}",
        f"created: {created}",
        f"status: {status}",
        f"priority: {priority}",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        body_lines.append("## Sub-plan registry")
        body_lines.append("")
        body_lines.append("| # | Sub-plan | Status |")
        body_lines.append("|---|---|---|")
        for sp in subplans:
            body_lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
        body_lines.append("")
    (plans_dir / name).write_text("\n".join(body_lines), encoding="utf-8")


def _write_subplan(plans_dir: Path, name: str, *, status: str = "pending",
                   current_step: int = 0, estimated_steps: int = 3) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"plan: {name.replace('.md', '')}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        f"last_updated: 2026-06-07\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _read_loop_status(plans_dir: Path) -> dict:
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("loop_status", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import loop_status
    loop_status._resolve_plans_dir = lambda start: (plans_dir, "test")
    return loop_status.resolve_status(Path(plans_dir))


def _read_scan_projects(tmp_home: Path) -> list[dict]:
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


def _run_promote(plans_dir: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(PROMOTE_SCRIPT),
         "--plans-dir", str(plans_dir), "--dry-run"],
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


# ── tests ───────────────────────────────────────────────────────────

class TestPendingAsQueued:
    """Legacy ``status: pending`` masters behave like ``queued``."""

    def test_pending_master_with_work_is_runnable(self, tmp_path):
        """A pending master with non-shipped sub-plans is runnable in all readers."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-pending-work.md", status="pending",
                      subplans=["2026-06-08-task.md"])
        _write_subplan(plans, "2026-06-08-task.md", status="pending",
                       current_step=0, estimated_steps=3)

        # loop_status: should see pending work.
        ls_data = _read_loop_status(plans)
        assert ls_data["queue_exit"] == 1, "loop_status should see pending work"
        assert ls_data["next"] is not None
        assert "task" in ls_data["next"]["fname"]

        # scan_projects: should report as runnable.
        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert any(p["key"] == "test-proj" for p in scan_results), (
            "scan_projects should report project as runnable"
        )

        # promote_next_master: should promote the pending master.
        promote_data = _run_promote(plans)
        assert promote_data["promoted"] == "MASTER-pending-work.md", (
            f"promote should select pending master, got {promote_data['promoted']}"
        )

    def test_pending_master_all_shipped_is_skipped(self, tmp_path):
        """An all-shipped pending master is skipped (consistent with queued)."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-pending-done.md", status="pending",
                      subplans=["2026-06-07-done.md"])
        _write_subplan(plans, "2026-06-07-done.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ls_data = _read_loop_status(plans)
        assert ls_data["queue_exit"] == 0, "loop_status should see all shipped"

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert not any(p["key"] == "test-proj" for p in scan_results), (
            "scan_projects should NOT report all-shipped pending master as runnable"
        )

        promote_data = _run_promote(plans)
        assert promote_data["promoted"] is None, "promote should have nothing to do"

    def test_pending_active_shipped_prefer_pending_with_work(self, tmp_path):
        """Pending master with work is selected over an all-shipped active master."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-active-done.md", status="active",
                      subplans=["2026-06-07-done.md"])
        _write_subplan(plans, "2026-06-07-done.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_master(plans, "MASTER-pending-real.md", status="pending",
                      priority=5, subplans=["2026-06-08-real.md"])
        _write_subplan(plans, "2026-06-08-real.md", status="pending",
                       current_step=0, estimated_steps=4)

        ls_data = _read_loop_status(plans)
        assert ls_data["queue_exit"] == 1
        assert ls_data["next"] is not None
        assert "real" in ls_data["next"]["fname"]

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert any(p["key"] == "test-proj" for p in scan_results)

        promote_data = _run_promote(plans)
        assert promote_data["promoted"] == "MASTER-pending-real.md"
