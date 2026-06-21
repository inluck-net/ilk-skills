"""Tests for render_tray action rows (Start now / Resume).

Covers AC-2 from tray-actions-render sub-plan:
  - runnable=True → "Start now" row with action={kind:"run", project_key, path}
  - parked=True   → "Resume" row with action={kind:"resume", project_key, path}
  - Neither appears when its condition is false
  - Four-state coverage: running / runnable-idle / parked / all-shipped
  - Existing rows (status/report) preserved
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_tray import render_tray  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNSET = object()


def _make_entry(
    key: str = "proj",
    *,
    alive: bool = False,
    state: str = "none",
    step: str = "",
    next_subplan: str = "",
    blocked: bool = False,
    blocked_reason: str | None = None,
    runnable: bool = False,
    manually_runnable: bool = False,
    parked: bool = False,
    path: str | None = None,
    model: str = "",
) -> dict:
    """Build a single status_all entry dict with action flags."""
    return {
        "project_key": key,
        "path": path if path is not None else f"/fake/{key}",
        "active_master": f"MASTER-2026-06-19-{key}.md" if next_subplan or step else "",
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": {"pid": 123 if alive else 0, "state": state, "alive": alive},
        "last_class": None,
        "model": model,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "classification": None,
        "blocked_expiry": None,
        "report_path": None,
        "runnable": runnable,
        "manually_runnable": manually_runnable,
        "parked": parked,
    }


def _action_rows(rows: list[dict]) -> list[dict]:
    """Filter rows to only action rows (kind: run or resume)."""
    return [r for r in rows if r["action"]["kind"] in ("run", "resume")]


def _status_rows(rows: list[dict]) -> list[dict]:
    """Filter rows to only status rows (kind: status)."""
    return [r for r in rows if r["action"]["kind"] == "status"]


# ---------------------------------------------------------------------------
# State: running (loop alive) — no action rows
# ---------------------------------------------------------------------------

class TestRunning:
    def test_no_start_now_when_running(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "run" for r in actions)

    def test_no_resume_when_running(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "resume" for r in actions)

    def test_status_row_preserved_when_running(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running", step="2/5")]
        view = render_tray(entries)
        statuses = _status_rows(view["rows"])
        assert len(statuses) == 1
        assert statuses[0]["action"]["kind"] == "status"


# ---------------------------------------------------------------------------
# State: runnable-idle (active master, pending work, not alive)
# ---------------------------------------------------------------------------

class TestRunnableIdle:
    def test_start_now_row_present(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True, step="1/4", next_subplan="auth")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        run_rows = [r for r in actions if r["action"]["kind"] == "run"]
        assert len(run_rows) == 1
        assert run_rows[0]["label"] == "Start now"

    def test_start_now_action_shape(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True, path="/home/user/proj")]
        view = render_tray(entries)
        run_rows = [r for r in _action_rows(view["rows"]) if r["action"]["kind"] == "run"]
        action = run_rows[0]["action"]
        assert action["kind"] == "run"
        assert action["project_key"] == "proj"
        assert action["path"] == "/home/user/proj"

    def test_no_resume_when_runnable(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True)]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "resume" for r in actions)

    def test_status_row_preserved_when_runnable(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True, step="1/4")]
        view = render_tray(entries)
        statuses = _status_rows(view["rows"])
        assert len(statuses) == 1

    def test_start_now_when_manually_runnable_but_not_runnable(self) -> None:
        """AC-2: manually_runnable=True shows Start now even when runnable=False."""
        entries = [_make_entry("proj", manually_runnable=True, runnable=False, step="1/4")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        run_rows = [r for r in actions if r["action"]["kind"] == "run"]
        assert len(run_rows) == 1
        assert run_rows[0]["label"] == "Start now"


# ---------------------------------------------------------------------------
# State: parked (blacklisted) — Resume row appears
# ---------------------------------------------------------------------------

class TestParked:
    def test_resume_row_present(self) -> None:
        entries = [_make_entry("proj", parked=True, blocked=True, blocked_reason="within-backoff")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        resume_rows = [r for r in actions if r["action"]["kind"] == "resume"]
        assert len(resume_rows) == 1
        assert resume_rows[0]["label"] == "Resume"

    def test_resume_action_shape(self) -> None:
        entries = [_make_entry("proj", parked=True, blocked=True, path="/data/proj")]
        view = render_tray(entries)
        resume_rows = [r for r in _action_rows(view["rows"]) if r["action"]["kind"] == "resume"]
        action = resume_rows[0]["action"]
        assert action["kind"] == "resume"
        assert action["project_key"] == "proj"
        assert action["path"] == "/data/proj"

    def test_no_start_now_when_parked(self) -> None:
        entries = [_make_entry("proj", parked=True, blocked=True)]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "run" for r in actions)

    def test_status_row_preserved_when_parked(self) -> None:
        entries = [_make_entry("proj", parked=True, blocked=True)]
        view = render_tray(entries)
        statuses = _status_rows(view["rows"])
        assert len(statuses) == 1
        # Parked status row should show BLOCKED label.
        assert "BLOCKED" in statuses[0]["label"]


# ---------------------------------------------------------------------------
# State: all-shipped (no dispatchable work) — no action rows
# ---------------------------------------------------------------------------

class TestAllShipped:
    def test_no_start_now_when_shipped(self) -> None:
        entries = [_make_entry("proj", step="", next_subplan="")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "run" for r in actions)

    def test_no_resume_when_shipped(self) -> None:
        entries = [_make_entry("proj", step="", next_subplan="")]
        view = render_tray(entries)
        actions = _action_rows(view["rows"])
        assert not any(r["action"]["kind"] == "resume" for r in actions)


# ---------------------------------------------------------------------------
# Multi-project mixed states
# ---------------------------------------------------------------------------

class TestMixedStates:
    def test_running_and_runnable_both_render(self) -> None:
        entries = [
            _make_entry("running-proj", alive=True, state="running"),
            _make_entry("idle-proj", manually_runnable=True, step="1/4"),
        ]
        view = render_tray(entries)
        rows = view["rows"]
        # running-proj: 1 status row only
        # idle-proj: 1 status row + 1 Start now row
        assert len(rows) == 3
        run_rows = [r for r in rows if r["action"]["kind"] == "run"]
        assert len(run_rows) == 1
        assert run_rows[0]["project_key"] == "idle-proj"

    def test_parked_and_runnable_coexist(self) -> None:
        entries = [
            _make_entry("parked-proj", parked=True, blocked=True),
            _make_entry("runnable-proj", manually_runnable=True),
        ]
        view = render_tray(entries)
        rows = view["rows"]
        resume_rows = [r for r in rows if r["action"]["kind"] == "resume"]
        run_rows = [r for r in rows if r["action"]["kind"] == "run"]
        assert len(resume_rows) == 1
        assert len(run_rows) == 1
        assert resume_rows[0]["project_key"] == "parked-proj"
        assert run_rows[0]["project_key"] == "runnable-proj"


# ---------------------------------------------------------------------------
# Edge: action rows carry correct path
# ---------------------------------------------------------------------------

class TestActionPath:
    def test_start_now_uses_entry_path(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True, path="C:\\Users\\me\\proj")]
        view = render_tray(entries)
        run_rows = [r for r in view["rows"] if r["action"]["kind"] == "run"]
        assert run_rows[0]["action"]["path"] == "C:\\Users\\me\\proj"

    def test_resume_uses_entry_path(self) -> None:
        entries = [_make_entry("proj", parked=True, blocked=True, path="/home/me/proj")]
        view = render_tray(entries)
        resume_rows = [r for r in view["rows"] if r["action"]["kind"] == "resume"]
        assert resume_rows[0]["action"]["path"] == "/home/me/proj"

    def test_empty_path_when_missing(self) -> None:
        entries = [_make_entry("proj", manually_runnable=True, path="")]
        view = render_tray(entries)
        run_rows = [r for r in view["rows"] if r["action"]["kind"] == "run"]
        assert run_rows[0]["action"]["path"] == ""


# ---------------------------------------------------------------------------
# AC-5: model label suffix
# ---------------------------------------------------------------------------

class TestModelLabel:
    """AC-5: running row includes 'running on <model>' when model is present."""

    def test_running_row_with_model(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running",
                               model="claude-sonnet-4-20250514")]
        view = render_tray(entries)
        status = _status_rows(view["rows"])[0]
        assert "running on claude-sonnet-4-20250514" in status["label"]

    def test_running_row_without_model(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running")]
        view = render_tray(entries)
        status = _status_rows(view["rows"])[0]
        assert "running on" not in status["label"]

    def test_running_row_empty_model(self) -> None:
        entries = [_make_entry("proj", alive=True, state="running", model="")]
        view = render_tray(entries)
        status = _status_rows(view["rows"])[0]
        assert "running on" not in status["label"]

    def test_idle_row_with_model_no_suffix(self) -> None:
        """Non-running rows don't show model suffix."""
        entries = [_make_entry("proj", alive=False, state="none",
                               model="claude-sonnet-4-20250514")]
        view = render_tray(entries)
        status = _status_rows(view["rows"])[0]
        assert "running on" not in status["label"]
