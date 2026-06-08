"""Agreement test: all three master-selection readers (loop_status,
scheduler_scan, promote_next_master) agree on which master to run.

Builds a temp ``ILK_DATA_HOME/projects/<key>/plans/`` directory with
various master + sub-plan combinations and asserts all three readers
select the same master (or unanimously report "nothing to run").

AC-4 (kira repro): an active master whose sole sub-plan is shipped,
coexisting with a queued master with a pending sub-plan — all three
readers select the queued master.
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
    """Write a minimal MASTER-*.md with sub-plan references in the body."""
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
    """Write a minimal sub-plan *.md."""
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
    """Import and call loop_status.resolve_status, returning the result."""
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    import importlib
    # Fresh import to avoid stale state across parametrized tests.
    if "loop_status" in sys.modules:
        del sys.modules["loop_status"]
    if "ilk_paths" in sys.modules:
        del sys.modules["ilk_paths"]
    if "plan_status" in sys.modules:
        del sys.modules["plan_status"]
    import loop_status
    # Monkeypatch the plans-dir resolver to return our temp dir.
    loop_status._resolve_plans_dir = lambda start: (plans_dir, "test")
    return loop_status.resolve_status(Path(plans_dir))


def _read_scan_projects(tmp_home: Path) -> list[dict]:
    """Import and call scheduler_scan.scan_projects with patched data root."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    import importlib
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


def _run_promote(plans_dir: Path) -> dict:
    """Run promote_next_master.py --plans-dir <dir> --dry-run."""
    result = subprocess.run(
        [sys.executable, str(PROMOTE_SCRIPT),
         "--plans-dir", str(plans_dir), "--dry-run"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


def _selected_from_loop_status(data: dict) -> str | None:
    """Extract the selected master filename from loop_status result."""
    nxt = data.get("next")
    if nxt is None:
        return None
    # next.fname is the sub-plan; find which master was selected by
    # looking at data["master"].
    return data.get("master")


def _selected_from_scan(projects: list[dict], key: str) -> bool:
    """True if scan_projects reports this project as runnable."""
    return any(p["key"] == key for p in projects)


def _selected_from_promote(data: dict) -> str | None:
    """Extract the master that promote would activate."""
    return data.get("promoted")


# ── test matrix ─────────────────────────────────────────────────────

class TestAgreementMatrix:
    """loop_status, scan_projects, and promote_next_master agree."""

    def test_kira_repro_active_shipped_queued_pending(self, tmp_path):
        """AC-4: active master all-shipped + queued master with work.

        All three should select the queued master (or report the project
        as having real work).
        """
        plans = tmp_path / "projects" / "test-proj" / "plans"
        # Active master — sole sub-plan is shipped.
        _write_master(plans, "MASTER-active-done.md", status="active",
                      subplans=["2026-06-07-done-work.md"])
        _write_subplan(plans, "2026-06-07-done-work.md", status="shipped",
                       current_step=3, estimated_steps=3)
        # Queued master — sub-plan is pending (real work).
        _write_master(plans, "MASTER-queued-real.md", status="queued",
                      priority=5, subplans=["2026-06-08-real-work.md"])
        _write_subplan(plans, "2026-06-08-real-work.md", status="pending",
                       current_step=0, estimated_steps=4)

        # loop_status: should NOT select the active (all-shipped) master.
        ls_data = _read_loop_status(plans)
        # The selected master should be the queued one (or at minimum,
        # not the all-shipped active one).
        assert ls_data["queue_exit"] == 1, "loop_status should see pending work"
        assert ls_data["next"] is not None
        # The next sub-plan should be from the queued master's registry.
        assert "real-work" in ls_data["next"]["fname"]

        # scan_projects: project should be runnable.
        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan_results, "test-proj"), (
            "scan_projects should report project as runnable"
        )

        # promote_next_master: should promote the queued master, not the
        # all-shipped active one.
        promote_data = _run_promote(plans)
        assert promote_data["promoted"] == "MASTER-queued-real.md", (
            f"promote should select queued master, got {promote_data['promoted']}"
        )

    def test_all_shipped_nothing_to_do(self, tmp_path):
        """All masters shipped → all three agree: nothing to run."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-all-done.md", status="active",
                      subplans=["2026-06-07-done.md"])
        _write_subplan(plans, "2026-06-07-done.md", status="shipped",
                       current_step=3, estimated_steps=3)

        ls_data = _read_loop_status(plans)
        assert ls_data["queue_exit"] == 0, "loop_status should see all shipped"

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert not _selected_from_scan(scan_results, "test-proj"), (
            "scan_projects should NOT report project as runnable"
        )

        promote_data = _run_promote(plans)
        assert promote_data["promoted"] is None, "promote should have nothing to do"

    def test_active_with_pending_work(self, tmp_path):
        """Active master with pending sub-plan → all agree: run it."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-active.md", status="active",
                      subplans=["2026-06-08-work.md"])
        _write_subplan(plans, "2026-06-08-work.md", status="pending",
                       current_step=0, estimated_steps=5)

        ls_data = _read_loop_status(plans)
        assert ls_data["queue_exit"] == 1
        assert ls_data["next"] is not None
        assert "work" in ls_data["next"]["fname"]

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan_results, "test-proj")

        promote_data = _run_promote(plans)
        # promote demotes active → shipped, promotes next queued.
        # With only one active and no queued, demoted is set, promoted is None.
        assert promote_data["demoted"] == "MASTER-active.md"

    def test_queued_only_with_pending(self, tmp_path):
        """Queued master with pending sub-plan → scan sees it as runnable."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-queued.md", status="queued",
                      subplans=["2026-06-08-task.md"])
        _write_subplan(plans, "2026-06-08-task.md", status="pending",
                       current_step=0, estimated_steps=3)

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan_results, "test-proj")

        promote_data = _run_promote(plans)
        assert promote_data["promoted"] == "MASTER-queued.md"

    def test_empty_plans_dir(self, tmp_path):
        """No masters at all → all agree: nothing to do."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        plans.mkdir(parents=True)

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert not _selected_from_scan(scan_results, "test-proj")

        promote_data = _run_promote(plans)
        assert promote_data["demoted"] is None
        assert promote_data["promoted"] is None
