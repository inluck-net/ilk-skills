"""Red-first tests for the batch-gate append freeze.

AC-1:  while the batch gate is running, an attempt to append a sub-plan to
       that master is refused, with a message naming the batch, the gate's
       start time, and the reason.
AC-1b: the refusal is exercised through the real append path — the same
       entry point /ilk-plan workflow #3 uses.
AC-2:  the refusal defers rather than discards — the refused sub-plan is
       routed to the next batch, and the caller can distinguish "deferred"
       from "rejected".
AC-3:  appending before the gate starts still works exactly as today.
AC-4:  the freeze lifts once the gate completes, whatever its verdict
       (pass or fail).
AC-5:  a stale running-state does not freeze the queue forever — if the
       gate's process is gone but the state still says "running", the
       freeze must resolve rather than block indefinitely.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_running_marker(runtime_dir: Path, pid: int, started_at: str) -> None:
    """Write a batch-gate.running marker with pid and start time."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = runtime_dir / "batch-gate.running"
    data = {"pid": pid, "started_at": started_at}
    marker.write_text(json.dumps(data), encoding="utf-8")


def _read_running_marker(runtime_dir: Path) -> dict | None:
    """Read the running marker, returning None if absent."""
    marker = runtime_dir / "batch-gate.running"
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _make_master_plan(plans_dir: Path, slug: str = "my-batch") -> Path:
    """Create a minimal master plan file with a sub-plan registry."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = plans_dir / f"MASTER-2026-08-25-{slug}-execution-plan.md"
    master.write_text(
        "---\n"
        f"master_plan: 2026-08-25-{slug}\n"
        "batch_date: 2026-08-25\n"
        "status: active\n"
        "total_tickets: 1\n"
        "---\n\n"
        f"# MASTER plan: {slug}\n\n"
        "## Sub-plan registry\n\n"
        "| # | Slug | Steps | Status |\n"
        "|---|---|---|---|\n"
        "| 1 | existing-sub | 3 | in-progress |\n",
        encoding="utf-8",
    )
    return master


# ── AC-1: refused while running ─────────────────────────────────────────────

class TestAC1RefusedWhileRunning:
    """Appending a sub-plan while the batch gate is running must be refused."""

    def test_refusal_names_batch_and_reason(self, tmp_path: Path) -> None:
        """AC-1: refusal message includes batch name, start time, and reason."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        _write_running_marker(runtime, pid=os.getpid(), started_at="2026-08-25T10:00:00+08:00")

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="new-sub",
            body="# New sub-plan\n",
        )
        assert result.status == "deferred"
        assert "batch" in result.message.lower() or "gate" in result.message.lower()
        assert "2026-08-25T10:00:00" in result.message


# ── AC-1b: refusal exercised through the real append path ───────────────────

class TestAC1bThroughAppendPath:
    """The freeze must be checked in the actual append entry point, not just
    in a predicate called in isolation."""

    def test_append_entry_point_checks_freeze(self, tmp_path: Path) -> None:
        """AC-1b: calling the append function (the real path) respects the freeze."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        _write_running_marker(runtime, pid=os.getpid(), started_at="2026-08-25T10:00:00+08:00")

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="deferred-sub",
            body="# Deferred\n",
        )
        # Must be deferred, not silently appended
        assert result.status == "deferred"
        # The sub-plan file must NOT exist in the plans dir
        assert not (plans / "2026-08-25-deferred-sub.md").exists()


# ── AC-2: defers rather than discards ───────────────────────────────────────

class TestAC2DefersNotDiscards:
    """The refusal must route the sub-plan to the next batch, not drop it."""

    def test_deferred_result_is_distinguishable(self, tmp_path: Path) -> None:
        """AC-2: caller can tell 'deferred' from 'rejected'."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        _write_running_marker(runtime, pid=os.getpid(), started_at="2026-08-25T10:00:00+08:00")

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="new-sub",
            body="# body\n",
        )
        # status must be the string "deferred", not "rejected" or "error"
        assert result.status == "deferred"
        # deferred_to must name the next batch or be explicitly set
        assert result.deferred_to is not None


# ── AC-3: appending before gate starts still works ──────────────────────────

class TestAC3AppendBeforeGateStarts:
    """Appending when no running marker exists must succeed exactly as today."""

    def test_append_succeeds_without_running_marker(self, tmp_path: Path) -> None:
        """AC-3: no marker → append proceeds normally."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        # No running marker written

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="normal-sub",
            body="# Normal sub-plan\n",
        )
        assert result.status == "appended"
        # The sub-plan file should exist
        assert (plans / "2026-08-25-normal-sub.md").exists()


# ── AC-4: freeze lifts after gate completes ─────────────────────────────────

class TestAC4FreezeLiftsOnCompletion:
    """The freeze must lift once the gate completes, pass or fail."""

    def test_append_allowed_after_gate_passes(self, tmp_path: Path) -> None:
        """AC-4: gate passed → marker removed → append works."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        # Write then remove running marker (gate completed)
        _write_running_marker(runtime, pid=99999, started_at="2026-08-25T10:00:00+08:00")
        (runtime / "batch-gate.running").unlink()

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="post-pass",
            body="# After pass\n",
        )
        assert result.status == "appended"

    def test_append_allowed_after_gate_fails(self, tmp_path: Path) -> None:
        """AC-4: gate failed → marker removed → append works."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        # Write then remove running marker (gate completed with failure)
        _write_running_marker(runtime, pid=99999, started_at="2026-08-25T10:00:00+08:00")
        (runtime / "batch-gate.running").unlink()

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="post-fail",
            body="# After fail\n",
        )
        assert result.status == "appended"


# ── AC-5: stale running-state resolves ──────────────────────────────────────

class TestAC5StaleRunningState:
    """A stale running marker (pid dead) must not freeze the queue forever."""

    def test_dead_pid_resolves_stale_marker(self, tmp_path: Path) -> None:
        """AC-5: pid is gone → marker is stale → append works."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        # Use a pid that definitely does not exist
        _write_running_marker(runtime, pid=99999, started_at="2026-08-25T10:00:00+08:00")

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="stale-test",
            body="# Stale test\n",
        )
        # Stale marker → append should proceed
        assert result.status == "appended"

    def test_live_pid_freezes_queue(self, tmp_path: Path) -> None:
        """AC-5: pid is alive → marker is fresh → freeze applies."""
        from batch_gate import append_subplan_if_allowed

        runtime = tmp_path / "runtime"
        plans = tmp_path / "plans"
        _make_master_plan(plans)
        # Use our own pid — it's alive
        _write_running_marker(runtime, pid=os.getpid(), started_at="2026-08-25T10:00:00+08:00")

        result = append_subplan_if_allowed(
            plans_dir=plans,
            runtime_dir=runtime,
            slug="live-test",
            body="# Live test\n",
        )
        # Live pid → freeze applies → deferred
        assert result.status == "deferred"
