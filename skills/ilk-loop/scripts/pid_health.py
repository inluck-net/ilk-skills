"""Cross-platform PID health validation for ilk-loop status and watchdog.

Shared by status_progress.py, status_all.py, and watchdog scripts to avoid
duplicated PID-check logic with inconsistent semantics.

Usage::

    from pid_health import pid_alive, ilk_pid_alive, validate_pid

    alive = pid_alive(12345)          # does *some* process hold this PID?
    ours  = ilk_pid_alive(12345)      # …and is it an ilk process?
    ok, reason = validate_pid(12345, expected_command="python3")

Anything reading a *sentinel* or *pidfile* wants ``ilk_pid_alive``: the
PID recorded there was written by a past run and may since have been
recycled.  ``pid_alive`` is for PIDs you hold yourself.
"""
from __future__ import annotations

import os
import subprocess
import sys


def pid_alive(pid: int) -> bool:
    """Check whether *pid* refers to a live process.

    POSIX: signal 0 is a no-op delivery; ``PermissionError`` means the
    process exists but is owned by another user (still alive).

    Windows: ``tasklist /FI "PID eq <pid>"`` is used because
    ``os.kill(pid, 0)`` has unreliable semantics on Windows.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
        return str(pid) in out
    # POSIX
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False
    return True


def pid_cmdline(pid: int) -> str | None:
    """Return the *full* command line for *pid*, or ``None`` if unavailable.

    Distinct from ``_pid_command_name``, which returns only the base
    executable name: every ilk runner is ``bash``/``pwsh``/``python3``,
    so the base name cannot tell an ilk loop from any other shell.  The
    script path lives in argv[1..], which only the full command line has.
    """
    if pid <= 0:
        return None
    if sys.platform == "win32":
        # tasklist has no command-line column; CIM does.
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None
        return out.strip() or None
    # Linux: /proc/<pid>/cmdline is NUL-separated argv.
    cmdline = f"/proc/{pid}/cmdline"
    if os.path.exists(cmdline):
        try:
            with open(cmdline, "rb") as fh:
                raw = fh.read()
            argv = [p.decode(errors="replace") for p in raw.split(b"\x00") if p]
            if argv:
                return " ".join(argv)
        except OSError:
            pass
    # macOS / BSD: -ww defeats the width-based truncation ps applies to argv.
    try:
        out = subprocess.check_output(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return out.strip() or None


# Command-line substrings that identify a process as belonging to ilk.
# Kept byte-identical to the `case` patterns in _ilk_pid.sh:ilk_pid_alive —
# the two implementations answer the same question for the same pidfiles,
# and a divergence between them is a status display that contradicts the
# scheduler.  Matched case-insensitively against the full command line.
ILK_PROCESS_PATTERNS = (
    "run_ilk_loop",
    "watchdog.sh",
    "watchdog.ps1",
    "scheduler.sh",
    "scheduler_scan",
    "scheduler.ps1",
)


def ilk_pid_alive(pid: int) -> bool:
    """True only when *pid* is alive AND is actually an ilk process.

    Python mirror of ``_ilk_pid.sh:ilk_pid_alive`` (bash), which the
    scheduler and launcher already use.  ``pid_alive`` alone answers
    "does *some* process hold this PID", which is not the question a
    sentinel asks: PIDs are recycled, so a run abandoned before it could
    write a terminal state reads as live again the moment the OS hands
    its number to an unrelated process.

    Observed 2026-08-13: a gh-triage sentinel written 2026-07-08 named
    PID 18920, which by then belonged to a ``/bin/zsh -c … pytest``
    shell — the menu-bar count said two loops were running while only
    one was.

    An unreadable command line falls back to bare liveness: over-
    reporting a run as live is the safe direction, since the stale-
    running path it would otherwise take is what parks a project.
    """
    if not pid_alive(pid):
        return False
    cmd = pid_cmdline(pid)
    if not cmd:
        # Can't determine the command; treat alive as sufficient.
        return True
    low = cmd.lower()
    return any(pat in low for pat in ILK_PROCESS_PATTERNS)


def _pid_command_name(pid: int) -> str | None:
    """Return the base command name for *pid*, or ``None`` if unavailable."""
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
            # CSV format: "image name","pid","session name","mem usage"
            for line in out.splitlines():
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2 and parts[1].strip('"') == str(pid):
                    return parts[0].strip('"').lower()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None
    # POSIX: try /proc first (Linux), fall back to ps (macOS/BSD)
    cmdline = f"/proc/{pid}/cmdline"
    if os.path.exists(cmdline):
        try:
            raw = open(cmdline, "rb").read()
            # /proc/pid/cmdline is NUL-separated
            first = raw.split(b"\x00")[0]
            return os.path.basename(first.decode(errors="replace")).lower()
        except OSError:
            pass
    # macOS / BSD: use ps
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "comm="],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        name = out.strip()
        if name:
            return os.path.basename(name).lower()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def pid_command_alive(pid: int, expected_command: str) -> bool:
    """Check whether *pid* is alive AND its command matches *expected_command*.

    *expected_command* is compared case-insensitively against the base
    process name (e.g. ``"python3"``, ``"bash"``, ``"pwsh"``).  If the
    command cannot be determined, falls back to ``pid_alive`` (returns
    ``True`` if the process exists at all).
    """
    if not pid_alive(pid):
        return False
    actual = _pid_command_name(pid)
    if actual is None:
        # Can't determine command; treat alive as sufficient.
        return True
    return expected_command.lower() in actual


def validate_pid(pid: int, expected_command: str | None = None) -> tuple[bool, str]:
    """Validate PID with optional command identity check.

    Returns ``(ok, reason)`` where *ok* is ``True`` when the PID is
    healthy, and *reason* is a human-readable explanation suitable for
    status output.

    >>> validate_pid(12345)
    (True, 'pid 12345 is alive')
    >>> validate_pid(99999)
    (False, 'pid 99999 is not alive')
    >>> validate_pid(12345, 'python3')
    (True, 'pid 12345 is alive (command=python3)')
    """
    if pid <= 0:
        return False, f"pid {pid} is invalid"
    if not pid_alive(pid):
        return False, f"pid {pid} is not alive"
    if expected_command is not None:
        actual = _pid_command_name(pid)
        if actual is not None and expected_command.lower() not in actual:
            return False, f"pid {pid} is alive but command is {actual!r}, expected {expected_command!r}"
        return True, f"pid {pid} is alive (command={actual or '?'})"
    return True, f"pid {pid} is alive"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check PID health.")
    parser.add_argument("pid", type=int, help="Process ID to check")
    parser.add_argument("--command", "-c", default=None, help="Expected command name")
    args = parser.parse_args()

    ok, reason = validate_pid(args.pid, args.command)
    print(reason)
    sys.exit(0 if ok else 1)
