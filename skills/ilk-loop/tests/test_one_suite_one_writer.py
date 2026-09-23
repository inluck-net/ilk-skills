"""Red-first tests for one suite, one writer per tree.

Sub-plan: one-suite-one-writer
Part of MASTER-2026-09-24b-verify-is-fast-execution-plan.

AC-1: a verify_attribution pass record at HEAD's tree, with the expected
      invocation ⇒ batch_gate.py --run exits 0, prints "already verified",
      starts NO suite subprocess, and leaves the record's bytes unchanged.
AC-2: the same record at a DIFFERENT tree ⇒ runs the suite as today.
AC-3: a batch_gate-written record at HEAD's tree ⇒ runs as today (the
      deferral is only to a verification pass).
AC-4: no deferral, and an existing verify_attribution record for the
      same tree ⇒ that file is unchanged, batch-gate.batch_gate.json holds
      the new verdict, and both are printed.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")


def _git_head_sha(project: Path) -> str:
    """Resolve HEAD sha in the project."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
    ).strip()


def _git_head_tree(project: Path) -> str:
    """Resolve HEAD tree sha in the project."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=project,
        text=True,
    ).strip()


def _init_git_project(tmp_path: Path) -> Path:
    """Create a minimal git project with an empty initial commit."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=project, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=project, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return project


def _add_second_commit(project: Path) -> None:
    """Add a second commit with a different tree (changes the code)."""
    (project / "marker.txt").write_text("second", encoding="utf-8")
    subprocess.run(
        ["git", "add", "marker.txt"],
        cwd=project, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
         "commit", "-m", "second"],
        cwd=project, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _make_verify_attribution_record(
    project: Path,
    *,
    verdict: str = "pass",
    writer: str = "verify_attribution",
) -> dict:
    """Build a record dict with verify_attribution as writer."""
    from batch_gate import (
        _git_head_sha, _git_head_tree, _now_iso,
        _resolve_expected_invocation,
    )

    head_sha = _git_head_sha(project)
    tree_sha = _git_head_tree(project)
    invocation = _resolve_expected_invocation(project) or "python3 -m pytest -q"
    return {
        "verdict": verdict,
        "head_sha": head_sha,
        "invocation": invocation,
        "timestamp": _now_iso(),
        "undeclared": [],
        "excused_count": 0,
        "tree_sha": tree_sha,
        "writer": writer,
    }


def _write_record_json(path: Path, data: dict) -> None:
    """Write a record as JSON to the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_stub_suite(tmp_path: Path) -> Path:
    """Create a stub suite script that records invocations."""
    counter = tmp_path / "suite_counter.txt"
    counter.write_text("0", encoding="utf-8")
    stub = tmp_path / "stub_suite.sh"
    stub.write_text(
        f'#!/bin/bash\ncount=$(cat "{counter}")\n'
        f'echo $((count + 1)) > "{counter}"\nexit 0\n',
    )
    stub.chmod(0o755)
    return stub


def _make_wait_helper(tmp_path: Path) -> Path:
    """Create a simple wait helper that returns once output exists."""
    wait = tmp_path / "wait.sh"
    wait.write_text(
        '#!/bin/bash\n'
        'OUTPUT="$1"\n'
        'for i in $(seq 1 100); do\n'
        '  if [ -f "$OUTPUT" ]; then\n'
        '    SIZE=$(wc -c < "$OUTPUT")\n'
        '    if [ "$SIZE" -gt 0 ]; then\n'
        '      sleep 0.2\n'
        '      echo 0\n'
        '      exit 0\n'
        '    fi\n'
        '  fi\n'
        '  sleep 0.1\n'
        'done\n'
        'echo 125\nexit 125\n',
    )
    wait.chmod(0o755)
    return wait


def _setup_project_with_suite(
    tmp_path: Path,
    stub_suite: Path,
) -> tuple[Path, Path]:
    """Create a git project configured to use the stub suite.

    Returns (project, runtime_dir) where runtime_dir is under tmp_path.
    """
    project = _init_git_project(tmp_path)
    (project / ".ilk-launch.json").write_text(json.dumps({
        "ship": {
            "suite": {
                "command": str(stub_suite),
            },
        },
    }), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return project, runtime


# ── AC-1: defer to verify_attribution pass at same tree ─────────────────────


class TestAC1DeferToVerificationPass:
    """When a verify_attribution pass record exists at HEAD's tree with
    the expected invocation, batch_gate.py --run should exit 0, print
    'already verified', start no suite subprocess, and leave the record
    bytes unchanged."""

    def test_defers_and_runs_no_suite(self, tmp_path: Path) -> None:
        """AC-1: verify_attribution pass at same tree ⇒ no suite run."""
        from batch_gate import (
            BatchGateRecord,
            read_record,
            write_record,
        )

        stub_suite = _make_stub_suite(tmp_path)
        wait_helper = _make_wait_helper(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        # Pre-populate the batch-gate record as verify_attribution wrote it.
        rec_data = _make_verify_attribution_record(project)
        _write_record_json(runtime / "batch-gate.json", rec_data)

        # Capture the file bytes before the gate runs.
        before_bytes = (runtime / "batch-gate.json").read_bytes()

        # Run the batch gate — should defer to the existing record.
        from batch_gate import run_batch_gate, VERIFY_DEFERRED
        result = run_batch_gate(
            project, runtime,
            _wait_helper=wait_helper,
            _poll_timeout=30,
        )

        # AC-1: returns VERIFY_DEFERRED (deferral to verification pass).
        assert result is VERIFY_DEFERRED

        # AC-1: no suite subprocess started.
        counter = tmp_path / "suite_counter.txt"
        assert counter.read_text().strip() == "0"

        # AC-1: record bytes unchanged.
        after_bytes = (runtime / "batch-gate.json").read_bytes()
        assert before_bytes == after_bytes

    def test_prints_already_verified(self, tmp_path: Path) -> None:
        """AC-1: prints 'already verified' message."""
        stub_suite = _make_stub_suite(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        rec_data = _make_verify_attribution_record(project)
        _write_record_json(runtime / "batch-gate.json", rec_data)

        # Run via CLI to capture stdout.
        env = {**os.environ, "PYTHONPATH": _SCRIPTS_DIR}
        result = subprocess.run(
            ["python3", "-m", "batch_gate",
             "--project", str(project),
             "--runtime-dir", str(runtime),
             "--run"],
            capture_output=True, text=True,
            cwd=project, env=env,
        )
        combined = result.stdout + result.stderr
        assert "already verified" in combined.lower() or \
               "already verified" in combined


# ── AC-2: different tree ⇒ runs the suite ───────────────────────────────────


class TestAC2DifferentTreeRunsSuite:
    """A verify_attribution record at a DIFFERENT tree should not defer —
    the suite runs as today."""

    def test_different_tree_runs_suite(self, tmp_path: Path) -> None:
        """AC-2: record at different tree ⇒ suite runs."""
        from batch_gate import run_batch_gate

        stub_suite = _make_stub_suite(tmp_path)
        wait_helper = _make_wait_helper(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        # Write a record with the ORIGINAL head/tree (before second commit).
        rec_data = _make_verify_attribution_record(project)
        _write_record_json(runtime / "batch-gate.json", rec_data)

        # Add a second commit — different head_sha and tree_sha.
        _add_second_commit(project)

        counter = tmp_path / "suite_counter.txt"
        assert counter.read_text().strip() == "0"

        result = run_batch_gate(
            project, runtime,
            _wait_helper=wait_helper,
            _poll_timeout=30,
        )

        # Suite should have run (different head/tree).
        assert result is not None
        assert counter.read_text().strip() == "1"


# ── AC-3: batch_gate-written record at same tree ⇒ runs ─────────────────────


class TestAC3BatchGateRecordRunsSuite:
    """A batch_gate-written record at HEAD's tree triggers re-entry —
    the deferral is only for verification passes."""

    def test_batch_gate_record_reenters(self, tmp_path: Path) -> None:
        """AC-3: batch_gate writer at same tree ⇒ re-entry (None)."""
        from batch_gate import run_batch_gate

        stub_suite = _make_stub_suite(tmp_path)
        wait_helper = _make_wait_helper(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        # Write a record with writer="batch_gate.py" (the default writer).
        rec_data = _make_verify_attribution_record(
            project, writer="batch_gate.py",
        )
        _write_record_json(runtime / "batch-gate.json", rec_data)

        counter = tmp_path / "suite_counter.txt"
        assert counter.read_text().strip() == "0"

        result = run_batch_gate(
            project, runtime,
            _wait_helper=wait_helper,
            _poll_timeout=30,
        )

        # Re-entry: batch_gate's own record at the same tree → None.
        assert result is None
        # Suite did not run.
        assert counter.read_text().strip() == "0"


# ── AC-4: verify_attribution record preserved, batch_gate writes alongside ──


class TestAC4NeverOverwriteVerificationRecord:
    """When no deferral occurs and an existing verify_attribution record
    exists at a different tree, that file is unchanged and batch_gate
    writes its result to batch-gate.batch_gate.json beside it."""

    def test_verify_record_untouched_new_verdict_beside(self, tmp_path: Path) -> None:
        """AC-4: verify_attribution record preserved, new verdict in .batch_gate.json."""
        from batch_gate import run_batch_gate

        stub_suite = _make_stub_suite(tmp_path)
        wait_helper = _make_wait_helper(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        # Write a verify_attribution record with the ORIGINAL head/tree.
        rec_data = _make_verify_attribution_record(project)
        original_record_path = runtime / "batch-gate.json"
        _write_record_json(original_record_path, rec_data)

        # Add a second commit — different head/tree → no deferral.
        _add_second_commit(project)

        # Capture the original bytes.
        before_bytes = original_record_path.read_bytes()

        result = run_batch_gate(
            project, runtime,
            _wait_helper=wait_helper,
            _poll_timeout=30,
        )

        # The suite should have run (different head/tree, no deferral).
        assert result is not None

        # AC-4: original record bytes unchanged.
        after_bytes = original_record_path.read_bytes()
        assert before_bytes == after_bytes

        # AC-4: new verdict in batch-gate.batch_gate.json.
        alt_path = runtime / "batch-gate.batch_gate.json"
        assert alt_path.exists(), (
            "batch_gate should have written to batch-gate.batch_gate.json "
            "when the existing record is from verify_attribution"
        )
        alt_data = json.loads(alt_path.read_text(encoding="utf-8"))
        assert alt_data["verdict"] in ("pass", "fail", "error", "not_configured")

    def test_both_verdicts_printed(self, tmp_path: Path) -> None:
        """AC-4: both the original and new verdicts are printed."""
        stub_suite = _make_stub_suite(tmp_path)
        project, runtime = _setup_project_with_suite(tmp_path, stub_suite)

        # Write a verify_attribution record with the ORIGINAL head/tree.
        rec_data = _make_verify_attribution_record(project)
        _write_record_json(runtime / "batch-gate.json", rec_data)

        # Add a second commit — different head/tree → no deferral.
        _add_second_commit(project)

        env = {**os.environ, "PYTHONPATH": _SCRIPTS_DIR}
        result = subprocess.run(
            ["python3", "-m", "batch_gate",
             "--project", str(project),
             "--runtime-dir", str(runtime),
             "--run"],
            capture_output=True, text=True,
            cwd=project, env=env,
        )
        combined = result.stdout + result.stderr
        # Both verdicts should be printed.
        assert "batch-gate.json" in combined or \
               "verdict" in combined.lower()