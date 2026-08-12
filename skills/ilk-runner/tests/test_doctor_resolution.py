"""Regression tests for doctor.py path/master resolution.

Three defects found by live-running `/ilk-doctor` against a real project
immediately after the sub-plan shipped (2026-08-12).  All three shared one
shape: the tool answered confidently from the wrong place, and reported the
resulting emptiness as ``pass``.

  A. ``_resolve_project_data`` returned the data ROOT (``~/.ilk-data``) instead
     of the project's data dir (``~/.ilk-data/projects/<key>``), so gates 0, 4
     and 6 consulted ``~/.ilk-data/logs/runs`` and
     ``~/.ilk-data/runtime/launcher/run.lock`` — paths that do not exist — and
     each returned ``pass`` ("no runs directory", "run.lock does not exist").
  B. The project key was built with ``str(path).replace("/", "-")`` with no
     lowercasing and no length cap, diverging from the canonical
     ``ilk_paths.project_key``.  It only worked on case-insensitive APFS.
  C. ``_gate_master_status`` / ``_gate_subplan_statuses`` used
     ``sorted(glob("MASTER-*.md"))[0]`` — the alphabetically first master, not
     the active one.  On a real project with 17 masters it reported a master
     from two months earlier as "shipped — all work complete".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import doctor


def _write_master(plans_dir: Path, name: str, *, status: str,
                  created: str = "2026-01-01T00:00:00+08:00",
                  subplans: list[str] | None = None) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        f"created: {created}",
        f"status: {status}",
        "priority: 0",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines += ["## Sub-plan registry", "", "| # | Sub-plan | Status |", "|---|---|---|"]
        lines += [f"| 1 | [{sp}](./{sp}) | pending |" for sp in subplans]
        lines.append("")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _write_subplan(plans_dir: Path, name: str, *, status: str) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / name).write_text(
        "---\n"
        f"plan: {name[:-3]}\n"
        f"status: {status}\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "---\n"
        f"\n# {name}\n",
        encoding="utf-8",
    )


# ── Bug A: the project data dir must include projects/<key> ─────────────────

def test_project_data_dir_is_scoped_to_the_project(tmp_path, monkeypatch):
    """AC: gates must consult ~/.ilk-data/projects/<key>/..., not ~/.ilk-data/...

    This is the defect that made gate 0 report "no runs directory" as a pass
    while the project's real runs dir was full of logs.
    """
    data_root = tmp_path / "data"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))

    resolved = doctor._resolve_project_data(project)

    assert resolved != data_root, (
        "returned the data ROOT; every runtime/log path would miss projects/<key>"
    )
    rel = resolved.relative_to(data_root)
    assert rel.parts[0] == "projects", f"expected projects/<key>, got {rel}"
    assert len(rel.parts) == 2, f"expected exactly projects/<key>, got {rel}"


def test_runs_dir_resolves_under_the_project(tmp_path, monkeypatch):
    """A real runs dir must be found, not reported absent."""
    data_root = tmp_path / "data"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))

    # Build the runs dir at the CANONICAL location, independent of whatever
    # doctor resolves — otherwise the test is tautological.
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
    )
    import ilk_paths

    key = ilk_paths.project_key(project)
    runs = data_root / "projects" / key / "logs" / "runs" / "20260812-160437"
    runs.mkdir(parents=True)
    (runs / "iter-01.log").write_text("working\n", encoding="utf-8")

    project_data = doctor._resolve_project_data(project)
    result = doctor._gate_progress_over_time(project_data, sample_interval=0.01)
    assert "no runs directory" not in result.evidence, (
        f"failed to see a real runs dir: {result.evidence}"
    )


# ── Bug B: the key must match the canonical helper ──────────────────────────

def test_project_key_matches_canonical_helper(tmp_path, monkeypatch):
    """doctor must derive the same key as ilk_paths.project_key.

    A divergent key silently reads a different project's plans (or none) —
    and on a case-SENSITIVE filesystem finds nothing at all.
    """
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
    )
    import ilk_paths

    data_root = tmp_path / "data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))
    # Mixed-case path — the original bug produced "Users-chad-Projects-..."
    project = tmp_path / "Users" / "Chad" / "Projects" / "MyRepo"
    project.mkdir(parents=True)

    expected = ilk_paths.project_key(project)
    resolved = doctor._resolve_project_data(project)

    assert resolved.name == expected, (
        f"key diverged: doctor={resolved.name!r} canonical={expected!r}"
    )
    assert resolved.name == resolved.name.lower(), "key must be lowercase"


# ── Bug C: pick the ACTIVE master, not the alphabetically first ─────────────

def test_master_status_picks_the_active_master(tmp_path):
    """With several masters present, the active one decides the verdict.

    Regression: an alphabetically-earlier `shipped` master made the doctor
    report "all work complete" while an `active` master was mid-run.
    """
    plans = tmp_path / "plans"
    _write_master(plans, "MASTER-2026-06-15-old-thing.md", status="shipped")
    _write_master(plans, "MASTER-2026-08-12-current-thing.md", status="active",
                  created="2026-08-12T15:00:00+08:00")

    result = doctor._gate_master_status(plans)

    assert "all work complete" not in result.evidence, (
        f"reported the stale shipped master: {result.evidence}"
    )
    assert "current-thing" in result.artifact, (
        f"consulted the wrong master: {result.artifact}"
    )


def test_subplan_statuses_follow_the_active_master(tmp_path):
    """Sub-plan gate must read the active master's registry, not the first one."""
    plans = tmp_path / "plans"
    _write_master(plans, "MASTER-2026-06-15-old-thing.md", status="shipped",
                  subplans=["2026-06-15-done.md"])
    _write_subplan(plans, "2026-06-15-done.md", status="shipped")
    _write_master(plans, "MASTER-2026-08-12-current-thing.md", status="active",
                  created="2026-08-12T15:00:00+08:00",
                  subplans=["2026-08-12-todo.md"])
    _write_subplan(plans, "2026-08-12-todo.md", status="pending")

    result = doctor._gate_subplan_statuses(plans)

    assert "all 1 sub-plan(s) shipped" not in result.evidence, (
        f"read the stale master's registry: {result.evidence}"
    )


# ── Bug B/D: the blacklist gate must actually be implemented ────────────────

def test_blacklist_gate_is_implemented(tmp_path, monkeypatch):
    """Gate 3 shipped as the step-0 skeleton stub ('unknown: not implemented').

    An always-unknown gate makes every verdict 'unknown', which trains the
    operator to ignore the field.
    """
    data_root = tmp_path / "data"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))
    project_data = doctor._resolve_project_data(project)
    (project_data / "runtime" / "launcher" / "postmortems").mkdir(parents=True)

    result = doctor._gate_blacklist(project_data)

    assert "not implemented" not in result.evidence, (
        f"gate 3 is still a stub: {result.evidence}"
    )


# ── Bug E: "no sentinel" must not be reported as "no run has started" ───────

def test_missing_sentinel_with_runs_present_is_not_a_pass(tmp_path, monkeypatch):
    """A completed run that left no exit sentinel is a finding, not a pass.

    Observed 2026-08-12: gate 6 said "no last-exit.json (no run has started)"
    for a project whose run had just finished 10 iterations — gate 0 had read
    iter-10.log from it in the same report.
    """
    data_root = tmp_path / "data"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))
    project_data = doctor._resolve_project_data(project)
    runs = project_data / "logs" / "runs" / "20260812-160437"
    runs.mkdir(parents=True)
    (runs / "iter-10.log").write_text("done\n", encoding="utf-8")

    result = doctor._gate_sentinel_vs_reality(project_data, live_pids=[])

    assert "no run has started" not in result.evidence, (
        f"claimed no run started while a run dir exists: {result.evidence}"
    )
    assert result.status != "pass", (
        f"a run with no exit sentinel must not read as pass: {result.status}"
    )
    assert "20260812-160437" in result.evidence


def test_missing_sentinel_with_no_runs_is_a_pass(tmp_path, monkeypatch):
    """Genuinely-never-run projects still pass cleanly (no false alarm)."""
    data_root = tmp_path / "data"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))
    project_data = doctor._resolve_project_data(project)

    result = doctor._gate_sentinel_vs_reality(project_data, live_pids=[])

    assert result.status == "pass", result.evidence
    assert "no run has started" in result.evidence
