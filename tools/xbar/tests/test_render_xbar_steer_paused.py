"""The xbar row says ``(paused)`` when the runner idles on ``pause.flag``.

A paused runner keeps its pid alive, so without this the row carries the
running ``*`` icon and no heartbeat — it reads as a run just starting
(observed 2026-09-29, 30 minutes on a stale flag).  See
skills/ilk-loop/tests/test_status_all_steer_paused.py for the payload side.
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
        "active_master": "MASTER-2026-09-08b-x.md",
        "next_subplan": "state-ownership-verify",
        "step": "0/2",
        "sentinel": {"pid": 4242, "state": "running", "alive": True},
        "last_class": None,
        "model": "glm-5.3",
        "runnable": False,
        "parked": False,
        "manually_runnable": False,
        "blocked": False,
        "blocked_reason": None,
    }
    e.update(extra)
    return e


def _row(out: str) -> str:
    rows = [l for l in out.splitlines() if "proj" in l and not l.startswith("--")]
    assert len(rows) == 1, rows
    return rows[0]


def test_paused_row_says_paused_and_submenu_says_why():
    out = render_xbar([_entry(steer_paused=True,
                              steer_paused_reason="design review before more patches")])
    assert "(paused)" in _row(out)
    assert "--paused: design review before more patches" in out.splitlines()


def test_paused_without_reason_still_marks_the_row():
    out = render_xbar([_entry(steer_paused=True, steer_paused_reason="")])
    assert "(paused)" in _row(out)


def test_unpaused_and_pre_field_payloads_render_unchanged():
    """False, and ABSENT (an older status_all paired with this renderer)."""
    for e in (_entry(steer_paused=False, steer_paused_reason=""), _entry()):
        out = render_xbar([e])
        assert "(paused)" not in _row(out)
        assert not any(l.startswith("--paused:") for l in out.splitlines())
