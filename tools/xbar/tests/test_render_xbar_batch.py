"""The row names its batch: "<batch> M/N" ahead of the sub-plan name.

Operator request 2026-09-20: M = registry position of the rendered
sub-plan (shipped ones counted), N = sub-plans in the batch, plus a
"(+K batches)" suffix when the project owes more than the current one.

Mirrors the heartbeat test's stale-checkout concern: the renderer and
status_all ship in the same repo but install via symlinked plugin, so a
payload WITHOUT the batch fields must render exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_xbar import render_xbar  # noqa: E402


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


def _row(entries: list[dict]) -> str:
    out = render_xbar(entries)
    lines = [
        l.split("|", 1)[0].strip()
        for l in out.splitlines()
        if l.startswith("* ") or l.startswith("- ")
    ]
    return lines[0]


def test_batch_position_precedes_subplan_name() -> None:
    row = _row([_entry(
        batch="pv5-rereview", subplan_index=3, subplan_count=7,
        pending_batches=1,
    )])
    # "pv5-rereview 3/7" BEFORE the sub-plan name; the sub-plan's own
    # "2/6" step count must not be readable as the batch position.
    assert "pv5-rereview 3/7  some-subplan  2/6" in row


def test_pending_batches_suffix_only_above_one() -> None:
    multi = _row([_entry(
        batch="b", subplan_index=1, subplan_count=2, pending_batches=3,
    )])
    assert "(+3 batches)" in multi
    lone = _row([_entry(
        batch="b", subplan_index=1, subplan_count=2, pending_batches=1,
    )])
    assert "batches" not in lone


def test_stale_payload_without_batch_fields_renders_unchanged() -> None:
    row = _row([_entry()])
    assert "some-subplan" in row and "2/6" in row
    assert "/" not in row.split("some-subplan")[0].replace("* proj", "")
