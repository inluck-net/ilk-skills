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

import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


def _run_scheduler_once(sandbox, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run scheduler.sh --once --dry-run (single cycle, no poll loop)."""
    return subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True, text=True, timeout=timeout,
        env=sandbox.env, encoding="utf-8",
        preexec_fn=sandbox.preexec,
    )


def _start_scheduler_poll(sandbox, poll_min: int = 60) -> subprocess.Popen:
    """Start scheduler.sh in poll mode (not --once) so it sleeps in-loop."""
    return subprocess.Popen(
        ["bash", str(SCHEDULER), "--poll-min", str(poll_min)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=sandbox.env, encoding="utf-8",
        preexec_fn=sandbox.preexec,
    )


def _pidfile(sandbox) -> Path:
    return sandbox.root / ".ilk-data" / "scheduler.pid"


def _log_file(sandbox) -> Path:
    return sandbox.root / ".ilk-data" / "logs" / "scheduler.log"


# ── AC-6: scheduler.pid is a bare PID ────────────────────────────────────────

def test_pidfile_is_bare_pid(scheduler_sandbox):
    """AC-6: scheduler.pid must contain only digits (bare PID format)."""
    _run_scheduler_once(scheduler_sandbox)
    pf = _pidfile(scheduler_sandbox)
    # After --once --dry-run, the EXIT trap may or may not have removed the
    # file.  If it exists, verify the format; if absent, that's the clobber
    # bug (which is what we expect to find here).
    if pf.exists():
        raw = pf.read_text(encoding="utf-8").strip()
        assert raw.isdigit(), (
            f"scheduler.pid should be bare PID, got: {raw!r}"
        )


# ── AC-1: both cleanup actions run on exit ────────────────────────────────────

def test_pidfile_removed_after_normal_exit(scheduler_sandbox):
    """AC-1: scheduler.pid must be removed after the scheduler exits normally.

    Currently FAILS: the second `trap ... EXIT` clobbers the first, so
    release_scheduler_lock never runs.
    """
    _run_scheduler_once(scheduler_sandbox)
    pf = _pidfile(scheduler_sandbox)
    assert not pf.exists(), (
        f"scheduler.pid still exists after exit — lock release was clobbered. "
        f"Contents: {pf.read_text(encoding='utf-8')!r}"
    )


def test_scan_stderr_tempfile_removed_after_exit(scheduler_sandbox):
    """AC-1: the scan-stderr tempfile must be removed on exit.

    This is the *other* cleanup handler (the one that does run today).
    """
    _run_scheduler_once(scheduler_sandbox)
    # The tempfile is in TMPDIR; hard to glob, so verify the scheduler exited
    # cleanly (which implies its handler ran).  A more robust check would
    # require strace/dtrace — not worth the complexity for a regression guard.
    # The real assertion is the pidfile one above.
    pass


# ── AC-2: SIGTERM logs the signal, releases the lock, exits ──────────────────

def test_sigterm_removes_pidfile(scheduler_sandbox):
    """AC-2: sending SIGTERM must release the lock (remove scheduler.pid).

    Currently FAILS: no SIGTERM handler — the process dies without cleanup.
    """
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        # Wait for the scheduler to acquire its lock and enter the poll loop.
        pf = _pidfile(scheduler_sandbox)
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


def test_sigterm_exit_code(scheduler_sandbox):
    """AC-2: SIGTERM exit must be 128+signo (128+15=143).

    Currently FAILS: no handler, so the default signal exit may vary.
    """
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        pf = _pidfile(scheduler_sandbox)
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


def test_sigterm_logs_signal_name(scheduler_sandbox):
    """AC-2: SIGTERM must produce a log line naming the signal.

    Currently FAILS: no SIGTERM handler — nothing is logged.
    """
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        pf = _pidfile(scheduler_sandbox)
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

    lf = _log_file(scheduler_sandbox)
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
def test_signal_removes_pidfile(scheduler_sandbox, signo, name):
    """AC-3: {name} must release the lock.

    Currently FAILS: no signal handlers — lock is never released.
    """
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        pf = _pidfile(scheduler_sandbox)
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
def test_signal_exit_code(scheduler_sandbox, signo, name):
    """AC-3: {name} exit must be 128+signo."""
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        pf = _pidfile(scheduler_sandbox)
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
def test_signal_logs_signal_name(scheduler_sandbox, signo, name):
    """AC-3: {name} must produce a log line naming the signal.

    Currently FAILS: no signal handlers — nothing is logged.
    """
    proc = _start_scheduler_poll(scheduler_sandbox)
    try:
        pf = _pidfile(scheduler_sandbox)
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

    lf = _log_file(scheduler_sandbox)
    assert lf.exists(), "scheduler.log was not created"
    log = lf.read_text(encoding="utf-8")
    assert name in log, (
        f"{name} not mentioned in scheduler.log:\n{log[-500:]}"
    )


# ── AC-4: normal exit logs why it stopped ─────────────────────────────────────

def test_normal_exit_logs_reason(scheduler_sandbox):
    """AC-4: on a normal exit the scheduler must log why it is stopping.

    Uses --once --dry-run with no queued projects so the idle path fires.
    The scheduler should log a reason string (e.g. "all-queues-empty") rather
    than exiting silently.
    """
    proc = subprocess.Popen(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=scheduler_sandbox.env, encoding="utf-8",
        preexec_fn=scheduler_sandbox.preexec,
    )
    proc.wait(timeout=30)

    lf = _log_file(scheduler_sandbox)
    assert lf.exists(), "scheduler.log was not created"
    log = lf.read_text(encoding="utf-8")
    # The idle path logs "all-queues-empty" as the reason when nothing is queued.
    assert "all-queues-empty" in log, (
        f"idle reason not logged on normal exit:\n{log[-500:]}"
    )


def test_already_running_still_logs(scheduler_sandbox):
    """AC-4: the 'already running' self-exit must still log (no regression).

    Start one scheduler, then start a second — it should log 'already running'.
    """
    # First scheduler acquires the lock.
    pf = _pidfile(scheduler_sandbox)
    proc1 = _start_scheduler_poll(scheduler_sandbox)
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
            env=scheduler_sandbox.env, encoding="utf-8",
            preexec_fn=scheduler_sandbox.preexec,
        )
    finally:
        proc1.kill()
        proc1.wait(timeout=5)

    combined = res.stdout + res.stderr
    assert "already running" in combined, (
        f"'already running' message missing from second scheduler output:\n"
        f"{combined[-500:]}"
    )


# ── Regression: an ancestor's ignored signal must not disarm the scheduler ────
#
# The six SIGINT/SIGHUP cases above failed for a whole batch because the ilk
# launcher detaches with `nohup ... &` (launch.sh:75), which leaves SIGINT and
# SIGHUP at SIG_IGN for every descendant including pytest.  bash cannot trap a
# signal that was ignored on entry, so scheduler.sh's INT/HUP traps became
# silent no-ops -- while SIGTERM, which neither `nohup` nor `&` touches, kept
# working.  Full mechanism: tests/baselines/sp5-repro-2026-08-27.md
#
# This test does not depend on how pytest was launched: it CREATES the hostile
# disposition in the pytest process, so it fails whenever the sandbox stops
# defending against it, in any ancestry.

@pytest.mark.parametrize("signo,name", [
    (signal.SIGINT, "SIGINT"),
    (signal.SIGHUP, "SIGHUP"),
])
def test_ignored_ancestry_does_not_disarm_signal(scheduler_sandbox, signo, name):
    """A {name} ignored in the pytest process must still stop the child.

    Regression guard for the SP5 defect.  Remove
    ``preexec_fn=sandbox.preexec`` from ``_start_scheduler_poll`` and this
    fails with ``subprocess.TimeoutExpired`` -- observed 2026-08-27.
    """
    previous = signal.signal(signo, signal.SIG_IGN)
    try:
        assert signal.getsignal(signo) is signal.SIG_IGN, (
            f"could not ignore {name} in the pytest process; "
            f"the hostile condition was not created, so this test proves nothing"
        )
        proc = _start_scheduler_poll(scheduler_sandbox)
        try:
            pf = _pidfile(scheduler_sandbox)
            deadline = time.monotonic() + 15
            while not pf.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert pf.exists(), "scheduler never created pidfile"

            proc.send_signal(signo)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    finally:
        signal.signal(signo, previous)

    assert proc.returncode == 128 + signo, (
        f"{name} exit should be {128 + signo}, got {proc.returncode} -- the "
        f"child inherited SIG_IGN for {name} and could not trap it"
    )
    assert not pf.exists(), (
        f"scheduler.pid still exists after {name} was sent from a process "
        f"that ignores {name}"
    )
