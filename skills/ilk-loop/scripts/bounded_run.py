"""Bounded subprocess execution with process-group cleanup on timeout.

``run()`` spawns a child in its own process group (POSIX) so that on
timeout the entire tree — not just the direct child — is killed.

Judgment call (MASTER-2026-10-01b): SIGTERM, then SIGKILL after 5 s,
to the process group.  This gives a test runner a chance to flush its
output.  The SIGKILL bounds that.

Stall detection (R2e): samples the process group's CPU time every
``STALL_SAMPLE_S`` seconds.  When the sliding window of
``STALL_WINDOW_S / STALL_SAMPLE_S`` samples shows < ``STALL_CPU_S``
gain, the gate is flagged once via stderr and an audit event.  A
``None`` sample (ps failed) is skipped, not counted as idle.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections import deque
from typing import Optional, Tuple, Union

from ilk_audit import write_event

# Seconds to wait after SIGTERM before escalating to SIGKILL.
_SIGTERM_GRACE_S = 5

# ── Stall-detection constants (R2e) ──────────────────────────────────────────
# Basis: the 10:03 recorder used 3.8 s of CPU in 26 min (≈0.15 s per 10 min);
# a pytest run uses tens of CPU-seconds per minute.  Wrong if a real gate
# legitimately idles > 10 min (then it is flagged, harmlessly).
STALL_WINDOW_S = 600       # sliding window length
STALL_CPU_S = 1.0          # minimum CPU gain across the window
STALL_SAMPLE_S = 30        # sampling interval


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
    """POSIX implementation: new process group + SIGTERM/SIGKILL on timeout.

    Also samples the process group's CPU time every ``STALL_SAMPLE_S``
    seconds and flags a stall once when the sliding window shows no
    meaningful CPU gain.
    """
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

    # Resolve the command string for stall messages.
    cmd_str = argv_or_cmd if isinstance(argv_or_cmd, str) else " ".join(argv_or_cmd)

    try:
        stdout, stderr = _communicate_with_stall_detection(
            proc, timeout=timeout, cmd_str=cmd_str,
        )
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


def _communicate_with_stall_detection(
    proc: subprocess.Popen,
    *,
    timeout: int,
    cmd_str: str,
) -> Tuple[str, str]:
    """Wait for *proc* with repeated ``communicate`` calls, sampling CPU.

    Returns ``(stdout, stderr)`` on normal exit.  Raises
    ``subprocess.TimeoutExpired`` if *timeout* passes.
    """
    import time as _time

    window: deque[tuple[float, float]] = deque()  # (timestamp, cpu_s)
    window_max = max(1, int(STALL_WINDOW_S / STALL_SAMPLE_S))
    flagged = False
    remaining = timeout
    stall_line: Optional[str] = None

    while remaining > 0:
        wait = min(STALL_SAMPLE_S, remaining)
        try:
            stdout, stderr = proc.communicate(timeout=wait)
            # Append any stall line to stderr before returning.
            if stall_line is not None:
                stderr = stderr + stall_line
            return stdout, stderr
        except subprocess.TimeoutExpired:
            remaining -= wait
            if remaining <= 0:
                raise
            # Sample CPU time of the process group.
            try:
                pgid = os.getpgid(proc.pid)
                cpu = _group_cpu_s(pgid)
            except (OSError, ProcessLookupError):
                cpu = None
            now = _time.monotonic()
            if cpu is not None:
                window.append((now, cpu))
                if len(window) > window_max:
                    window.popleft()
                # Check for stall: window full and CPU gain below threshold.
                if not flagged and len(window) == window_max:
                    cpu_gain = window[-1][1] - window[0][1]
                    if cpu_gain < STALL_CPU_S:
                        idle_min = round((window[-1][0] - window[0][0]) / 60, 1)
                        # Find what the gate is waiting in.
                        try:
                            waiting = _deepest_descendant(proc.pid)
                        except Exception:
                            waiting = None
                        suffix = f" waiting in: {waiting[:80]}" if waiting else ""
                        stall_line = (
                            f"[gate-stalled] {cmd_str[:120]} idle {idle_min} min "
                            f"({cpu_gain:.1f} s CPU){suffix}\n"
                        )
                        try:
                            project = os.path.basename(os.getcwd())
                            write_event(
                                "gate-stalled", project,
                                command=cmd_str[:200],
                                idle_min=idle_min,
                                **({"waiting_in": waiting[:200]} if waiting else {}),
                            )
                        except Exception:
                            pass
                        flagged = True
    raise subprocess.TimeoutExpired(cmd_str, timeout)


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


# ── Stall-detection helpers (R2e) ────────────────────────────────────────────

def _parse_time(raw: str) -> Optional[float]:
    """Parse ``[[hh:]mm:]ss[.ff]`` from ``ps -o time=`` to seconds."""
    raw = raw.strip()
    if not raw or raw == "-":
        return None
    parts = raw.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return None
    except ValueError:
        return None


def _group_cpu_s(pgid: int) -> Optional[float]:
    """Return total CPU seconds for all processes in process group *pgid*.

    Returns ``None`` if ``ps`` fails or produces no parseable output.
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pgid=,time="],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode != 0:
            return None
        total = 0.0
        found = False
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                line_pgid = int(parts[0])
            except ValueError:
                continue
            if line_pgid != pgid:
                continue
            t = _parse_time(parts[1])
            if t is not None:
                total += t
                found = True
        return total if found else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _deepest_descendant(pid: int) -> Optional[str]:
    """Walk the process tree from *pid* and return the deepest live descendant's command.

    Returns ``None`` if *pid* is dead or ``ps`` fails.
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,command="],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode != 0:
            return None
        # Build parent → children map and pid → command map.
        children: dict[int, list[int]] = {}
        commands: dict[int, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                line_pid = int(parts[0])
                line_ppid = int(parts[1])
            except ValueError:
                continue
            children.setdefault(line_ppid, []).append(line_pid)
            commands[line_pid] = parts[2]
        # Walk from pid to the deepest leaf.
        current = pid
        visited: set[int] = set()
        while current not in visited:
            visited.add(current)
            kids = children.get(current, [])
            if not kids:
                break
            current = kids[0]
        return commands.get(current)
    except (subprocess.TimeoutExpired, OSError):
        return None