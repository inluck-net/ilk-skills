"""Tests for doctor.py — the /ilk-doctor diagnostic tool.

Steps 0-2: skeleton, plan-state gates, and locks/processes/sentinel gates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import doctor


# ── Helpers (adapted from test_master_pending_as_queued.py) ─────────────────

def _write_master(plans_dir: Path, name: str, *, status: str = "queued",
                  priority: int = 0, created: str = "2026-08-12T00:00:00+08:00",
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


def _setup_project(tmp_path: Path, *, master_status: str = "queued",
                   subplan_status: str = "pending") -> Path:
    """Create a minimal project structure with plans."""
    project = tmp_path / "test-project"
    project.mkdir()
    # The doctor resolves plans via _resolve_plans_dir, which looks at
    # ~/.ilk-data/projects/<key>/plans/ or docs/plans/.
    # We'll patch _resolve_plans_dir in each test.
    plans = project / "docs" / "plans"
    _write_master(plans, "MASTER-2026-08-12-test.md", status=master_status,
                  subplans=["2026-08-12-task.md"])
    _write_subplan(plans, "2026-08-12-task.md", status=subplan_status,
                   current_step=0, estimated_steps=3)
    return project


# ── Tests ───────────────────────────────────────────────────────────────────

class TestGateWalkSkeleton:
    """The gate walk: gates 1-2 are implemented; the rest return unknown."""

    def test_no_gate_is_an_unimplemented_stub(self, tmp_path):
        """Every gate in GATE_ORDER is implemented.

        This test previously asserted the OPPOSITE — that `blacklist` returned
        ``unknown: not implemented``.  It shipped that way: the step-0 skeleton
        placeholder was never replaced, and the test locked it in.  A permanently
        `unknown` gate makes every verdict `unknown`, which trains the operator
        to ignore the field.  Inverted 2026-08-12 when the gate was implemented.
        """
        project = _setup_project(tmp_path)
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            report = doctor.run_doctor(project, sample_interval=0.1)
        finally:
            doctor._resolve_plans_dir = original

        assert len(report.gates) == 8, f"Expected 8 gates, got {len(report.gates)}"
        stubs = [g.name for g in report.gates if "not implemented" in g.evidence]
        assert not stubs, f"gates still stubbed out: {stubs}"

    def test_walk_stops_at_first_blocked(self, tmp_path):
        """The walk stops at the first gate returning blocked."""
        project = _setup_project(tmp_path)

        # Monkey-patch gate 1 (master-status) to return blocked.
        original_gate = doctor._gate_master_status
        doctor._gate_master_status = lambda plans_dir: doctor.GateResult(
            name="master-status",
            status="blocked",
            evidence="master is draft",
            artifact="MASTER.md",
        )
        original_plans = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            report = doctor.run_doctor(project, sample_interval=0.1)
        finally:
            doctor._gate_master_status = original_gate
            doctor._resolve_plans_dir = original_plans

        # Gate 0 (progress-over-time) ran — it returns pass when no runs exist.
        assert report.gates[0].name == "progress-over-time"
        assert report.gates[0].status == "pass"

        # Gate 1 (master-status) ran and was blocked.
        assert report.gates[1].name == "master-status"
        assert report.gates[1].status == "blocked"

        # No further gates ran.
        assert len(report.gates) == 2, (
            f"Expected 2 gates (stopped at blocker), got {len(report.gates)}"
        )

        # Verdict names the blocker.
        assert "blocked" in report.verdict
        assert "master-status" in report.verdict

    def test_unknown_never_counts_as_pass(self, tmp_path):
        """AC-3: a report with any unknown gate never says 'pass'.

        The unknown is now injected explicitly.  This test used to rely on the
        `blacklist` gate being a permanent stub, so it passed for the wrong
        reason and would have gone green even if the invariant broke.
        """
        project = _setup_project(tmp_path)
        original_plans = doctor._resolve_plans_dir
        original_gate = doctor._gate_config_resolution
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        doctor._gate_config_resolution = lambda *a, **k: doctor.GateResult(
            name="config-resolution",
            status="unknown",
            evidence="injected: cannot read .ilk-launch.json",
            artifact="<test>",
        )
        try:
            report = doctor.run_doctor(project, sample_interval=0.1)
        finally:
            doctor._resolve_plans_dir = original_plans
            doctor._gate_config_resolution = original_gate

        assert any(g.status == "unknown" for g in report.gates), (
            "the injected unknown gate did not reach the report"
        )
        assert "pass" not in report.verdict, (
            f"Verdict should not contain 'pass' when gates are unknown: {report.verdict}"
        )
        assert "unknown" in report.verdict

    def test_gate_order_matches_spec(self):
        """The gate order matches the sub-plan specification."""
        expected = [
            "progress-over-time",
            "master-status",
            "subplan-statuses",
            "blacklist",
            "lock-holders",
            "process-set",
            "sentinel-vs-reality",
            "config-resolution",
        ]
        assert doctor.GATE_ORDER == expected

    def test_all_gate_names_unique(self):
        """Gate names are unique (no duplicates)."""
        assert len(doctor.GATE_ORDER) == len(set(doctor.GATE_ORDER))


class TestMasterStatusGate:
    """Gate 1 (master-status) returns the right status for each fixture."""

    def _run_gate(self, tmp_path, *, master_status, subplan_status="pending"):
        project = _setup_project(
            tmp_path, master_status=master_status, subplan_status=subplan_status
        )
        plans = project / "docs" / "plans"
        return doctor._gate_master_status(plans)

    def test_draft_master_is_blocked(self, tmp_path):
        r = self._run_gate(tmp_path, master_status="draft")
        assert r.status == "blocked"
        assert "draft" in r.evidence

    def test_paused_master_is_blocked(self, tmp_path):
        r = self._run_gate(tmp_path, master_status="paused")
        assert r.status == "blocked"
        assert "paused" in r.evidence

    def test_queued_master_passes(self, tmp_path):
        r = self._run_gate(tmp_path, master_status="queued")
        assert r.status == "pass"
        assert "queued" in r.evidence

    def test_active_master_passes(self, tmp_path):
        r = self._run_gate(tmp_path, master_status="active")
        assert r.status == "pass"
        assert "active" in r.evidence

    def test_shipped_master_passes(self, tmp_path):
        r = self._run_gate(tmp_path, master_status="shipped")
        assert r.status == "pass"
        assert "shipped" in r.evidence

    def test_no_master_is_blocked(self, tmp_path):
        """A plans directory with no MASTER-*.md is blocked."""
        plans = tmp_path / "empty-plans"
        plans.mkdir()
        r = doctor._gate_master_status(plans)
        assert r.status == "blocked"
        assert "no MASTER" in r.evidence

    def test_legacy_pending_normalizes_to_queued(self, tmp_path):
        """Legacy 'pending' master status normalizes to 'queued' (runnable)."""
        r = self._run_gate(tmp_path, master_status="pending")
        assert r.status == "pass"
        assert "queued" in r.evidence


class TestSubplanStatusesGate:
    """Gate 2 (subplan-statuses) returns the right status for each fixture."""

    def _run_gate_with_subplans(self, tmp_path, *, master_status="queued",
                                 subplan_statuses: dict[str, str] | None = None):
        """Create a project with a master and multiple sub-plans."""
        project = tmp_path / "test-project"
        project.mkdir()
        plans = project / "docs" / "plans"
        plans.mkdir(parents=True)

        if subplan_statuses is None:
            subplan_statuses = {"2026-08-12-task.md": "pending"}

        subplan_names = list(subplan_statuses.keys())
        _write_master(plans, "MASTER-2026-08-12-test.md", status=master_status,
                      subplans=subplan_names)
        for name, status in subplan_statuses.items():
            _write_subplan(plans, name, status=status)

        return doctor._gate_subplan_statuses(plans)

    def test_all_shipped_passes(self, tmp_path):
        r = self._run_gate_with_subplans(
            tmp_path,
            subplan_statuses={"2026-08-12-a.md": "shipped", "2026-08-12-b.md": "shipped"},
        )
        assert r.status == "pass"
        assert "all" in r.evidence and "shipped" in r.evidence

    def test_blocked_only_is_blocked(self, tmp_path):
        """All sub-plans blocked (not runnable) → gate blocked."""
        r = self._run_gate_with_subplans(
            tmp_path,
            subplan_statuses={"2026-08-12-a.md": "blocked", "2026-08-12-b.md": "blocked"},
        )
        assert r.status == "blocked"
        assert "no runnable" in r.evidence

    def test_healthy_queued_with_pending_passes(self, tmp_path):
        """Queued master with a pending sub-plan → pass."""
        r = self._run_gate_with_subplans(
            tmp_path,
            master_status="queued",
            subplan_statuses={"2026-08-12-task.md": "pending"},
        )
        assert r.status == "pass"
        assert "runnable" in r.evidence

    def test_mixed_blocked_and_shipped_is_blocked(self, tmp_path):
        """Blocked + shipped, no pending/in-progress → blocked."""
        r = self._run_gate_with_subplans(
            tmp_path,
            subplan_statuses={
                "2026-08-12-a.md": "shipped",
                "2026-08-12-b.md": "blocked",
            },
        )
        assert r.status == "blocked"
        assert "no runnable" in r.evidence

    def test_in_progress_is_runnable(self, tmp_path):
        """in-progress counts as runnable."""
        r = self._run_gate_with_subplans(
            tmp_path,
            subplan_statuses={"2026-08-12-task.md": "in-progress"},
        )
        assert r.status == "pass"

    def test_no_master_returns_unknown(self, tmp_path):
        """No master file → cannot evaluate sub-plans."""
        plans = tmp_path / "empty-plans"
        plans.mkdir()
        r = doctor._gate_subplan_statuses(plans)
        assert r.status == "unknown"

    def test_artifact_points_to_master(self, tmp_path):
        """The artifact field names the master file consulted."""
        r = self._run_gate_with_subplans(tmp_path)
        assert "MASTER" in r.artifact


class TestLockHoldersGate:
    """Gate 4 (lock-holders) tests."""

    def test_no_lock_file_passes(self, tmp_path):
        """No run.lock → pass."""
        project_data = tmp_path / "data"
        project_data.mkdir()
        r = doctor._gate_lock_holders(project_data)
        assert r.status == "pass"
        assert "does not exist" in r.evidence

    def test_lock_with_no_holders_passes(self, tmp_path):
        """run.lock exists but no process holds it → pass (stale lock)."""
        project_data = tmp_path / "data"
        lock_dir = project_data / "runtime" / "launcher"
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / "run.lock"
        lock_path.write_text("stale lock", encoding="utf-8")
        r = doctor._gate_lock_holders(project_data)
        # If lsof is available, it should report no holders.
        # If lsof is unavailable, it should report unknown.
        assert r.status in ("pass", "unknown"), f"Unexpected status: {r.status}"

    def test_lock_with_live_holder_is_blocked(self, tmp_path):
        """A live process holding the lock file → blocked."""
        import time
        project_data = tmp_path / "data"
        lock_dir = project_data / "runtime" / "launcher"
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / "run.lock"
        lock_path.write_text("locked", encoding="utf-8")

        # Start a child process that holds the file open.
        # Must assign the fd to a variable so Python doesn't GC it.
        import subprocess as sp
        child = sp.Popen(
            ["python3", "-c",
             f"f = open('{lock_path}', 'rb'); import time; time.sleep(60)"],
            stdout=sp.PIPE, stderr=sp.PIPE,
        )
        try:
            # Give the child time to start and open the file.
            time.sleep(0.5)
            r = doctor._gate_lock_holders(project_data)
            # If lsof is available, it should find our child.
            if r.status != "unknown":
                assert r.status == "blocked", f"Expected blocked, got {r.status}: {r.evidence}"
                assert str(child.pid) in r.evidence
        finally:
            child.terminate()
            child.wait(timeout=5)


class TestProcessSetGate:
    """Gate 5 (process-set) tests."""

    def test_no_runners_passes(self, tmp_path):
        """No matching runner processes → pass."""
        project = tmp_path / "nonexistent-project-path-12345"
        r = doctor._gate_process_set(project)
        assert r.status == "pass"
        assert "no runner" in r.evidence.lower()


class TestSentinelVsRealityGate:
    """Gate 6 (sentinel-vs-reality) tests."""

    def _write_sentinel(self, project_data: Path, *, state: str = "running",
                         pid: int = 99999, run_id: str = "test-run"):
        path = project_data / "runtime" / "launcher"
        path.mkdir(parents=True, exist_ok=True)
        sentinel = {
            "state": state,
            "pid": pid,
            "run_id": run_id,
            "iteration": 1,
            "exit_code": None,
            "generated_at": "2026-08-12T00:00:00+08:00",
        }
        (path / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")

    def test_no_sentinel_passes(self, tmp_path):
        """No last-exit.json → pass."""
        project_data = tmp_path / "data"
        project_data.mkdir()
        r = doctor._gate_sentinel_vs_reality(project_data)
        assert r.status == "pass"
        assert "no last-exit" in r.evidence

    def test_terminal_state_passes(self, tmp_path):
        """Terminal sentinel state → pass regardless of pid."""
        project_data = tmp_path / "data"
        for state in ["shipped", "local_checks_failed", "interrupted",
                       "error", "max-iterations", "budget_exhausted", "startup-hang"]:
            self._write_sentinel(project_data, state=state, pid=99999)
            r = doctor._gate_sentinel_vs_reality(project_data)
            assert r.status == "pass", f"State '{state}' should pass, got {r.status}"

    def test_running_with_no_live_pids_is_stale(self, tmp_path):
        """'running' sentinel but no live runners → blocked (stale)."""
        project_data = tmp_path / "data"
        self._write_sentinel(project_data, state="running", pid=99999, run_id="stale-run")
        r = doctor._gate_sentinel_vs_reality(project_data, live_pids=[])
        assert r.status == "blocked"
        assert "stale" in r.evidence.lower()
        assert "stale-run" in r.evidence

    def test_running_with_matching_pid_passes(self, tmp_path):
        """'running' sentinel and the pid is in the live set → pass."""
        project_data = tmp_path / "data"
        self._write_sentinel(project_data, state="running", pid=12345)
        r = doctor._gate_sentinel_vs_reality(project_data, live_pids=["12345"])
        assert r.status == "pass"
        assert "alive" in r.evidence

    def test_running_with_mismatched_pids_is_stale(self, tmp_path):
        """'running' sentinel but pid not in live set → blocked."""
        project_data = tmp_path / "data"
        self._write_sentinel(project_data, state="running", pid=99999)
        r = doctor._gate_sentinel_vs_reality(project_data, live_pids=["11111", "22222"])
        assert r.status == "blocked"
        assert "stale" in r.evidence.lower() or "not in set" in r.evidence.lower()


class TestProgressOverTimeGate:
    """Gate 0 (progress-over-time) tests."""

    def _setup_run(self, project_data: Path, run_id: str = "test-run-001"):
        """Create a run directory with an iter log."""
        run_dir = project_data / "logs" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def test_no_runs_dir_passes(self, tmp_path):
        """No runs directory → pass."""
        project_data = tmp_path / "data"
        project_data.mkdir()
        r = doctor._gate_progress_over_time(project_data, sample_interval=0.05)
        assert r.status == "pass"
        assert "no runs" in r.evidence.lower()

    def test_no_iter_logs_passes(self, tmp_path):
        """Empty run directory → pass."""
        project_data = tmp_path / "data"
        run_dir = self._setup_run(project_data)
        r = doctor._gate_progress_over_time(project_data, sample_interval=0.05)
        assert r.status == "pass"
        assert "no iter" in r.evidence.lower()

    def test_static_file_is_quiet(self, tmp_path):
        """Untouched iter log → quiet, and 'stalled' absent from evidence."""
        project_data = tmp_path / "data"
        run_dir = self._setup_run(project_data)
        iter_file = run_dir / "iter-01.jsonl"
        iter_file.write_text('{"event": "start"}\n', encoding="utf-8")

        r = doctor._gate_progress_over_time(project_data, sample_interval=0.05)
        assert r.status == "pass"
        assert "quiet" in r.evidence
        assert "stalled" not in r.evidence.lower(), (
            f"'stalled' must never appear in a single-sample result: {r.evidence}"
        )

    def test_growing_file_is_progressing(self, tmp_path):
        """Iter log appended to between samples → progressing."""
        import threading
        import time

        project_data = tmp_path / "data"
        run_dir = self._setup_run(project_data)
        iter_file = run_dir / "iter-01.jsonl"
        iter_file.write_text('{"event": "start"}\n', encoding="utf-8")

        # Append to the file in a background thread during the sample interval.
        def append_after_delay():
            time.sleep(0.03)
            with open(iter_file, "a", encoding="utf-8") as f:
                f.write('{"event": "step"}\n{"event": "step2"}\n')

        t = threading.Thread(target=append_after_delay)
        t.start()

        r = doctor._gate_progress_over_time(project_data, sample_interval=0.1)
        t.join()

        assert r.status == "pass"
        assert "progressing" in r.evidence

    def test_artifact_names_the_iter_file(self, tmp_path):
        """The artifact field names the specific iter file sampled."""
        project_data = tmp_path / "data"
        run_dir = self._setup_run(project_data)
        iter_file = run_dir / "iter-05.jsonl"
        iter_file.write_text('{"event": "x"}\n', encoding="utf-8")

        r = doctor._gate_progress_over_time(project_data, sample_interval=0.05)
        assert "iter-05" in r.artifact


class TestConfigResolutionGate:
    """Gate 7 (config-resolution) tests."""

    def test_no_config_uses_defaults(self, tmp_path):
        """No .ilk-launch.json → defaults."""
        project = tmp_path / "proj"
        project.mkdir()
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "nonexistent"
        try:
            r = doctor._gate_config_resolution(project)
        finally:
            doctor._resolve_plans_dir = original
        assert r.status == "pass"
        assert "default" in r.evidence

    def test_config_with_values(self, tmp_path):
        """Config with max_iterations and timeout → reported."""
        project = tmp_path / "proj"
        project.mkdir()
        cfg = {"max_iterations": 50, "iteration_timeout_min": 45}
        (project / ".ilk-launch.json").write_text(json.dumps(cfg), encoding="utf-8")

        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "nonexistent"
        try:
            r = doctor._gate_config_resolution(project)
        finally:
            doctor._resolve_plans_dir = original

        assert r.status == "pass"
        assert "max_iterations=50" in r.evidence
        assert "iteration_timeout_min=45" in r.evidence

    def test_config_partial_values(self, tmp_path):
        """Config with only some values → others use defaults."""
        project = tmp_path / "proj"
        project.mkdir()
        cfg = {"iteration_timeout_min": 60}
        (project / ".ilk-launch.json").write_text(json.dumps(cfg), encoding="utf-8")

        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "nonexistent"
        try:
            r = doctor._gate_config_resolution(project)
        finally:
            doctor._resolve_plans_dir = original

        assert r.status == "pass"
        assert "iteration_timeout_min=60" in r.evidence
        assert "default" in r.evidence

    def test_artifact_names_the_config_file(self, tmp_path):
        """The artifact field names the config file consulted."""
        project = tmp_path / "proj"
        project.mkdir()
        cfg_path = project / ".ilk-launch.json"
        cfg_path.write_text("{}", encoding="utf-8")

        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "nonexistent"
        try:
            r = doctor._gate_config_resolution(project)
        finally:
            doctor._resolve_plans_dir = original

        assert ".ilk-launch.json" in r.artifact


class TestReadOnlyProof:
    """AC-4: doctor.py leaves the project's .ilk-data tree byte-identical."""

    def _hash_tree(self, root: Path) -> dict[str, str]:
        """Hash every file under root, returning {relative_path: sha256}."""
        import hashlib
        hashes = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return hashes

    def test_doctor_leaves_data_unchanged(self, tmp_path):
        """Running doctor.py must not modify any file in the project data tree."""
        # Set up a project with plans, sentinel, config, etc.
        project = tmp_path / "proj"
        project.mkdir()
        plans = project / "docs" / "plans"
        _write_master(plans, "MASTER-2026-08-12-test.md", status="queued",
                      subplans=["2026-08-12-task.md"])
        _write_subplan(plans, "2026-08-12-task.md", status="pending")
        (project / ".ilk-launch.json").write_text(
            '{"max_iterations": 50}', encoding="utf-8"
        )

        # Also create a fake .ilk-data tree.
        data_dir = tmp_path / "ilk-data"
        runtime = data_dir / "runtime" / "launcher"
        runtime.mkdir(parents=True)
        (runtime / "last-exit.json").write_text(
            '{"state":"shipped","pid":null,"run_id":"x","iteration":0}',
            encoding="utf-8",
        )
        (runtime / "run.lock").write_text("stale", encoding="utf-8")

        # Hash before.
        hashes_before = self._hash_tree(data_dir)

        # Patch doctor to use our data dir.
        original_data = doctor._resolve_project_data
        original_plans = doctor._resolve_plans_dir
        doctor._resolve_project_data = lambda p: data_dir
        doctor._resolve_plans_dir = lambda p: plans
        try:
            doctor.run_doctor(project, sample_interval=0.05)
        finally:
            doctor._resolve_project_data = original_data
            doctor._resolve_plans_dir = original_plans

        # Hash after.
        hashes_after = self._hash_tree(data_dir)

        assert hashes_before == hashes_after, (
            f"doctor.py modified the data tree! Changed files: "
            f"{set(hashes_before.items()) ^ set(hashes_after.items())}"
        )


class TestJsonOutput:
    """AC-6: --json emits structured data."""

    def test_json_output_is_valid(self, tmp_path, capsys):
        """--json produces valid JSON with the expected structure."""
        project = _setup_project(tmp_path)
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            doctor.main(["--project-path", str(project), "--json", "--sample-interval", "0.05"])
        finally:
            doctor._resolve_plans_dir = original

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "project_path" in data
        assert "gates" in data
        assert "verdict" in data
        assert isinstance(data["gates"], list)
        assert len(data["gates"]) == 8

    def test_json_gate_has_expected_fields(self, tmp_path, capsys):
        """Each gate in JSON output has name, status, evidence, artifact."""
        project = _setup_project(tmp_path)
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            doctor.main(["--project-path", str(project), "--json", "--sample-interval", "0.05"])
        finally:
            doctor._resolve_plans_dir = original

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        for gate in data["gates"]:
            assert "name" in gate
            assert "status" in gate
            assert "evidence" in gate
            assert "artifact" in gate


class TestGateResultDataclass:
    """GateResult fields are correctly set."""

    def test_gate_result_fields(self):
        r = doctor.GateResult(
            name="test-gate",
            status="blocked",
            evidence="something failed",
            artifact="/path/to/file",
        )
        assert r.name == "test-gate"
        assert r.status == "blocked"
        assert r.evidence == "something failed"
        assert r.artifact == "/path/to/file"

    def test_gate_result_default_artifact(self):
        r = doctor.GateResult(name="x", status="unknown", evidence="n/a")
        assert r.artifact == ""


class TestDoctorReportDataclass:
    """DoctorReport serialization."""

    def test_to_dict_empty(self):
        report = doctor.DoctorReport(project_path="/tmp/test")
        d = report.to_dict()
        assert d["project_path"] == "/tmp/test"
        assert d["gates"] == []
        assert d["verdict"] == ""

    def test_to_dict_with_gates(self):
        report = doctor.DoctorReport(project_path="/tmp/test")
        report.gates = [
            doctor.GateResult(name="g1", status="pass", evidence="ok"),
            doctor.GateResult(name="g2", status="blocked", evidence="fail", artifact="x"),
        ]
        report.verdict = "blocked: g2 — fail"
        d = report.to_dict()
        assert len(d["gates"]) == 2
        assert d["gates"][0]["status"] == "pass"
        assert d["gates"][1]["artifact"] == "x"
        assert "blocked" in d["verdict"]
