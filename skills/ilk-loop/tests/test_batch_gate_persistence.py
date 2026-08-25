"""Red-first tests for the four batch-gate defects found in /ilk-ship Phase 0.

Measured on run 20260825-180144: the gate ran a 337.14s suite that reported
32 failed / 7 errors, then wrote **nothing** to disk and exited 0, while the
runner printed "[batch-gate] Gate completed."  Four independent defects:

D1  ``run_batch_gate`` never persists on the success path — ``write_record``
    had exactly one non-test caller, inside ``except Exception``.
D2  ``wait_for_background_output.sh`` waits for an ``[exited with code N]``
    marker that the *harness* writes when it auto-backgrounds a Bash call.
    ``_run_gate_inner`` uses ``subprocess.Popen`` and nothing writes it, so
    the poll always burns its full bound and returns 125 → verdict is ``fail``
    unconditionally, whatever the suite did.  (0 occurrences of the marker in
    3063 lines of the real batch-gate-suite.output.)
D3  ``main()`` prints the verdict and exits 0 for every verdict, so the
    runner's (correctly fixed) ``|| gate_exit=$?`` idiom cannot see a failure.
D4  ``batch-gate.running`` is acquired but never released, and
    ``_acquire_gate_lock`` refuses on mere file presence — so one gate run
    permanently disables the gate for that project.

The pre-existing tests missed D2 because they inject a *stub* wait helper
that only checks the file is non-empty.  These tests use the real one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REAL_WAIT_HELPER = SCRIPTS / "wait_for_background_output.sh"
BATCH_GATE_CLI = SCRIPTS / "batch_gate.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_project(tmp: Path, suite_command: str | None) -> Path:
    """A git repo with one empty commit, optionally carrying a ship suite."""
    project = tmp / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project, check=True, capture_output=True,
    )
    if suite_command is not None:
        (project / ".ilk-launch.json").write_text(
            json.dumps({"ship": {"suite": {"command": suite_command}}}),
            encoding="utf-8",
        )
    return project


def _dead_pid() -> int:
    """A pid that is certainly gone — spawned, waited, reaped."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ── D1: the success path persists the record ────────────────────────────────

class TestD1SuccessPathPersists:
    """A verdict that is computed but not written is a verdict nobody has."""

    def test_passing_suite_writes_record_to_disk(self, tmp_path: Path) -> None:
        from batch_gate import read_record, record_path, run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"

        rec = run_batch_gate(project, runtime,
                             _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert rec is not None
        assert record_path(runtime).is_file(), (
            "run_batch_gate returned a record but wrote no file — "
            f"{record_path(runtime)} absent"
        )
        on_disk = read_record(runtime)
        assert on_disk is not None
        assert on_disk.verdict == rec.verdict
        assert on_disk.head_sha == rec.head_sha

    def test_not_configured_writes_record_to_disk(self, tmp_path: Path) -> None:
        from batch_gate import read_record, run_batch_gate

        project = _make_project(tmp_path, None)
        runtime = tmp_path / "runtime"

        rec = run_batch_gate(project, runtime)

        assert rec is not None and rec.verdict == "not_configured"
        on_disk = read_record(runtime)
        assert on_disk is not None, "not_configured verdict was not persisted"
        assert on_disk.verdict == "not_configured"

    def test_persisted_head_sha_is_the_project_head(self, tmp_path: Path) -> None:
        from batch_gate import read_record, run_batch_gate

        project = _make_project(tmp_path, "true")
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        runtime = tmp_path / "runtime"

        run_batch_gate(project, runtime,
                       _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        on_disk = read_record(runtime)
        assert on_disk is not None
        assert on_disk.head_sha == head


# ── D2: the verdict tracks the suite, not the poll bound ────────────────────

class TestD2VerdictTracksSuiteExit:
    """With the REAL wait helper, the marker must actually appear."""

    def test_passing_suite_is_a_pass(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        rec = run_batch_gate(project, tmp_path / "runtime",
                             _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert rec is not None
        assert rec.verdict == "pass", (
            "A suite that exits 0 must record pass.  Got "
            f"{rec.verdict!r} — the poll never saw an exit marker."
        )

    def test_failing_suite_is_a_fail(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "false")
        rec = run_batch_gate(project, tmp_path / "runtime",
                             _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert rec is not None
        assert rec.verdict == "fail"

    def test_exit_marker_is_written_to_the_output_file(self, tmp_path: Path) -> None:
        """The helper's contract: the output file ends with the marker."""
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"
        run_batch_gate(project, runtime,
                       _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        output = (runtime / "batch-gate-suite.output").read_text(encoding="utf-8")
        assert "[exited with code 0]" in output, (
            "wait_for_background_output.sh greps for this marker; nothing "
            f"wrote it.  Output was:\n{output!r}"
        )

    def test_pass_does_not_take_the_whole_poll_bound(self, tmp_path: Path) -> None:
        """A trivially-passing suite must not burn the poll timeout.

        This is the cost defect: 263 of the run's 600 gate seconds were spent
        waiting for a marker that could never appear.
        """
        import time
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        started = time.monotonic()
        run_batch_gate(project, tmp_path / "runtime",
                       _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)
        elapsed = time.monotonic() - started

        assert elapsed < 15, (
            f"gate took {elapsed:.1f}s for a `true` suite with a 30s poll "
            "bound — the poll is not terminating on the suite's exit"
        )


# ── D3: the CLI's exit status carries the verdict ───────────────────────────

class TestD3CliExitStatus:
    """The runner reads ``$?``; a fail verdict must make it non-zero."""

    def _run_cli(self, project: Path, runtime: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BATCH_GATE_CLI),
             "--project", str(project), "--runtime-dir", str(runtime),
             "--run", "--poll-timeout", "30"],
            capture_output=True, text=True, timeout=120, encoding="utf-8",
        )

    def test_failing_verdict_exits_non_zero(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path, "false")
        result = self._run_cli(project, tmp_path / "runtime")

        assert "verdict=fail" in result.stdout
        assert result.returncode != 0, (
            "A fail verdict exited 0 — the runner's failure branch cannot "
            f"fire.  stdout={result.stdout!r}"
        )

    def test_passing_verdict_exits_zero(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path, "true")
        result = self._run_cli(project, tmp_path / "runtime")

        assert "verdict=pass" in result.stdout
        assert result.returncode == 0, (
            f"A pass verdict must exit 0.  stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )

    def test_not_configured_exits_zero_and_names_the_gap(self, tmp_path: Path) -> None:
        """not_configured stays distinct from fail.

        SP6 created a separate verdict precisely so "no suite" does not read
        as "suite failed"; collapsing them into one exit code undoes that.
        """
        project = _make_project(tmp_path, None)
        result = self._run_cli(project, tmp_path / "runtime")

        assert "verdict=not_configured" in result.stdout
        assert result.returncode == 0


# ── D4: the running marker is a liveness lock, not a tombstone ──────────────

class TestD4LockLifecycle:
    """One gate run must not permanently disable the gate for a project."""

    def test_marker_is_removed_when_the_gate_finishes(self, tmp_path: Path) -> None:
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"
        run_batch_gate(project, runtime,
                       _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert not (runtime / "batch-gate.running").is_file(), (
            "batch-gate.running survived a completed gate — the next batch's "
            "gate will refuse to acquire it forever"
        )

    def test_dead_marker_does_not_block_a_later_gate(self, tmp_path: Path) -> None:
        """A crashed gate leaves a marker; the next one must still run."""
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "batch-gate.running").write_text(
            json.dumps({"pid": _dead_pid(),
                        "started_at": "2026-08-25T10:00:00+08:00"}),
            encoding="utf-8",
        )

        rec = run_batch_gate(project, runtime,
                             _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert rec is not None, (
            "a stale marker from a dead gate process blocked a new gate run"
        )
        assert rec.verdict == "pass"

    def test_live_marker_still_blocks_re_entry(self, tmp_path: Path) -> None:
        """Regression guard: a genuinely running gate is still exclusive."""
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "batch-gate.running").write_text(
            json.dumps({"pid": os.getpid(),
                        "started_at": "2026-08-25T10:00:00+08:00"}),
            encoding="utf-8",
        )

        assert run_batch_gate(project, runtime,
                              _wait_helper=REAL_WAIT_HELPER,
                              _poll_timeout=30) is None

    def test_same_head_does_not_run_twice(self, tmp_path: Path) -> None:
        """AC-1 preserved: the guard is now the record, not the tombstone."""
        from batch_gate import run_batch_gate

        counter = tmp_path / "counter.txt"
        counter.write_text("0", encoding="utf-8")
        stub = tmp_path / "stub.sh"
        stub.write_text(
            f'#!/bin/bash\ncount=$(cat "{counter}")\n'
            f'echo $((count + 1)) > "{counter}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)

        project = _make_project(tmp_path, str(stub))
        runtime = tmp_path / "runtime"

        first = run_batch_gate(project, runtime,
                               _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)
        second = run_batch_gate(project, runtime,
                                _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert first is not None and second is None
        assert counter.read_text(encoding="utf-8").strip() == "1"

    def test_new_head_runs_a_new_gate(self, tmp_path: Path) -> None:
        """A record for a different HEAD must not suppress this batch's gate."""
        from batch_gate import run_batch_gate

        project = _make_project(tmp_path, "true")
        runtime = tmp_path / "runtime"

        first = run_batch_gate(project, runtime,
                               _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)
        assert first is not None

        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "next"],
            cwd=project, check=True, capture_output=True,
        )
        second = run_batch_gate(project, runtime,
                                _wait_helper=REAL_WAIT_HELPER, _poll_timeout=30)

        assert second is not None, "a new HEAD must get its own gate run"
        assert second.head_sha != first.head_sha
