"""Red-first: the xbar row names the gate the runner is in.

Sub-plan `the-panel-shows-the-gate` (SP2 of
MASTER-2026-09-29d-the-panel-names-the-runners-phase).

Covers:
  AC-1 — `status_all` emits ``phase``, ``phase_slug``, ``phase_step`` and
         ``phase_elapsed_s``.  All null unless ``sentinel.alive`` and
         ``phase.json`` parses and its ``pid`` equals the sentinel pid and
         is alive and its ``run_id`` equals the sentinel's.  Never raises.
  AC-2 — phase ``gate`` or ``batch-gate`` ⇒ ``render_xbar`` replaces the
         heartbeat fragment with ``gate <phase_slug> <phase_step> · <elapsed>``
         (``batch gate · <elapsed>`` for batch-gate).
  AC-3 — When ``next_subplan`` or ``active_master`` is empty but
         ``phase_slug`` is set, the row still names ``phase_slug``.
  AC-4 — phase ``agent`` or ``between``, or fields absent/null ⇒ today's
         row, byte-identical.  A pin covers the pre-field payload.
  AC-5 — ``render_tray.py`` renders a payload carrying the new fields
         without error.
  AC-6 — Replay pin: a payload shaped like status_all at 09:09
         (active_master "", iteration 1, heartbeat_s 880) plus phase gate
         ``contract-gates-batch-verify`` step 0 renders
         ``gate contract-gates-batch-verify 0 · …``, with no ``♥``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "xbar"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "tray"))

from render_xbar import render_xbar  # noqa: E402
from render_tray import render_tray  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────

def _entry(
    key: str = "proj",
    *,
    alive: bool = True,
    step: str = "some-subplan 2/6",
    next_subplan: str = "2026-09-29-some-subplan.md",
    active_master: str = "MASTER-2026-09-29d.md",
    heartbeat: dict | None = None,
    phase: dict | None = None,
) -> dict:
    """A status_all entry.  ``heartbeat=None`` means liveness fields absent."""
    e = {
        "project_key": key,
        "path": f"/fake/{key}",
        "repo_path": None,
        "orphaned": False,
        "active_master": active_master,
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": {"pid": 4242 if alive else 0,
                     "state": "running" if alive else "shipped",
                     "alive": alive},
        "last_class": None,
        "model": "sonnet" if alive else "",
        "runnable": False,
        "parked": False,
        "manually_runnable": not alive,
        "blocked": False,
        "blocked_reason": None,
    }
    if heartbeat is not None:
        e.update(heartbeat)
    if phase is not None:
        e.update(phase)
    return e


def _row_for(out: str, key: str) -> str:
    """Return the single menu row mentioning ``key`` (excluding sub-items)."""
    rows = [l for l in out.splitlines()
            if key in l and not l.startswith("--")]
    assert len(rows) == 1, f"expected 1 row for {key}, got {rows}"
    return rows[0]


# ── AC-1: status_all payload fields ────────────────────────────────

def test_status_all_emits_phase_fields_when_alive():
    """AC-1: status_all returns phase/phase_slug/phase_step/phase_elapsed_s."""
    from status_all import resolve_project_status
    from unittest.mock import patch
    import tempfile, os

    # Build a minimal project data dir with sentinel + phase.json
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "projects" / "proj"
        (proj / "plans").mkdir(parents=True)
        launcher = proj / "runtime" / "launcher"
        launcher.mkdir(parents=True)
        # sentinel: alive
        (launcher / "last-exit.json").write_text(
            '{"state":"running","pid":4242,"run_id":"20260929-083918"}'
        )
        # phase.json: gate
        (launcher / "phase.json").write_text(
            '{"phase":"gate","slug":"contract-gates-batch-verify","step":0,'
            '"started_at":1727570000,"pid":4242,"run_id":"20260929-083918"}'
        )
        with patch("status_all.ilk_pid_alive", return_value=True), \
             patch("status_all.pid_alive", return_value=True), \
             patch("status_all.external_launcher_dir", return_value=launcher):
            result = resolve_project_status(proj)
    # Fields must be PRESENT and populated (sentinel alive + phase.json valid)
    assert "phase" in result, "phase key missing from payload"
    assert result["phase"] is not None, "phase must be populated when alive"
    assert "phase_slug" in result
    assert result["phase_slug"] is not None
    assert "phase_step" in result
    assert result["phase_step"] is not None
    assert "phase_elapsed_s" in result
    assert result["phase_elapsed_s"] is not None


def test_status_all_phase_null_when_sentinel_dead():
    """AC-1: stale phase.json (dead pid) ⇒ phase fields present but null."""
    from status_all import resolve_project_status
    from unittest.mock import patch
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "projects" / "proj"
        (proj / "plans").mkdir(parents=True)
        (proj / "runtime" / "launcher").mkdir(parents=True)
        (proj / "runtime" / "launcher" / "last-exit.json").write_text(
            '{"state":"running","pid":4242,"run_id":"20260929-083918"}'
        )
        (proj / "runtime" / "launcher" / "phase.json").write_text(
            '{"phase":"gate","slug":"contract-gates-batch-verify","step":0,'
            '"started_at":1727570000,"pid":4242,"run_id":"20260929-083918"}'
        )
        with patch("status_all.pid_alive", return_value=False):
            result = resolve_project_status(proj)
    # Fields must be PRESENT (status_all emits them) but null (stale = absent)
    assert "phase" in result, "phase key missing from payload"
    assert result["phase"] is None, "stale phase must be null"
    assert "phase_slug" in result
    assert result["phase_slug"] is None
    assert "phase_step" in result
    assert result["phase_step"] is None
    assert "phase_elapsed_s" in result
    assert result["phase_elapsed_s"] is None


# ── AC-2: gate row replaces heartbeat ──────────────────────────────

def test_gate_phase_replaces_heartbeat_fragment():
    """AC-2: phase gate ⇒ row shows ``gate <slug> <step> · <elapsed>``, no ``♥``."""
    out = render_xbar([_entry(
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "gate",
            "phase_slug": "contract-gates-batch-verify",
            "phase_step": 0,
            "phase_elapsed_s": 120,
        },
    )])
    row = _row_for(out, "proj")
    assert "gate contract-gates-batch-verify 0" in row, row
    assert "♥" not in row, f"heartbeat must be replaced: {row}"


def test_batch_gate_phase_renders_batch_gate():
    """AC-2: phase batch-gate ⇒ row shows ``batch gate · <elapsed>``."""
    out = render_xbar([_entry(
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "batch-gate",
            "phase_slug": "some-batch",
            "phase_step": 0,
            "phase_elapsed_s": 60,
        },
    )])
    row = _row_for(out, "proj")
    assert "batch gate" in row, row
    assert "♥" not in row, f"heartbeat must be replaced: {row}"


# ── AC-3: sub-plan fallback ────────────────────────────────────────

def test_phase_slug_shown_when_active_master_empty():
    """AC-3: active_master "" but phase_slug set ⇒ row still names the slug."""
    out = render_xbar([_entry(
        active_master="",
        next_subplan="",
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "gate",
            "phase_slug": "contract-gates-batch-verify",
            "phase_step": 0,
            "phase_elapsed_s": 120,
        },
    )])
    row = _row_for(out, "proj")
    assert "contract-gates-batch-verify" in row, row


# ── AC-4: unchanged when phase absent or agent/between ─────────────

def test_pre_phase_entry_still_renders():
    """AC-4: fields ABSENT (old payload) ⇒ today's row, byte-identical."""
    entry = _entry(heartbeat={
        "run_id": "20260929-083918",
        "iteration": 1,
        "iteration_elapsed_s": 1802,
        "heartbeat_s": 880,
    })
    assert "phase" not in entry, "fixture must be pre-phase shaped"
    out = render_xbar([entry])
    row = _row_for(out, "proj")
    assert "iter 1" in row, row
    assert "♥" in row, row
    assert "gate" not in row, row


def test_phase_agent_renders_heartbeat():
    """AC-4: phase agent ⇒ today's row with heartbeat, not a gate row."""
    out = render_xbar([_entry(
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "agent",
            "phase_slug": "the-panel-shows-the-gate",
            "phase_step": 1,
            "phase_elapsed_s": 60,
        },
    )])
    row = _row_for(out, "proj")
    assert "iter 1" in row, row
    assert "♥" in row, row
    assert "gate" not in row, row


def test_phase_between_renders_heartbeat():
    """AC-4: phase between ⇒ today's row with heartbeat, not a gate row."""
    out = render_xbar([_entry(
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "between",
            "phase_slug": "",
            "phase_step": None,
            "phase_elapsed_s": None,
        },
    )])
    row = _row_for(out, "proj")
    assert "iter 1" in row, row
    assert "♥" in row, row


# ── AC-5: render_tray tolerates new fields ─────────────────────────

def test_render_tray_with_phase_fields():
    """AC-5: render_tray renders a payload carrying phase fields without error."""
    entry = _entry(heartbeat={
        "run_id": "20260929-083918",
        "iteration": 1,
        "iteration_elapsed_s": 1802,
        "heartbeat_s": 880,
    })
    entry.update({
        "phase": "gate",
        "phase_slug": "contract-gates-batch-verify",
        "phase_step": 0,
        "phase_elapsed_s": 120,
    })
    spec = render_tray([entry])
    assert spec is not None
    assert "rows" in spec


# ── AC-6: replay pin ───────────────────────────────────────────────

def test_replay_pin_083918():
    """AC-6: the 09:09 payload + phase gate renders ``gate <slug> 0 · …``, no ``♥``."""
    out = render_xbar([_entry(
        active_master="",
        next_subplan="",
        heartbeat={
            "run_id": "20260929-083918",
            "iteration": 1,
            "iteration_elapsed_s": 1802,
            "heartbeat_s": 880,
        },
        phase={
            "phase": "gate",
            "phase_slug": "contract-gates-batch-verify",
            "phase_step": 0,
            "phase_elapsed_s": 120,
        },
    )])
    row = _row_for(out, "proj")
    assert "gate contract-gates-batch-verify 0" in row, row
    assert "♥" not in row, f"heartbeat must be replaced: {row}"
