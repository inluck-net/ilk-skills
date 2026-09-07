"""Host deploy status resolver — three states for Phase 4.

Invokes bounce_daemons.sh in detect-only mode and parses its per-daemon
output into a single host-level status:

  ok             — all daemons fresh
  stale-daemon   — at least one daemon stale (none unreachable)
  unreachable    — at least one daemon unreachable, or output unparseable

This module does NOT reimplement staleness detection.  It delegates entirely
to bounce_daemons.sh (SP1's artifact) and interprets the output.

**Transport.** ``bounce_daemons.sh`` acts on the machine it runs on and contains
no ssh of its own, so a host other than this one is reached by running it over
``ssh <host> ...``.  A declared host is treated as LOCAL only when it is named
in ``local_hosts`` (CLI: ``--local-host``) or matches this machine's hostname;
every other host is remote.  ``--bouncer`` is therefore the bouncer's path *on
that host*, not a local path, for every remote entry.

Why this is spelled out: until 2026-09-07 the resolver had no ssh at all and
``resolve_hosts`` simply ran the local bouncer once per declared host.  A
two-host deploy bounced this machine twice and printed ``rezmac: ok`` -- the
state that asserts "install succeeded AND all daemons are current" -- for a
host it had never contacted.  The rezmac scheduler pid was unchanged from two
days earlier, and running the bouncer there over ssh reported
``scheduler -- stale``.  Nothing in the output distinguished the false ``ok``
from a real one.

**Fail-closed rule:** if the resolver sees no recognised prefix, or the
exit code is outside {0, 1, 2}, it returns 'unreachable'.  This prevents
a cosmetic change to bounce_daemons.sh from silently making stale hosts
report as 'ok'.  ssh's own transport failures (exit 255: cannot resolve, refused,
auth) land outside {0, 1, 2} and so resolve to 'unreachable' by the same rule --
never to 'ok'.  A host that cannot be reached must be indistinguishable from a
host that is failing, and distinguishable from one that is fine.

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
import os
import shlex
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

# Recognised prefixes from bounce_daemons.sh output lines.
# A single module-level constant — tests import this rather than
# re-listing the strings, which prevents drift between producer and consumer.
RECOGNISED_PREFIXES = ("fresh:", "stale:", "unreachable:", "bouncing:")

# Exit codes that bounce_daemons.sh is guaranteed to produce (SP2 contract).
_VALID_EXIT_CODES = {0, 1, 2}

# Local timeout for the bouncer; remote adds ssh connect + launchctl settle.
_LOCAL_TIMEOUT_S = 30
_REMOTE_TIMEOUT_S = 120
_SSH_CONNECT_TIMEOUT_S = 10

# Names that always mean "the machine this process is on".
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _local_aliases() -> frozenset[str]:
    """Names by which this machine can refer to itself, lowercased."""
    names = set(_LOOPBACK_NAMES)
    for probe in (socket.gethostname, socket.getfqdn):
        try:
            raw = probe()
        except OSError:
            continue
        if not raw:
            continue
        raw = raw.lower()
        names.add(raw)
        names.add(raw.split(".", 1)[0])  # short form of a .local / FQDN name
    return frozenset(names)


def is_local_host(host: str, local_hosts: Sequence[str] | None = None) -> bool:
    """True if *host* names the machine this process runs on.

    ``local_hosts`` is the explicit answer and wins outright -- a deploy label
    need not resemble a resolvable hostname.  On the machine that prompted the
    2026-09-07 fix the declared hosts were ``chad-mbp`` and ``rezmac`` while
    ``hostname`` returned ``Chads-MacBook-Pro.local``, so ``chad-mbp`` matches
    nothing by inference and ``ssh chad-mbp`` fails to resolve.  Guessing wrong
    in the safe direction means an ssh attempt that fails to 'unreachable',
    which is honest; guessing wrong the other way is the bug being fixed.
    """
    needle = (host or "").strip().lower()
    if local_hosts is not None and any(
        needle == (h or "").strip().lower() for h in local_hosts
    ):
        return True
    return needle in _local_aliases()


def resolve_host(
    bouncer_path: Path,
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    bounce_hosts: bool = False,
    env_override: dict | None = None,
    remote_host: str | None = None,
    ssh_program: str = "ssh",
) -> str:
    """Resolve a single host's deploy status.

    Args:
        bouncer_path: Path to bounce_daemons.sh (or a test fake).  When
                      *remote_host* is set this is the path ON THAT HOST and is
                      deliberately NOT checked for local existence -- a local
                      stat of a remote path is a negative from the wrong search
                      space, which is how a remote host came to read 'ok'.
        tmp_path: Scratch directory for environment setup.
        log_file: If set, the bouncer's invocation log is written here
                  (the bouncer must read $BOUNCER_LOG to use this).
                  LOCAL ONLY -- a remote run always logs to /dev/null, because
                  a local path would name a file on the wrong machine.
        bounce_hosts: If True, permits actual bouncing (omits --check).
                      If False (default), uses --check (detect-only).
        env_override: If set, use this environment instead of os.environ.
                      Used by contract tests that need a hermetic harness.
        remote_host: If set, run the bouncer on that host over ssh instead of
                     locally.  None (default) means run here, which is the
                     pre-2026-09-07 behaviour and keeps every local caller
                     unchanged.
        ssh_program: ssh executable.  Overridden by tests with a fake that
                     records its argv, so the remote path is asserted without
                     a network.

    Returns:
        One of 'ok', 'stale-daemon', 'unreachable'.
    """
    if remote_host is None:
        # A missing bouncer script means the host is unreachable.
        if not bouncer_path.exists():
            return "unreachable"

        # Build the command.  --check is the default (detect-only) mode.
        cmd = [str(bouncer_path)]
        if not bounce_hosts:
            cmd.append("--check")
        timeout_s = _LOCAL_TIMEOUT_S
    else:
        # Remote: hand the whole invocation to the remote shell.  BatchMode
        # refuses interactive auth so an unusable host fails fast to 255
        # rather than blocking on a prompt until the timeout.
        remote_argv = [str(bouncer_path)]
        if not bounce_hosts:
            remote_argv.append("--check")
        # cd into the bouncer's own directory first.  bounce_daemons.sh
        # derives the tree HEAD it compares against from the CWD, and ssh
        # lands in $HOME: measured on rezmac 2026-09-07, the identical script
        # reported `fresh` from the clone and
        # `stale (recorded 0231718..., HEAD unknown)` from $HOME.  A remote
        # check run from the wrong directory therefore reports every host
        # stale and would bounce a current daemon on every release.  git
        # discovers the repo by walking up, so the script's own directory is
        # enough and no clone-root layout is assumed.
        remote_cmd = "cd {} && BOUNCER_LOG=/dev/null {}".format(
            shlex.quote(str(bouncer_path.parent)),
            " ".join(shlex.quote(a) for a in remote_argv),
        )
        cmd = [
            ssh_program,
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
            remote_host,
            remote_cmd,
        ]
        timeout_s = _REMOTE_TIMEOUT_S

    # Set up environment for the bouncer.
    # Always set BOUNCER_LOG so the bouncer's log line doesn't fail under
    # set -e when no log file is requested.  /dev/null discards the output.
    base_env = env_override if env_override is not None else os.environ
    env_extra = {"BOUNCER_LOG": str(log_file) if log_file is not None else "/dev/null"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
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
    *,
    local_hosts: Sequence[str] | None = None,
    **kwargs: object,
) -> dict[str, str]:
    """Resolve every declared host and return an ordered mapping.

    Args:
        hosts: Declared host list (order is preserved in the result).
        bouncer_for_host: Callable that returns the bouncer path for a host.
                          For a remote host this is the path on THAT host.
        tmp_path: Scratch directory passed to resolve_host.
        local_hosts: Declared names that are this machine.  Anything not local
                     by this list or by hostname match is reached over ssh.
                     Pass the whole host list to force all-local behaviour,
                     which is what a fake-bouncer test means.
        **kwargs: Forwarded to resolve_host (log_file, bounce_hosts,
                  env_override, ssh_program).

    Returns:
        dict[str, str] with exactly one entry per declared host.
        A host whose probe raises or is missing resolves to 'unreachable'.

    Postcondition: every declared host appears in the result.  The assertion
    is in production code so the CLI benefits from it (AC-4 design decision).

    Each host gets its OWN transport decision.  Before 2026-09-07 there was no
    decision to make: every declared host ran the local bouncer, so a two-host
    deploy bounced this machine twice and reported the remote host 'ok'
    untouched.
    """
    result: dict[str, str] = {}
    for host in hosts:
        try:
            bouncer = bouncer_for_host(host)
            remote = None if is_local_host(host, local_hosts) else host
            result[host] = resolve_host(  # type: ignore[arg-type]
                bouncer, tmp_path, remote_host=remote, **kwargs,
            )
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
      python3 host_deploy_status.py --bouncer <p1> --bouncer <p2> \
          --hosts h1,h2 --local-host h1

    Every host not named by --local-host (and not matching this machine's
    hostname) is reached over ssh, and its --bouncer path is resolved on
    that host.  Each printed line names its transport, so a report cannot
    claim a remote host without saying how it was reached.
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
    parser.add_argument(
        "--local-host",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Declared host name that IS this machine (repeatable). Every other "
            "host in --hosts is reached over ssh, so its --bouncer path is the "
            "path on that host. Omit only when the declared name matches this "
            "machine's hostname -- a deploy label like 'chad-mbp' usually does "
            "not, and an unmatched name is ssh'd and fails to 'unreachable'."
        ),
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
        local_declared = args.local_host or None
        results = resolve_hosts(
            host_list,
            lambda h: bouncer_map[h],
            Path("/tmp"),
            local_hosts=local_declared,
            bounce_hosts=bounce,
        )
        for host in host_list:
            transport = "local" if is_local_host(host, local_declared) else "ssh"
            print(f"{host}: {results[host]} ({transport})")
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
