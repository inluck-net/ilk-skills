"""Dispatch-on-drain tests: planner verification dispatched when a master drains.

Tests the idempotent dispatch logic added to ``scheduler_scan.py`` that
fires when ``reconcile_master_status`` flips a master to ``shipped``.

AC-1: exactly one dispatch across two consecutive scan passes.
AC-3: supervised_only masters are skipped.
AC-4: blacklisted projects are skipped.

Hermetic — no live scheduler, no launcher, no servers.
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


def _write_master(
    plans_dir: Path,
    name: str,
    *,
    status: str = "active",
    subplans: list[str] | None = None,
    supervised_only: bool = False,
) -> None:
    """Write a minimal MASTER-*.md."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        "created: 2026-07-29T00:00:00+08:00",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
    ]
    if supervised_only:
        lines.append("supervised_only: true")
    lines += [
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines += [
            "## Sub-plan registry",
            "",
            "| # | Sub-plan | Status |",
            "|---|---|---|",
        ]
        for sp in subplans:
            lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
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
        "last_updated: 2026-07-29\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _write_blacklist_postmortem(project_dir: Path) -> None:
    """Write a postmortem that makes ``is_blacklisted`` return True."""
    pm_dir = project_dir / "runtime" / "launcher" / "postmortems"
    pm_dir.mkdir(parents=True, exist_ok=True)
    # Use a naive timestamp (no tz offset) to match ``datetime.now()`` in
    # ``blacklist_status.is_blacklisted`` — a tz-aware generated_at with
    # a naive ``now`` raises TypeError on comparison.
    pm = (
        "---\n"
        "classification: stuck-no-progress\n"
        "generated_at: 2099-01-01T00:00:00\n"
        "---\n"
        "\n# stuck\n"
    )
    (pm_dir / "test-postmortem.md").write_text(pm, encoding="utf-8")


def _setup_project(
    tmp_path: Path,
    key: str = "test-proj",
    *,
    master_status: str = "active",
    subplan_status: str = "shipped",
    supervised_only: bool = False,
    blacklisted: bool = False,
    last_launch_path: str = "/some/repo",
) -> Path:
    """Scaffold a full project data dir with master + subplan + launcher."""
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    _write_master(
        plans_dir,
        "MASTER-test.md",
        status=master_status,
        subplans=["2026-07-29-work.md"],
        supervised_only=supervised_only,
    )
    _write_subplan(plans_dir, "2026-07-29-work.md", status=subplan_status)

    if last_launch_path:
        launcher_dir = project_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        (launcher_dir / "last-launch.json").write_text(
            json.dumps({"project_path": last_launch_path}),
            encoding="utf-8",
        )

    if blacklisted:
        _write_blacklist_postmortem(project_dir)

    return project_dir


def _run_scan(tmp_home: Path) -> list[dict]:
    """Import and call ``scheduler_scan.scan_projects`` with patched root."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "blacklist_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan.ilk_data_root = lambda: tmp_home
    return scheduler_scan.scan_projects()


def _call_dispatch(
    project_dir: Path,
    master_path: Path,
    plans_dir: Path,
    *,
    launch_fn=None,
) -> None:
    """Call ``_dispatch_verification_on_drain`` directly."""
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "blacklist_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    scheduler_scan._dispatch_verification_on_drain(
        project_dir, master_path, plans_dir,
        _launch_fn=launch_fn,
    )


# ── tests ───────────────────────────────────────────────────────────


class TestDispatchOnDrain:
    """AC-1, AC-3, AC-4: dispatch when a master drains."""

    def test_dispatch_on_reconcile(self, tmp_path):
        """AC-1: a master that flips to shipped triggers exactly one dispatch."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path, master_status="active")

        def capture_launch(cmd):
            dispatches.append(cmd)

        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # First call: master flips active → shipped, dispatch fires.
        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 1, "exactly one dispatch on first pass"
        assert "--engine" in dispatches[0]
        assert "claude" in dispatches[0]

        # Verify marker was written.
        marker = project_dir / "runtime" / "verification-dispatched.json"
        assert marker.exists()
        data = json.loads(marker.read_text(encoding="utf-8"))
        assert data["master"] == "MASTER-test.md"

    def test_idempotent_second_pass(self, tmp_path):
        """AC-1: second dispatch call is a no-op (marker blocks it)."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path, master_status="active")

        def capture_launch(cmd):
            dispatches.append(cmd)

        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # First pass.
        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 1

        # Second pass — marker exists, must NOT dispatch again.
        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 1, (
            "second pass must NOT dispatch (idempotency guard)"
        )

    def test_two_consecutive_scan_passes(self, tmp_path):
        """AC-1: full scan_projects path — two passes produce one dispatch."""
        dispatches: list[list[str]] = []

        # Patch subprocess.Popen to capture dispatches.
        import subprocess
        original_popen = subprocess.Popen

        def mock_popen(cmd, **kwargs):
            dispatches.append(cmd)
            # Return a mock process that's already "finished".
            class MockProc:
                pid = 99999
                returncode = 0
            return MockProc()

        project_dir = _setup_project(
            tmp_path, master_status="active", subplan_status="shipped",
        )

        # Patch scheduler_scan's subprocess for both passes.
        sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
        sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
        for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "blacklist_status"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import scheduler_scan
        scheduler_scan.ilk_data_root = lambda: tmp_path
        scheduler_scan.subprocess.Popen = mock_popen

        try:
            # First scan: master flips to shipped → dispatch fires.
            _run_scan(tmp_path)
            first_count = len(dispatches)

            # Second scan: already shipped → no dispatch.
            _run_scan(tmp_path)
            second_count = len(dispatches)

            assert first_count == 1, f"expected 1 dispatch, got {first_count}"
            assert second_count == 1, (
                f"expected still 1 dispatch after second scan, got {second_count}"
            )
        finally:
            scheduler_scan.subprocess.Popen = original_popen

    def test_supervised_only_skipped(self, tmp_path):
        """AC-3: supervised_only master is NOT dispatched."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path, master_status="active", supervised_only=True,
        )

        def capture_launch(cmd):
            dispatches.append(cmd)

        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 0, (
            "supervised_only master must NOT be dispatched"
        )
        # Marker must NOT be written.
        marker = project_dir / "runtime" / "verification-dispatched.json"
        assert not marker.exists()

    def test_blacklisted_project_skipped(self, tmp_path):
        """AC-4: blacklisted project is NOT dispatched."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(
            tmp_path, master_status="active", blacklisted=True,
        )

        def capture_launch(cmd):
            dispatches.append(cmd)

        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 0, (
            "blacklisted project must NOT be dispatched"
        )

    def test_no_launcher_no_crash(self, tmp_path):
        """AC-7: missing launcher script is non-fatal (no exception)."""
        project_dir = _setup_project(tmp_path, master_status="active")
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # Patch _SKILL_ROOT to a non-existent path.
        sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
        sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
        for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "blacklist_status"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import scheduler_scan
        scheduler_scan._SKILL_ROOT = tmp_path / "nonexistent-skill-root"

        # Must not raise.
        scheduler_scan._dispatch_verification_on_drain(
            project_dir, master_path, plans_dir,
        )

    def test_no_repo_path_no_crash(self, tmp_path):
        """AC-7: unresolvable repo_path is non-fatal."""
        project_dir = _setup_project(
            tmp_path, master_status="active", last_launch_path=None,
        )
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        # Must not raise.
        _call_dispatch(project_dir, master_path, plans_dir)

    def test_launch_failure_non_fatal(self, tmp_path):
        """AC-7: launcher raising is non-fatal."""
        project_dir = _setup_project(tmp_path, master_status="active")
        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        def failing_launch(cmd):
            raise OSError("launcher exploded")

        # Must not raise.
        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=failing_launch)

    def test_engine_is_claude(self, tmp_path):
        """AC-2 (partial): dispatch uses --engine claude (planner home)."""
        dispatches: list[list[str]] = []
        project_dir = _setup_project(tmp_path, master_status="active")

        def capture_launch(cmd):
            dispatches.append(cmd)

        plans_dir = project_dir / "plans"
        master_path = plans_dir / "MASTER-test.md"

        _call_dispatch(project_dir, master_path, plans_dir,
                       launch_fn=capture_launch)
        assert len(dispatches) == 1
        cmd = dispatches[0]
        engine_idx = cmd.index("--engine")
        assert cmd[engine_idx + 1] == "claude", (
            "must dispatch with --engine claude (planner home)"
        )
