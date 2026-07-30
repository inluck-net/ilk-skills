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

    def test_engine_is_claude_planner_home(self, tmp_path):
        """AC-2: dispatch uses --engine claude → launcher resolves
        CLAUDE_CONFIG_DIR to ~/.claude (planner home), never
        ~/.claude-worker.  The flag is the mechanism; the launcher's
        ``resolve_engine`` function maps it to the correct home.
        Also verifies --max-iterations 1 (one-shot verification)."""
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

        # Verify engine is claude (planner), not claude-worker.
        engine_idx = cmd.index("--engine")
        assert cmd[engine_idx + 1] == "claude", (
            "must dispatch with --engine claude (planner home, ~/.claude), "
            "not claude-worker (worker home, ~/.claude-worker)"
        )
        # Verify one-shot: max-iterations 1 prevents the session from looping.
        iter_idx = cmd.index("--max-iterations")
        assert cmd[iter_idx + 1] == "1"

        # Verify the launcher script is the real launch.sh, not a shim.
        assert cmd[1].endswith("launch.sh"), (
            "must use the real launcher script"
        )


class TestNoPollingInDispatch:
    """AC-8: no polling loop in any model-invoked path."""

    def test_no_sleep_in_dispatch_code(self):
        """AC-8: grep the dispatch function source for sleep/poll patterns.
        A model session that polls and relaunched spawned duplicate loops
        on 2026-06-22 (decomposition-principles §21)."""
        import inspect
        sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
        sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
        for mod_name in ("scheduler_scan", "ilk_paths", "plan_status"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import scheduler_scan

        source = inspect.getsource(scheduler_scan._dispatch_verification_on_drain)
        # These patterns indicate a polling loop — forbidden in a
        # model-invoked path.
        forbidden = ["time.sleep", "while True", "poll(", "await"]
        for pattern in forbidden:
            assert pattern not in source, (
                f"dispatch function contains '{pattern}' — "
                f"AC-8 forbids polling in model-invoked paths"
            )


class TestDrainVerifyPromoteJoin:
    """AC-5, AC-6: the drain→verify→promote join end to end.

    One test exercises the WHOLE join — not the halves.  Two green halves
    are how the join stayed broken while every component passed.
    """

    def _write_master_with_builds_on(
        self, plans_dir, name, *, status, subplans, builds_on,
    ):
        """Write a MASTER with a ``builds_on`` frontmatter field."""
        plans_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"title: {name}",
            "created: 2026-07-29T00:00:00+08:00",
            f"status: {status}",
            "priority: 0",
            "pause_after_ship: false",
            f"builds_on: {builds_on}",
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

    def _write_subplan_with_verification(
        self, plans_dir, name, *, status="shipped",
        verification_tier="compile-only", verified=None,
    ):
        """Write a sub-plan with verification_tier and optional verified."""
        plans_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"plan: {name.replace('.md', '')}",
            f"status: {status}",
            "current_step: 3",
            "estimated_steps: 3",
            "last_updated: 2026-07-29",
            f"verification_tier: {verification_tier}",
        ]
        if verified is not None:
            lines.append(f"verified: {verified}")
        lines += [
            "---",
            "",
            f"# {name}",
            "",
        ]
        (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")

    def _run_promote(self, plans_dir):
        """Run promote_next_master.py --plans-dir <dir> --dry-run."""
        import subprocess
        PROMOTE_SCRIPT = SCRIPTS_ILK_LOOP / "promote_next_master.py"
        result = subprocess.run(
            [sys.executable, str(PROMOTE_SCRIPT),
             "--plans-dir", str(plans_dir), "--dry-run"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        return json.loads(result.stdout)

    def test_verified_unblocks_promotion(self, tmp_path):
        """AC-5: verified: true on a compile-only dependency unblocks
        promotion of a master that builds_on it.

        Exercises the WHOLE join:
        1. Master A ships with verification_tier: compile-only.
        2. Master B builds_on A's slug.
        3. A's sub-plan has verified: true.
        4. promote_next_master promotes B (not blocked).
        """
        plans = tmp_path / "projects" / "test-proj" / "plans"
        plans.mkdir(parents=True, exist_ok=True)

        # Master A — shipped, compile-only, verified.
        _write_master(
            plans, "MASTER-A.md", status="shipped",
            subplans=["2026-07-29-dep-work.md"],
        )
        self._write_subplan_with_verification(
            plans, "2026-07-29-dep-work.md",
            status="shipped", verification_tier="compile-only",
            verified="true",
        )

        # Master B — queued, builds_on A's slug.
        self._write_master_with_builds_on(
            plans, "MASTER-B.md", status="queued",
            subplans=["2026-07-29-consumer.md"],
            builds_on="dep-work",
        )
        _write_subplan(
            plans, "2026-07-29-consumer.md", status="pending",
        )

        promote_data = self._run_promote(plans)
        assert promote_data["promoted"] == "MASTER-B.md", (
            f"verified dependency should unblock promotion, "
            f"got {promote_data}"
        )
        assert promote_data.get("skipped_unverified") is None, (
            "no unverified blockers expected"
        )

    def test_unverified_blocks_promotion(self, tmp_path):
        """AC-6 (partial): absent verified on a compile-only dependency
        blocks promotion.  The failure path must not resemble success."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        plans.mkdir(parents=True, exist_ok=True)

        # Master A — shipped, compile-only, NOT verified.
        _write_master(
            plans, "MASTER-A.md", status="shipped",
            subplans=["2026-07-29-dep-work.md"],
        )
        self._write_subplan_with_verification(
            plans, "2026-07-29-dep-work.md",
            status="shipped", verification_tier="compile-only",
            # verified is None (absent) → unverified
        )

        # Master B — queued, builds_on A's slug.
        self._write_master_with_builds_on(
            plans, "MASTER-B.md", status="queued",
            subplans=["2026-07-29-consumer.md"],
            builds_on="dep-work",
        )
        _write_subplan(
            plans, "2026-07-29-consumer.md", status="pending",
        )

        promote_data = self._run_promote(plans)
        assert promote_data["promoted"] is None, (
            "unverified compile-only dependency must block promotion"
        )
        assert promote_data.get("skipped_unverified"), (
            "skipped_unverified should list the blocking dependency"
        )
        blocker_masters = [
            entry["master"]
            for entry in promote_data["skipped_unverified"]
        ]
        assert "MASTER-B.md" in blocker_masters

    def test_loop_verified_tier_does_not_block(self, tmp_path):
        """A loop-verified dependency does NOT block promotion
        (only compile-only and device-manual tiers require verification)."""
        plans = tmp_path / "projects" / "test-proj" / "plans"
        plans.mkdir(parents=True, exist_ok=True)

        # Master A — shipped, loop-verified (not compile-only).
        _write_master(
            plans, "MASTER-A.md", status="shipped",
            subplans=["2026-07-29-auto-work.md"],
        )
        self._write_subplan_with_verification(
            plans, "2026-07-29-auto-work.md",
            status="shipped", verification_tier="loop-verified",
        )

        # Master B — queued, builds_on A's slug.
        self._write_master_with_builds_on(
            plans, "MASTER-B.md", status="queued",
            subplans=["2026-07-29-consumer.md"],
            builds_on="auto-work",
        )
        _write_subplan(
            plans, "2026-07-29-consumer.md", status="pending",
        )

        promote_data = self._run_promote(plans)
        assert promote_data["promoted"] == "MASTER-B.md", (
            "loop-verified dependency should NOT block promotion"
        )
