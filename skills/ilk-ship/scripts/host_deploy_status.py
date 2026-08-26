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
from collections.abc import Callable
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


def resolve_hosts(
    hosts: list[str],
    bouncer_for_host: Callable[[str], Path],
    tmp_path: Path,
    **kwargs: object,
) -> dict[str, str]:
    """Resolve every declared host and return an ordered mapping.

    Args:
        hosts: Declared host list (order is preserved in the result).
        bouncer_for_host: Callable that returns the bouncer path for a host.
        tmp_path: Scratch directory passed to resolve_host.
        **kwargs: Forwarded to resolve_host (log_file, bounce_hosts, env_override).

    Returns:
        dict[str, str] with exactly one entry per declared host.
        A host whose probe raises or is missing resolves to 'unreachable'.

    Postcondition: every declared host appears in the result.  The assertion
    is in production code so the CLI benefits from it (AC-4 design decision).
    """
    result: dict[str, str] = {}
    for host in hosts:
        try:
            bouncer = bouncer_for_host(host)
            result[host] = resolve_host(bouncer, tmp_path, **kwargs)  # type: ignore[arg-type]
        except Exception:
            result[host] = "unreachable"

    # Postcondition — a host dropped by the resolver is indistinguishable
    # from a passing one.  Assert rather than silently accept.
    missing = set(hosts) - set(result.keys())
    assert not missing, f"resolve_hosts dropped hosts: {missing}"
    return result


# Exit-code mapping — mirrors bounce_daemons.sh's contract so a caller
# can branch on either the printed line or the process exit code.
_STATE_EXIT_CODES = {
    "ok": 0,
    "stale-daemon": 1,
    "unreachable": 2,
}


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for host deploy status resolution.

    Usage (single host — legacy):
      python3 host_deploy_status.py --bouncer <path> [--bounce-hosts]

    Usage (multi-host — AC-6):
      python3 host_deploy_status.py --bouncer <path1> --bouncer <path2> ... --hosts h1,h2,...
    """
    parser = argparse.ArgumentParser(
        description="Resolve deploy status via bounce_daemons.sh.",
    )
    parser.add_argument(
        "--bouncer",
        action="append",
        default=[],
        help="Path to bounce_daemons.sh (repeat for multi-host, one per host).",
    )
    parser.add_argument(
        "--hosts",
        default=None,
        help="Comma-separated host list.  Omit for single-host mode.",
    )
    parser.add_argument(
        "--bounce-hosts",
        action="store_true",
        default=False,
        help="Permit actual bouncing (omit --check). Without this flag, detect-only.",
    )
    args = parser.parse_args(argv)

    bounce = args.bounce_hosts

    if args.hosts:
        # Multi-host mode — resolve every declared host.
        host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
        bouncers = [Path(b) for b in args.bouncer]
        if len(bouncers) != len(host_list):
            print(
                f"error: {len(host_list)} hosts but {len(bouncers)} --bouncer paths",
                file=sys.stderr,
            )
            sys.exit(2)
        bouncer_map = dict(zip(host_list, bouncers))
        results = resolve_hosts(
            host_list, lambda h: bouncer_map[h], Path("/tmp"), bounce_hosts=bounce,
        )
        for host in host_list:
            print(f"{host}: {results[host]}")
        # Exit non-zero if any host is not ok.
        if any(s != "ok" for s in results.values()):
            sys.exit(1)
    else:
        # Single-host mode — legacy behaviour.
        if not args.bouncer:
            print("error: --bouncer is required", file=sys.stderr)
            sys.exit(2)
        bouncer = Path(args.bouncer[0])
        state = resolve_host(bouncer, Path("/tmp"), bounce_hosts=bounce)
        print(state)
        sys.exit(_STATE_EXIT_CODES.get(state, 2))


if __name__ == "__main__":
    main()
