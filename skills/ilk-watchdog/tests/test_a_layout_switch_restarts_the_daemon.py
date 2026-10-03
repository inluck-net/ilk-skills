"""Red-first tests for path-aware daemon freshness and layout-switch bounce.

Part of sub-plan a-layout-switch-restarts-the-daemon (step 0).

Covers AC-1..AC-4:

  AC-1  (xfail) State toolkit_head = HEAD, but recorded pid's command line runs
        /clone/…/scheduler.sh while plist names /home/.ilk/current/…/scheduler.sh:
        --check prints stale and names both paths.
  AC-2  (xfail) install.sh --layout release --apply with a fake plist and a fake
        launchctl invokes bootout + bootstrap exactly once.
  AC-3  (control) Same head, same path, live pid → fresh.
  AC-4  (control) install.sh --layout release (dry run) never calls launchctl.

Drives bounce_daemons.sh and install.sh as subprocesses with injected fakes.
Never invokes the real launchctl — the root conftest host guard denies it.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import textwrap
import stat
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"
_INSTALL_SH = _REPO_ROOT / "install.sh"

_SCHEDULER_LABEL = "net.inluck.ilk.scheduler"
_SCHEDULER_SCRIPT = "skills/ilk-watchdog/scripts/scheduler.sh"
# The "old" path the daemon was launched from (clone layout).
_CLONE_SCRIPT = "/Users/chad/Projects/github/inluck-net/ilk-skills/skills/ilk-watchdog/scripts/scheduler.sh"
# The "new" path the plist names (release layout).
_RELEASE_SCRIPT = "/Users/chad/.ilk/current/skills/ilk-watchdog/scripts/scheduler.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fake_launchctl(tmp_path: Path) -> Path:
    """Create a fake launchctl that logs argv and exits per-verb RC.

    Reads exit codes from environment variables:
      ILK_FAKE_LAUNCHCTL_BOOTSTRAP_RC  — exit code for bootstrap verb (default 0)
      ILK_FAKE_LAUNCHCTL_PRINT_RC      — exit code for print verb (default 0)

    Returns the path to the fake binary.
    """
    fake = tmp_path / "bin" / "launchctl"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "$@" >> "$LAUNCHCTL_LOG"
            verb="$1"
            case "$verb" in
                bootstrap)
                    exit "${ILK_FAKE_LAUNCHCTL_BOOTSTRAP_RC:-0}"
                    ;;
                print)
                    exit "${ILK_FAKE_LAUNCHCTL_PRINT_RC:-0}"
                    ;;
                bootout)
                    exit 0
                    ;;
                *)
                    exit 0
                    ;;
            esac
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _write_fake_git(tmp_path: Path, head_sha: str) -> Path:
    """Create a fake git that returns head_sha for rev-parse HEAD."""
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
            if [[ "$1" == "rev-parse" && "$2" == "--show-toplevel" ]]; then
                echo "/fake/repo"
                exit 0
            fi
            if [[ "$1" == "status" ]]; then
                echo ""
                exit 0
            fi
            exit 1
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _write_fake_ps(tmp_path: Path, pid: int, command_line: str) -> Path:
    """Create a fake ps that returns command_line for the given pid.

    Returns the path to the fake binary.
    """
    fake = tmp_path / "bin" / "ps"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Parse -p <pid> -o command=
            target_pid=""
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    -p) shift; target_pid="$1" ;;
                    *) ;;
                esac
                shift
            done
            if [[ "$target_pid" == "{pid}" ]]; then
                echo "{command_line}"
                exit 0
            fi
            echo ""
            exit 0
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _write_scheduler_plist(path: Path, script_path: str) -> None:
    """Write a scheduler plist with ProgramArguments naming script_path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": _SCHEDULER_LABEL,
        "ProgramArguments": ["/bin/bash", script_path, "--poll-min", "5"],
        "RunAtLoad": True,
    }
    with open(path, "wb") as f:
        plistlib.dump(plist, f)


def _run_bounce(
    tmp_path: Path,
    *,
    state: dict | None = None,
    head_sha: str = "abc123",
    pid: int | None = None,
    pid_command: str | None = None,
    plist_script: str = _RELEASE_SCRIPT,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Set up the hermetic environment and run bounce_daemons.sh."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ilk_data = home / ".ilk-data"
    ilk_data.mkdir(exist_ok=True)

    # Write state file
    state_file = ilk_data / "scheduler.state.json"
    if state is not None:
        state_file.write_text(json.dumps(state), encoding="utf-8")

    # Fake binaries
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_launchctl(tmp_path)
    _write_fake_git(tmp_path, head_sha)
    if pid is not None and pid_command is not None:
        _write_fake_ps(tmp_path, pid, pid_command)

    # Launchctl log
    launchctl_log = tmp_path / "launchctl.log"
    launchctl_log.write_text("", encoding="utf-8")

    # Plist
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    _write_scheduler_plist(
        plist_dir / "net.inluck.ilk.scheduler.plist",
        plist_script,
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ILK_DATA_HOME": str(ilk_data),
        "LAUNCHCTL_LOG": str(launchctl_log),
        "ILK_BOUNCE_PLATFORM": "Darwin",
        "ILK_BOUNCE_DAEMON_LOADED": "1",
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
        "ILK_BOUNCE_ALLOW_DURING_RUN": "1",
    }

    cmd = ["bash", str(_BOUNCE_SH)]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )


def _read_launchctl_log(tmp_path: Path) -> list[str]:
    """Read the lines logged by the fake launchctl."""
    log = tmp_path / "launchctl.log"
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# AC-1: pid command line differs from plist script path → stale
# ---------------------------------------------------------------------------

class TestPidPathDiffersIsStale:
    """AC-1: recorded pid runs /clone/…/scheduler.sh while plist names
    /home/.ilk/current/…/scheduler.sh → --check prints stale and names
    both paths."""

    def test_check_reports_stale_with_both_paths(self, tmp_path: Path) -> None:
        pid = 12345
        result = _run_bounce(
            tmp_path,
            state={"pid": pid, "started_at": "2026-10-03T12:00:00+0800", "toolkit_head": "abc123"},
            head_sha="abc123",
            pid=pid,
            pid_command=f"/bin/bash {_CLONE_SCRIPT} --poll-min 5",
            plist_script=_RELEASE_SCRIPT,
            extra_args=["--check"],
        )
        assert result.returncode == 0, f"expected exit 0 (--check), got {result.returncode}"
        assert "stale" in result.stdout
        assert _CLONE_SCRIPT in result.stdout or "clone" in result.stdout.lower()
        assert _RELEASE_SCRIPT in result.stdout or "current" in result.stdout.lower()


# ---------------------------------------------------------------------------
# AC-2: install.sh --layout release --apply bounces after rewiring plist
# ---------------------------------------------------------------------------

class TestInstallLayoutBounces:
    """AC-2: install.sh --layout release --apply with a fake plist and
    a fake launchctl invokes bootout + bootstrap exactly once."""

    def test_apply_bounces_once(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)

        # Seed a "current" symlink pointing at a release dir.
        releases_parent = home / ".ilk"
        releases_dir = releases_parent / "releases" / "v0.9.140"
        releases_dir.mkdir(parents=True)
        # Create skills/ skeleton in the release dir.
        skills_dir = releases_dir / "skills" / "ilk-watchdog" / "scripts"
        skills_dir.mkdir(parents=True)
        # Copy scheduler.sh into the release dir so the plist can reference it.
        # (The real script is not needed — the fake launchctl doesn't run it.)
        (skills_dir / "scheduler.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

        current_link = releases_parent / "current"
        current_link.symlink_to(releases_dir)

        # Write a plist naming the release path.
        plist_dir = home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "net.inluck.ilk.scheduler.plist"
        # Start with the clone path so --layout release changes it.
        _write_scheduler_plist(plist_path, "/clone/old/scheduler.sh")

        # Fake binaries
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        _write_fake_launchctl(tmp_path)

        launchctl_log = tmp_path / "launchctl.log"
        launchctl_log.write_text("", encoding="utf-8")

        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LAUNCHCTL_LOG": str(launchctl_log),
            "ILK_RELEASES_ROOT": str(releases_dir.parent),
            "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
            "ILK_BOUNCE_ALLOW_DURING_RUN": "1",
            "ILK_BOUNCE_PLATFORM": "Darwin",
            "ILK_BOUNCE_DAEMON_LOADED": "1",
            "ILK_DATA_HOME": str(home / ".ilk-data"),
        }

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--layout", "release", "--apply"],
            env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, f"install.sh failed: {result.stderr}"
        log_lines = _read_launchctl_log(tmp_path)
        bootstrap_count = sum(1 for line in log_lines if "bootstrap" in line)
        bootout_count = sum(1 for line in log_lines if "bootout" in line)
        assert bootout_count == 1, f"expected 1 bootout, got {bootout_count}: {log_lines}"
        assert bootstrap_count == 1, f"expected 1 bootstrap, got {bootstrap_count}: {log_lines}"


# ---------------------------------------------------------------------------
# AC-3 (control): same head, same path, live pid → fresh
# ---------------------------------------------------------------------------

class TestSamePathIsFresh:
    """AC-3: state toolkit_head == HEAD, recorded pid runs the SAME script
    path as the plist → --check reports fresh."""

    def test_fresh_when_paths_match(self, tmp_path: Path) -> None:
        pid = 12345
        result = _run_bounce(
            tmp_path,
            state={"pid": pid, "started_at": "2026-10-03T12:00:00+0800", "toolkit_head": "abc123"},
            head_sha="abc123",
            pid=pid,
            pid_command=f"/bin/bash {_RELEASE_SCRIPT} --poll-min 5",
            plist_script=_RELEASE_SCRIPT,
            extra_args=["--check"],
        )
        assert result.returncode == 0
        assert "fresh" in result.stdout


# ---------------------------------------------------------------------------
# AC-4 (control): install.sh --layout release (dry run) never calls launchctl
# ---------------------------------------------------------------------------

class TestDryRunNeverCallsLaunchctl:
    """AC-4: install.sh --layout release (no --apply) never invokes launchctl."""

    def test_dry_run_no_launchctl(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)

        releases_parent = home / ".ilk"
        releases_dir = releases_parent / "releases" / "v0.9.140"
        releases_dir.mkdir(parents=True)
        skills_dir = releases_dir / "skills" / "ilk-watchdog" / "scripts"
        skills_dir.mkdir(parents=True)
        (skills_dir / "scheduler.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

        current_link = releases_parent / "current"
        current_link.symlink_to(releases_dir)

        plist_dir = home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_path = plist_dir / "net.inluck.ilk.scheduler.plist"
        _write_scheduler_plist(plist_path, "/clone/old/scheduler.sh")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        _write_fake_launchctl(tmp_path)

        launchctl_log = tmp_path / "launchctl.log"
        launchctl_log.write_text("", encoding="utf-8")

        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LAUNCHCTL_LOG": str(launchctl_log),
            "ILK_RELEASES_ROOT": str(releases_dir.parent),
            "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
            "ILK_BOUNCE_ALLOW_DURING_RUN": "1",
        }

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--layout", "release"],
            env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, f"install.sh failed: {result.stderr}"
        log_lines = _read_launchctl_log(tmp_path)
        assert len(log_lines) == 0, f"dry run invoked launchctl: {log_lines}"