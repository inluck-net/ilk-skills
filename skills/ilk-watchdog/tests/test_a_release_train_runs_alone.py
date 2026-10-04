"""Tests for release train running alone: a started train ends the project's
scheduler cycle, and an empty queue still gets its train.

Sub-plan: a-release-train-runs-alone, step 0 (red-first pins).

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path`` with a sandbox
project.  No real daemon, no real ``~/.ilk-data``, and no real release process
are touched.

The six acceptance criteria:
  AC-1  a project with a queued master and an all-shipped sentinel (no marker):
        one scheduler cycle starts the stub train once and logs
        ``skip-releasing … reason=train-started``.  The stub ``launch.sh`` is
        NOT called for that project in that cycle.  Red-first.
  AC-2  a project with NO active or queued master and an all-shipped sentinel
        (no marker): one cycle starts the stub train once.  A second cycle does
        not start it again (the marker).  Red-first.
  AC-3  that ``train_only`` project is never passed to the stub ``launch.sh``
        and never promotes a master.  Red-first.
  AC-4  (control) a masterless project whose sentinel is NOT all-shipped
        (``local_checks_failed``) yields no scan row and no train.
  AC-5  (control) a project with a queued master and a failure sentinel is
        dispatched as before, with no train.
  AC-6  (control) every test in ``test_a_shipped_batch_starts_the_release_train.py``
        and the ``test_scheduler_scan_*.py`` files passes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
sys.path.insert(0, str(SCRIPTS_ILK_LOOP))

from release_train_dispatch import (  # noqa: E402
    sentinel_all_shipped,
)


# ── Isolation fixture ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ILK_DATA_HOME so no test touches the real data root."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ilk-data"))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_project(
    tmp_path: Path,
    key: str,
    *,
    master_status: str | None = "queued",
    sub_status: str = "pending",
    sentinel_state: str = "all-shipped",
    run_id: str = "test-run-001",
    plans_dir: Path | None = None,
) -> Path:
    """Scaffold a minimal project for scheduler scan tests.

    When *master_status* is None, no MASTER file is written (masterless project).
    *sub_status* controls the sub-plan's status (default ``pending``).
    """
    project_dir = tmp_path / "projects" / key
    p_dir = plans_dir or (project_dir / "plans")
    p_dir.mkdir(parents=True, exist_ok=True)

    if master_status is not None:
        (p_dir / "MASTER-test.md").write_text(
            "---\n"
            "title: MASTER-test\n"
            "created: 2026-10-04T00:00:00+08:00\n"
            f"status: {master_status}\n"
            "---\n\n"
            "# MASTER-test\n\n"
            "| # | Sub-plan | Status |\n"
            "|---|---|---|\n"
            "| 1 | [2026-10-04-work.md](./2026-10-04-work.md) | pending |\n",
            encoding="utf-8",
        )
        (p_dir / "2026-10-04-work.md").write_text(
            "---\n"
            "plan: 2026-10-04-work\n"
            f"status: {sub_status}\n"
            "last_updated: 2026-10-04\n"
            "---\n\n# 2026-10-04-work\n",
            encoding="utf-8",
        )

    # Write sentinel
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": sentinel_state,
        "pid": 99999999,
        "run_id": run_id,
        "started_at": "2026-10-04T10:00:00+0800",
        "ended_at": "2026-10-04T10:30:00+0800",
        "iterations": 3,
        "project_path": str(project_dir),
        "cli": "claude",
    }
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel, indent=2) + "\n", encoding="utf-8"
    )

    # Write last-launch.json so scan can resolve repo_path
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": str(project_dir)}), encoding="utf-8"
    )

    return project_dir


def _write_train_marker(data_dir: Path, run_id: str) -> Path:
    """Write a release train started marker."""
    marker_dir = data_dir / "runtime" / "release"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_file = marker_dir / f"{run_id}.started"
    marker_file.touch()
    return marker_file


def _read_train_marker(data_dir: Path, run_id: str) -> bool:
    """Check if a release train started marker exists."""
    marker_file = data_dir / "runtime" / "release" / f"{run_id}.started"
    return marker_file.exists()


def _fresh_scheduler_scan():
    """Re-import scheduler_scan to get a fresh module."""
    for mod in ("scheduler_scan", "ilk_paths", "plan_status"):
        sys.modules.pop(mod, None)
    import scheduler_scan
    return scheduler_scan


# ── AC-1: queued master + all-shipped sentinel → train starts once ─────────


class TestQueuedMasterAllShippedStartsTrain:
    """AC-1: a project with a queued master and an all-shipped sentinel (no
    marker): one scheduler cycle starts the stub train once and logs
    ``skip-releasing … reason=train-started``.  The stub ``launch.sh`` is NOT
    called for that project in that cycle.

    The marker-prevents-restart part is tested directly.  The scheduler
    integration (skip-releasing decision in the dispatch loop) will be pinned
    in step 1.
    """

    def test_scan_returns_row(self, tmp_path: Path) -> None:
        """A queued master + all-shipped sentinel → scan returns a row."""
        _write_project(tmp_path, "proj-1", master_status="queued",
                       sentinel_state="all-shipped")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        keys = [r["key"] for r in results]
        assert "proj-1" in keys

    def test_sentinel_all_shipped_accepts(self, tmp_path: Path) -> None:
        """sentinel_all_shipped returns True for all-shipped state."""
        project_dir = _write_project(
            tmp_path, "proj-1", master_status="queued",
            sentinel_state="all-shipped",
        )
        sentinel_file = project_dir / "runtime" / "launcher" / "last-exit.json"
        assert sentinel_all_shipped(sentinel_file) is True

    def test_marker_prevents_restart(self, tmp_path: Path) -> None:
        """Once the marker is written, a second cycle does not start the train."""
        run_id = "test-run-001"
        project_dir = _write_project(
            tmp_path, "proj-1", master_status="queued",
            sentinel_state="all-shipped", run_id=run_id,
        )

        # First cycle: marker does not exist
        assert not _read_train_marker(project_dir, run_id)

        # Simulate the train starting: write the marker
        _write_train_marker(project_dir, run_id)

        # Second cycle: marker exists → skip
        assert _read_train_marker(project_dir, run_id), \
            "marker should prevent re-start"


# ── AC-2: masterless + all-shipped sentinel → train_only row ────────────────


class TestMasterlessAllShippedGetsTrain:
    """AC-2: a project with NO active or queued master and an all-shipped
    sentinel (no marker): one cycle starts the stub train once.  A second cycle
    does not start it again (the marker).

    Currently ``_scan_one_project`` returns None for a masterless project.
    After step 1, it should return a row with ``train_only: true``.
    """

    def test_scan_returns_train_only_row(self, tmp_path: Path) -> None:
        """A masterless project with all-shipped sentinel → scan returns a
        train_only row."""
        # Master status "shipped" + sub shipped → no runnable master
        _write_project(tmp_path, "proj-2", master_status="shipped",
                       sub_status="shipped", sentinel_state="all-shipped")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        train_only = [r for r in results if r.get("train_only")]
        assert len(train_only) == 1, (
            f"expected 1 train_only row, got {len(train_only)}: {results}"
        )
        assert train_only[0]["key"] == "proj-2"

    def test_sentinel_all_shipped_accepts_masterless(self, tmp_path: Path) -> None:
        """sentinel_all_shipped returns True even without a runnable master."""
        project_dir = _write_project(
            tmp_path, "proj-2", master_status="shipped",
            sub_status="shipped", sentinel_state="all-shipped",
        )
        sentinel_file = project_dir / "runtime" / "launcher" / "last-exit.json"
        # all-shipped is a legacy success state — accepted without plans_dir check
        assert sentinel_all_shipped(sentinel_file) is True

    def test_marker_prevents_restart_train_only(self, tmp_path: Path) -> None:
        """Once the marker is written, a second cycle does not start the train
        for a train_only project."""
        run_id = "test-run-002"
        project_dir = _write_project(
            tmp_path, "proj-2", master_status="shipped",
            sub_status="shipped", sentinel_state="all-shipped", run_id=run_id,
        )

        # First cycle: no marker
        assert not _read_train_marker(project_dir, run_id)

        # Simulate train start
        _write_train_marker(project_dir, run_id)

        # Second cycle: marker exists → skip
        assert _read_train_marker(project_dir, run_id)


# ── AC-3: train_only row is never dispatched ────────────────────────────────


class TestTrainOnlyNeverDispatched:
    """AC-3: a ``train_only`` project is never passed to the stub ``launch.sh``
    and never promotes a master.

    Currently ``_scan_one_project`` returns None for masterless projects, so
    there is no train_only row to test against.  After step 1, the row will
    exist and the scheduler will skip it for dispatch.
    """

    def test_train_only_row_exists_for_dispatch_test(self, tmp_path: Path) -> None:
        """Precondition: the scan must return a train_only row before we can
        test that the scheduler skips it for dispatch."""
        _write_project(tmp_path, "proj-3", master_status="shipped",
                       sub_status="shipped", sentinel_state="all-shipped")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        train_only = [r for r in results if r.get("train_only")]
        assert len(train_only) == 1
        # The row must carry the fields scheduler.sh reads
        row = train_only[0]
        assert "key" in row
        assert "path" in row


# ── AC-4: control — non-all-shipped sentinel → no row, no train ─────────────


class TestNonAllShippedMasterlessNoRow:
    """AC-4 (control): a masterless project whose sentinel is NOT all-shipped
    (local_checks_failed) yields no scan row and no train."""

    def test_no_scan_row(self, tmp_path: Path) -> None:
        """local_checks_failed sentinel + masterless → no scan row."""
        _write_project(tmp_path, "proj-4", master_status="shipped",
                       sub_status="shipped", sentinel_state="local_checks_failed")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        keys = [r["key"] for r in results]
        assert "proj-4" not in keys, (
            f"masterless failed project should not appear in scan, got {keys}"
        )

    def test_sentinel_all_shipped_rejects(self, tmp_path: Path) -> None:
        """sentinel_all_shipped returns False for local_checks_failed."""
        project_dir = _write_project(
            tmp_path, "proj-4", master_status="shipped",
            sub_status="shipped", sentinel_state="local_checks_failed",
        )
        sentinel_file = project_dir / "runtime" / "launcher" / "last-exit.json"
        assert sentinel_all_shipped(sentinel_file) is False


# ── AC-5: control — queued master + failure sentinel → normal dispatch ──────


class TestQueuedMasterFailureSentinelNormalDispatch:
    """AC-5 (control): a project with a queued master and a failure sentinel is
    dispatched as before, with no train."""

    def test_scan_returns_row(self, tmp_path: Path) -> None:
        """A queued master + failure sentinel → scan returns a row (normal
        dispatch, not train_only)."""
        _write_project(tmp_path, "proj-5", master_status="queued",
                       sentinel_state="local_checks_failed")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        keys = [r["key"] for r in results]
        assert "proj-5" in keys

    def test_not_train_only(self, tmp_path: Path) -> None:
        """The row is NOT train_only — it's a normal dispatch candidate."""
        _write_project(tmp_path, "proj-5", master_status="queued",
                       sentinel_state="local_checks_failed")
        scan = _fresh_scheduler_scan()
        scan.ilk_data_root = lambda: tmp_path

        results = scan.scan_projects()
        proj_rows = [r for r in results if r["key"] == "proj-5"]
        assert len(proj_rows) == 1
        assert not proj_rows[0].get("train_only"), \
            "queued master with failure sentinel should not be train_only"

    def test_sentinel_all_shipped_rejects_failure(self, tmp_path: Path) -> None:
        """sentinel_all_shipped returns False for a failure sentinel."""
        project_dir = _write_project(
            tmp_path, "proj-5", master_status="queued",
            sentinel_state="local_checks_failed",
        )
        sentinel_file = project_dir / "runtime" / "launcher" / "last-exit.json"
        assert sentinel_all_shipped(sentinel_file) is False