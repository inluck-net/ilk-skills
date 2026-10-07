"""Tests that a scheduler-started release train runs in its own session.

Sub-plan: a-train-runs-in-its-own-session, step 0 (red-first pins).

The first scheduler-started release train (v0.9.162 → v0.9.163) killed itself
because it shared the scheduler's launchd process group.  When the train's own
local bounce ran ``launchctl bootout`` on the scheduler job, launchd killed
every process in that group — including the train.

These tests pin the contract that the train is launched through
``spawn_detached.py`` (which calls ``os.setsid()`` + ``os.execvp()``), putting
it in its own session and process group so it survives the bounce.

Acceptance criteria:
  AC-1  detached child is in its own session and group, same pid after exec
  AC-2  detached child survives a SIGTERM to the parent's process group;
        a plain nohup child in the same group does not (positive experiment)
  AC-3  spawn_detached.py with no arguments exits 2
  AC-4  scheduler.sh's maybe_start_release_train uses spawn_detached.py,
        not nohup
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Paths ────────────────────────────────────────────────────────────────────

WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPAWN_DETACHED = WATCHDOG_SCRIPTS / "spawn_detached.py"
SCHEDULER_SH = WATCHDOG_SCRIPTS / "scheduler.sh"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _python_path() -> str:
    """Return the real interpreter path (not 'python3' — spawn_detached execs)."""
    return sys.executable


def _spawn_detached_path() -> str:
    return str(SPAWN_DETACHED)


# ── AC-1: detached child is in its own session and group, same pid ──────────


def test_detached_child_own_session_and_group(tmp_path: Path) -> None:
    """AC-1: spawn_detached.py puts the child in its own session and group.

    The child's pid reported by ``$!`` must equal the exec'd process's pid,
    and its session and process group must differ from the parent's.
    """
    py = _python_path()
    sd = _spawn_detached_path()
    # Parent spawns a detached child, echoes the pid, then exits immediately.
    # No ``wait`` — the child outlives the parent.
    # Use --log so the child's pid print is captured (spawn_detached redirects
    # stdout to /dev/null without --log).
    log_file = tmp_path / "child.log"
    # Child prints its pid and stays alive (sleep 30) so we can check its sid.
    parent_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        "{py}" "{sd}" --log "{log_file}" "{py}" -c "import os, sys, time; print(os.getpid(), flush=True); time.sleep(30)" &
        echo $!
    """)
    script_path = tmp_path / "parent.sh"
    script_path.write_text(parent_script)
    script_path.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        # Read the echo_pid from the parent's stdout.  The child's pid print
        # goes to the log file (spawn_detached redirects stdout).
        echo_line = proc.stdout.readline()
        proc.wait(timeout=5)
        echo_pid = int(echo_line.strip())

        # Read child_pid from the log file.
        import time
        for _ in range(20):
            if log_file.exists() and log_file.read_text().strip():
                break
            time.sleep(0.25)
        assert log_file.exists(), f"log file not created: {log_file}"
        child_pid = int(log_file.read_text().strip())
        assert child_pid == echo_pid, (
            f"exec must keep the pid: $!={echo_pid} vs child print={child_pid}"
        )

        # The child must be in its own session and process group.
        child_sid = os.getsid(child_pid)
        child_pgid = os.getpgid(child_pid)
        # The parent already exited, so use the child's own sid/pgid as
        # reference for "parent session" — the parent was in a different
        # session (start_new_session=True) and the child must NOT be in it.
        # We verify by checking the child is a session leader (sid == pid)
        # and a process-group leader (pgid == pid).
        assert child_sid == child_pid, (
            f"child must be session leader: sid={child_sid}, pid={child_pid}"
        )
        assert child_pgid == child_pid, (
            f"child must be process-group leader: pgid={child_pgid}, pid={child_pid}"
        )
    finally:
        try:
            os.kill(child_pid, signal.SIGTERM)
        except Exception:
            pass
        proc.kill()
        proc.wait(timeout=5)


# ── AC-2: detached child survives the job's group kill ──────────────────────


def test_detached_survives_group_kill(tmp_path: Path) -> None:
    """AC-2: a detached child survives SIGTERM to the parent's process group.

    A plain ``nohup sleep`` child started in the same bash command must die
    (proving the kill reached the group); the detached child must survive.
    """
    py = _python_path()
    sd = _spawn_detached_path()
    # Parent stays alive (sleep 60) so we can kill its process group.
    parent_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Detached child (through spawn_detached.py)
        "{py}" "{sd}" sleep 30 &
        DETACHED_PID=$!
        # Control child (plain nohup, same process group)
        nohup sleep 30 &
        NOHUP_PID=$!
        echo "$DETACHED_PID $NOHUP_PID"
        sleep 60
    """)
    script_path = tmp_path / "parent.sh"
    script_path.write_text(parent_script)
    script_path.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    detached_pid = None
    nohup_pid = None
    try:
        # Read PIDs line by line — don't use communicate() (parent sleeps 60).
        line = proc.stdout.readline().decode().strip()
        parts = line.split()
        assert len(parts) == 2, f"expected 2 pids, got: {line!r}"
        detached_pid = int(parts[0])
        nohup_pid = int(parts[1])

        import time

        # Wait a moment for children to start.
        time.sleep(0.5)

        # Kill the parent's entire process group.
        parent_pgid = os.getpgid(proc.pid)
        os.killpg(parent_pgid, signal.SIGTERM)

        # Give the kernel a moment to propagate.
        time.sleep(1)

        # Control: nohup child must be dead (it was in the same group).
        nohup_alive = True
        try:
            os.kill(nohup_pid, 0)
        except ProcessLookupError:
            nohup_alive = False
        assert not nohup_alive, (
            f"nohup control child {nohup_pid} must be dead after group kill"
        )

        # Positive experiment: detached child must be alive.
        detached_alive = True
        try:
            os.kill(detached_pid, 0)
        except ProcessLookupError:
            detached_alive = False
        assert detached_alive, (
            f"detached child {detached_pid} must survive the group kill"
        )
    finally:
        if detached_pid is not None:
            try:
                os.kill(detached_pid, signal.SIGTERM)
            except Exception:
                pass
        if nohup_pid is not None:
            try:
                os.kill(nohup_pid, signal.SIGTERM)
            except Exception:
                pass
        proc.kill()
        proc.wait(timeout=5)


# ── AC-3: spawn_detached.py with no arguments exits 2 ───────────────────────


def test_spawn_detached_no_args_exits_2() -> None:
    """AC-3: spawn_detached.py with no arguments prints usage and exits 2."""
    result = subprocess.run(
        [_python_path(), _spawn_detached_path()],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}; "
        f"stderr={result.stderr.decode()!r}"
    )
    stderr_text = result.stderr.decode()
    # Must contain a usage message — not a Python traceback or FileNotFoundError.
    assert "Usage" in stderr_text or "usage" in stderr_text, (
        f"stderr must contain a usage message, got: {stderr_text!r}"
    )


# ── AC-4: scheduler.sh uses spawn_detached.py, not nohup ────────────────────


def test_scheduler_uses_spawn_detached_not_nohup() -> None:
    """AC-4: maybe_start_release_train launches through spawn_detached.py.

    The function body must have exactly one line containing both
    ``$_RELEASE_TRAIN_SCRIPT" run`` and ``$_SPAWN_DETACHED"``, and no line
    starting with ``nohup``.  ``_SPAWN_DETACHED`` must be defined and end in
    ``spawn_detached.py"``.
    """
    content = SCHEDULER_SH.read_text(encoding="utf-8")

    # Extract maybe_start_release_train function body.
    in_func = False
    brace_depth = 0
    func_lines: list[str] = []
    for line in content.splitlines():
        if "maybe_start_release_train()" in line and "{" in line:
            in_func = True
            brace_depth = 1
            continue
        if in_func:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                break
            func_lines.append(line)

    body = "\n".join(func_lines)

    # Exactly one line with both markers.
    matching = [
        l for l in func_lines
        if '$_RELEASE_TRAIN_SCRIPT" run' in l and '$_SPAWN_DETACHED"' in l
    ]
    assert len(matching) == 1, (
        f"expected 1 line with both $_RELEASE_TRAIN_SCRIPT and $_SPAWN_DETACHED, "
        f"got {len(matching)}: {matching!r}"
    )

    # No nohup in the function body.
    nohup_lines = [
        l for l in func_lines if l.lstrip().startswith("nohup")
    ]
    assert len(nohup_lines) == 0, (
        f"nohup must not appear in maybe_start_release_train, "
        f"found: {nohup_lines!r}"
    )

    # _SPAWN_DETACHED is defined somewhere in the file and ends in spawn_detached.py".
    spawn_def_lines = [
        l for l in content.splitlines()
        if "_SPAWN_DETACHED=" in l
    ]
    assert len(spawn_def_lines) >= 1, (
        "_SPAWN_DETACHED= not found in scheduler.sh"
    )
    assert any('spawn_detached.py"' in l for l in spawn_def_lines), (
        f"_SPAWN_DETACHED must end in spawn_detached.py, "
        f"got: {spawn_def_lines!r}"
    )