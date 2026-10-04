"""Red-first pins: sentinel_all_shipped accepts blocked-no-runnable when every
active master's sub-plans are shipped.

Sub-plan: the-train-starts-after-a-real-batch, step 0.

Each test builds a throwaway data root under ``tmp_path`` with a fake project,
master and sub-plans.  No real ``~/.ilk-data``, no real ``claude``, no real
``launchctl``, no real ``release_train.py``, and no real ``scheduler.sh`` are
touched.

Seven acceptance criteria:
  AC-1  blocked-no-runnable + shipped master + 2 shipped sub-plans → True.
  AC-2  batch-h shape: master A shipped + master B queued with 3 pending → True.
  AC-3  (control) active master 1 shipped + 1 blocked → False; 1 shipped + 1
        pending → False; registered sub-plan file missing → False.
  AC-4  (control) iterations: 0 → False; held_by set → False; no MASTER in
        plans dir → False; no plans dir → False.
  AC-5  (control) all-shipped / already-shipped / shipped stay True with NO
        plans dir; running / local_checks_failed / no-progress stay False.
  AC-6  default plans_dir resolves from sentinel path → True.
  AC-7  (control) every test in test_a_shipped_batch_starts_the_release_train.py
        passes unchanged (not re-tested here — that file's own gate covers it).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"

sys.path.insert(0, str(WATCHDOG_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train_dispatch import sentinel_all_shipped  # noqa: E402


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
    path: Path,
    *,
    state: str = "blocked-no-runnable",
    iterations: int = 3,
    held_by: str | None = None,
) -> Path:
    """Write a sentinel file at *path* and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "state": state,
        "pid": 99999999,
        "run_id": "test-run-001",
        "started_at": "2026-10-04T10:00:00+0800",
        "ended_at": "2026-10-04T10:30:00+0800",
        "iterations": iterations,
        "project_path": str(path.parent.parent.parent / "repo"),
        "cli": "claude",
    }
    if held_by is not None:
        data["held_by"] = held_by
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _write_master(
    plans_dir: Path,
    filename: str,
    *,
    status: str = "active",
    subplan_files: list[str] | None = None,
) -> Path:
    """Write a MASTER plan that registers *subplan_files* in a table."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    rows = ""
    if subplan_files:
        for i, fname in enumerate(subplan_files):
            rows += f"| {i} | [{fname}](./{fname}) | item {i} | 2 | pending |\n"
    body = f"""---
master_plan: test-batch
batch_date: 2026-10-04
status: {status}
total_tickets: {len(subplan_files or [])}
---

# MASTER plan: test batch

## Sub-plan registry

| # | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|
{rows}"""
    (plans_dir / filename).write_text(body, encoding="utf-8")
    return plans_dir / filename


def _write_subplan(
    plans_dir: Path,
    filename: str,
    *,
    status: str = "pending",
) -> Path:
    """Write a minimal sub-plan file."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = f"""---
plan: {filename.replace('.md', '')}
status: {status}
current_step: 0
estimated_steps: 2
---

# Sub-plan: {filename}
"""
    (plans_dir / filename).write_text(body, encoding="utf-8")
    return plans_dir / filename


# ── AC-1: blocked-no-runnable + shipped master + shipped sub-plans → True ───


@pytest.mark.xfail(strict=True, reason="AC-1: blocked-no-runnable not yet accepted")
def test_ac1_blocked_no_runnable_all_shipped(tmp_path: Path) -> None:
    """A blocked-no-runnable sentinel where every active master's sub-plans
    shipped should return True (the train should start)."""
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"
    sentinel_file = data_dir / "runtime" / "launcher" / "last-exit.json"

    _write_sentinel(sentinel_file, state="blocked-no-runnable", iterations=3)
    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="shipped",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-04-b.md", status="shipped")

    assert sentinel_all_shipped(sentinel_file, plans_dir=plans_dir) is True


@pytest.mark.xfail(strict=True, reason="AC-1b: active master (not shipped) with all sub-plans shipped")
def test_ac1b_active_master_all_subplans_shipped(tmp_path: Path) -> None:
    """Same as AC-1 but the master is still status: active (not yet flipped
    to shipped).  Should still return True."""
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"
    sentinel_file = data_dir / "runtime" / "launcher" / "last-exit.json"

    _write_sentinel(sentinel_file, state="blocked-no-runnable", iterations=3)
    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="active",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-04-b.md", status="shipped")

    assert sentinel_all_shipped(sentinel_file, plans_dir=plans_dir) is True


# ── AC-2: batch-h shape → True ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="AC-2: batch-h shape not yet accepted")
def test_ac2_batch_h_shape(tmp_path: Path) -> None:
    """Master A shipped (all sub-plans shipped) + master B queued with 3 pending
    sub-plans → True (only active masters are checked)."""
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"
    sentinel_file = data_dir / "runtime" / "launcher" / "last-exit.json"

    _write_sentinel(sentinel_file, state="blocked-no-runnable", iterations=2)

    # Master A: shipped, all sub-plans shipped
    _write_master(
        plans_dir,
        "MASTER-2026-10-03-a.md",
        status="shipped",
        subplan_files=["2026-10-03-x.md", "2026-10-03-y.md"],
    )
    _write_subplan(plans_dir, "2026-10-03-x.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-03-y.md", status="shipped")

    # Master B: queued, sub-plans pending (not active, so not checked)
    _write_master(
        plans_dir,
        "MASTER-2026-10-04-b.md",
        status="queued",
        subplan_files=["2026-10-04-p.md", "2026-10-04-q.md", "2026-10-04-r.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-p.md", status="pending")
    _write_subplan(plans_dir, "2026-10-04-q.md", status="pending")
    _write_subplan(plans_dir, "2026-10-04-r.md", status="pending")

    assert sentinel_all_shipped(sentinel_file, plans_dir=plans_dir) is True


# ── AC-3: (control) not all shipped → False ─────────────────────────────────


def test_ac3_blocked_no_runnable_is_false(tmp_path: Path) -> None:
    """blocked-no-runnable → False on today's code (no plans_dir check yet).

    In step 1, this test will be updated to pass plans_dir and verify the
    enhanced logic (active master with mixed sub-plans still → False).
    """
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac3_active_master_mixed_subplans_shipped_blocked(tmp_path: Path) -> None:
    """Active master with 1 shipped + 1 blocked → False.

    Builds the plans structure now; will call with plans_dir in step 1.
    Today the function returns False for blocked-no-runnable unconditionally.
    """
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"

    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="active",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-04-b.md", status="blocked")

    sentinel_file = _write_sentinel(
        data_dir / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
    )
    # Today: blocked-no-runnable → False unconditionally
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac3_active_master_mixed_subplans_shipped_pending(tmp_path: Path) -> None:
    """Active master with 1 shipped + 1 pending → False.

    Builds the plans structure now; will call with plans_dir in step 1.
    """
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"

    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="active",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-04-b.md", status="pending")

    sentinel_file = _write_sentinel(
        data_dir / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac3_registered_subplan_file_missing(tmp_path: Path) -> None:
    """Active master registers a sub-plan whose file is missing → False.

    Builds the plans structure now; will call with plans_dir in step 1.
    """
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"

    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="active",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    # 2026-10-04-b.md deliberately not written

    sentinel_file = _write_sentinel(
        data_dir / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
    )
    assert sentinel_all_shipped(sentinel_file) is False


# ── AC-4: (control) guard conditions → False ────────────────────────────────


def test_ac4_iterations_zero(tmp_path: Path) -> None:
    """blocked-no-runnable with iterations: 0 → False (pre-loop exit).

    Today the function returns False for blocked-no-runnable unconditionally.
    In step 1, this test will be updated to pass plans_dir and verify the
    iterations guard.
    """
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
        iterations=0,
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac4_held_by_set(tmp_path: Path) -> None:
    """blocked-no-runnable with held_by set → False (human park).

    Today the function returns False for blocked-no-runnable unconditionally.
    In step 1, this test will be updated to pass plans_dir and verify the
    held_by guard.
    """
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
        iterations=3,
        held_by="human",
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac4_no_master_in_plans_dir(tmp_path: Path) -> None:
    """blocked-no-runnable with no MASTER in plans dir → False.

    Today the function returns False for blocked-no-runnable unconditionally.
    In step 1, this test will be updated to pass an empty plans_dir and verify
    the no-master guard.
    """
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
        iterations=3,
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac4_no_plans_dir(tmp_path: Path) -> None:
    """blocked-no-runnable with no plans dir at all → False.

    Today the function returns False for blocked-no-runnable unconditionally.
    In step 1, this test will be updated to pass a nonexistent plans_dir path
    and verify the OSError guard.
    """
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="blocked-no-runnable",
        iterations=3,
    )
    assert sentinel_all_shipped(sentinel_file) is False


# ── AC-5: (control) old states work without plans_dir ───────────────────────


def test_ac5_all_shipped_stays_true_no_plans_dir(tmp_path: Path) -> None:
    """all-shipped → True even without a plans dir."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="all-shipped",
    )
    assert sentinel_all_shipped(sentinel_file) is True


def test_ac5_already_shipped_stays_true_no_plans_dir(tmp_path: Path) -> None:
    """already-shipped → True even without a plans dir."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="already-shipped",
    )
    assert sentinel_all_shipped(sentinel_file) is True


def test_ac5_shipped_stays_true_no_plans_dir(tmp_path: Path) -> None:
    """shipped → True even without a plans dir."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="shipped",
    )
    assert sentinel_all_shipped(sentinel_file) is True


def test_ac5_running_stays_false(tmp_path: Path) -> None:
    """running → False."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="running",
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac5_local_checks_failed_stays_false(tmp_path: Path) -> None:
    """local_checks_failed → False."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="local_checks_failed",
    )
    assert sentinel_all_shipped(sentinel_file) is False


def test_ac5_no_progress_stays_false(tmp_path: Path) -> None:
    """no-progress → False."""
    sentinel_file = _write_sentinel(
        tmp_path / "runtime" / "launcher" / "last-exit.json",
        state="no-progress",
    )
    assert sentinel_all_shipped(sentinel_file) is False


# ── AC-6: default plans_dir resolves from sentinel path ─────────────────────


@pytest.mark.xfail(strict=True, reason="AC-6: default plans_dir resolution not yet implemented")
def test_ac6_default_plans_dir_from_sentinel_path(tmp_path: Path) -> None:
    """A sentinel at <tmp>/projects/k/runtime/launcher/last-exit.json
    resolves <tmp>/projects/k/plans as the default plans_dir."""
    data_dir = tmp_path / "projects" / "k"
    plans_dir = data_dir / "plans"
    sentinel_file = data_dir / "runtime" / "launcher" / "last-exit.json"

    _write_sentinel(sentinel_file, state="blocked-no-runnable", iterations=3)
    _write_master(
        plans_dir,
        "MASTER-2026-10-04-batch.md",
        status="active",
        subplan_files=["2026-10-04-a.md", "2026-10-04-b.md"],
    )
    _write_subplan(plans_dir, "2026-10-04-a.md", status="shipped")
    _write_subplan(plans_dir, "2026-10-04-b.md", status="shipped")

    # Call without plans_dir — should resolve from sentinel path
    assert sentinel_all_shipped(sentinel_file) is True