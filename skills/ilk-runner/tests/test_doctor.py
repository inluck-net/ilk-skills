"""Tests for doctor.py — the /ilk-doctor diagnostic tool.

Step 0: gate-walk skeleton with first-blocker semantics.
"""
from __future__ import annotations

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
    """The gate walk returns unknown for every gate in step 0."""

    def test_all_gates_return_unknown(self, tmp_path):
        """Every gate returns status=unknown with evidence='not implemented'."""
        project = _setup_project(tmp_path)
        # Patch the plans dir resolver to use our tmp project.
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            report = doctor.run_doctor(project, sample_interval=0.1)
        finally:
            doctor._resolve_plans_dir = original

        assert len(report.gates) == 8, f"Expected 8 gates, got {len(report.gates)}"
        for gate in report.gates:
            assert gate.status == "unknown", (
                f"Gate {gate.name!r} should be unknown, got {gate.status}"
            )
            assert gate.evidence == "not implemented", (
                f"Gate {gate.name!r} evidence should be 'not implemented', "
                f"got {gate.evidence!r}"
            )

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

        # Gate 0 (progress-over-time) ran and was unknown.
        assert report.gates[0].name == "progress-over-time"
        assert report.gates[0].status == "unknown"

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
        """A report with any unknown gate never says 'pass'."""
        project = _setup_project(tmp_path)
        original = doctor._resolve_plans_dir
        doctor._resolve_plans_dir = lambda p: project / "docs" / "plans"
        try:
            report = doctor.run_doctor(project, sample_interval=0.1)
        finally:
            doctor._resolve_plans_dir = original

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
