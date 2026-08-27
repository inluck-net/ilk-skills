"""Red-first: the xbar row shows an iteration heartbeat.

Sub-plan `the-panel-shows-a-heartbeat` (SP4 of
MASTER-2026-08-27-a-harness-reads-only-its-own-sandbox).

Covers:
  AC-4 — `render_xbar.py` appends `iter <N> · <elapsed> · ♥<age>s` to the row
         when the four liveness fields are present.
  AC-5 — a **pre-SP4-shaped** entry (fields *absent*, not merely null) still
         renders.  The renderer and `status_all.py` ship in the same repo but
         are installed via a symlinked plugin, so a stale checkout can pair a
         new renderer with an old payload.

The renderer stays pure: `heartbeat_s` arrives already computed.  A renderer
that stats files is a renderer that can fail, and it fails inside a 10-second
menu-bar refresh where nobody sees the traceback.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_xbar import render_xbar  # noqa: E402


def _entry(
    key: str = "proj",
    *,
    alive: bool = True,
    step: str = "some-subplan 2/6",
    next_subplan: str = "2026-08-27-some-subplan.md",
    heartbeat: dict | None = None,
) -> dict:
    """A status_all entry.  `heartbeat=None` means the SP4 fields are ABSENT."""
    e = {
        "project_key": key,
        "path": f"/fake/{key}",
        "repo_path": None,
        "orphaned": False,
        "active_master": "MASTER-2026-08-27-x.md",
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
    return e


def _row_for(out: str, key: str) -> str:
    """Return the single menu row mentioning `key` (excluding sub-items)."""
    rows = [l for l in out.splitlines()
            if key in l and not l.startswith("--")]
    assert len(rows) == 1, f"expected 1 row for {key}, got {rows}"
    return rows[0]


# ── AC-4 ────────────────────────────────────────────────────────────

def test_row_shows_iteration_elapsed_and_heartbeat():
    """AC-4: the fragment carries iteration, elapsed, and heartbeat age."""
    out = render_xbar([_entry(heartbeat={
        "run_id": "20260827-130050",
        "iteration": 3,
        "iteration_elapsed_s": 725,
        "heartbeat_s": 4,
    })])
    row = _row_for(out, "proj")
    assert "iter 3" in row, row
    assert "12m" in row, f"725s should render as 12m: {row}"
    assert "♥4s" in row, row


def test_heartbeat_fragment_follows_the_step_fragment():
    """AC-4: order is key → sub-plan → step → heartbeat, left to right."""
    out = render_xbar([_entry(step="some-subplan 2/6", heartbeat={
        "run_id": "20260827-130050",
        "iteration": 3,
        "iteration_elapsed_s": 725,
        "heartbeat_s": 4,
    })])
    row = _row_for(out, "proj")
    assert row.index("2/6") < row.index("iter 3"), row


def test_elapsed_under_a_minute_renders_seconds():
    out = render_xbar([_entry(heartbeat={
        "run_id": "r", "iteration": 1,
        "iteration_elapsed_s": 42, "heartbeat_s": 0,
    })])
    row = _row_for(out, "proj")
    assert "iter 1" in row and "42s" in row, row


def test_elapsed_over_an_hour_renders_hours_and_minutes():
    out = render_xbar([_entry(heartbeat={
        "run_id": "r", "iteration": 7,
        "iteration_elapsed_s": 3 * 3600 + 25 * 60, "heartbeat_s": 11,
    })])
    row = _row_for(out, "proj")
    assert "3h25m" in row, row


# ── AC-5 (backward compatibility) ───────────────────────────────────

def test_pre_sp4_entry_still_renders():
    """AC-5: fields ABSENT (old payload, new renderer) must not raise."""
    entry = _entry(heartbeat=None)
    assert "heartbeat_s" not in entry, "fixture must be pre-SP4 shaped"
    out = render_xbar([entry])
    row = _row_for(out, "proj")
    assert "2/6" in row
    assert "iter" not in row, f"no heartbeat fragment without the fields: {row}"
    assert "♥" not in row, row


def test_null_fields_render_no_fragment():
    """AC-5: fields present-but-null (dead project, per AC-2) add nothing."""
    out = render_xbar([_entry(alive=False, heartbeat={
        "run_id": None, "iteration": None,
        "iteration_elapsed_s": None, "heartbeat_s": None,
    })])
    row = _row_for(out, "proj")
    assert "♥" not in row, row
    assert "iter" not in row, row


def test_partial_fields_render_no_fragment():
    """A truncated payload must degrade to silence, not to a half-row.

    Not a hypothetical: `status_all` and the renderer are separately
    installed, so any subset of the four fields can arrive.
    """
    out = render_xbar([_entry(heartbeat={
        "run_id": "20260827-130050", "iteration": 3,
        # iteration_elapsed_s and heartbeat_s absent
    })])
    row = _row_for(out, "proj")
    assert "♥" not in row, row


def test_non_integer_fields_do_not_raise():
    """A malformed payload must not blank the whole panel."""
    out = render_xbar([_entry(heartbeat={
        "run_id": "r", "iteration": "3",
        "iteration_elapsed_s": "725", "heartbeat_s": None,
    })])
    assert "proj" in out
