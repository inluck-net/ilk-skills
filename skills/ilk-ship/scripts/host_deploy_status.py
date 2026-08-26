"""Host deploy status resolver — three states for Phase 4.

Invokes bounce_daemons.sh in detect-only mode and parses its per-daemon
output into a single host-level status:

  ok             — all daemons fresh
  stale-daemon   — at least one daemon stale (none unreachable)
  unreachable    — at least one daemon unreachable

This module does NOT reimplement staleness detection.  It delegates entirely
to bounce_daemons.sh (SP1's artifact) and interprets the output.

Exit-code contract from bounce_daemons.sh:
  0 — all fresh (no stale, no unreachable)
  1 — bounced at least one (only when --bounce-hosts omitted ⇒ --check)
  2 — at least one unreachable

Usage:
  from host_deploy_status import resolve_host
  status = resolve_host(bouncer_path, tmp_path)
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_host(
    bouncer_path: Path,
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    bounce_hosts: bool = False,
) -> str:
    """Resolve a single host's deploy status.

    Args:
        bouncer_path: Path to bounce_daemons.sh (or a test fake).
        tmp_path: Scratch directory for environment setup.
        log_file: If set, the bouncer's invocation log is written here
                  (the bouncer must read $BOUNCER_LOG to use this).
        bounce_hosts: If True, permits actual bouncing (omits --check).
                      If False (default), uses --check (detect-only).

    Returns:
        One of 'ok', 'stale-daemon', 'unreachable'.
    """
    # A missing bouncer script means the host is unreachable.
    if not bouncer_path.exists():
        return "unreachable"

    # Build the command.  --check is the default (detect-only) mode.
    cmd = [str(bouncer_path)]
    if not bounce_hosts:
        cmd.append("--check")

    # Set up environment for the bouncer.
    # Always set BOUNCER_LOG so the bouncer's log line doesn't fail under
    # set -e when no log file is requested.  /dev/null discards the output.
    env_extra = {"BOUNCER_LOG": str(log_file) if log_file is not None else "/dev/null"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**__import__("os").environ, **env_extra},
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unreachable"

    exit_code = result.returncode
    output = result.stdout.strip()

    # Parse per-daemon output lines.
    has_stale = False
    has_unreachable = False

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("unreachable:"):
            has_unreachable = True
        elif line.startswith("stale:"):
            has_stale = True

    # Exit code 2 or any unreachable line → unreachable.
    if exit_code == 2 or has_unreachable:
        return "unreachable"

    # Any stale line (exit 0 in --check mode) → stale-daemon.
    if has_stale:
        return "stale-daemon"

    # All fresh.
    return "ok"
