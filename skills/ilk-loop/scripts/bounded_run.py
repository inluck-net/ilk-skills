"""Bounded subprocess execution with process-group cleanup on timeout.

``run()`` spawns a child in its own process group (POSIX) so that on
timeout the entire tree — not just the direct child — is killed.

Judgment call (MASTER-2026-10-01b): SIGTERM, then SIGKILL after 5 s,
to the process group.  This gives a test runner a chance to flush its
output.  The SIGKILL bounds that.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Optional, Tuple, Union

# Seconds to wait after SIGTERM before escalating to SIGKILL.
_SIGTERM_GRACE_S = 5


def run(
    argv_or_cmd: Union[str, list],
    *,
    cwd: str,
    timeout: int,
    shell: bool = False,
    env: Optional[dict] = None,
) -> Tuple[Optional[int], str, str, bool]:
    """Run *argv_or_cmd* with a wall-clock *timeout* (seconds).

    Returns ``(returncode, stdout, stderr, timed_out)``.

    *argv_or_cmd* may be:
    - a **string** with ``shell=True`` — run via the system shell;
    - a **list** with ``shell=False`` — run directly (the list is
      ``[executable, arg1, …]``).

    On POSIX, the child runs in its own process group so that a timeout
    kills the whole tree.  On Windows (``os.name == "nt"``), behaviour
    falls back to ``subprocess.run`` — Windows has no POSIX process
    groups in this form.

    Encoding is ``utf-8`` with ``errors="replace"``, matching the
    callers in ``run_local_checks``, ``verification_record``, and
    ``red_owner``.
    """
    if os.name == "nt":
        return _run_nt(argv_or_cmd, cwd=cwd, timeout=timeout,
                       shell=shell, env=env)
    return _run_posix(argv_or_cmd, cwd=cwd, timeout=timeout,
                      shell=shell, env=env)


# ── POSIX ────────────────────────────────────────────────────────────────────

def _run_posix(
    argv_or_cmd: Union[str, list],
    *,
    cwd: str,
    timeout: int,
    shell: bool,
    env: Optional[dict],
) -> Tuple[Optional[int], str, str, bool]:
    """POSIX implementation: new process group + SIGTERM/SIGKILL on timeout."""
    kwargs: dict = dict(
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,  # own process group
    )
    if shell:
        kwargs["shell"] = True
    else:
        kwargs["shell"] = False

    proc = subprocess.Popen(argv_or_cmd, **kwargs)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        # SIGTERM the whole process group.
        pgid = os.getpgid(proc.pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
        # Give it a grace period to flush.
        try:
            stdout, stderr = proc.communicate(timeout=_SIGTERM_GRACE_S)
        except subprocess.TimeoutExpired:
            # Escalate to SIGKILL.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            stdout, stderr = proc.communicate()
        return None, stdout, stderr, True


# ── Windows ──────────────────────────────────────────────────────────────────

def _run_nt(
    argv_or_cmd: Union[str, list],
    *,
    cwd: str,
    timeout: int,
    shell: bool,
    env: Optional[dict],
) -> Tuple[Optional[int], str, str, bool]:
    """Windows fallback: plain ``subprocess.run`` (no process groups)."""
    try:
        cp = subprocess.run(
            argv_or_cmd, cwd=cwd, timeout=timeout, shell=shell, env=env,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        return cp.returncode, cp.stdout, cp.stderr, False
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return None, stdout, stderr, True