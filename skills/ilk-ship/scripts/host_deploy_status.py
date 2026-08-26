"""Host deploy status resolver — three states for Phase 4.

Invokes bounce_daemons.sh in detect-only mode and parses its per-daemon
output into a single host-level status:

  ok             — all daemons fresh
  stale-daemon   — at least one daemon stale (none unreachable)
  unreachable    — at least one daemon unreachable, or output unparseable

This module does NOT reimplement staleness detection.  It delegates entirely
to bounce_daemons.sh (SP1's artifact) and interprets the output.

**Fail-closed rule:** if the resolver sees no recognised prefix, or the
exit code is outside {0, 1, 2}, it returns 'unreachable'.  This prevents
a cosmetic change to bounce_daemons.sh from silently making stale hosts
report as 'ok'.

Recognised prefixes: RECOGNISED_PREFIXES (module-level constant).

Exit-code contract from bounce_daemons.sh:
  0 — all fresh (no stale, no unreachable)
  1 — bounced at least one (only when --bounce-hosts omitted ⇒ --check)
  2 — at least one unreachable

Usage:
  from host_deploy_status import resolve_host
  status = resolve_host(bouncer_path, tmp_path)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Recognised prefixes from bounce_daemons.sh output lines.
# A single module-level constant — tests import this rather than
# re-listing the strings, which prevents drift between producer and consumer.
RECOGNISED_PREFIXES = ("fresh:", "stale:", "unreachable:", "bouncing:")

# Exit codes that bounce_daemons.sh is guaranteed to produce (SP2 contract).
_VALID_EXIT_CODES = {0, 1, 2}


def resolve_host(
    bouncer_path: Path,
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    bounce_hosts: bool = False,
    env_override: dict | None = None,
) -> str:
    """Resolve a single host's deploy status.

    Args:
        bouncer_path: Path to bounce_daemons.sh (or a test fake).
        tmp_path: Scratch directory for environment setup.
        log_file: If set, the bouncer's invocation log is written here
                  (the bouncer must read $BOUNCER_LOG to use this).
        bounce_hosts: If True, permits actual bouncing (omits --check).
                      If False (default), uses --check (detect-only).
        env_override: If set, use this environment instead of os.environ.
                      Used by contract tests that need a hermetic harness.

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
    base_env = env_override if env_override is not None else __import__("os").environ
    env_extra = {"BOUNCER_LOG": str(log_file) if log_file is not None else "/dev/null"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**base_env, **env_extra},
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unreachable"

    exit_code = result.returncode
    output = result.stdout.strip()

    # Parse per-daemon output lines.
    has_stale = False
    has_unreachable = False
    has_recognised = False

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("unreachable:"):
            has_unreachable = True
            has_recognised = True
        elif line.startswith("stale:"):
            has_stale = True
            has_recognised = True
        elif line.startswith(RECOGNISED_PREFIXES):
            # fresh: or bouncing: — recognised, but doesn't change state.
            has_recognised = True

    # Fail closed: exit code outside {0,1,2} means the script aborted
    # partway, so its output is not a complete report.
    if exit_code not in _VALID_EXIT_CODES:
        return "unreachable"

    # Fail closed: no recognised prefix means we cannot parse the output.
    if not has_recognised:
        return "unreachable"

    # Exit code 2 or any unreachable line → unreachable.
    if exit_code == 2 or has_unreachable:
        return "unreachable"

    # Any stale line (exit 0 in --check mode) → stale-daemon.
    if has_stale:
        return "stale-daemon"

    # All fresh.
    return "ok"


# Exit-code mapping — mirrors bounce_daemons.sh's contract so a caller
# can branch on either the printed line or the process exit code.
_STATE_EXIT_CODES = {
    "ok": 0,
    "stale-daemon": 1,
    "unreachable": 2,
}


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for host deploy status resolution.

    Usage:
      python3 host_deploy_status.py --bouncer <path> [--bounce-hosts]
    """
    parser = argparse.ArgumentParser(
        description="Resolve a single host's deploy status via bounce_daemons.sh.",
    )
    parser.add_argument(
        "--bouncer",
        required=True,
        help="Path to bounce_daemons.sh (or a test fake).",
    )
    parser.add_argument(
        "--bounce-hosts",
        action="store_true",
        default=False,
        help="Permit actual bouncing (omit --check). Without this flag, detect-only.",
    )
    args = parser.parse_args(argv)

    bouncer = Path(args.bouncer)
    state = resolve_host(bouncer, Path("/tmp"), bounce_hosts=args.bounce_hosts)
    print(state)
    sys.exit(_STATE_EXIT_CODES.get(state, 2))


if __name__ == "__main__":
    main()
