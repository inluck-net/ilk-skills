"""Tests for render_xbar action lines (Start now / Resume).

Covers AC-3 from tray-actions-render sub-plan:
  - runnable=True → "Start now" clickable line with bash=<run_script>
  - parked=True   → "Resume" clickable line invoking blacklist_status.py ack
  - Neither appears when its condition is false
  - Four-state coverage: running / runnable-idle / parked / all-shipped
  - bash=/terminal=false shape verified
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_xbar import render_xbar  # noqa: E402


# Fixed script paths for deterministic assertions.
RUN_SCRIPT = "/opt/ilk/skills/ilk-runner/scripts/ilk-run.sh"
RESUME_SCRIPT = "/opt/ilk/skills/ilk-watchdog/scripts/blacklist_status.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    parked: bool = False,
    path: str = "",
) -> dict:
    """Build a single status_all entry dict with action flags."""
    return {
        "project_key": key,
        "path": path or f"/fake/{key}",
        "active_master": f"MASTER-2026-06-19-{key}.md" if next_subplan or step else "",
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": {"pid": 123 if alive else 0, "state": state, "alive": alive},
        "last_class": None,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "classification": None,
        "blocked_expiry": None,
        "report_path": None,
        "runnable": runnable,
        "parked": parked,
    }


def _render(*entries: dict) -> str:
    """Render entries with fixed script paths for assertion."""
    return render_xbar(list(entries), run_script=RUN_SCRIPT, resume_script=RESUME_SCRIPT)


def _action_lines(text: str) -> list[str]:
    """Extract sub-item lines (prefixed with --) from xbar output."""
    return [line for line in text.splitlines() if line.startswith("--")]


def _start_now_lines(text: str) -> list[str]:
    """Extract 'Start now' action lines."""
    return [line for line in text.splitlines() if "Start now" in line]


def _resume_lines(text: str) -> list[str]:
    """Extract 'Resume' action lines."""
    return [line for line in text.splitlines() if "Resume" in line]


# ---------------------------------------------------------------------------
# State: running (loop alive) — no action lines
# ---------------------------------------------------------------------------

class TestRunning:
    def test_no_start_now_when_running(self) -> None:
        entry = _make_entry("proj", alive=True, state="running")
        text = _render(entry)
        assert not _start_now_lines(text)

    def test_no_resume_when_running(self) -> None:
        entry = _make_entry("proj", alive=True, state="running")
        text = _render(entry)
        assert not _resume_lines(text)


# ---------------------------------------------------------------------------
# State: runnable-idle (active master, pending work, not alive)
# ---------------------------------------------------------------------------

class TestRunnableIdle:
    def test_start_now_line_present(self) -> None:
        entry = _make_entry("proj", runnable=True, step="1/4", next_subplan="auth")
        text = _render(entry)
        starts = _start_now_lines(text)
        assert len(starts) == 1
        assert "Start now" in starts[0]

    def test_start_now_has_bash_and_param1(self) -> None:
        entry = _make_entry("proj", runnable=True, path="/home/user/proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        # !r adds quotes: bash='<script>' param1='<path>'
        assert f"bash='{RUN_SCRIPT}'" in starts[0]
        assert "param1='/home/user/proj'" in starts[0]

    def test_start_now_path_in_line(self) -> None:
        entry = _make_entry("proj", runnable=True, path="/home/user/proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "/home/user/proj" in starts[0]

    def test_start_now_has_terminal_false(self) -> None:
        entry = _make_entry("proj", runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "terminal=false" in starts[0]

    def test_start_now_has_refresh_true(self) -> None:
        entry = _make_entry("proj", runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "refresh=true" in starts[0]

    def test_no_resume_when_runnable(self) -> None:
        entry = _make_entry("proj", runnable=True)
        text = _render(entry)
        assert not _resume_lines(text)

    def test_start_now_is_sub_item(self) -> None:
        entry = _make_entry("proj", runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert starts[0].startswith("--")


# ---------------------------------------------------------------------------
# State: parked (blacklisted) — Resume line appears
# ---------------------------------------------------------------------------

class TestParked:
    def test_resume_line_present(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True, blocked_reason="within-backoff")
        text = _render(entry)
        resumes = _resume_lines(text)
        assert len(resumes) == 1
        assert "Resume" in resumes[0]

    def test_resume_invokes_blacklist_status_ack(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True, path="/data/proj")
        text = _render(entry)
        resumes = _resume_lines(text)
        # !r repr: param1='<script>'
        assert f"param1='{RESUME_SCRIPT}'" in resumes[0]
        assert "param2=ack" in resumes[0]
        assert "param3=--project" in resumes[0]

    def test_resume_passes_project_path(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True, path="/data/proj")
        text = _render(entry)
        resumes = _resume_lines(text)
        # !r repr: param4='/data/proj'
        assert "param4='/data/proj'" in resumes[0]

    def test_resume_has_terminal_false(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True)
        text = _render(entry)
        resumes = _resume_lines(text)
        assert "terminal=false" in resumes[0]

    def test_resume_has_refresh_true(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True)
        text = _render(entry)
        resumes = _resume_lines(text)
        assert "refresh=true" in resumes[0]

    def test_resume_is_sub_item(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True)
        text = _render(entry)
        resumes = _resume_lines(text)
        assert resumes[0].startswith("--")

    def test_no_start_now_when_parked(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True)
        text = _render(entry)
        assert not _start_now_lines(text)


# ---------------------------------------------------------------------------
# State: all-shipped (no dispatchable work) — no action lines
# ---------------------------------------------------------------------------

class TestAllShipped:
    def test_no_start_now_when_shipped(self) -> None:
        entry = _make_entry("proj", step="", next_subplan="")
        text = _render(entry)
        assert not _start_now_lines(text)

    def test_no_resume_when_shipped(self) -> None:
        entry = _make_entry("proj", step="", next_subplan="")
        text = _render(entry)
        assert not _resume_lines(text)


# ---------------------------------------------------------------------------
# Mixed states
# ---------------------------------------------------------------------------

class TestMixedStates:
    def test_runnable_and_parked_both_render(self) -> None:
        entries = [
            _make_entry("runnable-proj", runnable=True),
            _make_entry("parked-proj", parked=True, blocked=True),
        ]
        text = _render(*entries)
        starts = _start_now_lines(text)
        resumes = _resume_lines(text)
        assert len(starts) == 1
        assert len(resumes) == 1
        assert "runnable-proj" in starts[0]
        assert "parked-proj" in resumes[0]


# ---------------------------------------------------------------------------
# Edge: action lines carry correct path
# ---------------------------------------------------------------------------

class TestActionPath:
    def test_start_now_uses_entry_path(self) -> None:
        entry = _make_entry("proj", runnable=True, path="C:\\Users\\me\\proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        # !r repr doubles backslashes: param1='C:\\Users\\me\\proj'
        assert "param1='C:\\\\Users\\\\me\\\\proj'" in starts[0]

    def test_resume_uses_entry_path(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True, path="/home/me/proj")
        text = _render(entry)
        resumes = _resume_lines(text)
        # !r repr: param4='/home/me/proj'
        assert "param4='/home/me/proj'" in resumes[0]
