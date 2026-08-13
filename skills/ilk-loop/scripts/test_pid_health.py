"""Tests for pid_health module — stale-running sentinel detection.

Run with: python3 test_pid_health.py
Stdlib only, no pytest dependency.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pid_health  # noqa: E402

_failures: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(label + (f": {detail}" if detail else ""))


# ── pid_alive ─────────────────────────────────────────────────────────────────

def test_pid_alive_current_process() -> None:
    """Current process PID should be alive."""
    print("test_pid_alive_current_process:")
    _check("current pid alive", pid_health.pid_alive(os.getpid()))


def test_pid_alive_nonexistent() -> None:
    """A very large PID that doesn't exist should not be alive."""
    print("test_pid_alive_nonexistent:")
    _check("99999999 not alive", not pid_health.pid_alive(99999999))


def test_pid_alive_zero() -> None:
    """PID 0 should not be alive (invalid)."""
    print("test_pid_alive_zero:")
    _check("pid 0 not alive", not pid_health.pid_alive(0))


def test_pid_alive_negative() -> None:
    """Negative PID should not be alive."""
    print("test_pid_alive_negative:")
    _check("pid -1 not alive", not pid_health.pid_alive(-1))


# ── pid_command_alive ─────────────────────────────────────────────────────────

def test_pid_command_alive_matching() -> None:
    """Current process should match its own command name."""
    print("test_pid_command_alive_matching:")
    # Python process should match 'python'
    _check("python matches", pid_health.pid_command_alive(os.getpid(), "python"))


def test_pid_command_alive_not_matching() -> None:
    """Current process should not match an unrelated command."""
    print("test_pid_command_alive_not_matching:")
    _check("nonexistent-cmd doesn't match", not pid_health.pid_command_alive(os.getpid(), "nonexistent-cmd-xyz"))


def test_pid_command_alive_dead_pid() -> None:
    """Dead PID should return False regardless of command."""
    print("test_pid_command_alive_dead_pid:")
    _check("dead pid returns False", not pid_health.pid_command_alive(99999999, "python"))


# ── validate_pid ──────────────────────────────────────────────────────────────

def test_validate_pid_alive() -> None:
    """Alive PID without command check."""
    print("test_validate_pid_alive:")
    ok, reason = pid_health.validate_pid(os.getpid())
    _check("alive ok", ok, reason)


def test_validate_pid_dead() -> None:
    """Dead PID should fail."""
    print("test_validate_pid_dead:")
    ok, reason = pid_health.validate_pid(99999999)
    _check("dead fails", not ok, reason)


def test_validate_pid_with_matching_command() -> None:
    """Alive PID with matching command."""
    print("test_validate_pid_with_matching_command:")
    ok, reason = pid_health.validate_pid(os.getpid(), "python")
    _check("matching command ok", ok, reason)


def test_validate_pid_with_wrong_command() -> None:
    """Alive PID with wrong command should fail."""
    print("test_validate_pid_with_wrong_command:")
    ok, reason = pid_health.validate_pid(os.getpid(), "nonexistent-cmd-xyz")
    _check("wrong command fails", not ok, reason)


# ── pid_cmdline / ilk_pid_alive ───────────────────────────────────────────────

@contextlib.contextmanager
def _stub_process(script_name: str):
    """Spawn a live python process whose argv contains *script_name*."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stub = Path(tmpdir) / script_name
        stub.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(stub)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            yield proc.pid
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def test_pid_cmdline_includes_argv() -> None:
    """pid_cmdline must expose argv[1..], not just the executable name."""
    print("test_pid_cmdline_includes_argv:")
    with _stub_process("some_marker_script.py") as pid:
        cmd = pid_health.pid_cmdline(pid)
        _check("cmdline is readable", bool(cmd), repr(cmd))
        _check("cmdline contains the script arg",
               bool(cmd) and "some_marker_script.py" in cmd, repr(cmd))
        # The distinction that matters: the base name alone cannot tell an
        # ilk runner from any other interpreter.
        base = pid_health._pid_command_name(pid)
        _check("base name alone would not distinguish",
               base is not None and "some_marker_script.py" not in base, repr(base))


def test_pid_cmdline_dead_pid() -> None:
    """A dead PID has no command line."""
    print("test_pid_cmdline_dead_pid:")
    _check("dead pid -> None", pid_health.pid_cmdline(99999999) is None)


def test_ilk_pid_alive_accepts_runner() -> None:
    """A live process whose argv names a runner counts as an ilk process."""
    print("test_ilk_pid_alive_accepts_runner:")
    with _stub_process("run_ilk_loop_stub.py") as pid:
        _check("run_ilk_loop_* is alive", pid_health.ilk_pid_alive(pid))


def test_ilk_pid_alive_rejects_foreign() -> None:
    """A live process that is not ilk must not read as alive.

    This is the recycled-PID case: os.getpid() here is the test runner,
    which is exactly the sort of unrelated process the OS hands a stale
    sentinel's PID number to.
    """
    print("test_ilk_pid_alive_rejects_foreign:")
    _check("bare liveness says alive", pid_health.pid_alive(os.getpid()))
    _check("ilk_pid_alive says not ours", not pid_health.ilk_pid_alive(os.getpid()))


def test_ilk_pid_alive_dead_pid() -> None:
    """A dead PID is not alive under either check."""
    print("test_ilk_pid_alive_dead_pid:")
    _check("dead pid -> False", not pid_health.ilk_pid_alive(99999999))


def test_ilk_pid_alive_matches_bash_patterns() -> None:
    """The pattern list must stay in sync with _ilk_pid.sh.

    Divergence between the two implementations is a status display that
    contradicts the scheduler about whether a project is busy.
    """
    print("test_ilk_pid_alive_matches_bash_patterns:")
    helper = Path(__file__).resolve().parent / "_ilk_pid.sh"
    text = helper.read_text(encoding="utf-8")
    for pat in pid_health.ILK_PROCESS_PATTERNS:
        _check(f"_ilk_pid.sh also matches {pat!r}", f"*{pat}*" in text)


# ── stale-running sentinel scenario ───────────────────────────────────────────

def test_stale_running_scenario() -> None:
    """Simulate: last-exit.json says running but PID is dead.

    Creates a temp dir with last-exit.json pointing at a dead PID,
    then validates the PID is not alive.
    """
    print("test_stale_running_scenario:")
    with tempfile.TemporaryDirectory() as tmpdir:
        rt = Path(tmpdir)
        sentinel = {"state": "running", "pid": 99999999, "iterations": 3}
        (rt / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")

        # Read it back
        loaded = json.loads((rt / "last-exit.json").read_text(encoding="utf-8"))
        _check("sentinel state is running", loaded["state"] == "running")
        _check("sentinel pid is 99999999", loaded["pid"] == 99999999)

        # PID should be dead
        pid = loaded["pid"]
        alive = pid_health.pid_alive(pid)
        _check("sentinel pid is dead", not alive)

        # This is the stale-running condition
        stale = loaded["state"] == "running" and not alive
        _check("stale-running detected", stale)


def test_healthy_running_scenario() -> None:
    """Simulate: last-exit.json says running and PID is alive."""
    print("test_healthy_running_scenario:")
    with tempfile.TemporaryDirectory() as tmpdir:
        rt = Path(tmpdir)
        sentinel = {"state": "running", "pid": os.getpid(), "iterations": 5}
        (rt / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")

        loaded = json.loads((rt / "last-exit.json").read_text(encoding="utf-8"))
        alive = pid_health.pid_alive(loaded["pid"])
        _check("current pid is alive", alive)
        stale = loaded["state"] == "running" and not alive
        _check("not stale-running", not stale)


# ── runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_pid_alive_current_process,
        test_pid_alive_nonexistent,
        test_pid_alive_zero,
        test_pid_alive_negative,
        test_pid_command_alive_matching,
        test_pid_command_alive_not_matching,
        test_pid_command_alive_dead_pid,
        test_validate_pid_alive,
        test_validate_pid_dead,
        test_validate_pid_with_matching_command,
        test_validate_pid_with_wrong_command,
        test_pid_cmdline_includes_argv,
        test_pid_cmdline_dead_pid,
        test_ilk_pid_alive_accepts_runner,
        test_ilk_pid_alive_rejects_foreign,
        test_ilk_pid_alive_dead_pid,
        test_ilk_pid_alive_matches_bash_patterns,
        test_stale_running_scenario,
        test_healthy_running_scenario,
    ]
    for t in tests:
        t()
        print()

    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
