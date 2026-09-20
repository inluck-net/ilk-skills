"""Tray parity for the batch row: "<batch> M/N" ahead of the sub-plan name.

Operator request 2026-09-20; the xbar twin is
tools/xbar/tests/test_render_xbar_batch.py.  The two renderers drift
exactly when only one carries a change — this pins the Windows side.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_tray import render_tray  # noqa: E402


def _entry(**extra) -> dict:
    e = {
        "project_key": "proj",
        "path": "/fake/proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": "MASTER-x.md",
        "next_subplan": "some-subplan",
        "step": "2/6",
        "sentinel": {"pid": 1, "state": "running", "alive": True},
        "last_class": None,
        "model": "",
        "runnable": True,
        "parked": False,
        "manually_runnable": True,
        "blocked": False,
        "run_id": None,
        "iteration": None,
        "iteration_elapsed_s": None,
        "heartbeat_s": None,
    }
    e.update(extra)
    return e


def _first_label(spec: dict) -> str:
    return spec["rows"][0]["label"]


def test_batch_position_precedes_subplan_name() -> None:
    spec = render_tray([_entry(
        batch="pv5-rereview", subplan_index=3, subplan_count=7,
        pending_batches=2,
    )])
    label = _first_label(spec)
    assert "pv5-rereview 3/7" in label
    assert label.index("pv5-rereview 3/7") < label.index("some-subplan")
    # Badge AHEAD of the (short) key, not trailing (operator spec 2026-09-20).
    assert label.startswith("+2 proj")


def test_stale_payload_without_batch_fields_renders_unchanged() -> None:
    spec = render_tray([_entry()])
    label = _first_label(spec)
    assert "some-subplan" in label and "2/6" in label
    assert "batches" not in label
