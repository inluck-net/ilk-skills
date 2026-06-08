"""Scheduler dispatch test: repo_path resolution → dispatchable / skip-unresolved.

Tests the cross-platform Python core that BOTH scheduler.ps1 and
scheduler.sh consume. Hermetic — no live scheduler, no servers.

Part of sub-plan: scheduler-dispatch-verification (step 0).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"


# ── helpers ─────────────────────────────────────────────────────────


def _write_project_data(
    tmp_path: Path,
    key: str,
    *,
    master_status: str = "active",
    subplan_status: str = "pending",
    last_launch_path: str | None = None,
) -> Path:
    """Scaffold a temp project data dir under ``tmp_path/projects/<key>/``.

    Creates a minimal MASTER with one sub-plan reference and optionally
    a ``runtime/launcher/last-launch.json`` carrying *last_launch_path*.
    """
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Minimal MASTER-*.md
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-06-08T00:00:00+08:00\n"
        f"status: {master_status}\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | pending |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

    # Minimal sub-plan
    subplan = (
        "---\n"
        "plan: 2026-06-08-work\n"
        f"status: {subplan_status}\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-08\n"
        "---\n"
        "\n"
        "# 2026-06-08-work\n"
    )
    (plans_dir / "2026-06-08-work.md").write_text(subplan, encoding="utf-8")

    # Optional last-launch.json
    if last_launch_path is not None:
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        data = {"project_path": last_launch_path}
        (launcher_dir / "last-launch.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    return project_dir


def _read_scan_projects(tmp_home: Path) -> list[dict]:
    """Import and call ``scheduler_scan.scan_projects`` with patched data root."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


def _read_resolve_repo_path(project_dir: Path, key: str) -> str | None:
    """Import and call ``scheduler_scan.resolve_repo_path``."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    return scheduler_scan.resolve_repo_path(project_dir, key)


# ── tests ───────────────────────────────────────────────────────────


class TestSchedulerDispatch:
    """repo_path resolution → dispatchable / skip-unresolved."""

    def test_dispatchable_with_last_launch(self, tmp_path):
        """AC-1: active master + non-shipped sub-plan + valid last-launch.json
        → dispatchable (non-null repo_path)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )

        # Via scan_projects (the full scheduler path)
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] == "/some/real/repo"

        # Via resolve_repo_path directly
        repo_path = _read_resolve_repo_path(project_dir, "test-proj")
        assert repo_path == "/some/real/repo"

    def test_skip_unresolved_without_last_launch(self, tmp_path):
        """AC-2: active master + non-shipped sub-plan + no last-launch.json
        + not in registry → skip-unresolved (null repo_path)."""
        project_dir = _write_project_data(
            tmp_path, "test-proj",
            master_status="active",
            subplan_status="pending",
        )

        # Via scan_projects
        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] is None

        # Via resolve_repo_path directly
        repo_path = _read_resolve_repo_path(project_dir, "test-proj")
        assert repo_path is None

    def test_dispatchable_queued_master(self, tmp_path):
        """AC-1 variant: queued master + valid last-launch.json → dispatchable."""
        _write_project_data(
            tmp_path, "test-proj",
            master_status="queued",
            subplan_status="pending",
            last_launch_path="/some/real/repo",
        )

        scan = _read_scan_projects(tmp_home=tmp_path)
        assert len(scan) == 1
        assert scan[0]["key"] == "test-proj"
        assert scan[0]["repo_path"] == "/some/real/repo"
