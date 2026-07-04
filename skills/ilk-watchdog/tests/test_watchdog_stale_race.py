"""Runtime gate for the stale non-success sentinel race fix (.ps1).

Reproduces the incident: a freshly-launched watchdog reads the PREVIOUS run's
leftover terminal sentinel (state=local_checks_failed) on its first poll and
classifies it — before the new loop overwrites the sentinel with state=running.

The fix: Get-StartupSentinelAction now accepts -LoopAlive; when the sentinel is
a stale non-success AND the loop PID is alive, it returns 'stale-ignore' and
the watchdog keeps watching.

Sub-plan: 2026-07-03-watchdog-stale-nonsuccess-ps
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hashlib
import re

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WATCHDOG_PS1 = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.ps1"

# Skip on non-Windows or when powershell is not available
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("powershell"),
    reason="Windows-only test requiring powershell.exe",
)

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _powershell_exe() -> str:
    return shutil.which("powershell") or "powershell.exe"


# ── helpers ──────────────────────────────────────────────────────────────────


def _project_key(project_path: Path) -> str:
    """Compute the project key from the absolute path (same algorithm as ilk_paths.py)."""
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


def _dirs(data_home: Path, key: str):
    """Return (runtime_dir, launcher_dir, watchdog_dir) for a project key."""
    rt = data_home / "projects" / key / "runtime"
    return rt, rt / "launcher", rt / "watchdog"


def _write_stale_sentinel(runtime_dir: Path, state: str, ended_at: str, run_id: str = "prev-run"):
    """Write a stale last-exit.json."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "run_id": run_id,
        "iteration": 4,
        "exit_code": 1,
        "ended_at": ended_at,
        "generated_at": ended_at,
    }
    (runtime_dir / "last-exit.json").write_text(
        json.dumps(sentinel, indent=2), encoding="utf-8"
    )


def _write_pid_file(launcher_dir: Path, pid: int):
    """Write a running.pid file."""
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "running.pid").write_text(str(pid), encoding="utf-8")


def _start_throwaway_process() -> subprocess.Popen:
    """Start a long-lived powershell process to serve as a 'live loop PID'."""
    return subprocess.Popen(
        [_powershell_exe(), "-NoProfile", "-Command", "Start-Sleep 120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _init_git_repo(project_path: Path):
    """Initialize a minimal git repo so ilk_paths.py can resolve the project."""
    subprocess.run(
        ["git", "init", str(project_path)],
        capture_output=True, timeout=10,
    )
    # Create a dummy commit so the repo has a HEAD
    (project_path / "README.md").touch()
    subprocess.run(
        ["git", "-C", str(project_path), "add", "."],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(project_path), "commit", "-m", "init", "--allow-empty"],
        capture_output=True, timeout=10,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"},
    )


def _start_watchdog(project_path: Path, data_home: Path) -> subprocess.Popen:
    """Launch watchdog.ps1 non-detached in a background subprocess."""
    env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
    return subprocess.Popen(
        [
            _powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(_WATCHDOG_PS1),
            "-ProjectPath", str(project_path),
            "-PollMin", "1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


def _read_activity_log(watchdog_dir: Path) -> str:
    """Read the watchdog activity.log, return empty string if missing."""
    log_path = watchdog_dir / "activity.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8-sig")


def _wait_for_log(watchdog_dir: Path, timeout: float = 15.0) -> str:
    """Poll activity.log until it has content or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _read_activity_log(watchdog_dir)
        if text.strip():
            return text
        time.sleep(0.5)
    return _read_activity_log(watchdog_dir)


# ── AC-2 / AC-3: function-level tests ────────────────────────────────────────


def _dot_source_and_call(data_home: Path, project_path: Path, call_expr: str) -> str:
    """Dot-source watchdog.ps1 and evaluate a PowerShell expression.

    Uses a temp .ps1 file because -Command mode has scoping issues with
    dot-sourced functions on some PowerShell versions.
    """
    script = (
        f"$env:ILK_DOTSOURCE_ONLY = '1'\n"
        f"$env:ILK_DATA_HOME = '{data_home}'\n"
        f". '{_WATCHDOG_PS1}' -ProjectPath '{project_path}'\n"
        f"{call_expr}\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        tmp_ps1 = f.name
    try:
        result = subprocess.run(
            [_powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", tmp_ps1],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, (
            f"Dot-source + call failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return result.stdout.strip()
    finally:
        os.unlink(tmp_ps1)


def test_stale_nonsuccess_alive_returns_stale_ignore(tmp_path):
    """AC-2: stale non-success + LoopAlive $true → 'stale-ignore'."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()
    lt = "2026-07-03T12:00:00"
    call = (
        f"$lt = [datetime]'{lt}'; "
        f"$a = Get-StartupSentinelAction -State 'local_checks_failed' "
        f"-EndedAt '2026-07-03T11:00:00' -LaunchTime $lt -LoopStatusExit 1 -LoopAlive $true; "
        f"Write-Host $a"
    )
    out = _dot_source_and_call(data_home, project_path, call)
    assert out.strip() == "stale-ignore", f"Expected stale-ignore, got: {out}"


def test_stale_nonsuccess_dead_returns_classify(tmp_path):
    """AC-3: stale non-success + LoopAlive $false → 'classify'."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()
    lt = "2026-07-03T12:00:00"
    call = (
        f"$lt = [datetime]'{lt}'; "
        f"$a = Get-StartupSentinelAction -State 'local_checks_failed' "
        f"-EndedAt '2026-07-03T11:00:00' -LaunchTime $lt -LoopStatusExit 1 -LoopAlive $false; "
        f"Write-Host $a"
    )
    out = _dot_source_and_call(data_home, project_path, call)
    assert out.strip() == "classify", f"Expected classify, got: {out}"


def test_fresh_nonsuccess_alive_returns_classify(tmp_path):
    """AC-3: fresh non-success (EndedAt >= LaunchTime) + LoopAlive $true → 'classify'."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()
    lt = "2026-07-03T12:00:00"
    call = (
        f"$lt = [datetime]'{lt}'; "
        f"$a = Get-StartupSentinelAction -State 'local_checks_failed' "
        f"-EndedAt '2026-07-03T13:00:00' -LaunchTime $lt -LoopStatusExit 1 -LoopAlive $true; "
        f"Write-Host $a"
    )
    out = _dot_source_and_call(data_home, project_path, call)
    assert out.strip() == "classify", f"Expected classify, got: {out}"


# ── Race reproduction: full watchdog spawn ───────────────────────────────────


def test_race_reproduction_live_pid_stale_nonsuccess(tmp_path):
    """AC-6: watchdog against a stale non-success sentinel with a LIVE running.pid
    logs stale-ignore and does NOT classify.

    This reproduces the exact incident: previous run's leftover local_checks_failed
    coincides with a fresh loop coming up (live PID).
    """
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()

    # Resolve dirs via ilk_paths.py
    _init_git_repo(project_path)
    key = _project_key(project_path)
    rt_dir, launcher_dir, watchdog_dir = _dirs(data_home, key)

    # Write stale non-success sentinel (ended well before "now")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    _write_stale_sentinel(rt_dir, "local_checks_failed", past)

    # Start a live throwaway process and write its PID
    throwaway = _start_throwaway_process()
    try:
        _write_pid_file(launcher_dir, throwaway.pid)

        # Launch watchdog
        wd = _start_watchdog(project_path, data_home)
        try:
            # Wait for the activity log to have content
            log_text = _wait_for_log(watchdog_dir, timeout=20)

            # The watchdog should log stale-ignore (keep watching), NOT classifying
            assert "classifying" not in log_text, (
                f"Watchdog should NOT classify a stale sentinel with live PID.\n"
                f"Activity log:\n{log_text}"
            )
            assert "ignoring" in log_text or "stale" in log_text.lower(), (
                f"Expected stale-ignore / ignoring log line.\nActivity log:\n{log_text}"
            )
        finally:
            wd.kill()
            wd.wait(timeout=5)
    finally:
        throwaway.kill()
        throwaway.wait(timeout=5)


def test_race_reproduction_dead_pid_stale_nonsuccess(tmp_path):
    """Negative: watchdog against a stale non-success sentinel with a DEAD running.pid
    DOES classify (adjudicate a genuinely-dead run).

    This is the counter-case: if there's no live loop, classification is correct.
    """
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()

    _init_git_repo(project_path)
    key = _project_key(project_path)
    rt_dir, launcher_dir, watchdog_dir = _dirs(data_home, key)

    # Write stale non-success sentinel
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    _write_stale_sentinel(rt_dir, "local_checks_failed", past)

    # Write a definitely-dead PID (99999 is virtually never alive on Windows)
    _write_pid_file(launcher_dir, 99999)

    # Launch watchdog
    wd = _start_watchdog(project_path, data_home)
    try:
        log_text = _wait_for_log(watchdog_dir, timeout=20)

        assert "classifying" in log_text, (
            f"Watchdog should classify a stale sentinel with dead PID.\n"
            f"Activity log:\n{log_text}"
        )
    finally:
        wd.kill()
        wd.wait(timeout=5)
