"""Sub-plan ``a-timed-out-gate-takes-its-children`` step 1 — bounded_run tests.

AC-1 through AC-4 for the bounded-run feature.  All tests are plain
(no xfail) — ``bounded_run`` is implemented and the spawns are routed.

AC-1: ``bounded_run.run("true; sleep 47", shell=True, timeout=2)``
      returns ``timed_out=True``, and within 10 s no process matches the
      unique marker embedded in the command.
AC-2: ``run_local_checks.run_one`` with a compound sleeping command and
      short timeout gives the outcome ``error``, contains ``timeout after 2s``,
      and leaves 0 surviving processes.
AC-3: a command that exits normally keeps its stdout, stderr and returncode
      unchanged through ``run_one``.
AC-4: ``red_owner._run_test`` and ``verification_record.run_suite`` with a
      compound sleeping command and a short timeout leave 0 survivors.

Every AC uses a unique marker per test and kills its survivors in
a ``finally``, so a failing test cannot leak processes into the suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_local_checks  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _unique_marker() -> str:
    """Return a unique string to embed in a command so pgrep can find it."""
    return f"ilk-bounded-run-test-{os.getpid()}-{time.monotonic_ns()}"


def _sleep_cmd(marker: str) -> str:
    """Return a compound command that blocks and can be found by pgrep.

    ``sleep`` on macOS rejects arbitrary strings as the time argument,
    so we embed the unique *marker* in an ``echo`` before the sleep.
    ``pgrep -f <marker>`` then matches the full command line.
    """
    return f"echo {marker}; sleep 47"


def _pgrep(pattern: str) -> list[int]:
    """Return pids whose command line matches *pattern*, or empty list."""
    r = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10,
    )
    if r.returncode == 0 and r.stdout.strip():
        return [int(p) for p in r.stdout.strip().split() if p.isdigit()]
    return []


def _kill_survivors(pattern: str) -> None:
    """SIGKILL any process whose command line matches *pattern*."""
    for pid in _pgrep(pattern):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


# ── AC-1: bounded_run kills the whole process group ──────────────────────────

def test_ac1_bounded_run_kills_process_group(tmp_path: Path) -> None:
    marker = _unique_marker()
    try:
        from bounded_run import run

        rc, stdout, stderr, timed_out = run(
            _sleep_cmd(marker), shell=True, timeout=2, cwd=str(tmp_path),
        )
        assert timed_out is True, f"expected timed_out=True, got {timed_out}"
        # Wait up to 10 s for the child to be reaped.
        deadline = time.monotonic() + 10
        survivors = _pgrep(marker)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.2)
            survivors = _pgrep(marker)
        assert survivors == [], f"sleep command survived: pids {survivors}"
    finally:
        _kill_survivors(marker)


# ── AC-2: run_one with a compound command leaves no survivors ────────────────

def test_ac2_run_one_leaves_no_survivors(tmp_path: Path) -> None:
    marker = _unique_marker()
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = run_local_checks.run_one(
            {"command": _sleep_cmd(marker), "timeout": 2},
            scope="step",
            project=repo,
        )
        assert result.outcome == "error", f"expected outcome=error, got {result.outcome}"
        assert "timeout after 2s" in (result.error or ""), (
            f"expected 'timeout after 2s' in error, got {result.error!r}"
        )
        # Wait up to 10 s for the child to be reaped.
        deadline = time.monotonic() + 10
        survivors = _pgrep(marker)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.2)
            survivors = _pgrep(marker)
        assert survivors == [], f"sleep command survived: pids {survivors}"
    finally:
        _kill_survivors(marker)


# ── AC-3: normal exit preserves stdout, stderr, returncode ──────────────────

def test_ac3_normal_exit_preserves_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = run_local_checks.run_one(
        {"command": "printf ok; exit 3", "timeout": 10},
        scope="step",
        project=repo,
    )
    assert result.exit_code == 3, f"expected exit_code=3, got {result.exit_code}"
    assert "ok" in (result.stdout_tail or ""), (
        f"expected 'ok' in stdout_tail, got {result.stdout_tail!r}"
    )


# ── AC-4: red_owner._run_test and verification_record.run_suite ──────────────

def test_ac4_red_owner_run_test_leaves_no_survivors(tmp_path: Path) -> None:
    marker = _unique_marker()
    try:
        from red_owner import _run_test

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(repo), capture_output=True,
        )
        (repo / "dummy.py").write_text("pass\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True,
        )

        _run_test(repo, _sleep_cmd(marker), ["dummy.py"])

        deadline = time.monotonic() + 10
        survivors = _pgrep(marker)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.2)
            survivors = _pgrep(marker)
        assert survivors == [], f"sleep command survived: pids {survivors}"
    finally:
        _kill_survivors(marker)


def test_ac4_run_suite_leaves_no_survivors(tmp_path: Path) -> None:
    marker = _unique_marker()
    try:
        from verification_record import run_suite

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)

        try:
            run_suite(
                project=repo,
                invocation=_sleep_cmd(marker),
                timeout=2,
            )
        except TimeoutError:
            pass  # expected: suite exceeded timeout

        deadline = time.monotonic() + 10
        survivors = _pgrep(marker)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.2)
            survivors = _pgrep(marker)
        assert survivors == [], f"sleep command survived: pids {survivors}"
    finally:
        _kill_survivors(marker)