"""Runtime gate for the stale non-success sentinel race fix (.sh).

Mirrors the PS test_watchdog_stale_race.py but drives the bash
startup_sentinel_action() function and watchdog.sh.

Sub-plan: 2026-07-03-watchdog-stale-nonsuccess-sh
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
_WATCHDOG_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"

# Skip when bash is not available
pytestmark = pytest.mark.skipif(
    not shutil.which("bash"),
    reason="bash not available",
)

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _bash_exe() -> str:
    return shutil.which("bash") or "bash"


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


def _extract_function(func_name: str) -> str:
    """Extract a top-level function from watchdog.sh via sed."""
    result = subprocess.run(
        [_bash_exe(), "-c",
         f"sed -n '/^{func_name}()/,/^}}/p' '{_WATCHDOG_SH}'"],
        capture_output=True, text=True, timeout=10, encoding="utf-8",
    )
    assert result.returncode == 0, f"Failed to extract {func_name}: {result.stderr}"
    assert result.stdout.strip(), f"Function {func_name} not found in watchdog.sh"
    return result.stdout


def _call_startup_action(state: str, ended_epoch: int, launch_epoch: int,
                         loop_status_exit: int, loop_alive: bool) -> str:
    """Call startup_sentinel_action via bash subprocess (sed-extract + eval)."""
    alive_str = "true" if loop_alive else "false"
    func_code = _extract_function("startup_sentinel_action")
    script = f"{func_code}\nstartup_sentinel_action '{state}' {ended_epoch} {launch_epoch} {loop_status_exit} {alive_str}"
    result = subprocess.run(
        [_bash_exe(), "-c", script],
        capture_output=True, text=True, timeout=10, encoding="utf-8",
    )
    assert result.returncode == 0, (
        f"startup_sentinel_action failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result.stdout.strip()


# ── AC-2 / AC-3: function-level tests ────────────────────────────────────────


def test_stale_nonsuccess_alive_returns_stale_ignore():
    """AC-2: stale non-success + loop_alive=true → 'stale-ignore'."""
    out = _call_startup_action("local_checks_failed", 1000, 2000, 1, True)
    assert out == "stale-ignore", f"Expected stale-ignore, got: {out}"


def test_stale_nonsuccess_dead_returns_classify():
    """AC-3: stale non-success + loop_alive=false → 'classify'."""
    out = _call_startup_action("local_checks_failed", 1000, 2000, 1, False)
    assert out == "classify", f"Expected classify, got: {out}"


def test_fresh_nonsuccess_alive_returns_classify():
    """AC-3: fresh non-success (ended >= launch) + loop_alive=true → 'classify'."""
    out = _call_startup_action("local_checks_failed", 2000, 1000, 1, True)
    assert out == "classify", f"Expected classify, got: {out}"


def test_fresh_nonsuccess_dead_returns_classify():
    """AC-3: fresh non-success + loop_alive=false → 'classify'."""
    out = _call_startup_action("local_checks_failed", 2000, 1000, 1, False)
    assert out == "classify", f"Expected classify, got: {out}"


# ── AC-4: success matrix ─────────────────────────────────────────────────────


def test_stale_success_returns_stale_ignore():
    """AC-4a: stale success → 'stale-ignore'."""
    out = _call_startup_action("shipped", 1000, 2000, 0, True)
    assert out == "stale-ignore", f"Expected stale-ignore, got: {out}"


def test_fresh_success_exit0_returns_advance():
    """AC-4b: fresh success + loop_status_exit=0 → 'advance'."""
    out = _call_startup_action("shipped", 2000, 1000, 0, True)
    assert out == "advance", f"Expected advance, got: {out}"


def test_fresh_success_exit_nonzero_returns_work_pending():
    """AC-4c: fresh success + loop_status_exit!=0 → 'work-pending'."""
    out = _call_startup_action("shipped", 2000, 1000, 1, True)
    assert out == "work-pending", f"Expected work-pending, got: {out}"


def test_stale_all_shipped_returns_stale_ignore():
    """AC-4d: stale all-shipped → 'stale-ignore'."""
    out = _call_startup_action("all-shipped", 1000, 2000, 0, False)
    assert out == "stale-ignore", f"Expected stale-ignore, got: {out}"


def test_fresh_already_shipped_exit0_returns_advance():
    """AC-4e: fresh already-shipped + exit=0 → 'advance'."""
    out = _call_startup_action("already-shipped", 2000, 1000, 0, False)
    assert out == "advance", f"Expected advance, got: {out}"


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_ended_epoch_zero_treated_as_not_stale():
    """ended_epoch=0 (unparseable) → not stale → classify for non-success."""
    out = _call_startup_action("local_checks_failed", 0, 2000, 1, True)
    assert out == "classify", f"Expected classify, got: {out}"


def test_unknown_state_treated_as_non_success():
    """Unknown state (not in success set) → non-success path."""
    out = _call_startup_action("some-unknown-state", 2000, 1000, 1, True)
    assert out == "classify", f"Expected classify, got: {out}"


# ── Race reproduction: full watchdog.sh spawn ────────────────────────────────


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
    """Start a long-lived sleep process to serve as a 'live loop PID'."""
    return subprocess.Popen(
        [_bash_exe(), "-c", "sleep 120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _init_git_repo(project_path: Path):
    """Initialize a minimal git repo so ilk_paths.py can resolve the project."""
    subprocess.run(
        ["git", "init", str(project_path)],
        capture_output=True, timeout=10, encoding="utf-8",
    )
    (project_path / "README.md").touch()
    subprocess.run(
        ["git", "-C", str(project_path), "add", "."],
        capture_output=True, timeout=10, encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(project_path), "commit", "-m", "init", "--allow-empty"],
        capture_output=True, timeout=10,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"}, encoding="utf-8",
    )


def _start_watchdog(project_path: Path, data_home: Path, log_file: Path) -> subprocess.Popen:
    """Launch watchdog.sh non-detached in a background subprocess.

    Output goes to *log_file* instead of a pipe — avoids a Windows-specific
    OSError when bash pipes Python stdout through Git Bash.
    """
    env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
    fh = open(log_file, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen(
        [_bash_exe(), str(_WATCHDOG_SH),
         "--project-path", str(project_path),
         "--poll-interval-sec", "1"],
        stdout=fh,
        stderr=subprocess.STDOUT,
        env=env,
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Git Bash pipe encoding OSError on Windows — race reproduction verified on macOS/Linux",
)
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

        # Launch watchdog (output to file to avoid Windows pipe encoding issue)
        wd_log = tmp_path / "watchdog_stdout.log"
        wd = _start_watchdog(project_path, data_home, wd_log)
        try:
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Git Bash pipe encoding OSError on Windows — race reproduction verified on macOS/Linux",
)
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

    # Write a definitely-dead PID (99999 is virtually never alive)
    _write_pid_file(launcher_dir, 99999)

    # Launch watchdog (output to file to avoid Windows pipe encoding issue)
    wd_log = tmp_path / "watchdog_stdout.log"
    wd = _start_watchdog(project_path, data_home, wd_log)
    try:
        log_text = _wait_for_log(watchdog_dir, timeout=20)

        assert "classifying" in log_text, (
            f"Watchdog should classify a stale sentinel with dead PID.\n"
            f"Activity log:\n{log_text}"
        )
    finally:
        wd.kill()
        wd.wait(timeout=5)
