"""Tests for the scheduler state file (scheduler.state.json).

Validates AC-1..AC-6 from sub-plan the-scheduler-records-its-toolkit-head:
  AC-1  Writes {pid, started_at, toolkit_head} at startup.
  AC-2  toolkit_head resolved from the script's own path, not $PWD.
  AC-3  scheduler.pid stays a bare PID.
  AC-4  File rewritten on every start (stale doesn't survive).
  AC-5  Write failure never prevents startup (fail open).
  AC-6  ~/.ilk-data/ is created if absent.

Drives scheduler.sh with HOME=tmp_path and --once so it starts, writes, and
exits without entering its polling loop.  No test starts a real long-running
scheduler or touches the operator's ~/.ilk-data.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


def _run_scheduler(home: Path, *, cwd: Path | None = None,
                   extra_env: dict | None = None,
                   timeout: int = 30) -> subprocess.CompletedProcess:
    """Run scheduler.sh --once --dry-run with HOME=tmp_path."""
    env = {
        **os.environ,
        "HOME": str(home),
        "ILK_SKILL_HOME": str(SKILLS_DIR),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True, text=True, timeout=timeout,
        env=env, encoding="utf-8",
        cwd=str(cwd) if cwd else None,
    )


def _expected_toolkit_head() -> str:
    """The HEAD of the repo that contains the skills directory."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


# ── AC-1: writes {pid, started_at, toolkit_head} ────────────────────────────

def test_writes_state_file_with_required_fields(tmp_path):
    """AC-1: scheduler.state.json must contain pid, started_at, toolkit_head."""
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file(), (
        f"scheduler.state.json not created. "
        f"Contents of .ilk-data: {list((tmp_path / '.ilk-data').iterdir())}"
    )
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "pid" in data, f"missing 'pid' in {data}"
    assert "started_at" in data, f"missing 'started_at' in {data}"
    assert "toolkit_head" in data, f"missing 'toolkit_head' in {data}"


def test_pid_field_is_integer(tmp_path):
    """AC-1: pid must be an integer matching the scheduler's own PID."""
    res = _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert isinstance(data["pid"], int), f"pid should be int, got {type(data['pid'])}"
    assert data["pid"] > 0


def test_started_at_is_iso8601(tmp_path):
    """AC-1: started_at must be a parseable ISO-8601 timestamp."""
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    from datetime import datetime
    dt = datetime.fromisoformat(data["started_at"])
    assert dt.tzinfo is not None, "started_at should be timezone-aware"


# ── AC-2: toolkit_head from script path, not $PWD ────────────────────────────

def test_toolkit_head_matches_repo_head(tmp_path):
    """AC-2: toolkit_head must be the HEAD of the repo containing _SKILL_ROOT."""
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    expected = _expected_toolkit_head()
    assert data["toolkit_head"] == expected, (
        f"toolkit_head mismatch: got {data['toolkit_head'][:12]}, "
        f"expected {expected[:12]}"
    )


def test_toolkit_head_ignores_pwd(tmp_path):
    """AC-2: toolkit_head must be correct even when $PWD is unrelated."""
    unrelated = tmp_path / "unrelated_dir"
    unrelated.mkdir()
    _run_scheduler(tmp_path, cwd=unrelated)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    expected = _expected_toolkit_head()
    assert data["toolkit_head"] == expected, (
        f"toolkit_head wrong when cwd is unrelated: got {data['toolkit_head'][:12]}, "
        f"expected {expected[:12]}"
    )


def test_toolkit_head_is_40char_hex(tmp_path):
    """AC-2: toolkit_head must look like a 40-char lowercase hex SHA."""
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    head = data["toolkit_head"]
    assert len(head) == 40, f"toolkit_head should be 40 chars, got {len(head)}"
    assert all(c in "0123456789abcdef" for c in head), (
        f"toolkit_head should be lowercase hex, got {head}"
    )


# ── AC-3: scheduler.pid stays a bare PID ─────────────────────────────────────

def test_bare_pid(tmp_path):
    """AC-3: scheduler.pid must contain exactly a bare PID, nothing else."""
    _run_scheduler(tmp_path)
    pid_file = tmp_path / ".ilk-data" / "scheduler.pid"
    assert pid_file.is_file(), "scheduler.pid not created"
    raw = pid_file.read_text(encoding="utf-8")
    assert raw.strip() == raw.rstrip("\n"), (
        f"scheduler.pid has trailing whitespace: {raw!r}"
    )
    assert raw.strip().isdigit(), (
        f"scheduler.pid should be a bare PID (digits only), got: {raw!r}"
    )


# ── AC-4: file rewritten on every start ──────────────────────────────────────

def test_rewrite_on_restart(tmp_path):
    """AC-4: a stale state file from a previous start must not survive."""
    # First run — creates the file.
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file()
    first = json.loads(state_file.read_text(encoding="utf-8"))

    # Mutate the file to simulate a stale previous start.
    stale = {**first, "pid": 99999, "toolkit_head": "deadbeef" * 5}
    state_file.write_text(json.dumps(stale), encoding="utf-8")

    # Second run — must overwrite.
    _run_scheduler(tmp_path)
    second = json.loads(state_file.read_text(encoding="utf-8"))
    assert second["pid"] != 99999, (
        "stale state file survived — scheduler did not rewrite it"
    )
    assert second["toolkit_head"] != "deadbeef" * 5, (
        "stale toolkit_head survived — scheduler did not rewrite it"
    )


# ── AC-5: fail open — write failure never blocks startup ─────────────────────

def test_fail_open_on_readonly_dir(tmp_path):
    """AC-5: if the state file cannot be written, scheduler still starts."""
    data_dir = tmp_path / ".ilk-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / "scheduler.state.json"
    # Create the state file first, then make it read-only so the write fails.
    # The directory stays writable so the PID file can still be written.
    state_file.write_text('{"stale": true}', encoding="utf-8")
    state_file.chmod(0o444)
    try:
        res = _run_scheduler(tmp_path, timeout=30)
        # Scheduler must not crash — it should exit 0 (--once --dry-run).
        assert res.returncode == 0, (
            f"scheduler crashed when state file is read-only: "
            f"rc={res.returncode}\nstdout={res.stdout[-500:]}\n"
            f"stderr={res.stderr[-500:]}"
        )
    finally:
        state_file.chmod(0o644)


def test_fail_open_logs_degradation(tmp_path):
    """AC-5: a write failure should be logged, not silent."""
    data_dir = tmp_path / ".ilk-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / "scheduler.state.json"
    # Create the state file first, then make it read-only so the write fails.
    state_file.write_text('{"stale": true}', encoding="utf-8")
    state_file.chmod(0o444)
    try:
        res = _run_scheduler(tmp_path, timeout=30)
        combined = res.stdout + res.stderr
        assert any(
            word in combined.lower()
            for word in ["state", "degraded", "warn", "could not"]
        ), "write failure should produce a log message"
    finally:
        state_file.chmod(0o644)


def test_fail_open_on_no_git(tmp_path):
    """AC-5: if HEAD can't be resolved, scheduler still starts."""
    # Use a non-existent skill home so git rev-parse fails.
    fake_skills = tmp_path / "no-git-skills"
    fake_skills.mkdir()
    env_extra = {"ILK_SKILL_HOME": str(fake_skills)}
    res = _run_scheduler(tmp_path, extra_env=env_extra, timeout=30)
    assert res.returncode == 0, (
        f"scheduler crashed when HEAD can't be resolved: rc={res.returncode}"
    )


def test_fail_open_no_git_logs_degradation(tmp_path):
    """AC-5: unresolvable HEAD should be logged."""
    fake_skills = tmp_path / "no-git-skills"
    fake_skills.mkdir()
    env_extra = {"ILK_SKILL_HOME": str(fake_skills)}
    res = _run_scheduler(tmp_path, extra_env=env_extra, timeout=30)
    combined = res.stdout + res.stderr
    assert any(
        word in combined.lower()
        for word in ["head", "toolkit", "degraded", "warn", "git",
                     "could not", "resolve"]
    ), "unresolvable HEAD should produce a log message"


# ── AC-6: ~/.ilk-data/ is created if absent ──────────────────────────────────

def test_creates_data_dir_if_absent(tmp_path):
    """AC-6: ~/.ilk-data/ is created if it doesn't exist."""
    data_dir = tmp_path / ".ilk-data"
    assert not data_dir.exists(), "precondition: .ilk-data should not exist"
    _run_scheduler(tmp_path)
    assert data_dir.is_dir(), ".ilk-data was not created"


def test_creates_state_file_in_fresh_dir(tmp_path):
    """AC-6: state file is created even when .ilk-data didn't exist before."""
    _run_scheduler(tmp_path)
    state_file = tmp_path / ".ilk-data" / "scheduler.state.json"
    assert state_file.is_file(), "state file not created in fresh .ilk-data"
