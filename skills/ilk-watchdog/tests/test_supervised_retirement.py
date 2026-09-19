"""Red-first tests for the supervised_only retirement (Option B).

These tests pin the NEW desired behavior — that ``supervised_only`` is
tolerated-and-ignored everywhere — and are RED under the current code
(the skip checks still fire).  They go green in step 1 when the skip
checks and preflight hard-stop are removed.

AC-1: ``scheduler_scan.scan_projects`` dispatches a ``supervised_only: true``
      fixture master the same as a ``false`` one.
AC-2: ``preflight_decision`` returns ``block=false`` when
      ``supervised=true`` and ``scheduler_alive=true`` (hard-stop removed).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from test_master_selection_agreement import (
    _read_scan_projects,
    _selected_from_scan,
    _write_master,
    _write_subplan,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PREFLIGHT_SH = REPO_ROOT / "skills" / "ilk-runner" / "scripts" / "preflight.sh"


def _write_master_supervised(plans_dir: Path, name: str, *, status: str = "queued",
                              subplans: list[str] | None = None) -> None:
    """Write a MASTER with ``supervised_only: true``."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        "created: 2026-09-20T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        "supervised_only: true",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines.extend([
            "## Sub-plan registry",
            "",
            "| # | Sub-plan | Status |",
            "|---|---|---|",
        ])
        for sp in subplans:
            lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _run_preflight_decision(master_status: str, has_active: str,
                             supervised: str, scheduler_alive: str) -> dict[str, str]:
    """Call ``preflight_decision`` via bash dot-source and parse its output."""
    # Source the dot-source guard, call the function, parse output.
    cmd = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{PREFLIGHT_SH}'; "
        f"preflight_decision '{master_status}' '{has_active}' '{supervised}' '{scheduler_alive}'"
    )
    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"preflight_decision failed: exit={result.returncode} stderr={result.stderr}"
    )
    out = {}
    for line in result.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


# ── AC-1: scheduler_scan dispatches supervised_only fixture ──────────

class TestSupervisedRetirementScan:
    """AC-1: ``scheduler_scan`` does NOT skip ``supervised_only`` masters."""

    def test_scan_dispatches_supervised_only_master(self, tmp_path):
        """A supervised_only: true queued master is dispatched by scan_projects.

        Currently RED — scheduler_scan.py:294 and :430 both skip masters
        with supervised_only set.  Goes green when those checks are removed.
        """
        plans = tmp_path / "projects" / "test-proj" / "plans"
        _write_master_supervised(
            plans, "MASTER-supervised.md",
            status="queued",
            subplans=["2026-09-20-work.md"],
        )
        _write_subplan(plans, "2026-09-20-work.md", status="pending",
                       current_step=0, estimated_steps=4)

        scan_results = _read_scan_projects(tmp_home=tmp_path)
        assert _selected_from_scan(scan_results, "test-proj"), (
            "scheduler_scan must dispatch a supervised_only: true master "
            "after the retirement — the flag no longer gates dispatch"
        )

# ── AC-2: preflight passes with live scheduler ──────────────────────

class TestSupervisedRetirementPreflight:
    """AC-2: ``preflight_decision`` no longer blocks on supervised+alive."""

    def test_preflight_passes_supervised_with_live_scheduler(self):
        """supervised=true + scheduler_alive=true → block=false.

        Currently RED — preflight_decision hard-stops this combination.
        Goes green when the (a) branch is removed.
        """
        result = _run_preflight_decision("active", "true", "true", "true")
        assert result.get("block") == "false", (
            "after retirement, preflight must NOT block a supervised_only "
            "master when a scheduler is alive — the hard-stop is removed"
        )

    def test_preflight_passes_supervised_without_live_scheduler(self):
        """supervised=true + scheduler_alive=false → block=false (sanity).

        This case was already non-blocking before retirement; confirms the
        removal doesn't regress the non-scheduler path.
        """
        result = _run_preflight_decision("active", "false", "true", "false")
        assert result.get("block") == "false", (
            "supervised + no scheduler should pass (was already non-blocking)"
        )