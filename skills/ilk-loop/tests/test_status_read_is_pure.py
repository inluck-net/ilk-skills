"""Status read must not consume the dispatch trigger.

The defect: ``loop_status.py:357-358`` runs ``reconcile_master_status`` on
every invocation. ``scheduler_scan.py:383-398`` dispatches verification only
if *it* performed the completed→shipped transition. Two orderings over the
same fixture must each produce exactly one dispatch. Today,
resolve-then-scheduler produces zero — a read consumed the trigger.

This file is RED-FIRST: it pins the order-dependence as a failing test.
The fix (step 1) separates reconciliation from reporting.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"


# ── helpers ─────────────────────────────────────────────────────────


def _write_master(
    plans_dir: Path,
    name: str,
    *,
    status: str = "active",
    subplans: list[str] | None = None,
) -> None:
    """Write a minimal MASTER-*.md with a sub-plan registry."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name.replace('.md', '').replace('MASTER-', '')}",
        "created: 2026-09-08T00:00:00+08:00",
        f"status: {status}",
        "priority: null",
        "pause_after_ship: false",
        "supervised_only: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines += [
            "## Sub-plan registry",
            "",
            "| # | Order | Slug | Steps | Status |",
            "|---|---|---|---|---|",
        ]
        for sp in subplans:
            slug = sp.replace(".md", "")
            lines.append(f"| 1 | 1 | [{slug}](./{sp}) | 3 | shipped |")
        lines.append("")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _write_subplan(
    plans_dir: Path,
    name: str,
    *,
    status: str = "shipped",
    current_step: int = 3,
    estimated_steps: int = 3,
) -> None:
    """Write a minimal sub-plan."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"plan: {name.replace('.md', '')}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        "last_updated: 2026-09-08\n"
        "verification_tier: loop-verified\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _file_hash(path: Path) -> str:
    """SHA-256 of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup_project(
    tmp_path: Path,
    key: str = "test-proj",
    *,
    master_status: str = "active",
    subplan_status: str = "shipped",
    last_launch_path: str = "/some/repo",
) -> tuple[Path, Path]:
    """Scaffold a project data dir. Returns (project_dir, plans_dir)."""
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    _write_master(
        plans_dir,
        "MASTER-test.md",
        status=master_status,
        subplans=["2026-09-08-work.md"],
    )
    _write_subplan(plans_dir, "2026-09-08-work.md", status=subplan_status)

    if last_launch_path:
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        (launcher_dir / "last-launch.json").write_text(
            json.dumps({"project_path": last_launch_path}),
            encoding="utf-8",
        )

    return project_dir, plans_dir


def _import_fresh_loop_status():
    """Import ``loop_status`` with a clean module slate."""
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in (
        "loop_status", "plan_status", "plan_slug",
        "ilk_paths", "ship_audit", "ship_integrity", "ship_transition",
    ):
        sys.modules.pop(mod_name, None)
    import loop_status
    return loop_status


def _import_fresh_scheduler_scan():
    """Import ``scheduler_scan`` with a clean module slate."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in (
        "scheduler_scan", "ilk_paths", "plan_status", "plan_slug",
        "blacklist_status",
    ):
        sys.modules.pop(mod_name, None)
    import scheduler_scan
    return scheduler_scan


# ── tests ───────────────────────────────────────────────────────────


class TestStatusReadIsPure:
    """A status read must not mutate master files or consume dispatch triggers."""

    def test_scheduler_first_dispatches_once(self, tmp_path):
        """Baseline: scheduler-first ordering produces exactly one dispatch.

        ``scheduler_scan.scan_projects`` reconciles the master (active →
        shipped), records it in ``just_reconciled``, and dispatches
        verification. This is the working path.
        """
        home = tmp_path
        _setup_project(tmp_path)

        dispatches: list[list[str]] = []
        original_popen = None

        scheduler_scan = _import_fresh_scheduler_scan()
        import subprocess
        original_popen = subprocess.Popen

        def mock_popen(cmd, **kwargs):
            dispatches.append(cmd)

            class MockProc:
                pid = 99999
                returncode = 0

            return MockProc()

        scheduler_scan.ilk_data_root = lambda: home
        scheduler_scan.subprocess.Popen = mock_popen

        try:
            scheduler_scan.scan_projects()
        finally:
            scheduler_scan.subprocess.Popen = original_popen

        assert len(dispatches) == 1, (
            "scheduler-first must produce exactly 1 dispatch, "
            f"got {len(dispatches)}"
        )

    def test_resolve_then_scheduler_dispatches_once(self, tmp_path):
        """The defect: resolve-then-scheduler ordering yields zero dispatches.

        ``loop_status.resolve_status`` calls ``reconcile_master_status``
        first, flipping the master to shipped. When ``scheduler_scan`` runs,
        the reconcile returns False (already shipped) and
        ``just_reconciled`` stays empty → zero dispatches.
        """
        home = tmp_path
        _setup_project(tmp_path)

        # --- resolve first ---
        loop_status = _import_fresh_loop_status()
        loop_status._resolve_plans_dir = lambda cwd: (
            tmp_path / "projects" / "test-proj" / "plans", "external"
        )
        loop_status.resolve_status(cwd=tmp_path, json_mode=False)

        # --- scheduler second ---
        dispatches: list[list[str]] = []
        original_popen = None

        scheduler_scan = _import_fresh_scheduler_scan()
        import subprocess
        original_popen = subprocess.Popen

        def mock_popen(cmd, **kwargs):
            dispatches.append(cmd)

            class MockProc:
                pid = 99999
                returncode = 0

            return MockProc()

        scheduler_scan.ilk_data_root = lambda: home
        scheduler_scan.subprocess.Popen = mock_popen

        try:
            scheduler_scan.scan_projects()
        finally:
            scheduler_scan.subprocess.Popen = original_popen

        # BUG: this yields 0 dispatches. The fix (step 1) should make it 1.
        assert len(dispatches) == 1, (
            "resolve-then-scheduler must dispatch once, but got "
            f"{len(dispatches)} — the status read consumed the trigger"
        )

    def test_status_read_leaves_master_byte_identical(self, tmp_path):
        """Calling resolve_status must not alter any MASTER-*.md on disk."""
        _setup_project(tmp_path)
        plans_dir = tmp_path / "projects" / "test-proj" / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # Snapshot before.
        before_hash = _file_hash(master_path)

        loop_status = _import_fresh_loop_status()
        loop_status._resolve_plans_dir = lambda cwd: (plans_dir, "external")
        loop_status.resolve_status(cwd=tmp_path, json_mode=False)

        after_hash = _file_hash(master_path)

        assert before_hash == after_hash, (
            "resolve_status mutated MASTER-test.md — "
            "a status read must be a pure read"
        )

    def test_status_read_leaves_subplan_byte_identical(self, tmp_path):
        """Calling resolve_status must not alter any sub-plan file on disk."""
        _setup_project(tmp_path)
        plans_dir = tmp_path / "projects" / "test-proj" / "plans"
        subplan_path = plans_dir / "2026-09-08-work.md"

        before_hash = _file_hash(subplan_path)

        loop_status = _import_fresh_loop_status()
        loop_status._resolve_plans_dir = lambda cwd: (plans_dir, "external")
        loop_status.resolve_status(cwd=tmp_path, json_mode=False)

        after_hash = _file_hash(subplan_path)

        assert before_hash == after_hash, (
            "resolve_status mutated 2026-09-08-work.md"
        )
