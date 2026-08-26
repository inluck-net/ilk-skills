"""Tests for the scheduler health check (scheduler_health.sh).

Drives the check with a fake launchctl on PATH that records argv and can be
told the scheduler agent is present or absent.  Uses tmp HOME and pinned
ILK_DATA_HOME so nothing touches the real launchd domain.

Covers AC-1 through AC-6 from the sub-plan:
  AC-1: three states — loaded / absent / held — exit 0 / 1 / 2
  AC-2: absent + no hold → bootstrap and log
  AC-3: hold present → report held, zero bootstrap calls
  AC-4: loaded → report loaded, zero bootstrap calls
  AC-5: idempotent and bounded — two runs each produce one bootstrap
  AC-6: failed bootstrap → reported, not retried forever
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HEALTH_CHECK = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler_health.sh"
SCHEDULER_LABEL = "net.inluck.ilk.scheduler"
HEALTH_LABEL = "net.inluck.ilk.scheduler-health"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS LaunchAgent health check")


def _write_script(path: Path, content: str) -> None:
    """Write a script and make it executable."""
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_fake_launchctl(bin_dir: Path, *, present: bool = True, fail_bootstrap: bool = False) -> Path:
    """Create a fake launchctl that records invocations.

    Args:
        present: If True, `launchctl print` succeeds (agent loaded).
        fail_bootstrap: If True, `launchctl bootstrap` always fails.
    """
    recorder = bin_dir / "launchctl_invocations.txt"
    # The fake launchctl is a bash script that dispatches on subcommand.
    bootstrap_rc = "1" if fail_bootstrap else "0"
    print_rc = "0" if present else "1"
    script = f"""#!/usr/bin/env bash
RECORDER="{recorder}"
echo "$@" >> "$RECORDER"
case "$1" in
  print)
    exit {print_rc}
    ;;
  bootstrap)
    exit {bootstrap_rc}
    ;;
  bootout)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
    path = bin_dir / "launchctl"
    _write_script(path, script)
    return recorder


def _make_fake_bounce(bin_dir: Path, *, fail: bool = False) -> Path:
    """Create a fake bounce_daemons.sh for the bootstrap fallback path."""
    recorder = bin_dir / "bounce_invocations.txt"
    rc = "2" if fail else "1"
    script = f"""#!/usr/bin/env bash
echo "$@" >> "{recorder}"
exit {rc}
"""
    path = bin_dir / "bounce_daemons.sh"
    _write_script(path, script)
    return recorder


def _run_health_check(home: Path, ilk_data: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run scheduler_health.sh with controlled environment."""
    env = {
        **os.environ,
        "HOME": str(home),
        "ILK_DATA_HOME": str(ilk_data),
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(HEALTH_CHECK)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        encoding="utf-8",
    )


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def env(tmp_path):
    """Set up a controlled environment: tmp HOME, tmp ILK_DATA, bin on PATH."""
    home = tmp_path / "home"
    home.mkdir()
    ilk_data = tmp_path / "ilk-data"
    ilk_data.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Prepend fake bin to PATH so our launchctl wins.
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}:{original_path}"

    # Write the plist so the health check can find it.
    plist_dir = home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / f"{SCHEDULER_LABEL}.plist").write_text(
        '<?xml version="1.0"?>\n<plist version="1.0"><dict><key>Label</key>'
        f'<string>{SCHEDULER_LABEL}</string></dict></plist>\n',
        encoding="utf-8",
    )

    yield type("Env", (), {
        "home": home,
        "ilk_data": ilk_data,
        "bin_dir": bin_dir,
        "plist_dir": plist_dir,
        "original_path": original_path,
    })()

    os.environ["PATH"] = original_path


# ── AC-1: three states, exit codes ──────────────────────────────────────────


class TestAC1ThreeStates:
    """AC-1: health check reports loaded / absent / held → exit 0 / 1 / 2."""

    def test_loaded_exits_0(self, env):
        _make_fake_launchctl(env.bin_dir, present=True)
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 0, f"expected exit 0 for loaded, got {res.returncode}: {res.stderr}"
        assert "loaded" in res.stdout.lower()

    def test_absent_exits_1(self, env):
        _make_fake_launchctl(env.bin_dir, present=False)
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 1, f"expected exit 1 for absent, got {res.returncode}: {res.stderr}"
        assert "absent" in res.stdout.lower()

    def test_held_exits_2(self, env):
        _make_fake_launchctl(env.bin_dir, present=False)
        # Create the hold sentinel.
        (env.ilk_data / "scheduler.hold").touch()
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 2, f"expected exit 2 for held, got {res.returncode}: {res.stderr}"
        assert "held" in res.stdout.lower()


# ── AC-2: absent + no hold → bootstrap ──────────────────────────────────────


class TestAC2AbsentBootstrap:
    """AC-2: when absent and no hold, the check bootstraps and logs."""

    def test_bootstrap_called_on_absent(self, env):
        recorder = _make_fake_launchctl(env.bin_dir, present=False)
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 1
        invocations = recorder.read_text(encoding="utf-8") if recorder.exists() else ""
        assert "bootstrap" in invocations, (
            f"expected bootstrap in launchctl invocations, got:\n{invocations}"
        )

    def test_bootstrap_logged(self, env):
        _make_fake_launchctl(env.bin_dir, present=False)
        res = _run_health_check(env.home, env.ilk_data)
        # The health check should log what it did.
        output = res.stdout + res.stderr
        assert "bootstrap" in output.lower() or "restore" in output.lower(), (
            f"expected a log line about bootstrap/restore, got:\n{output}"
        )


# ── AC-3: hold → no action ─────────────────────────────────────────────────


class TestAC3HoldRespected:
    """AC-3: when a hold is in effect, the check does nothing and reports held."""

    def test_hold_no_bootstrap(self, env):
        recorder = _make_fake_launchctl(env.bin_dir, present=False)
        (env.ilk_data / "scheduler.hold").touch()
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 2
        invocations = recorder.read_text(encoding="utf-8") if recorder.exists() else ""
        # The hold must prevent any bootstrap attempt.
        assert "bootstrap" not in invocations, (
            f"hold should prevent bootstrap, but found:\n{invocations}"
        )

    def test_hold_reported_in_output(self, env):
        _make_fake_launchctl(env.bin_dir, present=False)
        (env.ilk_data / "scheduler.hold").touch()
        res = _run_health_check(env.home, env.ilk_data)
        assert "held" in res.stdout.lower()


# ── AC-4: loaded → no action ────────────────────────────────────────────────


class TestAC4LoadedNoAction:
    """AC-4: when the agent is loaded, the check does nothing and reports loaded."""

    def test_loaded_no_bootstrap(self, env):
        recorder = _make_fake_launchctl(env.bin_dir, present=True)
        res = _run_health_check(env.home, env.ilk_data)
        assert res.returncode == 0
        invocations = recorder.read_text(encoding="utf-8") if recorder.exists() else ""
        assert "bootstrap" not in invocations, (
            f"loaded agent should not trigger bootstrap, got:\n{invocations}"
        )

    def test_loaded_reported_in_output(self, env):
        _make_fake_launchctl(env.bin_dir, present=True)
        res = _run_health_check(env.home, env.ilk_data)
        assert "loaded" in res.stdout.lower()


# ── AC-5: idempotent and bounded ────────────────────────────────────────────


class TestAC5Idempotent:
    """AC-5: two runs back-to-back on absent produce one bootstrap each."""

    def test_two_runs_two_bootstraps(self, env):
        recorder = _make_fake_launchctl(env.bin_dir, present=False)
        _run_health_check(env.home, env.ilk_data)
        _run_health_check(env.home, env.ilk_data)
        invocations = recorder.read_text(encoding="utf-8") if recorder.exists() else ""
        bootstrap_count = invocations.count("bootstrap")
        assert bootstrap_count == 2, (
            f"expected exactly 2 bootstrap calls (one per run), got {bootstrap_count}:\n{invocations}"
        )


# ── AC-6: failed bootstrap → reported, not retried forever ──────────────────


class TestAC6FailedBootstrap:
    """AC-6: a failing bootstrap is reported, not retried indefinitely."""

    def test_failed_bootstrap_reported(self, env):
        _make_fake_launchctl(env.bin_dir, present=False, fail_bootstrap=True)
        res = _run_health_check(env.home, env.ilk_data)
        output = res.stdout + res.stderr
        # Should report the failure somehow — not silently swallow it.
        assert "fail" in output.lower() or "error" in output.lower() or "unreachable" in output.lower(), (
            f"expected failure report, got:\n{output}"
        )

    def test_failed_bootstrap_not_retried_forever(self, env):
        """The health check must not loop indefinitely on bootstrap failure.

        We verify by checking that launchctl bootstrap was called a bounded
        number of times (matching bounce_daemons.sh's 3-attempt limit).
        """
        recorder = _make_fake_launchctl(env.bin_dir, present=False, fail_bootstrap=True)
        res = _run_health_check(env.home, env.ilk_data)
        invocations = recorder.read_text(encoding="utf-8") if recorder.exists() else ""
        bootstrap_count = invocations.count("bootstrap")
        # bounce_daemons.sh retries up to 3 times. The health check should
        # not exceed that bound.
        assert bootstrap_count <= 3, (
            f"bootstrap called {bootstrap_count} times — expected ≤3 (bounce bound). "
            f"Invocations:\n{invocations}"
        )
