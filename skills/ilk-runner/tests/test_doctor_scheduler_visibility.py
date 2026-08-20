"""Gate `scheduler-visibility`: the doctor must not pass a project the
scheduler cannot see.

Regression guard for 2026-08-20. ilk-doctor walked its eight gates and returned
``verdict: pass: all gates clear`` for a project the cross-project scheduler had
been unable to dispatch for over an hour. Two gates already held the halves of
the answer -- ``master-status`` said ``active (runnable)`` and ``process-set``
said ``no runner processes found`` -- and nothing joined them, so the operator's
own diagnostic tool confirmed health while the project was stranded.

The cause lived one layer down: a ``TypeError`` inside
``scheduler_scan._scan_one_project``, swallowed per-project by design in
``scan_projects``. The gate asks the scanner directly so that failure has a
reporter.

Hermetic -- no live scheduler, no dispatch, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import doctor  # noqa: E402


def _make_project_data(tmp_path: Path, *, master_status: str = "active",
                       subplan_status: str = "in-progress") -> tuple[Path, Path]:
    """Return (project_data_dir, plans_dir) for a project with one master."""
    project_data = tmp_path / "projects" / "some-proj"
    plans_dir = project_data / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "MASTER-x-execution-plan.md").write_text(
        "---\n"
        "title: MASTER-x\n"
        "created: 2026-08-20T00:00:00+08:00\n"
        f"status: {master_status}\n"
        "priority: 1\n"
        "---\n\n"
        "# MASTER-x\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        f"| 1 | [2026-08-20-work.md](./2026-08-20-work.md) | {subplan_status} |\n",
        encoding="utf-8",
    )
    (plans_dir / "2026-08-20-work.md").write_text(
        "---\n"
        "plan: 2026-08-20-work\n"
        f"status: {subplan_status}\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-08-20\n"
        "---\n\n# work\n",
        encoding="utf-8",
    )
    return project_data, plans_dir


def test_gate_is_registered_and_ordered_before_config():
    """The gate exists, is wired, and runs before config-resolution."""
    assert "scheduler-visibility" in doctor.GATE_ORDER
    order = doctor.GATE_ORDER
    assert order.index("scheduler-visibility") > order.index("process-set")
    assert order.index("scheduler-visibility") < order.index("config-resolution")


def test_scan_raises_is_a_blocker(tmp_path, monkeypatch):
    """The 2026-08-20 shape: the scan raises -> blocked, not pass."""
    project_data, plans_dir = _make_project_data(tmp_path)

    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts"))
    import scheduler_scan

    def explode(_project_dir):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")

    monkeypatch.setattr(scheduler_scan, "_scan_one_project", explode)

    result = doctor._gate_scheduler_visibility(project_data, plans_dir)

    assert result.status == "blocked", (
        f"a scan that RAISES must block, got {result.status}: {result.evidence}"
    )
    assert "TypeError" in result.evidence
    assert "offset-naive" in result.evidence
    assert result.name == "scheduler-visibility"


def test_runnable_master_absent_from_scan_is_a_blocker(tmp_path, monkeypatch):
    """Stranded-active: runnable master, no scan entry, no exception."""
    project_data, plans_dir = _make_project_data(tmp_path)

    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts"))
    import scheduler_scan

    monkeypatch.setattr(scheduler_scan, "_scan_one_project", lambda _p: None)

    result = doctor._gate_scheduler_visibility(project_data, plans_dir)

    assert result.status == "blocked", (
        f"runnable-but-invisible must block, got {result.status}: {result.evidence}"
    )
    assert "STRANDED" in result.evidence


def test_shipped_master_absent_from_scan_passes(tmp_path, monkeypatch):
    """A finished project is legitimately not dispatchable — that is a pass."""
    project_data, plans_dir = _make_project_data(
        tmp_path, master_status="shipped", subplan_status="shipped",
    )

    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts"))
    import scheduler_scan

    monkeypatch.setattr(scheduler_scan, "_scan_one_project", lambda _p: None)

    result = doctor._gate_scheduler_visibility(project_data, plans_dir)

    assert result.status == "pass", f"{result.status}: {result.evidence}"
    assert "correctly so" in result.evidence


def test_visible_project_passes_with_its_scan_entry(tmp_path, monkeypatch):
    """When the scan does see it, the gate says so and quotes the entry."""
    project_data, plans_dir = _make_project_data(tmp_path)

    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts"))
    import scheduler_scan

    monkeypatch.setattr(
        scheduler_scan, "_scan_one_project",
        lambda _p: {
            "key": "some-proj",
            "path": str(project_data),
            "repo_path": "/repo/some",
            "oldest_queued_ts": "2026-08-20T00:00:00",
            "has_active_master": True,
        },
    )

    result = doctor._gate_scheduler_visibility(project_data, plans_dir)

    assert result.status == "pass", f"{result.status}: {result.evidence}"
    assert "2026-08-20T00:00:00" in result.evidence


def test_verdict_is_not_all_clear_when_project_is_invisible(tmp_path, monkeypatch):
    """End to end: the whole-report verdict must stop saying `pass`.

    This is the assertion the incident needed. Every OTHER gate passes; only
    scheduler-visibility fails, and the verdict has to change because of it.
    """
    project_data, plans_dir = _make_project_data(tmp_path)

    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts"))
    import scheduler_scan

    def explode(_project_dir):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")

    monkeypatch.setattr(scheduler_scan, "_scan_one_project", explode)

    # Neutralise every gate that runs BEFORE ours so the verdict is decided by
    # scheduler-visibility alone.
    for gate_name, fn_name in [
        ("progress-over-time", "_gate_progress_over_time"),
        ("master-status", "_gate_master_status"),
        ("subplan-statuses", "_gate_subplan_statuses"),
        ("blacklist", "_gate_blacklist"),
        ("lock-holders", "_gate_lock_holders"),
        ("process-set", "_gate_process_set"),
        ("sentinel-vs-reality", "_gate_sentinel_vs_reality"),
        ("config-resolution", "_gate_config_resolution"),
    ]:
        monkeypatch.setattr(
            doctor, fn_name,
            (lambda name: lambda *a, **k: doctor.GateResult(
                name=name, status="pass", evidence="stubbed pass",
            ))(gate_name),
        )
    monkeypatch.setattr(doctor, "_resolve_project_data", lambda p: project_data)
    monkeypatch.setattr(doctor, "_resolve_plans_dir", lambda p: plans_dir)

    report = doctor.run_doctor(tmp_path, sample_interval=0.01)

    assert not report.verdict.startswith("pass"), (
        f"an invisible project must not get an all-clear, got: {report.verdict}"
    )
    assert "scheduler-visibility" in report.verdict
