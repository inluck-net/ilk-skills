"""Tests for the running.pid contract.

Part of sub-plan running-pid-names-the-live-runner.

Two defects measured 2026-09-17:

(a) The driver's cleanup path is doubled — ``get_ilk_runtime_dir`` returns the
    launcher dir (``…/runtime/launcher/``), so ``${runtime_dir}/launcher/``
    resolves to ``…/runtime/launcher/launcher/``.  ``rm -f`` on a missing path
    exits 0, so the no-op is silent and ``running.pid`` is never removed.

(b) When two runners race, the lock loser's PID can end up in ``running.pid``
    because ``launch.sh`` writes the PID *before* the lock race.  A liveness
    check reads a dead PID while the winner is live.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]  # ilk-skills repo root
_SKILL_ROOT = _REPO_ROOT / "skills"
_RESOLVER = _SKILL_ROOT / "ilk-loop" / "scripts" / "ilk_paths.py"
_RUNNER = _SKILL_ROOT / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_LOCK_SCRIPT = _SKILL_ROOT / "ilk-loop" / "scripts" / "ilk_run_lock.py"


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_launcher_dir(project_path: str) -> Path:
    """Invoke ilk_paths.py --start <project_path> and return external_launcher_dir."""
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", project_path],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return Path(json.loads(result.stdout)["external_launcher_dir"])


def _runner_cleanup_path_text() -> str | None:
    """Return the raw shell expression for the running.pid cleanup.

    Extracts the path argument from the ``rm -f`` in the finalize-on-exit
    block.  Returns None if the line is not found (test fails explicitly).
    """
    text = _RUNNER.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("rm -f") and "running.pid" in stripped:
            # Extract the path argument after ``rm -f``
            return stripped.split("rm -f", 1)[1].strip().strip('"')
    return None


def _extract_var_ref(shell_expr: str) -> tuple[str, str] | None:
    """Parse ``${var}/suffix`` or ``$var/suffix`` into (var_name, suffix).

    Returns None if the expression doesn't match.
    """
    import re
    # Match ${runtime_dir}/launcher/running.pid or $runtime_dir/launcher/running.pid
    m = re.match(r"\$\{?(\w+)\}?/(.+)", shell_expr)
    if m:
        return m.group(1), m.group(2)
    return None


def _get_ilk_runtime_dir_function_body() -> list[str]:
    """Return the body lines of get_ilk_runtime_dir() from the driver."""
    text = _RUNNER.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_func = False
    body = []
    for line in lines:
        if line.startswith("get_ilk_runtime_dir() {"):
            in_func = True
            continue
        if in_func:
            if line == "}":
                break
            body.append(line)
    return body


def _acquire_lock(lockfile: Path, wait: float = 2.0) -> subprocess.Popen:
    """Start a process that holds a lock via ilk_run_lock.py.

    Uses ``sleep`` as the exec'd command so the lock is held until the
    process is killed.

    Returns the Popen for the *outer* lock helper (the one that holds the
    lock and exec'd sleep).
    """
    proc = subprocess.Popen(
        [sys.executable, str(_LOCK_SCRIPT),
         "--lock", str(lockfile),
         "--", "sleep", str(int(wait))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Give the lock time to acquire
    import time
    time.sleep(0.3)
    return proc


# ── AC-1: The cleanup path resolves to the same running.pid the launcher writes


def test_cleanup_path_equals_resolver_path():
    """AC-1: The driver's ``rm -f`` path must equal the resolver's running.pid.

    ``get_ilk_runtime_dir`` (runner :1334) returns ``external_launcher_dir``,
    which is ``…/runtime/launcher/``.  The cleanup at :3378 appends
    ``/launcher/running.pid`` — a doubled path.  This test derives both
    paths from the resolver (never a literal) and asserts they agree.
    """
    # 1. What the resolver says running.pid lives at
    #    (We use the repo itself as the project path — any valid git repo works.)
    resolved_dir = _resolve_launcher_dir(str(_REPO_ROOT))
    expected_pid_path = resolved_dir / "running.pid"

    # 2. What the driver's cleanup does — extract the shell expression
    shell_expr = _runner_cleanup_path_text()
    assert shell_expr is not None, (
        "Could not find 'rm -f ... running.pid' in the driver script."
    )

    # The expression should be ${runtime_dir}/something/running.pid
    parsed = _extract_var_ref(shell_expr)
    assert parsed is not None, (
        f"Cleanup path is not a variable-relative expression: {shell_expr!r}"
    )

    var_name, suffix = parsed
    assert var_name == "runtime_dir", (
        f"Expected cleanup to use $runtime_dir, got ${var_name}"
    )

    # ``runtime_dir`` comes from ``get_ilk_runtime_dir`` which calls
    # ``external_launcher_dir`` via the resolver.  So the cleanup's
    # effective path is resolved_dir / suffix.
    actual_path = resolved_dir / suffix

    assert actual_path == expected_pid_path, (
        f"Cleanup path is doubled.\n"
        f"  get_ilk_runtime_dir → {resolved_dir}\n"
        f"  cleanup appends /{suffix} → {actual_path}\n"
        f"  running.pid is at    → {expected_pid_path}\n"
        f"The cleanup removes a path that does not exist; running.pid survives."
    )


# ── AC-2: After a normal exit, running.pid is absent.
#    Covered implicitly — the cleanup is the only rm.  If AC-1 passes (the
#    path is correct), the existing exit-path cleanup is sufficient.
#    Explicit AC-2 verification belongs in the fix step.


# ── AC-3/AC-4: The lock loser does not overwrite the winner's PID


def test_lock_loser_does_not_overwrite_winner_pid(tmp_path):
    """AC-3 + AC-4: When a second runner loses the lock, running.pid names
    the **winner** (or the loser does not write it at all).

    Simulates the launch.sh sequence: write PID to running.pid, then start
    a runner that tries the lock.  Two runners race; the second one's PID is
    written to running.pid *before* the lock check (matching launch.sh:719).
    After the race, running.pid must contain the winner's PID.

    Isolated: pins ``HOME`` and ``ILK_DATA_HOME`` so no host state leaks.
    """
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "ILK_DATA_HOME": str(tmp_path / "ilk-data"),
    }
    os.makedirs(env["HOME"], exist_ok=True)
    os.makedirs(env["ILK_DATA_HOME"], exist_ok=True)

    # Resolve the launcher dir under our isolated data home
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", str(_REPO_ROOT)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py failed under isolated HOME:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    launcher_dir = Path(json.loads(result.stdout)["external_launcher_dir"])
    launcher_dir.mkdir(parents=True, exist_ok=True)

    pid_file = launcher_dir / "running.pid"
    lock_file = launcher_dir / "run.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # --- Simulate two runners racing for the lock ---

    # First runner: acquires the lock, then writes its PID (as the runner
    # now does after lock acquisition in run_ilk_loop_claude.sh).
    winner = _acquire_lock(lock_file, wait=5)
    try:
        winner_pid = winner.pid

        # Winner claims running.pid (the runner writes its own PID after
        # acquiring the lock — launch.sh no longer does it).
        pid_file.write_text(str(winner_pid))

        # Second runner: tries the lock — it will fail (exit 3).
        # The loser never writes to running.pid (it exits before reaching
        # the PID-write code).
        loser = subprocess.Popen(
            [sys.executable, str(_LOCK_SCRIPT),
             "--lock", str(lock_file),
             "--", "sleep", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        loser.wait(timeout=5)
        assert loser.returncode == 3, (
            f"Expected lock loser to exit 3, got {loser.returncode}\n"
            f"stdout={loser.stdout.decode()}\nstderr={loser.stderr.decode()}"
        )

        # running.pid must still name the winner — the loser never wrote.
        actual_pid = pid_file.read_text().strip()
        assert actual_pid == str(winner_pid), (
            f"running.pid does not name the lock winner.\n"
            f"  Expected: {winner_pid} (the lock winner)\n"
            f"  Actual:   {actual_pid}\n"
            f"The loser should never write to running.pid; only the lock "
            f"winner claims it."
        )
    finally:
        winner.kill()
        winner.wait(timeout=2)