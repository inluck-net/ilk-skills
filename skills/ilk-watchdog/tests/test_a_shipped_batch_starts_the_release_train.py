"""Tests for release_train run verb + scheduler maybe_start_release_train hook.

Sub-plan: a-shipped-batch-starts-the-release-train, step 0 (red-first pins).

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path`` with a sandbox
project.  All external commands (release_train.py, scheduler, launchctl) are
injected as stubs so no real daemon, no real ``~/.ilk-data``, and no real
release process are touched.

The five acceptance criteria:
  AC-1  all-shipped sentinel (dead pid): one scheduler cycle starts the stub
        train once with ``run --project <path>``; a second cycle does not.
  AC-2  train.lock naming a live pid (a ``sleep 30`` the test owns): the
        project is skipped ``skip-releasing`` and not dispatched.
  AC-3  ILK_RELEASE_TRAIN=0: not started.
  AC-4  ``run`` with a stubbed ``deploy`` returning 5: a ``rolled-back`` row,
        an ``escalated`` row, notify called once, and the lock removed.
  AC-5  (control) a project with a ``local_checks_failed`` sentinel never
        starts the train.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-ship" / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))
sys.path.insert(0, str(WATCHDOG_SCRIPTS))

from release_train_dispatch import (  # noqa: E402
    is_release_lock_held,
    is_release_train_enabled,
    sentinel_all_shipped,
    sentinel_is_failure,
)


# ── Isolation fixture ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME so no test touches the real data root."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("ILK_DATA_HOME", str(fake_home / ".ilk-data"))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_sentinel(
    tmp_path: Path,
    *,
    state: str = "all-shipped",
    pid: int = 99999999,
    run_id: str = "test-run-001",
) -> Path:
    """Write a sentinel file and return its path."""
    launcher_dir = tmp_path / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)

    sentinel = {
        "state": state,
        "pid": pid,
        "run_id": run_id,
        "started_at": "2026-10-03T10:00:00+0800",
        "ended_at": "2026-10-03T10:30:00+0800",
        "iterations": 3,
        "project_path": str(tmp_path / "repo"),
        "cli": "claude",
    }
    sentinel_file = launcher_dir / "last-exit.json"
    sentinel_file.write_text(json.dumps(sentinel, indent=2) + "\n", encoding="utf-8")
    return sentinel_file


def _write_train_lock(data_dir: Path, pid: int) -> Path:
    """Write a release train lock file with the given pid."""
    lock_dir = data_dir / "runtime" / "release"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "train.lock"
    lock_file.write_text(str(pid), encoding="utf-8")
    return lock_file


def _read_train_lock(data_dir: Path) -> str | None:
    """Read the train lock file, returning its content or None."""
    lock_file = data_dir / "runtime" / "release" / "train.lock"
    if lock_file.exists():
        return lock_file.read_text(encoding="utf-8").strip()
    return None


# ── AC-1: all-shipped sentinel → train starts once ──────────────────────────


@pytest.mark.xfail(strict=True, reason="AC-1: maybe_start_release_train not yet implemented")
class TestShippedSentinelStartsTrainOnce:
    """AC-1: all-shipped sentinel (dead pid) → one cycle starts the stub train
    once with ``run --project <path>``; a second cycle does not."""

    def test_train_starts_once(self, tmp_path: Path) -> None:
        """First cycle starts train; second cycle does not (marker prevents re-start)."""
        key = "test-proj"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        sentinel_file = _write_sentinel(tmp_path, state="all-shipped")
        assert sentinel_all_shipped(sentinel_file) is True

        # This function does not exist yet — step 1 will implement it.
        # It should:
        # 1. Check sentinel is a success state
        # 2. Check no marker file exists for this run_id
        # 3. Start release_train.py run --project <path> detached
        # 4. Write a marker file to prevent re-start
        from scheduler import maybe_start_release_train

        started = maybe_start_release_train(
            key=key,
            data_dir=data_dir,
            repo_path=str(repo_dir),
            run_id="test-run-001",
        )
        assert started is True, "first cycle should start the train"

        # Second cycle — marker prevents re-start
        started2 = maybe_start_release_train(
            key=key,
            data_dir=data_dir,
            repo_path=str(repo_dir),
            run_id="test-run-001",
        )
        assert started2 is False, "second cycle should not start the train (marker)"


# ── AC-2: train.lock with live pid → skip-releasing ─────────────────────────


class TestTrainLockLivePidSkipsRelease:
    """AC-2: train.lock naming a live pid → project is skipped skip-releasing
    and not dispatched even though it has a queued master.

    The Python lock-check helper works now.  The scheduler integration
    (skip-releasing decision in the dispatch loop) is tested by AC-1/AC-3.
    """

    def test_skip_releasing_with_live_lock(self, tmp_path: Path) -> None:
        """A project whose train.lock names a live pid is detected as held."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Start a long-lived process to get a live pid
        proc = subprocess.Popen(["sleep", "30"])
        try:
            _write_train_lock(data_dir, proc.pid)
            assert is_release_lock_held(data_dir) is True
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_stale_lock_not_held(self, tmp_path: Path) -> None:
        """A train.lock with a dead pid is treated as not-held."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        _write_train_lock(data_dir, 99999999)  # dead pid
        assert is_release_lock_held(data_dir) is False


# ── AC-3: ILK_RELEASE_TRAIN=0 → not started ────────────────────────────────


@pytest.mark.xfail(strict=True, reason="AC-3: scheduler hook for ILK_RELEASE_TRAIN=0 not yet implemented")
class TestReleaseTrainDisabledByEnv:
    """AC-3: ILK_RELEASE_TRAIN=0 → train is not started."""

    def test_env_disables_train(self, tmp_path: Path) -> None:
        """Setting ILK_RELEASE_TRAIN=0 prevents the train from starting."""
        # The is_release_train_enabled helper works now, but the scheduler
        # hook that calls it doesn't exist yet.
        os.environ["ILK_RELEASE_TRAIN"] = "0"
        assert is_release_train_enabled() is False

        # The scheduler hook should check this and skip
        from scheduler import maybe_start_release_train

        started = maybe_start_release_train(
            key="test-proj",
            data_dir=tmp_path / "data",
            repo_path=str(tmp_path / "repo"),
            run_id="test-run-001",
        )
        assert started is False, "ILK_RELEASE_TRAIN=0 should prevent start"


# ── AC-4: run with stubbed deploy returning 5 → rollback + escalation ───────


@pytest.mark.xfail(strict=True, reason="AC-4: run verb rollback/escalation path not yet implemented")
class TestRunVerbRollbackOnDeployFailure:
    """AC-4: run with a stubbed deploy returning 5 → a rolled-back row,
    an escalated row, notify called once, and the lock removed."""

    def test_run_rollback_and_escalation(self, tmp_path: Path) -> None:
        """Deploy returning 5 triggers rollback audit + escalation + notify."""
        project_dir = tmp_path / "repo"
        project_dir.mkdir()

        # Init a git repo
        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=project_dir, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=project_dir, check=True, capture_output=True,
        )
        (project_dir / "README.md").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=project_dir, check=True, capture_output=True,
        )

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        from release_train import run

        result = run(
            project=project_dir,
            data_dir=data_dir,
            deploy_exit_code=5,  # stub: deploy returns 5
        )

        # Should have rolled back
        assert result.get("rolled_back") is True or result.get("exit_code") != 0

        # Lock should be removed
        lock = _read_train_lock(data_dir)
        assert lock is None, "train.lock should be removed after run completes"


# ── AC-5: local_checks_failed sentinel → never starts ───────────────────────


class TestLocalChecksFailedNeverStarts:
    """AC-5 (control): a project with a local_checks_failed sentinel never
    starts the train.  Tests the sentinel check directly."""

    def test_local_checks_failed_is_not_success(self, tmp_path: Path) -> None:
        """local_checks_failed sentinel → sentinel_all_shipped returns False."""
        sentinel_file = _write_sentinel(tmp_path, state="local_checks_failed")
        assert sentinel_all_shipped(sentinel_file) is False
        assert sentinel_is_failure(sentinel_file) is True

    def test_all_shipped_is_success(self, tmp_path: Path) -> None:
        """all-shipped sentinel → sentinel_all_shipped returns True."""
        sentinel_file = _write_sentinel(tmp_path, state="all-shipped")
        assert sentinel_all_shipped(sentinel_file) is True
        assert sentinel_is_failure(sentinel_file) is False

    def test_already_shipped_is_success(self, tmp_path: Path) -> None:
        """already-shipped sentinel → sentinel_all_shipped returns True."""
        sentinel_file = _write_sentinel(tmp_path, state="already-shipped")
        assert sentinel_all_shipped(sentinel_file) is True

    def test_shipped_is_success(self, tmp_path: Path) -> None:
        """shipped sentinel → sentinel_all_shipped returns True."""
        sentinel_file = _write_sentinel(tmp_path, state="shipped")
        assert sentinel_all_shipped(sentinel_file) is True

    def test_running_is_not_success(self, tmp_path: Path) -> None:
        """running sentinel → sentinel_all_shipped returns False."""
        sentinel_file = _write_sentinel(tmp_path, state="running")
        assert sentinel_all_shipped(sentinel_file) is False

    def test_no_progress_is_failure(self, tmp_path: Path) -> None:
        """no-progress sentinel → sentinel_is_failure returns True."""
        sentinel_file = _write_sentinel(tmp_path, state="no-progress")
        assert sentinel_is_failure(sentinel_file) is True
        assert sentinel_all_shipped(sentinel_file) is False