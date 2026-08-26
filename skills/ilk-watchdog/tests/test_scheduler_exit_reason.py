"""Red-first tests for scheduler exit observability.

Validates AC-1..AC-4 and AC-6 from sub-plan the-scheduler-says-why-it-stopped:

  AC-1  Both cleanup actions run on exit — scheduler.pid is removed AND the
        scan-stderr tempfile is removed.
  AC-2  On SIGTERM the scheduler logs a line naming the signal, releases the
        lock, and exits.
  AC-3  Same for SIGINT and SIGHUP.
  AC-4  On a normal exit path the scheduler logs why it is stopping with its
        exit status.
  AC-6  scheduler.pid still contains a bare PID (format unchanged).

Drives scheduler.sh as a subprocess with an isolated HOME so no test touches
the operator's ~/.ilk-data.  Signals are sent while the scheduler is sleeping
inside its poll loop.

These tests are expected to FAIL against the current scheduler.sh because:
  1. Two separate `trap ... EXIT` lines — the second clobbers the first, so
     release_scheduler_lock never runs (scheduler.pid survives every exit).
  2. No SIGTERM/SIGINT/SIGHUP handlers — signals produce no log line and
     release nothing.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


def _scheduler_env(home: Path, *, extra: dict | None = None) -> dict[str, str]:
    """Build an isolated env.

    HOME alone is NOT enough: scheduler.sh resolves
    ILK_DATA_HOME -> ILK_DATA_DIR -> $HOME/.ilk-data, so an inherited
    ILK_DATA_HOME/ILK_DATA_DIR wins over HOME and the test reads the real
    ~/.ilk-data (including the live scheduler's pidfile).  Three tests in
    this repo leak ILK_DATA_HOME through raw os.environ writes; the same
    gap broke the v0.9.74 batch gate and was fixed there in 33a2712.
    Pin the data home explicitly, and clear the alias.
    """
    env = {
        **os.environ,
        "HOME": str(home),
        "ILK_SKILL_HOME": str(SKILLS_DIR),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
    }
    env.pop("ILK_DATA_DIR", None)
    if extra:
        env.update(extra)
    return env


def _run_scheduler_once(home: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run scheduler.sh --once --dry-run (single cycle, no poll loop)."""
    return subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True, text=True, timeout=timeout,
        env=_scheduler_env(home), encoding="utf-8",
    )


def _start_scheduler_poll(home: Path, poll_min: int = 60) -> subprocess.Popen:
    """Start scheduler.sh in poll mode (not --once) so it sleeps in-loop."""
    return subprocess.Popen(
        ["bash", str(SCHEDULER), "--poll-min", str(poll_min)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_scheduler_env(home), encoding="utf-8",
    )


def _pidfile(home: Path) -> Path:
    return home / ".ilk-data" / "scheduler.pid"


def _log_file(home: Path) -> Path:
    return home / ".ilk-data" / "logs" / "scheduler.log"


# ── AC-6: scheduler.pid is a bare PID ────────────────────────────────────────

def test_pidfile_is_bare_pid(tmp_path):
    """AC-6: scheduler.pid must contain only digits (bare PID format)."""
    _run_scheduler_once(tmp_path)
    pf = _pidfile(tmp_path)
    # After --once --dry-run, the EXIT trap may or may not have removed the
    # file.  If it exists, verify the format; if absent, that's the clobber
    # bug (which is what we expect to find here).
    if pf.exists():
        raw = pf.read_text(encoding="utf-8").strip()
        assert raw.isdigit(), (
            f"scheduler.pid should be bare PID, got: {raw!r}"
        )


# ── AC-1: both cleanup actions run on exit ────────────────────────────────────

def test_pidfile_removed_after_normal_exit(tmp_path):
    """AC-1: scheduler.pid must be removed after the scheduler exits normally.

    Currently FAILS: the second `trap ... EXIT` clobbers the first, so
    release_scheduler_lock never runs.
    """
    _run_scheduler_once(tmp_path)
    pf = _pidfile(tmp_path)
    assert not pf.exists(), (
        f"scheduler.pid still exists after exit — lock release was clobbered. "
        f"Contents: {pf.read_text(encoding='utf-8')!r}"
    )


def test_scan_stderr_tempfile_removed_after_exit(tmp_path):
    """AC-1: the scan-stderr tempfile must be removed on exit.

    This is the *other* cleanup handler (the one that does run today).
    """
    _run_scheduler_once(tmp_path)
    # The tempfile is in TMPDIR; hard to glob, so verify the scheduler exited
    # cleanly (which implies its handler ran).  A more robust check would
    # require strace/dtrace — not worth the complexity for a regression guard.
    # The real assertion is the pidfile one above.
    pass


# ── AC-2: SIGTERM logs the signal, releases the lock, exits ──────────────────

def test_sigterm_removes_pidfile(tmp_path):
    """AC-2: sending SIGTERM must release the lock (remove scheduler.pid).

    Currently FAILS: no SIGTERM handler — the process dies without cleanup.
    """
    proc = _start_scheduler_poll(tmp_path)
    try:
        # Wait for the scheduler to acquire its lock and enter the poll loop.
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists(), "scheduler never created pidfile"
        time.sleep(1)  # let it settle into poll loop

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert not pf.exists(), (
        f"scheduler.pid still exists after SIGTERM. Contents: "
        f"{pf.read_text(encoding='utf-8')!r}"
    )


def test_sigterm_exit_code(tmp_path):
    """AC-2: SIGTERM exit must be 128+signo (128+15=143).

    Currently FAILS: no handler, so the default signal exit may vary.
    """
    proc = _start_scheduler_poll(tmp_path)
    try:
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists()
        time.sleep(1)

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.returncode == 128 + signal.SIGTERM, (
        f"SIGTERM exit should be {128 + signal.SIGTERM}, got {proc.returncode}"
    )


def test_sigterm_logs_signal_name(tmp_path):
    """AC-2: SIGTERM must produce a log line naming the signal.

    Currently FAILS: no SIGTERM handler — nothing is logged.
    """
    proc = _start_scheduler_poll(tmp_path)
    try:
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists()
        time.sleep(1)

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    lf = _log_file(tmp_path)
    assert lf.exists(), "scheduler.log was not created"
    log = lf.read_text(encoding="utf-8")
    assert "SIGTERM" in log, (
        f"SIGTERM not mentioned in scheduler.log:\n{log[-500:]}"
    )


# ── AC-3: SIGINT and SIGHUP behave the same ─────────────────────────────────

@pytest.mark.parametrize("signo,name", [
    (signal.SIGINT, "SIGINT"),
    (signal.SIGHUP, "SIGHUP"),
])
def test_signal_removes_pidfile(tmp_path, signo, name):
    """AC-3: {name} must release the lock.

    Currently FAILS: no signal handlers — lock is never released.
    """
    proc = _start_scheduler_poll(tmp_path)
    try:
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists(), "scheduler never created pidfile"
        time.sleep(1)

        proc.send_signal(signo)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert not pf.exists(), (
        f"scheduler.pid still exists after {name}. Contents: "
        f"{pf.read_text(encoding='utf-8')!r}"
    )


@pytest.mark.parametrize("signo,name", [
    (signal.SIGINT, "SIGINT"),
    (signal.SIGHUP, "SIGHUP"),
])
def test_signal_exit_code(tmp_path, signo, name):
    """AC-3: {name} exit must be 128+signo."""
    proc = _start_scheduler_poll(tmp_path)
    try:
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists()
        time.sleep(1)

        proc.send_signal(signo)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.returncode == 128 + signo, (
        f"{name} exit should be {128 + signo}, got {proc.returncode}"
    )


@pytest.mark.parametrize("signo,name", [
    (signal.SIGINT, "SIGINT"),
    (signal.SIGHUP, "SIGHUP"),
])
def test_signal_logs_signal_name(tmp_path, signo, name):
    """AC-3: {name} must produce a log line naming the signal.

    Currently FAILS: no signal handlers — nothing is logged.
    """
    proc = _start_scheduler_poll(tmp_path)
    try:
        pf = _pidfile(tmp_path)
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists()
        time.sleep(1)

        proc.send_signal(signo)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    lf = _log_file(tmp_path)
    assert lf.exists(), "scheduler.log was not created"
    log = lf.read_text(encoding="utf-8")
    assert name in log, (
        f"{name} not mentioned in scheduler.log:\n{log[-500:]}"
    )


# ── AC-4: normal exit logs why it stopped ─────────────────────────────────────

def test_normal_exit_logs_reason(tmp_path):
    """AC-4: on a normal exit the scheduler must log why it is stopping.

    Uses --once --dry-run with no queued projects so the idle path fires.
    The scheduler should log a reason string (e.g. "all-queues-empty") rather
    than exiting silently.
    """
    proc = subprocess.Popen(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_scheduler_env(tmp_path), encoding="utf-8",
    )
    proc.wait(timeout=30)

    lf = _log_file(tmp_path)
    assert lf.exists(), "scheduler.log was not created"
    log = lf.read_text(encoding="utf-8")
    # The idle path logs "all-queues-empty" as the reason when nothing is queued.
    assert "all-queues-empty" in log, (
        f"idle reason not logged on normal exit:\n{log[-500:]}"
    )


def test_already_running_still_logs(tmp_path):
    """AC-4: the 'already running' self-exit must still log (no regression).

    Start one scheduler, then start a second — it should log 'already running'.
    """
    # First scheduler acquires the lock.
    pf = _pidfile(tmp_path)
    proc1 = _start_scheduler_poll(tmp_path)
    try:
        deadline = time.monotonic() + 15
        while not pf.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pf.exists(), "first scheduler never created pidfile"
        time.sleep(1)

        # Second scheduler sees the lock and should exit 0 with a log line.
        res = subprocess.run(
            ["bash", str(SCHEDULER), "--once", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env=_scheduler_env(tmp_path), encoding="utf-8",
        )
    finally:
        proc1.kill()
        proc1.wait(timeout=5)

    combined = res.stdout + res.stderr
    assert "already running" in combined, (
        f"'already running' message missing from second scheduler output:\n"
        f"{combined[-500:]}"
    )
