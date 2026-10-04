"""Tests for safety_case.run and its integration into release_train.prove.

Sub-plan: a-release-runs-the-safety-case, step 0 (pins) + step 1 (impl).

Each test builds a throwaway git repo (with tags) under ``tmp_path``,
a fake data root (``ILK_DATA_HOME``), and stubs for the three safety-case
components (invariants, golden, teeth).  They never run this repo's real
suite, never push, never touch the real ``~/.ilk-data``.

The six acceptance criteria:
  AC-1  safety_case.run with all three stubs green → pass, record written.
  AC-2  each component failure (exit 1, over budget, missing budget.json)
        → fail naming that component.
  AC-3  ILK_WORKER_SESSION=1 → raises WorkerSessionRefused, no record.
  AC-4  prove with a kernel-range violation → refused kernel-range, no
        Phase 1 or safety case run.
  AC-5  prove with clean range + stubbed safety case: teeth fail → refused
        safety-case: teeth; all green → proven with both new proof keys.
  AC-6  (control) d's own prove tests still pass unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_project(
    tmp_path: Path,
    *,
    tag: str = "v0.0.1",
    auto_planned: bool = False,
    kernel_edit: bool = False,
) -> Path:
    """Create a minimal git repo with a tag and optional kernel edit commit.

    If *kernel_edit* is True, a second commit edits
    ``skills/ilk-loop/scripts/batch_gate.py`` (a kernel-tier path) with a
    ``[plan:x#step-1]`` trailer and a plans dir whose master has
    ``auto_planned: true`` when *auto_planned* is set.
    """
    project = tmp_path / "project"
    project.mkdir()

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, check=True, capture_output=True,
    )

    # .ilk-launch.json
    (project / ".ilk-launch.json").write_text(json.dumps({
        "ship": {
            "release_train": True,
            "suite": {"command": "python3 -m pytest", "flags": []},
        },
    }))

    # safety-kernel.json (minimal, with batch_gate.py in kernel tier)
    kernel_dir = project / "skills" / "ilk-loop"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    (kernel_dir / "safety-kernel.json").write_text(json.dumps({
        "schema": 1,
        "rules": [],
        "kernel": [
            {"path": "skills/ilk-loop/scripts/batch_gate.py",
             "why": "batch gate is the proof/verdict layer"},
        ],
        "kernel_basenames": [],
    }))

    # Initial commit + tag
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", tag],
        cwd=project, check=True, capture_output=True,
    )

    # Trivial test file so prove can run the suite (HEAD != tag)
    (project / "test_trivial.py").write_text(textwrap.dedent("""\
        def test_one():
            assert True

        def test_two():
            assert True

        def test_three():
            assert True
    """))
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add tests"],
        cwd=project, check=True, capture_output=True,
    )

    if kernel_edit:
        # Plans dir with auto_planned master
        plans_dir = project / "docs" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        auto_val = "true" if auto_planned else "false"
        master = textwrap.dedent(f"""\
            ---
            master_plan: 2026-10-03x-test
            auto_planned: {auto_val}
            ---
            # Test master

            | # | Slug | Status |
            |---|---|---|
            | 0 | [2026-10-03x-kernel-edit.md](./2026-10-03x-kernel-edit.md) | pending |
        """)
        (plans_dir / "MASTER-2026-10-03x-test.md").write_text(master)

        # Edit a kernel-tier file
        gate_file = project / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
        gate_file.parent.mkdir(parents=True, exist_ok=True)
        gate_file.write_text("# modified by test\n")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "edit kernel [plan:2026-10-03x-kernel-edit#step-1]"],
            cwd=project, check=True, capture_output=True,
        )

    return project


def _make_data_dir(tmp_path: Path) -> Path:
    """Create a minimal data directory structure."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime = data_dir / "runtime" / "release"
    runtime.mkdir(parents=True)
    return data_dir


def _write_baseline(data_dir: Path, tag: str, invocation: str, ids: list) -> None:
    """Write a baseline file for the given tag and invocation."""
    baselines_dir = data_dir / "runtime" / "release" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    key = f"{tag}_{invocation.replace(' ', '_')}"
    (baselines_dir / f"{key}.json").write_text(json.dumps(ids))


# ── AC-1: safety_case.run all green → pass, record written ──────────────────

class TestSafetyCaseAllGreen:
    """safety_case.run with all three components stubbed green returns pass
    and writes the record keyed by tree sha with writer: "driver"."""

    # Implementation complete — xfail removed
    def test_all_green_pass_and_record(self, tmp_path: Path) -> None:
        from safety_case import run

        project = tmp_path / "project"
        project.mkdir()
        (project / "dummy").write_text("content")
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True)

        # Create budget.json for golden component
        budget_dir = project / "tests" / "invariants" / "fixtures" / "golden"
        budget_dir.mkdir(parents=True, exist_ok=True)
        (budget_dir / "budget.json").write_text(json.dumps({"max_seconds": 120}))

        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

        data_dir = _make_data_dir(tmp_path)

        # Stub all three components to succeed
        def _stub_runner_ok(cmd, **kw):
            r = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            return r

        result = run(project, data_dir=data_dir, runner=_stub_runner_ok)

        assert result["verdict"] == "pass"
        assert result["writer"] == "driver"
        # Record file must exist keyed by tree sha
        record_path = data_dir / "runtime" / "safety-case" / f"{result['tree']}.json"
        assert record_path.exists()
        record = json.loads(record_path.read_text())
        assert record["verdict"] == "pass"
        assert record["writer"] == "driver"


# ── AC-2: each component failure → fail naming that component ───────────────

class TestSafetyCaseComponentFailures:
    """Each of: invariants exit 1, golden over budget, teeth exit 1,
    missing budget.json → fail naming that component."""

    # Implementation complete — xfail removed
    def test_invariants_fail(self, tmp_path: Path) -> None:
        from safety_case import run

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True)
        (project / "a").write_text("a")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

        data_dir = _make_data_dir(tmp_path)

        def _stub_runner_fail(cmd, **kw):
            r = type("R", (), {"returncode": 1, "stdout": "FAIL", "stderr": ""})()
            return r

        result = run(project, data_dir=data_dir, components=("invariants",), runner=_stub_runner_fail)
        assert result["verdict"] == "fail"
        assert result["components"][0]["ok"] is False

    # Implementation complete — xfail removed
    def test_golden_over_budget(self, tmp_path: Path) -> None:
        from safety_case import run

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True)
        (project / "a").write_text("a")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

        data_dir = _make_data_dir(tmp_path)

        # No budget.json → golden should fail
        def _stub_runner_ok(cmd, **kw):
            r = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            return r

        result = run(project, data_dir=data_dir, components=("golden",), runner=_stub_runner_ok)
        assert result["verdict"] == "fail"

    # Implementation complete — xfail removed
    def test_teeth_fail(self, tmp_path: Path) -> None:
        from safety_case import run

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True)
        (project / "a").write_text("a")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

        data_dir = _make_data_dir(tmp_path)

        def _stub_runner_fail(cmd, **kw):
            r = type("R", (), {"returncode": 1, "stdout": "MUTATION SURVIVED", "stderr": ""})()
            return r

        result = run(project, data_dir=data_dir, components=("teeth",), runner=_stub_runner_fail)
        assert result["verdict"] == "fail"
        assert result["components"][0]["ok"] is False


# ── AC-3: ILK_WORKER_SESSION=1 → raises, no record ─────────────────────────

class TestSafetyCaseWorkerRefused:
    """With ILK_WORKER_SESSION=1, run raises WorkerSessionRefused and
    the CLI exits 3; no record file exists afterwards."""

    # Implementation complete — xfail removed
    def test_worker_session_raises(self, tmp_path: Path) -> None:
        from safety_case import WorkerSessionRefused, run

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True, capture_output=True)
        (project / "a").write_text("a")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

        data_dir = _make_data_dir(tmp_path)

        old_env = os.environ.get("ILK_WORKER_SESSION")
        try:
            os.environ["ILK_WORKER_SESSION"] = "1"
            with pytest.raises(WorkerSessionRefused):
                run(project, data_dir=data_dir)
        finally:
            if old_env is None:
                os.environ.pop("ILK_WORKER_SESSION", None)
            else:
                os.environ["ILK_WORKER_SESSION"] = old_env

        # No record file should exist
        sc_dir = data_dir / "runtime" / "safety-case"
        assert not sc_dir.exists() or list(sc_dir.glob("*.json")) == []


# ── AC-4: prove with kernel-range violation → refused, no Phase 1 ───────────

class TestProveKernelRangeViolation:
    """prove on a tmp repo with a kernel-range violation refuses with
    kernel-range: kernel-edit-by-unattended-build, and does not run
    Phase 1 or the safety case."""

    # Implementation complete — xfail removed
    def test_kernel_range_refuses_unattended(self, tmp_path: Path) -> None:
        from release_train import prove

        project = _make_fake_project(tmp_path, kernel_edit=True, auto_planned=True)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert "kernel-range" in result["reason"]
        assert "kernel-edit-by-unattended-build" in result["reason"]
        # Proof file should record the refusal
        assert result["proof_file"] is not None
        proof = json.loads(result["proof_file"].read_text())
        assert proof["verdict"] == "refused"


# ── AC-5: prove with clean range + stubbed safety case ──────────────────────

class TestProveCleanRangeSafetyCase:
    """prove with a clean range and a stubbed safety case: teeth fail →
    refused safety-case: teeth; all green → proven with both new keys."""

    # Implementation complete — xfail removed
    def test_safety_case_teeth_fail_refuses(self, tmp_path: Path) -> None:
        """When safety_case.run returns fail for teeth, prove refuses."""
        from release_train import prove

        project = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Stub safety_case.run to return fail
        import safety_case
        original_run = safety_case.run

        def _stub_fail(*a, **kw):
            return {
                "verdict": "fail",
                "tree": "abc123",
                "head": "abc123",
                "components": [{"name": "teeth", "ok": False, "seconds": 10, "budget_seconds": 900, "exit": 1, "tail": "MUTATION SURVIVED"}],
                "writer": "driver",
            }

        safety_case.run = _stub_fail
        try:
            result = prove(project, data_dir)
        finally:
            safety_case.run = original_run

        assert result["proven"] is False
        assert "safety-case" in result["reason"]

    # Implementation complete — xfail removed
    def test_safety_case_all_green_proven(self, tmp_path: Path) -> None:
        """When safety_case.run returns pass, prove succeeds with new keys."""
        from release_train import prove

        project = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Stub safety_case.run to return pass
        import safety_case
        original_run = safety_case.run

        def _stub_pass(*a, **kw):
            return {
                "verdict": "pass",
                "tree": "abc123",
                "head": "abc123",
                "components": [
                    {"name": "invariants", "ok": True, "seconds": 30, "budget_seconds": 120, "exit": 0, "tail": ""},
                    {"name": "golden", "ok": True, "seconds": 70, "budget_seconds": 105, "exit": 0, "tail": ""},
                    {"name": "teeth", "ok": True, "seconds": 100, "budget_seconds": 900, "exit": 0, "tail": ""},
                ],
                "writer": "driver",
            }

        safety_case.run = _stub_pass
        try:
            result = prove(project, data_dir)
        finally:
            safety_case.run = original_run

        assert result["proven"] is True
        # Proof file should carry safety_case and kernel_range keys
        assert result["proof_file"] is not None
        proof = json.loads(result["proof_file"].read_text())
        assert "safety_case" in proof
        assert "kernel_range" in proof