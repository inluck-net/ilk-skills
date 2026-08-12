"""Test: a master whose only outstanding sub-plan is blocked is NOT dispatchable.

Pins AC-4 of the scheduler-skips-a-drained-but-blocked-master sub-plan:
  - A project data dir with one ``active`` master registering 3 sub-plans
    (2 ``shipped``, 1 ``blocked``) must produce an empty scan result.
  - Flipping the blocked sub-plan back to ``pending`` must re-include the
    project.

The first test is xfail(strict=True) until step 2 lands the fix — the
failure is the proof the defect is real.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
SCAN_SCRIPT = SCRIPTS_ILK_WATCHDOG / "scheduler_scan.py"


# ── helpers (copied from test_master_pending_as_queued.py:26-55) ──────

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
        f"last_updated: 2026-08-12\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _run_scan(tmp_home: Path) -> list[dict]:
    """Run scheduler_scan.py as a subprocess with isolated HOME and ILK_SKILL_HOME."""
    env = {
        "HOME": str(tmp_home),
        "ILK_SKILL_HOME": str(REPO_ROOT / "skills"),
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [sys.executable, str(SCAN_SCRIPT)],
        capture_output=True, text=True, timeout=30,
        env=env, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    import json
    return json.loads(result.stdout)


# ── tests ─────────────────────────────────────────────────────────────

class TestBlockedNotDispatchable:
    """A master whose only outstanding sub-plan is blocked must not dispatch."""

    @pytest.mark.xfail(strict=True, reason="defect: scheduler_dispatches_blocked_master")
    def test_blocked_only_master_returns_empty_scan(self, tmp_path):
        """AC-4: active master with 2 shipped + 1 blocked → scan returns []."""
        plans = tmp_path / ".ilk-data" / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-2026-08-12-execution.md", status="active",
                      subplans=[
                          "2026-08-12-task-a.md",
                          "2026-08-12-task-b.md",
                          "2026-08-12-task-c.md",
                      ])
        _write_subplan(plans, "2026-08-12-task-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-08-12-task-b.md", status="shipped",
                       current_step=4, estimated_steps=4)
        _write_subplan(plans, "2026-08-12-task-c.md", status="blocked",
                       current_step=0, estimated_steps=4)

        scan = _run_scan(tmp_path)
        assert scan == [], (
            f"scan should return [] for blocked-only master, got {scan}"
        )

    def test_unblocked_subplan_re_includes_project(self, tmp_path):
        """Flipping the blocked sub-plan back to pending re-includes the project."""
        plans = tmp_path / ".ilk-data" / "projects" / "test-proj" / "plans"
        _write_master(plans, "MASTER-2026-08-12-execution.md", status="active",
                      subplans=[
                          "2026-08-12-task-a.md",
                          "2026-08-12-task-b.md",
                          "2026-08-12-task-c.md",
                      ])
        _write_subplan(plans, "2026-08-12-task-a.md", status="shipped",
                       current_step=3, estimated_steps=3)
        _write_subplan(plans, "2026-08-12-task-b.md", status="shipped",
                       current_step=4, estimated_steps=4)
        _write_subplan(plans, "2026-08-12-task-c.md", status="pending",
                       current_step=0, estimated_steps=4)

        scan = _run_scan(tmp_path)
        assert len(scan) == 1, (
            f"scan should include project with pending sub-plan, got {scan}"
        )
        assert scan[0]["key"] == "test-proj"
