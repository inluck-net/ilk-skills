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
        capture_output=True, text=True, timeout=30, env=env,
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
    assert data["KeepAlive"] is True
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
    res = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
