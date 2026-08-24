"""Tests for the macOS scheduler auto-start installer.

Validates plist generation + idempotent uninstall WITHOUT mutating the real
per-user launchd domain (ILK_AUTOSTART_NO_LOAD=1 skips the launchctl calls).
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALLER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "install-scheduler-autostart.sh"
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
LABEL = "net.inluck.ilk.scheduler"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS LaunchAgent installer")


def _run(args, home: Path):
    env = {**os.environ, "HOME": str(home), "ILK_AUTOSTART_NO_LOAD": "1"}
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, timeout=30, env=env, encoding="utf-8",
    )


def test_installer_exists_and_executable():
    assert INSTALLER.exists()
    assert os.access(INSTALLER, os.X_OK), "installer should be executable"


def test_install_writes_valid_plist(tmp_path):
    res = _run([], tmp_path)
    assert res.returncode == 0, res.stderr
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plist.is_file(), f"plist not written: {res.stdout}\n{res.stderr}"

    with plist.open("rb") as fh:
        data = plistlib.load(fh)  # also asserts it's well-formed XML plist

    assert data["Label"] == LABEL
    assert data["RunAtLoad"] is True
    # KeepAlive must be {SuccessfulExit: false} — restart on crash only,
    # not on clean exit-0 (lock contention). See sub-plan
    # 2026-06-26-scheduler-autostart-durability for the root-cause.
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    # Runs scheduler.sh as the daemon (NOT --detach: launchd owns the process).
    argv = data["ProgramArguments"]
    assert argv[0] == "/bin/bash"
    assert str(SCHEDULER) in argv
    assert "--detach" not in argv
    # PATH must cover Homebrew + user-local bin so gh / claude / screen resolve.
    agent_path = data["EnvironmentVariables"]["PATH"]
    assert "/opt/homebrew/bin" in agent_path
    assert str(tmp_path / ".local" / "bin") in agent_path


def test_install_is_idempotent(tmp_path):
    first = _run([], tmp_path)
    second = _run([], tmp_path)
    assert first.returncode == 0 and second.returncode == 0
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plist.is_file()


def test_uninstall_removes_plist(tmp_path):
    _run([], tmp_path)
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plist.is_file()
    res = _run(["--uninstall"], tmp_path)
    assert res.returncode == 0, res.stderr
    assert not plist.exists(), "uninstall should remove the plist"


def test_plist_passes_plutil_lint(tmp_path):
    if not shutil.which("plutil"):
        pytest.skip("plutil not available")
    _run([], tmp_path)
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    res = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr


def _run_real(args, home: Path, timeout: int = 120):
    """Run the installer WITHOUT ILK_AUTOSTART_NO_LOAD (touches real launchd)."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("ILK_AUTOSTART_NO_LOAD", None)
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, timeout=timeout, env=env, encoding="utf-8",
    )


def _kill_scheduler_daemon():
    """Best-effort kill of any lingering scheduler daemon started by launchd."""
    pidfile = Path.home() / ".ilk-data" / "scheduler.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, 9)
        except (ValueError, OSError):
            pass


def _read_scheduler_pid(home: Path) -> int | None:
    """Read the PID from the scheduler pidfile under the given HOME."""
    pidfile = home / ".ilk-data" / "scheduler.pid"
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except (ValueError, OSError):
        return None


# Judgment call, 2026-08-24: timeout(240), not a raise of the global bound.
# This test's own declared waits are 30x1s (pidfile) + 15x2s (launchd restart)
# = 60s of sleeps before any install or launchctl overhead, so it cannot fit
# pytest.ini's --timeout=60. Measured: it hit "Timeout (>60.0s)" at line 172
# on the first whole-suite run after the bound landed. 240 leaves ~4x headroom
# over the declared sleeps. Wrong if launchd restart latency ever exceeds
# ~3 min, in which case the test's own range(15) budget is the thing to fix.
@pytest.mark.timeout(240)
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launchctl required")
def test_restart_durability_keeps_agent_loaded(tmp_path):
    """AC-4: crash-kill → launchd restarts → agent still loaded.

    Exercises KeepAlive={SuccessfulExit:false}: after a SIGKILL crash,
    launchd must restart the daemon (crash = non-zero exit).  Verifies
    the label remains loaded and a new PID appears.  Skips on CI boxes
    that lack a GUI login domain.
    """
    uid = os.getuid()
    domain = f"gui/{uid}"

    # Pre-flight: can we reach the gui domain at all?  Skip if not.
    try:
        probe = subprocess.run(
            ["launchctl", "print", f"{domain}/{LABEL}"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
    except FileNotFoundError:
        pytest.skip("launchctl not available")
    # If the label is somehow already loaded, bail to avoid clobbering.
    if probe.returncode == 0:
        pytest.skip(f"{LABEL} already loaded — skipping to avoid disruption")

    # Install for real (no ILK_AUTOSTART_NO_LOAD).
    res = _run_real([], tmp_path)
    assert res.returncode == 0, f"install failed: {res.stdout}\n{res.stderr}"

    try:
        # Wait for the daemon to start and write its pidfile.
        pid1 = None
        for _ in range(30):
            pid1 = _read_scheduler_pid(tmp_path)
            if pid1 is not None:
                break
            import time
            time.sleep(1)
        assert pid1 is not None, "daemon did not write pidfile within 30s"

        # Verify the daemon is alive.
        assert os.kill(pid1, 0) is None, f"daemon PID {pid1} not alive"

        # SIGKILL the daemon — simulates a crash (non-zero exit).
        os.kill(pid1, 9)

        # Wait for launchd to restart the daemon (KeepAlive restarts on crash).
        # The new daemon should write a new pidfile with a different PID.
        pid2 = None
        for _ in range(15):
            import time
            time.sleep(2)
            pid2 = _read_scheduler_pid(tmp_path)
            if pid2 is not None and pid2 != pid1:
                break
            # pidfile might still be the dead PID; wait for it to change.
            pid2 = None

        assert pid2 is not None, (
            f"launchd did not restart daemon after SIGKILL — "
            f"KeepAlive={{SuccessfulExit:false}} not working. "
            f"Old PID was {pid1}."
        )

        # Assert label is still present in launchd.
        info = subprocess.run(
            ["launchctl", "print", f"{domain}/{LABEL}"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
        assert info.returncode == 0, f"label disappeared after crash: {info.stderr}"

    finally:
        # Always uninstall to clean up, then kill any lingering daemon.
        _run_real(["--uninstall"], tmp_path)
        _kill_scheduler_daemon()
