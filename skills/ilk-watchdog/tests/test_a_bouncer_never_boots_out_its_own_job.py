"""Red-first tests for the self-bootout guard in bounce_daemons.sh.

Covers AC-1..AC-3 from sub-plan a-bouncer-never-boots-out-its-own-job:

  AC-1  (refuses):  state file pid is in the bouncer's own process group
                     → exit 2, "refused:" + "process group", no bootout.
  AC-2  (control):  state file pid is in a DIFFERENT process group
                     → bounces normally (exit 1, bootout logged).
  AC-3  (no pid):   state file has no "pid" key
                     → bounces as today (exit 1, bootout logged).

Drives the real bounce_daemons.sh against the fake launchctl from
test_bounce_daemons.py.  Never invokes the real launchctl.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest

# Reuse helpers from the existing bounce test suite.
from test_bounce_daemons import (
    _BOUNCE_SH,
    _read_launchctl_log,
    _run_bounce,
)


# ---------------------------------------------------------------------------
# AC-1: refuses to boot out a daemon whose pid is in the bouncer's group
# ---------------------------------------------------------------------------

class TestSelfBootoutRefuses:
    """AC-1: bouncer inside the daemon's process group refuses with exit 2."""

    @pytest.mark.xfail(
        strict=True,
        reason="bouncer still boots out its own group",
    )
    def test_refuses_same_process_group(self, tmp_path):
        """pid in the bouncer's own process group → refuse, no bootout.

        The bouncer is a subprocess of pytest, so os.getpid() (pytest's pid)
        is in the same process group.  The guard must detect this and refuse.
        """
        # pytest's own pid is in the same process group as the bouncer subprocess.
        my_pid = os.getpid()
        state = {"pid": my_pid, "started_at": "2026-10-07T00:00:00Z", "toolkit_head": "OLD"}
        result = _run_bounce(
            tmp_path,
            state=state,
            head_sha="NEW",
        )
        assert result.returncode == 2, (
            f"expected exit 2 (refused), got {result.returncode}"
            f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "refused:" in combined, (
            f"stdout must contain 'refused:' — got:\n{result.stdout}"
        )
        assert "process group" in combined, (
            f"stdout must contain 'process group' — got:\n{result.stdout}"
        )
        launchctl_args = _read_launchctl_log(tmp_path)
        bootout_lines = [l for l in launchctl_args if "bootout" in l]
        assert len(bootout_lines) == 0, (
            f"refused bounce must not call bootout — got: {launchctl_args}"
        )


# ---------------------------------------------------------------------------
# AC-2: bounces normally when pid is in a different process group
# ---------------------------------------------------------------------------

class TestSelfBootoutBouncesDifferentGroup:
    """AC-2: state file pid in a different process group → bounces."""

    def test_bounces_different_process_group(self, tmp_path):
        """pid from a process in a different session/group → bounce as normal."""
        # Start a sleep in its own session so it's in a different process group.
        sleep_proc = subprocess.Popen(
            ["sleep", "30"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            state = {
                "pid": sleep_proc.pid,
                "started_at": "2026-10-07T00:00:00Z",
                "toolkit_head": "OLD",
            }
            result = _run_bounce(
                tmp_path,
                state=state,
                head_sha="NEW",
            )
            assert result.returncode == 1, (
                f"expected exit 1 (bounced), got {result.returncode}"
                f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
            launchctl_args = _read_launchctl_log(tmp_path)
            bootout_lines = [l for l in launchctl_args if "bootout" in l]
            bootstrap_lines = [l for l in launchctl_args if "bootstrap" in l]
            assert len(bootout_lines) > 0, (
                f"expected bootout in launchctl log — got: {launchctl_args}"
            )
            assert len(bootstrap_lines) > 0, (
                f"expected bootstrap in launchctl log — got: {launchctl_args}"
            )
        finally:
            try:
                os.kill(sleep_proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            sleep_proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# AC-3: bounces when state file has no "pid" key
# ---------------------------------------------------------------------------

class TestSelfBootoutNoPid:
    """AC-3: state file without "pid" → bounces as today."""

    def test_bounces_when_no_pid_in_state(self, tmp_path):
        """State file missing 'pid' → bounce (same as existing behaviour)."""
        state = {"started_at": "2026-10-07T00:00:00Z", "toolkit_head": "OLD"}
        result = _run_bounce(
            tmp_path,
            state=state,
            head_sha="NEW",
        )
        assert result.returncode == 1, (
            f"expected exit 1 (bounced), got {result.returncode}"
            f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        launchctl_args = _read_launchctl_log(tmp_path)
        bootout_lines = [l for l in launchctl_args if "bootout" in l]
        assert len(bootout_lines) > 0, (
            f"expected bootout in launchctl log — got: {launchctl_args}"
        )