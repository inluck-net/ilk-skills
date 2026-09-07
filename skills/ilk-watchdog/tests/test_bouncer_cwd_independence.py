"""Test: bounce_daemons.sh resolves the repo from its own location, not $PWD.

Measured failure (2026-09-07, rezmac): the identical script reports `fresh`
from the clone and `stale (recorded 0231718..., HEAD unknown)` from `$HOME`,
because ssh lands in `$HOME`.  v0.9.87 works around this by `cd`-ing inside
the ssh invocation; this test makes the workaround unnecessary.

Uses the ``scheduler_sandbox`` fixture (conftest.py:395) which pins HOME,
ILK_DATA_HOME **and** ILK_SKILL_HOME.  Pinning only HOME is a known trap
that makes these tests read the operator's real data home.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"


def _write_fake_launchctl(bin_dir: Path, log_path: Path) -> Path:
    """Create a minimal fake launchctl that logs argv and exits 0."""
    fake = bin_dir / "launchctl"
    fake.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$@" >> "{log_path}"
            exit 0
        """),
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def test_bouncer_resolves_repo_from_script_location(tmp_path, scheduler_sandbox):
    """Running bounce_daemons.sh from $HOME must not report 'HEAD unknown'.

    The script must resolve the repo from its own path (walking up to .git),
    not from $PWD.  This is the measured failure: ssh lands in $HOME, the
    script runs there, and ``git rev-parse HEAD`` fails because $HOME is not
    a repo.
    """
    sandbox = scheduler_sandbox
    home = sandbox.root
    ilk_data = home / ".ilk-data"
    ilk_data.mkdir(parents=True, exist_ok=True)

    # Write a state file with a known toolkit_head.
    recorded_head = "abc123def456"
    state_file = ilk_data / "scheduler.state.json"
    state_file.write_text(
        json.dumps({
            "pid": 12345,
            "started_at": "2026-09-08T10:00:00Z",
            "toolkit_head": recorded_head,
        }),
        encoding="utf-8",
    )

    # Fake binaries — put them on PATH ahead of everything.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    launchctl_log = tmp_path / "launchctl.log"
    launchctl_log.write_text("", encoding="utf-8")
    _write_fake_launchctl(bin_dir, launchctl_log)

    # Fake plist so the daemon appears loaded.
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    (plist_dir / "net.inluck.ilk.scheduler.plist").write_text(
        "<plist><!-- stub --></plist>", encoding="utf-8",
    )

    # Build env: sandbox.env already pins HOME, ILK_DATA_HOME, ILK_SKILL_HOME.
    # Add fake launchctl on PATH, allow foreign HOME, and set daemon loaded.
    env = {
        **sandbox.env,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LAUNCHCTL_LOG": str(launchctl_log),
        "ILK_BOUNCE_PLATFORM": "Darwin",
        "ILK_BOUNCE_DAEMON_LOADED": "1",
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }

    # Run the bouncer from $HOME — the measured failure scenario.
    result = subprocess.run(
        ["bash", str(_BOUNCE_SH)],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    combined = (result.stdout + result.stderr).lower()

    # The critical assertion: must NOT report 'HEAD unknown'.
    assert "head unknown" not in combined, (
        f"bouncer reported 'HEAD unknown' when run from $HOME — "
        f"it resolved the repo from $PWD instead of its own location. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # Must NOT report 'stale' with 'HEAD unknown' — the measured failure was:
    #   stale (recorded 0231718..., HEAD unknown)
    # Either fresh or stale-with-real-HEAD is acceptable; 'HEAD unknown' is not.
    assert "head unknown" not in combined, (
        f"bouncer resolved HEAD as 'unknown' from $HOME: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
