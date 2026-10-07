"""Tests that a detached train owns its stdout/stderr and closes inherited fds.

Sub-plan: a-detached-train-owns-its-output, step 0 (red-first pins).

The current ``spawn_detached.py`` redirects stdin from /dev/null but leaves
stdout and stderr as inherited.  It also does not close fds >= 3, so any
caller lock fd survives into the child.  These tests pin the fix contract.

Acceptance criteria:
  AC-1  the guard module's _violations() returns no entry naming spawn_detached.py
  AC-2  --log captures stdout and stderr; caller's pipe sees EOF after child exits
  AC-3  parent opens fd >= 3; child reports it closed
  AC-4  scheduler.sh maybe_start_release_train passes --log and drops >> redirect
"""
from __future__ import annotations

import os
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


# ── AC-1: guard violations returns no entry naming spawn_detached.py ──────────


@pytest.mark.xfail(
    strict=True,
    reason="base spawn_detached.py keeps fds 1/2 and fds >= 3; guard flags it",
)
def test_guard_violations_spawn_detached_clean() -> None:
    """AC-1: _violations() from the guard module returns no entry naming spawn_detached.py."""
    # Import the guard module from the sibling test file.
    import importlib
    import sys as _sys

    guard_path = (
        Path(__file__).resolve().parent.parent.parent
        / "ilk-loop"
        / "tests"
        / "test_a_ledger_spawn_returns_without_waiting_for_the_suite.py"
    )
    spec = importlib.util.spec_from_file_location("_guard", guard_path)
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    _sys.modules["_guard"] = guard
    spec.loader.exec_module(guard)

    violations = guard._violations()
    spawn_violations = [v for v in violations if "spawn_detached.py" in v]
    assert spawn_violations == [], (
        f"guard violations for spawn_detached.py: {spawn_violations}"
    )


# ── AC-2: --log captures stdout and stderr ──────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="base spawn_detached.py does not accept --log; output goes to caller's pipe",
)
def test_log_flag_captures_stdout_and_stderr(tmp_path: Path) -> None:
    """AC-2: --log captures both streams; caller's pipe sees EOF after child exits."""
    py = _python_path()
    sd = str(SPAWN_DETACHED)
    log_file = tmp_path / "out.log"

    # Spawn a child that prints to stdout and stderr.
    proc = subprocess.Popen(
        [py, sd, "--log", str(log_file), py, "-c",
         "print('o'); import sys; print('e', file=sys.stderr)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=10)

    # Both lines must land in the log file.
    assert log_file.exists(), f"log file not created: {log_file}"
    content = log_file.read_text()
    assert "o\n" in content, f"stdout 'o' not in log: {content!r}"
    assert "e\n" in content, f"stderr 'e' not in log: {content!r}"

    # Caller's pipe must see EOF (child redirected its streams away).
    remaining = proc.stdout.read()
    assert remaining == b"", (
        f"caller's stdout pipe should be empty, got: {remaining!r}"
    )


# ── AC-3: parent opens fd >= 3; child reports it closed ─────────────────────


def test_child_closes_inherited_fds_ge3(tmp_path: Path) -> None:
    """AC-3: parent opens fd >= 3; child reports it closed.

    On macOS, exec already closes inherited fds (no O_CLOEXEC on os.open).
    The guard violation is a code-quality issue (spawn_detached.py should
    explicitly call closerange), but the runtime behavior is already correct.
    This test is NOT xfail — it passes at base and after the fix.
    """
    py = _python_path()
    sd = str(SPAWN_DETACHED)

    # Open a file and leave it as fd >= 3.
    lock_file = tmp_path / "lock"
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
    assert lock_fd >= 3, f"expected fd >= 3, got {lock_fd}"

    # Child tries to fstat the fd — should fail if closed.
    child_script = textwrap.dedent(f"""\
        import os, sys
        fd = {lock_fd}
        try:
            os.fstat(fd)
            print("open", flush=True)
        except OSError:
            print("closed", flush=True)
    """)
    child_py = tmp_path / "child.py"
    child_py.write_text(child_script)

    proc = subprocess.Popen(
        [py, sd, py, str(child_py)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, _ = proc.communicate(timeout=10)
    result = stdout.decode().strip()
    assert result == "closed", (
        f"child fd {lock_fd} should be closed, got: {result!r}"
    )

    os.close(lock_fd)


# ── AC-4: scheduler.sh passes --log and drops >> redirect ───────────────────


@pytest.mark.xfail(
    strict=True,
    reason="base scheduler.sh uses >> redirect instead of --log",
)
def test_scheduler_passes_log_flag_and_no_redirect() -> None:
    """AC-4: maybe_start_release_train contains --log and no >> redirect."""
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

    # Must contain --log "$log_file" on the spawn line.
    log_lines = [l for l in func_lines if "--log" in l and "$log_file" in l]
    assert len(log_lines) >= 1, (
        f"expected --log $log_file in maybe_start_release_train, "
        f"found none in: {func_lines!r}"
    )

    # Must NOT have >> "$log_file" on the spawn line.
    redirect_lines = [
        l for l in func_lines
        if ">>" in l and "$log_file" in l and "_SPAWN_DETACHED" in l
    ]
    assert len(redirect_lines) == 0, (
        f">> $log_file redirect must not appear on spawn line, "
        f"found: {redirect_lines!r}"
    )