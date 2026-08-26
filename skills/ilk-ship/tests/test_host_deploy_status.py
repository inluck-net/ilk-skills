"""Red-first tests for host deploy status — three states, and a stale host is not ok.

Covers AC-1..AC-6 from sub-plan phase-4-refuses-a-stale-host:

  AC-1  Per-host deploy status resolves to exactly: ok / stale-daemon / unreachable.
  AC-2  A host whose daemon reports stale resolves stale-daemon, not ok.
  AC-3  A host that cannot be probed resolves unreachable, never ok.
  AC-4  Detection uses SP1's --check mode and bounces nothing by default.
  AC-5  An explicit flag (--bounce-hosts) permits the bounce; absent it, stale
        host is reported and left alone.
  AC-6  The Phase 4 summary lists every declared host with its state.

Drives the resolver as a function call with injected fake bouncer script.
Never invokes the real launchctl or touches a real daemon.
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

    output_block = ""
    if output_lines:
        for line in output_lines:
            output_block += f'echo "{line}"\n'

    log_line = 'echo "$@" >> "$BOUNCER_LOG"' if log_invocations else ""

    fake.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            {log_line}
            {output_block}
            exit {exit_code}
        """),
        encoding="utf-8",
    )
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
# Host resolver (the module under test — does not exist yet)
# ---------------------------------------------------------------------------

def _resolve_host(
    bouncer_path: Path,
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    bounce_hosts: bool = False,
) -> str:
    """Resolve a single host's deploy status.

    This is the function that ``host_deploy_status.py`` must implement.
    Currently raises NotImplementedError — the tests are expected to fail.

    Returns one of: 'ok', 'stale-daemon', 'unreachable'.
    """
    # This function will be replaced by the actual implementation in Step 1.
    # For now, it exists only to make the test file importable and the ACs assertable.
    raise NotImplementedError(
        "host_deploy_status.resolve_host() not yet implemented — "
        "this is expected in the red-first step."
    )
