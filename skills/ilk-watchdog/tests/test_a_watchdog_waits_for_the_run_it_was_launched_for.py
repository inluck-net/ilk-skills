"""Red-first pins: a superseded sentinel is awaited, not classified.

Sub-plan: a-watchdog-waits-for-the-run-it-was-launched-for
Design row: "The watchdog starts before the sentinel is rewritten"
  (docs/architecture/unattended-unblocking-design.md:98, table row 10 at :41)
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

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WATCHDOG_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
_WATCHDOG_PS1 = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.ps1"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"),
    reason="bash not available",
)

_ILK_LOOP_SCRIPTS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"
if str(_ILK_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ILK_LOOP_SCRIPTS))
from ilk_paths import project_key as _project_key  # noqa: E402


def _bash_exe() -> str:
    return shutil.which("bash") or "bash"


def _dirs(data_home: Path, key: str):
    rt = data_home / "projects" / key / "runtime"
    return rt, rt / "launcher", rt / "watchdog"


def _extract_function(func_name: str) -> str:
    result = subprocess.run(
        [_bash_exe(), "-c",
         f"sed -n '/^{func_name}()/,/^}}/p' '{_WATCHDOG_SH}'"],
        capture_output=True, text=True, timeout=10, encoding="utf-8",
    )
    assert result.returncode == 0, f"Failed to extract {func_name}: {result.stderr}"
    assert result.stdout.strip(), f"Function {func_name} not found in watchdog.sh"
    return result.stdout


def _call_startup_action(state: str, ended_epoch: int, launch_epoch: int,
                         loop_status_exit: int, loop_alive: bool,
                         superseded: bool = False) -> str:
    """Call startup_sentinel_action via bash subprocess (sed-extract + eval).

    Passes 6 arguments when superseded is True (the new parameter),
    5 arguments otherwise (back-compat with current code).
    """
    alive_str = "true" if loop_alive else "false"
    func_code = _extract_function("startup_sentinel_action")
    if superseded:
        super_str = "true"
        script = (
            f"{func_code}\n"
            f"startup_sentinel_action '{state}' {ended_epoch} {launch_epoch} "
            f"{loop_status_exit} {alive_str} {super_str}"
        )
    else:
        script = (
            f"{func_code}\n"
            f"startup_sentinel_action '{state}' {ended_epoch} {launch_epoch} "
            f"{loop_status_exit} {alive_str}"
        )
    result = subprocess.run(
        [_bash_exe(), "-c", script],
        capture_output=True, text=True, timeout=10, encoding="utf-8",
    )
    assert result.returncode == 0, (
        f"startup_sentinel_action failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result.stdout.strip()


# ── AC-1: superseded flag on startup_sentinel_action ──────────────────────────




@pytest.mark.xfail(strict=True, reason="Step 1: startup_sentinel_action does not yet accept 6th arg (superseded)")
def test_superseded_non_success_dead_returns_stale_ignore():
    """AC-1: non-success + superseded=true + dead PID → 'stale-ignore'.
    Today this returns 'classify'; the superseded flag must override.
    """
    out = _call_startup_action("local_checks_failed", 1000, 2000, 1, False, superseded=True)
    assert out == "stale-ignore", f"Expected stale-ignore, got: {out}"


# ── AC-2: sentinel_superseded_by_launch helper ────────────────────────────────


def _extract_superseded_function() -> str:
    return _extract_function("sentinel_superseded_by_launch")


def _call_superseded(sentinel_run_id: str, launch_log_dir: str,
                     launch_json_exists: bool = True) -> str:
    """Call sentinel_superseded_by_launch via bash subprocess.

    Sets up a temporary launcher dir with a last-launch.json (or not)
    and calls the helper.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        launcher_dir = Path(td) / "launcher"
        launcher_dir.mkdir()
        if launch_json_exists:
            payload = {"log_dir": launch_log_dir}
            (launcher_dir / "last-launch.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        func_code = _extract_superseded_function()
        # Also source read_last_exit_state pattern — the helper reads JSON with $PYTHON
        script = f"""{func_code}
sentinel_superseded_by_launch '{launcher_dir}' '{sentinel_run_id}'
"""
        result = subprocess.run(
            [_bash_exe(), "-c", script],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"sentinel_superseded_by_launch failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return result.stdout.strip()


@pytest.mark.xfail(strict=True, reason="Step 1: sentinel_superseded_by_launch does not yet exist")
def test_superseded_by_launch_earlier_run():
    """AC-2a: sentinel 20261003-120000, launch runs/20261003-180000 → true."""
    out = _call_superseded("20261003-120000", "/some/path/runs/20261003-180000")
    assert out == "true", f"Expected true, got: {out}"


@pytest.mark.xfail(strict=True, reason="Step 1: sentinel_superseded_by_launch does not yet exist")
def test_superseded_by_launch_equal_ids():
    """AC-2b: equal run ids → false."""
    out = _call_superseded("20261003-180000", "/some/path/runs/20261003-180000")
    assert out == "false", f"Expected false, got: {out}"


@pytest.mark.xfail(strict=True, reason="Step 1: sentinel_superseded_by_launch does not yet exist")
def test_superseded_by_launch_later_sentinel():
    """AC-2c: sentinel later than launch → false."""
    out = _call_superseded("20261003-200000", "/some/path/runs/20261003-180000")
    assert out == "false", f"Expected false, got: {out}"


@pytest.mark.xfail(strict=True, reason="Step 1: sentinel_superseded_by_launch does not yet exist")
def test_superseded_by_launch_missing_json():
    """AC-2d: missing last-launch.json → false."""
    out = _call_superseded("20261003-120000", "/some/path/runs/20261003-180000",
                           launch_json_exists=False)
    assert out == "false", f"Expected false, got: {out}"


@pytest.mark.xfail(strict=True, reason="Step 1: sentinel_superseded_by_launch does not yet exist")
def test_superseded_by_launch_non_timestamp_run_id():
    """AC-2e: non-timestamp run id → false."""
    out = _call_superseded("not-a-timestamp", "/some/path/runs/20261003-180000")
    assert out == "false", f"Expected false, got: {out}"


# ── AC-3 / AC-4: runtime watchdog spawn ───────────────────────────────────────


def _write_stale_sentinel(runtime_dir: Path, state: str, ended_at: str,
                          run_id: str = "prev-run"):
    launcher = runtime_dir / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "run_id": run_id,
        "iteration": 4,
        "exit_code": 1,
        "ended_at": ended_at,
        "generated_at": ended_at,
    }
    (launcher / "last-exit.json").write_text(
        json.dumps(sentinel, indent=2), encoding="utf-8"
    )


def _write_last_launch(launcher_dir: Path, log_dir: str):
    launcher_dir.mkdir(parents=True, exist_ok=True)
    payload = {"log_dir": log_dir}
    (launcher_dir / "last-launch.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _write_pid_file(launcher_dir: Path, pid: int):
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "running.pid").write_text(str(pid), encoding="utf-8")


def _init_git_repo(project_path: Path):
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
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"},
        encoding="utf-8",
    )


def _start_watchdog(project_path: Path, data_home: Path, log_file: Path) -> subprocess.Popen:
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
    log_path = watchdog_dir / "activity.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8-sig")


def _wait_for_log(watchdog_dir: Path, contains: str, timeout: float = 20.0) -> str:
    """Poll activity.log until it contains the target string or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _read_activity_log(watchdog_dir)
        if contains in text:
            return text
        time.sleep(0.5)
    return _read_activity_log(watchdog_dir)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Git Bash pipe encoding OSError on Windows",
)
@pytest.mark.xfail(strict=True, reason="Step 1: watchdog does not yet check last-launch.json for superseded sentinel")
def test_superseded_sentinel_awaits_not_classifies(tmp_path):
    """AC-3: stale sentinel with run_id < last-launch.json run_id, dead PID
    → watchdog logs 'awaiting the new runner's sentinel' and never 'classifying'.
    """
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()
    _init_git_repo(project_path)
    key = _project_key(project_path)
    rt_dir, launcher_dir, watchdog_dir = _dirs(data_home, key)

    # Stale sentinel: run_id 20261003-120000, ended 2 hours ago
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    _write_stale_sentinel(rt_dir, "local_checks_failed", past, run_id="20261003-120000")

    # last-launch.json says the latest run is 20261003-180000
    _write_last_launch(launcher_dir, "/some/path/runs/20261003-180000")

    # Dead PID
    _write_pid_file(launcher_dir, 99999)

    wd_log = tmp_path / "watchdog_stdout.log"
    wd = _start_watchdog(project_path, data_home, wd_log)
    try:
        log_text = _wait_for_log(watchdog_dir, "awaiting", timeout=25)

        assert "awaiting the new runner's sentinel" in log_text, (
            f"Expected 'awaiting the new runner's sentinel' in log.\nActivity log:\n{log_text}"
        )
        assert "classifying" not in log_text, (
            f"Watchdog should NOT classify a superseded sentinel.\nActivity log:\n{log_text}"
        )
    finally:
        wd.kill()
        wd.wait(timeout=5)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Git Bash pipe encoding OSError on Windows",
)
@pytest.mark.xfail(strict=True, reason="Step 1: watchdog does not yet check last-launch.json for superseded sentinel")
def test_superseded_then_new_sentinel_gets_classified(tmp_path):
    """AC-4: superseded sentinel → awaiting; then sentinel rewritten with
    matching run_id → watchdog classifies it.
    """
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "proj"
    project_path.mkdir()
    _init_git_repo(project_path)
    key = _project_key(project_path)
    rt_dir, launcher_dir, watchdog_dir = _dirs(data_home, key)

    # Stale sentinel: run_id 20261003-120000
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    _write_stale_sentinel(rt_dir, "local_checks_failed", past, run_id="20261003-120000")

    # last-launch.json: latest run is 20261003-180000
    _write_last_launch(launcher_dir, "/some/path/runs/20261003-180000")

    # Dead PID
    _write_pid_file(launcher_dir, 99999)

    wd_log = tmp_path / "watchdog_stdout.log"
    wd = _start_watchdog(project_path, data_home, wd_log)
    try:
        # Wait for the "awaiting" log
        log_text = _wait_for_log(watchdog_dir, "awaiting", timeout=25)
        assert "awaiting" in log_text, (
            f"Expected 'awaiting' in initial log.\nActivity log:\n{log_text}"
        )

        # Now rewrite the sentinel with the current run_id and a terminal non-success
        _write_stale_sentinel(rt_dir, "local_checks_failed", past, run_id="20261003-180000")

        # Wait for "classifying"
        log_text = _wait_for_log(watchdog_dir, "classifying", timeout=25)
        assert "classifying" in log_text, (
            f"Expected 'classifying' after sentinel rewritten with matching run_id.\n"
            f"Activity log:\n{log_text}"
        )
    finally:
        wd.kill()
        wd.wait(timeout=5)


# ── AC-6: PowerShell static check ─────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="Step 1: watchdog.ps1 does not yet have $Superseded parameter")
def test_ps1_get_startup_sentinel_action_has_superseded_param():
    """AC-6a: Get-StartupSentinelAction declares [bool]$Superseded parameter."""
    text = _WATCHDOG_PS1.read_text(encoding="utf-8")
    # Find the function and check for $Superseded param
    import re
    func_match = re.search(
        r'function\s+Get-StartupSentinelAction\s*\{(.*?)\n\}',
        text, re.DOTALL,
    )
    assert func_match, "Get-StartupSentinelAction function not found"
    func_body = func_match.group(1)
    assert '$Superseded' in func_body, (
        "Get-StartupSentinelAction missing $Superseded parameter"
    )


@pytest.mark.xfail(strict=True, reason="Step 1: watchdog.ps1 does not yet return stale-ignore on $Superseded")
def test_ps1_get_startup_sentinel_action_returns_stale_ignore_on_superseded():
    """AC-6b: Get-StartupSentinelAction returns 'stale-ignore' when $Superseded is true."""
    text = _WATCHDOG_PS1.read_text(encoding="utf-8")
    import re
    func_match = re.search(
        r'function\s+Get-StartupSentinelAction\s*\{(.*?)\n\}',
        text, re.DOTALL,
    )
    assert func_match, "Get-StartupSentinelAction function not found"
    func_body = func_match.group(1)
    assert 'Superseded' in func_body and 'stale-ignore' in func_body, (
        "Get-StartupSentinelAction does not return 'stale-ignore' on $Superseded"
    )


@pytest.mark.xfail(strict=True, reason="Step 1: PS1 call sites do not yet pass -Superseded")
def test_ps1_call_sites_pass_superseded():
    """AC-6c: both call sites of Get-StartupSentinelAction pass -Superseded."""
    text = _WATCHDOG_PS1.read_text(encoding="utf-8")
    import re
    # Find all call sites (Get-StartupSentinelAction followed by params)
    call_pattern = re.compile(
        r'Get-StartupSentinelAction\s+`?\s*\n\s*(.*?)(?=\n\s*\w|\n\s*})',
        re.DOTALL,
    )
    calls = call_pattern.findall(text)
    assert len(calls) >= 2, f"Expected at least 2 call sites, found {len(calls)}"
    for i, call_block in enumerate(calls):
        assert '-Superseded' in call_block, (
            f"Call site {i+1} does not pass -Superseded.\nBlock:\n{call_block}"
        )