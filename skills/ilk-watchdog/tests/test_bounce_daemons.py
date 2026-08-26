"""Red-first tests for bounce_daemons.sh — one implementation, three outcomes.

Covers AC-1..AC-9 from sub-plan the-bouncer-is-one-implementation:

  AC-1  Script exists, is executable, runs with no arguments.
  AC-2  Daemon set is declared data, not hard-coded in bounce logic.
  AC-3  State file present, toolkit_head == HEAD → no bounce.
  AC-4  toolkit_head != HEAD → bounce.
  AC-5  State file absent / empty / non-JSON / missing key → stale → bounce.
  AC-6  Bounce uses bootout+bootstrap, never kill.
  AC-7  --check mode reports staleness, bounces nothing.
  AC-8  Daemon not loaded or plist missing → reported as unreachable.
  AC-9  Exit status distinguishes: nothing-to-do / bounced / could-not-reach.

Drives the script as a subprocess with an injected fake ``launchctl`` earlier
on PATH that logs its argv to a file.  Never invokes the real ``launchctl``:
the root conftest host guard denies it, and a test that bounces the
operator's real scheduler is the defect this toolkit spent 2026-08-26 removing.
Points HOME at tmp_path so the state file read is hermetic.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from conftest import HostMutationBlocked

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fake_launchctl(tmp_path: Path) -> Path:
    """Create a fake launchctl that logs argv and exits 0.

    Returns the path to the fake binary.
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


def _write_fake_git(tmp_path: Path, head_sha: str) -> Path:
    """Create a fake git that returns ``head_sha`` for rev-parse HEAD.

    Handles ``git [-C <path>] rev-parse HEAD`` — the real script passes
    ``-C <toolkit_path>`` so we must skip those args.

    Returns the path to the fake binary.
    """
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Skip -C <path> if present.
            if [[ "$1" == "-C" ]]; then
                shift 2
            fi
            if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then
                echo "{head_sha}"
                exit 0
            fi
            # Pass through for other subcommands (not needed yet).
            exit 1
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _run_bounce(
    tmp_path: Path,
    *,
    state: dict | str | None = None,
    head_sha: str = "abc123",
    extra_args: list[str] | None = None,
    platform: str = "Darwin",
    daemon_loaded: bool = True,
    plist_exists: bool = True,
) -> subprocess.CompletedProcess:
    """Set up the hermetic environment and run bounce_daemons.sh.

    ``state`` controls the state file:
      - ``None``        → no state file (absent)
      - ``dict``        → JSON-serialized into the state file
      - ``"empty"``     → file exists but is empty
      - ``"non_json"``  → file exists with non-JSON content

    Returns the CompletedProcess so callers can inspect stdout, stderr, exit.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ilk_data = home / ".ilk-data"
    ilk_data.mkdir(exist_ok=True)

    # Write state file
    state_file = ilk_data / "scheduler.state.json"
    if state == "empty":
        state_file.write_text("", encoding="utf-8")
    elif state == "non_json":
        state_file.write_text("NOT JSON {{{", encoding="utf-8")
    elif state is not None:
        state_file.write_text(json.dumps(state), encoding="utf-8")

    # Fake binaries
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_launchctl = _write_fake_launchctl(tmp_path)
    fake_git = _write_fake_git(tmp_path, head_sha)

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
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LAUNCHCTL_LOG": str(launchctl_log),
        "ILK_BOUNCE_PLATFORM": platform,
        "ILK_BOUNCE_DAEMON_LOADED": "1" if daemon_loaded else "0",
    }

    cmd = ["bash", str(_BOUNCE_SH)]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def _read_launchctl_log(tmp_path: Path) -> list[str]:
    """Read the lines logged by the fake launchctl."""
    log = tmp_path / "launchctl.log"
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# AC-1: Script exists, is executable, runs with no arguments
# ---------------------------------------------------------------------------

class TestAc1ScriptExists:
    """AC-1: bounce_daemons.sh exists, is executable, runs with no args."""

    def test_file_exists(self):
        assert _BOUNCE_SH.exists(), f"bounce_daemons.sh not found at {_BOUNCE_SH}"

    def test_is_executable(self):
        st = _BOUNCE_SH.stat()
        assert st.st_mode & stat.S_IXUSR, "bounce_daemons.sh is not executable (owner)"
        assert st.st_mode & stat.S_IXGRP, "bounce_daemons.sh is not executable (group)"
        assert st.st_mode & stat.S_IXOTH, "bounce_daemons.sh is not executable (other)"

    def test_runs_with_no_args(self, tmp_path):
        """Script must not crash when invoked with zero arguments."""
        result = _run_bounce(tmp_path, state=None)
        # It may exit non-zero (stale), but it must not crash with a signal.
        assert result.returncode in (0, 1, 2), (
            f"Unexpected exit code {result.returncode}: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-3, AC-4, AC-5: Staleness decision
# ---------------------------------------------------------------------------

class TestAc3FreshState:
    """AC-3: toolkit_head == HEAD → no bounce, says so."""

    def test_fresh_state_no_bounce(self, tmp_path):
        head = "abc123def456"
        state = {"pid": 12345, "started_at": "2026-08-26T10:00:00Z", "toolkit_head": head}
        result = _run_bounce(tmp_path, state=state, head_sha=head)
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) == 0, (
            f"Expected no launchctl calls for fresh state, got: {launchctl_args}"
        )
        assert "no bounce" in result.stdout.lower() or "fresh" in result.stdout.lower(), (
            f"Script did not report freshness: stdout={result.stdout!r}"
        )


class TestAc4StaleState:
    """AC-4: toolkit_head != HEAD → bounce."""

    def test_stale_head_triggers_bounce(self, tmp_path):
        state = {"pid": 12345, "started_at": "2026-08-26T10:00:00Z", "toolkit_head": "old_head"}
        result = _run_bounce(tmp_path, state=state, head_sha="new_head")
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) > 0, "Expected launchctl calls for stale state, got none"
        # Must contain bootout and bootstrap
        all_args = " ".join(launchctl_args)
        assert "bootout" in all_args, f"Expected bootout in launchctl calls: {launchctl_args}"
        assert "bootstrap" in all_args, f"Expected bootstrap in launchctl calls: {launchctl_args}"


class TestAc5AbsentState:
    """AC-5: State file absent / empty / non-JSON / missing key → stale → bounce."""

    def test_absent_state_is_stale(self, tmp_path):
        """No state file at all → must bounce."""
        result = _run_bounce(tmp_path, state=None, head_sha="abc123")
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) > 0, (
            "Absent state file should trigger bounce, got no launchctl calls"
        )

    def test_empty_state_is_stale(self, tmp_path):
        """Empty file → must bounce."""
        result = _run_bounce(tmp_path, state="empty", head_sha="abc123")
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) > 0, (
            "Empty state file should trigger bounce"
        )

    def test_non_json_state_is_stale(self, tmp_path):
        """Non-JSON content → must bounce."""
        result = _run_bounce(tmp_path, state="non_json", head_sha="abc123")
        args = _read_launchctl_log(tmp_path)
        assert len(args) > 0, "Non-JSON state should trigger bounce"

    def test_missing_toolkit_head_key_is_stale(self, tmp_path):
        """State file present but missing toolkit_head → must bounce."""
        state = {"pid": 12345, "started_at": "2026-08-26T10:00:00Z"}  # no toolkit_head
        result = _run_bounce(tmp_path, state=state, head_sha="abc123")
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) > 0, (
            "Missing toolkit_head should trigger bounce"
        )


# ---------------------------------------------------------------------------
# AC-6: Bounce uses bootout+bootstrap, never kill
# ---------------------------------------------------------------------------

class TestAc6BounceMethod:
    """AC-6: bounce uses bootout+bootstrap, never kill."""

    def test_bounce_uses_bootout_bootstrap(self, tmp_path):
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(tmp_path, state=state, head_sha="new")
        args = _read_launchctl_log(tmp_path)
        all_args = " ".join(args)
        assert "bootout" in all_args, f"Expected bootout: {args}"
        assert "bootstrap" in all_args, f"Expected bootstrap: {args}"

    def test_bounce_never_calls_kill(self, tmp_path):
        """The script must not contain 'kill' as a bounce mechanism.

        This asserts on the script source — a kill is respawned by KeepAlive
        holding the same stale code, so an outcome-only assertion cannot tell
        the two apart.
        """
        source = _BOUNCE_SH.read_text(encoding="utf-8")
        # Allow 'kill' in comments, but not as a command.
        # Strip comments then check.
        lines = source.splitlines()
        code_lines = [line.split("#", 1)[0].strip() for line in lines]
        code_text = "\n".join(code_lines)
        # 'kill' should not appear as a standalone command in bounce logic.
        # Allow 'kill' as part of other words (e.g. 'skill').
        import re
        kill_commands = re.findall(r'\bkill\b', code_text)
        assert len(kill_commands) == 0, (
            f"bounce_daemons.sh contains 'kill' command — must use bootout/bootstrap"
        )


# ---------------------------------------------------------------------------
# AC-2: Daemon set is declared data
# ---------------------------------------------------------------------------

class TestAc2DaemonSet:
    """AC-2: daemon set is declared data, selected by platform."""

    def test_daemon_set_is_platform_declared(self, tmp_path):
        """On macOS (Darwin), only scheduler should be bounced (no tray)."""
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(
            tmp_path, state=state, head_sha="new", platform="Darwin",
        )
        args = _read_launchctl_log(tmp_path)
        # On macOS: scheduler only.  Each daemon gets bootout + bootstrap = 2 calls.
        # Scheduler label should appear; tray label should NOT.
        all_args = " ".join(args)
        assert "scheduler" in all_args.lower() or "ilk-scheduler" in all_args.lower(), (
            f"Expected scheduler in launchctl calls: {args}"
        )
        assert "tray" not in all_args.lower(), (
            f"Tray should not be bounced on macOS: {args}"
        )

    def test_daemon_set_adding_daemon_is_one_row_edit(self):
        """The daemon table in the script should be easy to extend.

        This is a structural check: the script should contain a data structure
        mapping platform to daemon entries, not a per-daemon if/else chain.
        """
        source = _BOUNCE_SH.read_text(encoding="utf-8")
        # Look for a declarative pattern: array/associative-array of daemons.
        # A hard-coded per-daemon bounce would have repeated 'bootout' blocks.
        # A table-driven approach has one bounce loop over a list.
        lower = source.lower()
        # At minimum, the script should have a section that declares daemons.
        assert "scheduler" in lower, "Script must reference 'scheduler' daemon"
        # The daemon set should be near a data structure, not inline in logic.
        # This is a weak check; the red-first tests will fail until the script exists.


# ---------------------------------------------------------------------------
# AC-7: --check mode
# ---------------------------------------------------------------------------

class TestAc7CheckMode:
    """AC-7: --check reports staleness, bounces nothing."""

    def test_check_mode_reports_without_bouncing(self, tmp_path):
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(
            tmp_path, state=state, head_sha="new", extra_args=["--check"],
        )
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) == 0, (
            f"--check must not call launchctl, got: {launchctl_args}"
        )
        # Must report staleness
        assert "stale" in result.stdout.lower() or "would bounce" in result.stdout.lower(), (
            f"--check should report staleness: stdout={result.stdout!r}"
        )

    def test_check_mode_on_fresh_state(self, tmp_path):
        head = "abc123"
        state = {"pid": 1, "started_at": "x", "toolkit_head": head}
        result = _run_bounce(
            tmp_path, state=state, head_sha=head, extra_args=["--check"],
        )
        launchctl_args = _read_launchctl_log(tmp_path)
        assert len(launchctl_args) == 0, (
            f"--check on fresh state must not call launchctl: {launchctl_args}"
        )


# ---------------------------------------------------------------------------
# AC-8: Per-daemon reporting — unreachable
# ---------------------------------------------------------------------------

class TestAc8UnreachableDaemon:
    """AC-8: daemon not loaded or plist missing → reported as unreachable."""

    def test_unloaded_daemon_reported_unreachable(self, tmp_path):
        """When daemon_loaded=False, the daemon should be reported unreachable."""
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(
            tmp_path, state=state, head_sha="new", daemon_loaded=False,
        )
        combined = (result.stdout + result.stderr).lower()
        assert "unreachable" in combined, (
            f"Expected 'unreachable' for unloaded daemon: stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_missing_plist_reported_unreachable(self, tmp_path):
        """When plist_exists=False, the daemon should be reported unreachable."""
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(
            tmp_path, state=state, head_sha="new", plist_exists=False,
        )
        combined = (result.stdout + result.stderr).lower()
        assert "unreachable" in combined, (
            f"Expected 'unreachable' for missing plist: stdout={result.stdout!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# AC-9: Exit status
# ---------------------------------------------------------------------------

class TestAc9ExitStatus:
    """AC-9: exit status distinguishes nothing-to-do / bounced / could-not-reach."""

    EXIT_NOTHING_TO_DO = 0
    EXIT_BOUNCED = 1
    EXIT_COULD_NOT_REACH = 2

    def test_exit_nothing_to_do(self, tmp_path):
        """Fresh state → exit 0 (nothing to do)."""
        head = "abc123"
        state = {"pid": 1, "started_at": "x", "toolkit_head": head}
        result = _run_bounce(tmp_path, state=state, head_sha=head)
        assert result.returncode == self.EXIT_NOTHING_TO_DO, (
            f"Expected exit {self.EXIT_NOTHING_TO_DO} for fresh state, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_exit_bounced(self, tmp_path):
        """Stale state → exit 1 (bounced)."""
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(tmp_path, state=state, head_sha="new")
        assert result.returncode == self.EXIT_BOUNCED, (
            f"Expected exit {self.EXIT_BOUNCED} for stale state, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_exit_could_not_reach(self, tmp_path):
        """Daemon not loaded → exit 2 (could not reach)."""
        state = {"pid": 1, "started_at": "x", "toolkit_head": "old"}
        result = _run_bounce(
            tmp_path, state=state, head_sha="new", daemon_loaded=False,
        )
        assert result.returncode == self.EXIT_COULD_NOT_REACH, (
            f"Expected exit {self.EXIT_COULD_NOT_REACH} for unreachable daemon, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Foreign HOME refusal (AC-1, AC-2, AC-5) — SP1 of MASTER-2026-08-26d
# ---------------------------------------------------------------------------

class TestForeignHomeRefusal:
    """A bounce must refuse under a foreign HOME unless explicitly overridden.

    AC-1: exit 2, message contains 'foreign HOME'.
    AC-2: refusal happens before any launchctl call (log is empty).
    AC-5: --check mode is also refused under a foreign HOME.
    """

    def test_foreign_home_refuses_exit2(self, tmp_path):
        """AC-1: foreign HOME → exit 2, stdout names 'foreign HOME'."""
        result = _run_bounce(tmp_path, state=None, head_sha="abc123")
        assert result.returncode == 2, (
            f"Expected exit 2 for foreign HOME, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        combined = (result.stdout + result.stderr).lower()
        assert "foreign home" in combined, (
            f"Expected 'foreign HOME' in output: stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_foreign_home_refuses_before_launchctl(self, tmp_path):
        """AC-2: refusal is before any launchctl call — log must be empty."""
        result = _run_bounce(tmp_path, state=None, head_sha="abc123")
        log = _read_launchctl_log(tmp_path)
        assert log == [], (
            f"Foreign HOME refusal must precede launchctl calls, but log has {len(log)} entry/entries: {log}"
        )

    def test_foreign_home_check_mode_also_refuses(self, tmp_path):
        """AC-5: --check under a foreign HOME still refuses (exit 2)."""
        result = _run_bounce(
            tmp_path, state=None, head_sha="abc123", extra_args=["--check"],
        )
        assert result.returncode == 2, (
            f"--check under foreign HOME must refuse, got exit {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Guard sees launchctl through a spawned shell (AC-4)
# ---------------------------------------------------------------------------

@pytest.mark.expects_blocked_host
def test_guard_catches_launchctl_through_bash(tmp_path):
    """AC-4: a test spawning 'bash -c launchctl ...' without allow_launchctl fails.

    The guard must catch launchctl reached through a spawned shell script,
    not only through a direct Python subprocess call.  This test is marked
    ``expects_blocked_host`` so its own guard recording is dropped.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", f"launchctl print gui/{os.getuid()}/x"],
            capture_output=True, text=True, timeout=5,
        )
        # If we get here, the guard did NOT catch launchctl through bash.
        # That's the gap this sub-plan exists to close.
        pytest.fail(
            "Guard did not block launchctl reached through bash — "
            "the host-mutation guard is blind to shell-spawned calls. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    except HostMutationBlocked:
        # Guard caught it — this is the green path.
        pass
