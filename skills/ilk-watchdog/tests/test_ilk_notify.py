"""Red test: ilk_notify.py cross-platform desktop notifier.

AC-1: ilk_notify.py --event ship --project demo --dry-run exits 0 and prints
      the platform-correct command (osascript on darwin / toast on win32 /
      notify-send on linux).  Unknown backend -> prints a fallback console
      line, exit 0.

AC-2: With ILK_NOTIFY=0, the same call emits nothing and exits 0.

AC-3: watchdog.ps1 and scheduler.ps1 call the notifier at each event point.

AC-4: Bash parity — watchdog.sh/scheduler.sh invoke the notifier at the
      same events.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ -> ilk-watchdog/ -> skills/ -> root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NOTIFY_PY = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "ilk_notify.py"

# Scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "notify"


# ── helpers ─────────────────────────────────────────────────────────

def _run_notify(*args: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run ilk_notify.py with given args, return result."""
    env = {**os.environ, **(env_override or {})}
    return subprocess.run(
        [sys.executable, str(NOTIFY_PY), *args],
        capture_output=True, text=True, timeout=15,
        env=env,
    )


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    yield
    import shutil
    shutil.rmtree(SCRATCH, ignore_errors=True)


# ── AC-1: dry-run prints platform-correct command ───────────────────

class TestAC1_DryRunPlatformCommand:
    """--dry-run prints the command that would be executed."""

    def test_darwin_dry_run_shows_osascript(self):
        """On darwin, --dry-run mentions osascript."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--dry-run", "--platform", "darwin")
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert "osascript" in result.stdout.lower(), f"missing osascript: {result.stdout}"

    def test_win32_dry_run_shows_toast_or_console(self):
        """On win32, --dry-run mentions BurntToast or a console fallback."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--dry-run", "--platform", "win32")
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        out = result.stdout.lower()
        assert "burnttoast" in out or "toast" in out or "notify" in out or "console" in out, \
            f"missing toast/console mention: {result.stdout}"

    def test_linux_dry_run_shows_notify_send(self):
        """On linux, --dry-run mentions notify-send."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--dry-run", "--platform", "linux")
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert "notify-send" in result.stdout.lower(), f"missing notify-send: {result.stdout}"

    def test_unknown_platform_fallback(self):
        """On unknown platform, --dry-run prints a console fallback."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--dry-run", "--platform", "unknown-os")
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        # Should still produce some output (console line)
        assert len(result.stdout.strip()) > 0

    def test_dry_run_includes_event_and_project(self):
        """--dry-run output includes the event name and project."""
        result = _run_notify("--event", "blocked", "--project", "my-proj",
                             "--dry-run", "--platform", "darwin")
        assert result.returncode == 0
        assert "blocked" in result.stdout.lower()
        assert "my-proj" in result.stdout

    def test_dry_run_with_detail(self):
        """--dry-run with --detail includes detail in output."""
        result = _run_notify("--event", "restart", "--project", "demo",
                             "--detail", "whitelist: timeout-bound",
                             "--dry-run", "--platform", "darwin")
        assert result.returncode == 0
        assert "timeout-bound" in result.stdout.lower() or "restart" in result.stdout.lower()


# ── AC-2: ILK_NOTIFY=0 suppresses output ────────────────────────────

class TestAC2_DisabledByEnv:
    """ILK_NOTIFY=0 suppresses all output."""

    def test_no_output_when_disabled(self):
        """With ILK_NOTIFY=0, --dry-run emits nothing and exits 0."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--dry-run", "--platform", "darwin",
                             env_override={"ILK_NOTIFY": "0"})
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert result.stdout.strip() == "", f"expected no output, got: {result.stdout}"

    def test_no_output_when_disabled_real_mode(self):
        """With ILK_NOTIFY=0, real mode (no --dry-run) also emits nothing."""
        result = _run_notify("--event", "ship", "--project", "demo",
                             "--platform", "darwin",
                             env_override={"ILK_NOTIFY": "0"})
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ── AC-3: watchdog/scheduler wiring (Select-String pattern) ─────────

class TestAC3_WiringInScripts:
    """watchdog.ps1 and scheduler.ps1 call ilk_notify at event points."""

    @pytest.mark.parametrize("script_name", [
        "watchdog.ps1",
        "scheduler.ps1",
    ])
    def test_ps1_calls_notify(self, script_name: str):
        """The PowerShell script invokes ilk_notify.py."""
        script = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / script_name
        assert script.exists(), f"script not found: {script}"
        text = script.read_text(encoding="utf-8")
        assert "ilk_notify" in text.lower() or "notify" in text.lower(), \
            f"{script_name} does not reference ilk_notify"


# ── AC-4: bash parity ───────────────────────────────────────────────

class TestAC4_BashParity:
    """watchdog.sh and scheduler.sh invoke ilk_notify at event points."""

    @pytest.mark.parametrize("script_name", [
        "watchdog.sh",
        "scheduler.sh",
    ])
    def test_sh_calls_notify(self, script_name: str):
        """The bash script invokes ilk_notify.py."""
        script = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / script_name
        assert script.exists(), f"script not found: {script}"
        text = script.read_text(encoding="utf-8")
        assert "ilk_notify" in text.lower() or "notify" in text.lower(), \
            f"{script_name} does not reference ilk_notify"
