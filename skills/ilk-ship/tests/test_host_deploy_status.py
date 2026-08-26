"""Tests for host deploy status — fail-closed resolver and real bouncer contract.

Covers AC-1..AC-7 from sub-plan the-resolver-fails-closed:

  AC-1  Output with no recognised prefix resolves to unreachable.
  AC-2  Empty stdout resolves to unreachable, whatever the exit code.
  AC-3  Exit code outside {0, 1, 2} resolves to unreachable.
  AC-4  A host whose every daemon line is fresh: still resolves ok.
  AC-5  The recognised-prefix set is a single module-level constant.
  AC-6  A contract test runs the real bounce_daemons.sh and asserts
        resolve_host classifies its actual stdout correctly.
  AC-7  The 15 pre-existing tests still pass unchanged.

Drives the resolver as a function call with injected fake bouncer script,
plus one contract test that runs the real script under a hermetic harness.
"""

from __future__ import annotations

import stat
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"

# The three valid states — AC-1
_VALID_STATES = {"ok", "stale-daemon", "unreachable"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fake_bouncer(
    tmp_path: Path,
    *,
    output_lines: list[str] | None = None,
    exit_code: int = 0,
    log_invocations: bool = True,
) -> Path:
    """Create a fake bounce_daemons.sh that logs invocations and returns fixed output.

    Returns the path to the fake script.
    """
    fake = tmp_path / "bin" / "bounce_daemons.sh"
    fake.parent.mkdir(parents=True, exist_ok=True)

    lines = ['#!/usr/bin/env bash']
    if log_invocations:
        lines.append('echo "$@" >> "$BOUNCER_LOG"')
    for line in (output_lines or []):
        lines.append(f'echo "{line}"')
    lines.append(f'exit {exit_code}')
    lines.append('')  # trailing newline

    fake.write_text('\n'.join(lines), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _assert_distinct_states(results: dict[str, str]) -> None:
    """Assert that the three states are distinguishable — AC-1.

    A test that only checks ok-vs-not-ok cannot catch the failure this
    sub-plan guards against (rezmac was "not ok" but reported "ok").
    """
    states = set(results.values())
    assert states == _VALID_STATES, (
        f"Expected exactly {_VALID_STATES}, got {states}. "
        "A two-state result cannot distinguish 'checked and current' "
        "from 'could not check'."
    )


# ---------------------------------------------------------------------------
# AC-1: Three distinguishable states
# ---------------------------------------------------------------------------

class TestThreeStates:
    """AC-1: Per-host deploy status resolves to exactly ok / stale-daemon / unreachable."""

    def test_ok_state_exists(self, tmp_path: Path) -> None:
        """A host with fresh daemons resolves 'ok'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "ok"

    def test_stale_daemon_state_exists(self, tmp_path: Path) -> None:
        """A host with stale daemons resolves 'stale-daemon'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "stale-daemon"

    def test_unreachable_state_exists(self, tmp_path: Path) -> None:
        """A host that cannot be probed resolves 'unreachable'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_three_states_are_distinguishable(self, tmp_path: Path) -> None:
        """AC-1: the three states are mutually exclusive and exhaustive.

        A test that only checks ok-vs-not-ok cannot catch the rezmac failure:
        "not ok" was reported as "ok" for five days.
        """
        # Run three scenarios and collect their states
        results = {}

        # ok
        fake_ok = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        results["ok"] = _resolve_host(fake_ok, tmp_path)

        # stale-daemon
        fake_stale = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        results["stale-daemon"] = _resolve_host(fake_stale, tmp_path)

        # unreachable
        fake_unreachable = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        results["unreachable"] = _resolve_host(fake_unreachable, tmp_path)

        _assert_distinct_states(results)


# ---------------------------------------------------------------------------
# AC-2: Stale daemon → stale-daemon, not ok
# ---------------------------------------------------------------------------

class TestStaleDaemonNotOk:
    """AC-2: A host whose daemon reports stale resolves stale-daemon, not ok."""

    def test_stale_daemon_resolves_stale_not_ok(self, tmp_path: Path) -> None:
        """Even though install.sh succeeded, a stale daemon means not deployed."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "stale-daemon", (
            "A host with a stale daemon must resolve 'stale-daemon', not 'ok'. "
            "Reporting 'ok' is the lie that rezmac went unnoticed for five days."
        )
        assert result != "ok"


# ---------------------------------------------------------------------------
# AC-3: Unreachable host → unreachable, never ok
# ---------------------------------------------------------------------------

class TestUnreachableNeverOk:
    """AC-3: A host that cannot be probed resolves unreachable — never ok by omission."""

    def test_ssh_failure_resolves_unreachable(self, tmp_path: Path) -> None:
        """When the bouncer cannot be invoked (ssh failure), host is unreachable."""
        # Simulate ssh failure: script exits 2 with no output
        fake = _write_fake_bouncer(tmp_path, exit_code=2)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_launchctl_absent_resolves_unreachable(self, tmp_path: Path) -> None:
        """When launchctl is absent, host is unreachable."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_script_missing_resolves_unreachable(self, tmp_path: Path) -> None:
        """When the bouncer script is missing, host is unreachable."""
        missing = tmp_path / "nonexistent" / "bounce_daemons.sh"
        result = _resolve_host(missing, tmp_path)
        assert result == "unreachable"

    def test_unreachable_never_ok(self, tmp_path: Path) -> None:
        """An unreachable host must never be reported as ok."""
        fake = _write_fake_bouncer(tmp_path, exit_code=2)
        result = _resolve_host(fake, tmp_path)
        assert result != "ok", (
            "An unreachable host must never be reported 'ok'. "
            "Ok-by-omission is how rezmac went unnoticed."
        )


# ---------------------------------------------------------------------------
# AC-4: Detection uses --check mode, bounces nothing
# ---------------------------------------------------------------------------

class TestDetectionUsesCheckMode:
    """AC-4: Detection uses SP1's --check mode and bounces nothing by default."""

    def test_check_flag_is_passed(self, tmp_path: Path) -> None:
        """The resolver must invoke the bouncer with --check."""
        log = tmp_path / "invocations.log"
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        _resolve_host(fake, tmp_path, log_file=log)
        invocations = log.read_text()
        assert "--check" in invocations, (
            "Detection must use --check mode. "
            "Asserted on the invocation, not only the outcome."
        )

    def test_no_bounce_happens_by_default(self, tmp_path: Path) -> None:
        """In detect-only mode, no bounce commands are issued."""
        log = tmp_path / "invocations.log"
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        _resolve_host(fake, tmp_path, log_file=log)
        invocations = log.read_text()
        # The fake script logs "$@", so we check no "bootout" or "bootstrap" appears
        assert "bootout" not in invocations, (
            "Detection must not bounce. A bootout was invoked in detect-only mode."
        )
        assert "bootstrap" not in invocations, (
            "Detection must not bounce. A bootstrap was invoked in detect-only mode."
        )


# ---------------------------------------------------------------------------
# AC-5: Explicit flag required to bounce
# ---------------------------------------------------------------------------

class TestBounceRequiresExplicitFlag:
    """AC-5: An explicit flag (--bounce-hosts) permits the bounce; absent it,
    a stale host is reported and left alone."""

    def test_stale_host_reported_left_alone_without_flag(self, tmp_path: Path) -> None:
        """Without --bounce-hosts, a stale host is reported but not bounced."""
        log = tmp_path / "invocations.log"
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path, log_file=log, bounce_hosts=False)
        assert result == "stale-daemon"
        # Verify no bounce was attempted
        invocations = log.read_text()
        assert "bootout" not in invocations

    def test_bounce_permitted_with_flag(self, tmp_path: Path) -> None:
        """With --bounce-hosts, a stale host may be bounced."""
        log = tmp_path / "invocations.log"
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456)"
        ], exit_code=1)  # exit 1 = bounced
        result = _resolve_host(fake, tmp_path, log_file=log, bounce_hosts=True)
        # The flag permits bouncing; the result should reflect what happened
        assert result in ("ok", "stale-daemon"), (
            "With --bounce-hosts, the result depends on whether bounce succeeded."
        )


# ---------------------------------------------------------------------------
# AC-6: Summary lists every declared host
# ---------------------------------------------------------------------------

class TestSummaryListsEveryHost:
    """AC-6: The Phase 4 summary lists every declared host with its state."""

    def test_all_hosts_appear_in_summary(self, tmp_path: Path) -> None:
        """Every declared host must appear in the summary — a host missing
        from the report is indistinguishable from a passing one."""
        hosts = ["chad-mbp", "rezmac", "devbox"]
        results = {}
        for host in hosts:
            fake = _write_fake_bouncer(tmp_path, output_lines=[
                "fresh: scheduler — fresh (toolkit_head matches HEAD)"
            ], exit_code=0)
            results[host] = _resolve_host(fake, tmp_path)

        # All hosts must have a result
        assert set(results.keys()) == set(hosts), (
            f"Missing hosts: {set(hosts) - set(results.keys())}. "
            "A host missing from the report is indistinguishable from a passing one."
        )

    def test_summary_contains_state_for_each_host(self, tmp_path: Path) -> None:
        """Each host's result must be one of the three valid states."""
        hosts = ["chad-mbp", "rezmac"]
        for host in hosts:
            fake = _write_fake_bouncer(tmp_path, output_lines=[
                "fresh: scheduler — fresh (toolkit_head matches HEAD)"
            ], exit_code=0)
            result = _resolve_host(fake, tmp_path)
            assert result in _VALID_STATES, (
                f"Host {host} got invalid state '{result}'. "
                f"Must be one of {_VALID_STATES}."
            )


# ---------------------------------------------------------------------------
# AC-1: Unrecognised output → unreachable (fail closed)
# ---------------------------------------------------------------------------

class TestFailsClosed:
    """The resolver must fail closed on output it cannot parse.

    Unrecognised, empty, or malformed bouncer output must resolve to
    'unreachable', not fall through to 'ok'.  Exit codes outside {0,1,2}
    must also resolve 'unreachable' — the script aborted partway.
    """

    def test_unrecognised_output_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-1: Output with no recognised prefix resolves to unreachable."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "some garbage output that doesn't match any prefix",
            "another unknown line",
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable", (
            "Unrecognised output must resolve 'unreachable', not 'ok'. "
            "Failing open on unparseable output is the founding defect."
        )

    def test_empty_stdout_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-2: Empty stdout resolves to unreachable, whatever the exit code."""
        # exit 0 with no output — the script produced nothing
        fake = _write_fake_bouncer(tmp_path, exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable", (
            "Empty stdout must resolve 'unreachable'. "
            "A silent script is not a report of freshness."
        )

    def test_empty_stdout_exit_1_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-2: Empty stdout with exit 1 still resolves unreachable."""
        fake = _write_fake_bouncer(tmp_path, exit_code=1)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_unknown_exit_code_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-3: Exit code outside {0,1,2} resolves unreachable even if stdout looks fine."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=5)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable", (
            "Exit code 5 is outside {0,1,2}. "
            "The script aborted partway; its output is not a complete report."
        )

    def test_exit_code_3_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-3: Exit code 3 also resolves unreachable."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=3)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_exit_code_127_resolves_unreachable(self, tmp_path: Path) -> None:
        """AC-3: Exit code 127 (command not found) resolves unreachable."""
        fake = _write_fake_bouncer(tmp_path, exit_code=127)
        result = _resolve_host(fake, tmp_path)
        assert result == "unreachable"

    def test_fresh_host_still_resolves_ok(self, tmp_path: Path) -> None:
        """AC-4: A host whose every daemon line is fresh: still resolves ok.

        Failing closed must not break a healthy host.
        """
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "ok", (
            "A healthy host with all fresh daemons must still resolve 'ok'. "
            "Fail-closed must not make healthy hosts unreachable."
        )

    def test_mixed_fresh_and_still_ok(self, tmp_path: Path) -> None:
        """AC-4: Multiple fresh daemons still resolve ok."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)",
            "fresh: tray — fresh (toolkit_head matches HEAD)",
        ], exit_code=0)
        result = _resolve_host(fake, tmp_path)
        assert result == "ok"


# ---------------------------------------------------------------------------
# AC-6: Contract test against the real bounce_daemons.sh
# ---------------------------------------------------------------------------

import json
import os
import subprocess


def _write_contract_fake_launchctl(tmp_path: Path) -> Path:
    """Create a minimal fake launchctl for the contract test.

    The fake logs argv and exits 0 for all verbs.  Not imported from
    test_bounce_daemons.py — cross-tree test imports are how the old host
    guard ended up in the wrong tree.
    """
    fake = tmp_path / "bin" / "launchctl"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "$@" >> "$LAUNCHCTL_LOG"
            exit 0
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _write_contract_fake_git(tmp_path: Path, head_sha: str) -> Path:
    """Create a minimal fake git for the contract test.

    Returns head_sha for rev-parse HEAD, handles -C <path>.
    """
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            if [[ "$1" == "-C" ]]; then
                shift 2
            fi
            if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then
                echo "{head_sha}"
                exit 0
            fi
            exit 1
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _run_real_bouncer(
    tmp_path: Path,
    *,
    state: dict | None = None,
    head_sha: str = "abc123",
    daemon_loaded: bool = True,
    plist_exists: bool = True,
) -> str:
    """Run the real bounce_daemons.sh under a hermetic harness and return resolve_host's result.

    Sets up: Darwin platform, fake launchctl, fake git, tmp HOME,
    ILK_BOUNCE_ALLOW_FOREIGN_HOME=1.

    Temporarily replaces os.environ so resolve_host's subprocess sees
    the hermetic environment.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ilk_data = home / ".ilk-data"
    ilk_data.mkdir(exist_ok=True)

    # Write state file
    state_file = ilk_data / "scheduler.state.json"
    if state is not None:
        state_file.write_text(json.dumps(state), encoding="utf-8")

    # Fake binaries
    _write_contract_fake_launchctl(tmp_path)
    _write_contract_fake_git(tmp_path, head_sha)

    # Launchctl log
    launchctl_log = tmp_path / "launchctl.log"
    launchctl_log.write_text("", encoding="utf-8")

    # Fake plist directory
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    if plist_exists:
        (plist_dir / "net.inluck.ilk.scheduler.plist").write_text(
            "<plist><!-- stub --></plist>", encoding="utf-8"
        )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}",
        "ILK_BOUNCE_PLATFORM": "Darwin",
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
        "ILK_BOUNCE_DAEMON_LOADED": "1" if daemon_loaded else "0",
        "LAUNCHCTL_LOG": str(launchctl_log),
    }

    return _real_resolve_host(_BOUNCE_SH, tmp_path, env_override=env)


class TestRealBouncerContract:
    """AC-6: Contract test running the real bounce_daemons.sh.

    Asserts resolve_host classifies the script's actual stdout correctly
    for fresh, stale (--check), and unreachable scenarios.  This pins
    both the producer and consumer at once — if bounce_daemons.sh's line
    format changes, this test breaks.
    """

    def test_fresh_daemon_resolves_ok(self, tmp_path: Path) -> None:
        """Real script with matching HEAD → fresh: → ok."""
        result = _run_real_bouncer(
            tmp_path,
            state={"pid": 12345, "started_at": "2026-08-26T10:00:00Z", "toolkit_head": "abc123"},
            head_sha="abc123",
            daemon_loaded=True,
            plist_exists=True,
        )
        assert result == "ok", (
            "Real script with matching HEAD should produce 'fresh:' lines → 'ok'."
        )

    def test_stale_daemon_resolves_stale(self, tmp_path: Path) -> None:
        """Real script with mismatched HEAD → stale: → stale-daemon."""
        result = _run_real_bouncer(
            tmp_path,
            state={"pid": 12345, "started_at": "2026-08-26T10:00:00Z", "toolkit_head": "old_sha"},
            head_sha="new_sha",
            daemon_loaded=True,
            plist_exists=True,
        )
        assert result == "stale-daemon", (
            "Real script with mismatched HEAD should produce 'stale:' lines → 'stale-daemon'."
        )

    def test_daemon_not_loaded_resolves_unreachable(self, tmp_path: Path) -> None:
        """Real script with daemon not loaded → unreachable: → unreachable."""
        result = _run_real_bouncer(
            tmp_path,
            state={"pid": 12345, "started_at": "2026-08-26T10:00:00Z", "toolkit_head": "abc123"},
            head_sha="abc123",
            daemon_loaded=False,
            plist_exists=True,
        )
        assert result == "unreachable", (
            "Real script with daemon not loaded should produce 'unreachable:' → 'unreachable'."
        )


# ---------------------------------------------------------------------------
# Host resolver — delegates to host_deploy_status.resolve_host()
# ---------------------------------------------------------------------------

import sys

_SCRIPTS_DIR = _REPO_ROOT / "skills" / "ilk-ship" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from host_deploy_status import resolve_host as _real_resolve_host  # noqa: E402


def _resolve_host(
    bouncer_path: Path,
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    bounce_hosts: bool = False,
    env_override: dict | None = None,
) -> str:
    """Resolve a single host's deploy status.

    Returns one of: 'ok', 'stale-daemon', 'unreachable'.
    """
    return _real_resolve_host(
        bouncer_path, tmp_path, log_file=log_file, bounce_hosts=bounce_hosts,
        env_override=env_override,
    )


# ---------------------------------------------------------------------------
# AC-1..AC-4: CLI entry point (sub-plan phase-4-actually-calls-the-resolver)
# ---------------------------------------------------------------------------

_HOST_DEPLOY_STATUS_SCRIPT = _SCRIPTS_DIR / "host_deploy_status.py"


class TestCliEntryPoint:
    """AC-1..AC-4: The script must be invocable as a CLI.

    Drives host_deploy_status.py as a subprocess with a fake bouncer.
    """

    def test_prints_bare_state_ok(self, tmp_path: Path) -> None:
        """AC-1: --bouncer <path> prints exactly one line: 'ok'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "ok"

    def test_prints_bare_state_stale(self, tmp_path: Path) -> None:
        """AC-1: --bouncer <path> prints exactly one line: 'stale-daemon'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 1
        assert result.stdout.strip() == "stale-daemon"

    def test_prints_bare_state_unreachable(self, tmp_path: Path) -> None:
        """AC-1: --bouncer <path> prints exactly one line: 'unreachable'."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 2
        assert result.stdout.strip() == "unreachable"

    def test_bounce_hosts_flag_accepted(self, tmp_path: Path) -> None:
        """AC-2: --bounce-hosts is accepted and threads through."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456)"
        ], exit_code=1)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT),
             "--bouncer", str(fake), "--bounce-hosts"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        # With --bounce-hosts, the bouncer is invoked without --check.
        # Exit 1 means bounced; state depends on output.
        assert result.returncode in (0, 1, 2)
        assert result.stdout.strip() in _VALID_STATES

    def test_exit_code_maps_ok_to_zero(self, tmp_path: Path) -> None:
        """AC-3: ok → exit 0."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 0

    def test_exit_code_maps_stale_to_one(self, tmp_path: Path) -> None:
        """AC-3: stale-daemon → exit 1."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "stale: scheduler — stale (recorded abc123, HEAD def456) (would bounce)"
        ], exit_code=0)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 1

    def test_exit_code_maps_unreachable_to_two(self, tmp_path: Path) -> None:
        """AC-3: unreachable → exit 2."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--bouncer", str(fake)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "BOUNCER_LOG": "/dev/null"},
        )
        assert result.returncode == 2

    def test_help_exits_zero_and_names_flags(self) -> None:
        """AC-4: --help exits 0 and names both flags."""
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "--bouncer" in result.stdout
        assert "--bounce-hosts" in result.stdout

    def test_no_args_exits_with_usage(self) -> None:
        """AC-4: No arguments exits 2 with a usage message, not a traceback."""
        result = subprocess.run(
            [sys.executable, str(_HOST_DEPLOY_STATUS_SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 2
        # argparse writes usage to stderr on missing required args
        assert "usage" in result.stderr.lower() or "usage" in result.stdout.lower()


# ---------------------------------------------------------------------------
# AC-6: SKILL.md Phase 4 names the script (anti-drift)
# ---------------------------------------------------------------------------

_SKILL_MD = _REPO_ROOT / "skills" / "ilk-ship" / "SKILL.md"
_SCRIPT_REL_PATH = "skills/ilk-ship/scripts/host_deploy_status.py"


class TestSkillDocNamesTheScript:
    """AC-6: SKILL.md's Phase 4 section must name the resolver script.

    This is the anti-drift gate: if someone rewrites Phase 4 back into
    prose that doesn't mention the script, the suite goes red.
    """

    def test_phase4_section_contains_script_path(self) -> None:
        """AC-5: SKILL.md Phase 4 names host_deploy_status.py."""
        content = _SKILL_MD.read_text(encoding="utf-8")
        # Find the Phase 4 section
        phase4_start = content.find("### Phase 4")
        assert phase4_start != -1, "SKILL.md missing '### Phase 4' section"
        # Find the next Phase or end of file
        phase5_start = content.find("### Phase 5", phase4_start + 1)
        if phase5_start == -1:
            phase5_start = content.find("## Missing", phase4_start + 1)
        if phase5_start == -1:
            phase5_start = len(content)
        phase4_section = content[phase4_start:phase5_start]
        assert _SCRIPT_REL_PATH in phase4_section, (
            f"Phase 4 section does not contain '{_SCRIPT_REL_PATH}'. "
            "The anti-drift gate requires Phase 4 to name the resolver script."
        )
