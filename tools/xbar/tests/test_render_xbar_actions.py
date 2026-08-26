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
# Must be a REAL file: render_xbar suppresses a clickable row whose target
# is missing, so a fictional path would render the "unavailable" notice
# instead of an action (see test_render_xbar_exec_independence.py).
RUN_SCRIPT = str(Path(__file__).resolve().parents[3]
                 / "skills" / "ilk-runner" / "scripts" / "ilk-run.sh")
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
    manually_runnable: bool = False,
    parked: bool = False,
    path: str = "",
    model: str = "",
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
        entry = _make_entry("proj", manually_runnable=True, step="1/4", next_subplan="auth")
        text = _render(entry)
        starts = _start_now_lines(text)
        assert len(starts) == 1
        assert "Start now" in starts[0]

    def test_start_now_has_bash_and_param1(self) -> None:
        entry = _make_entry("proj", manually_runnable=True, path="/home/user/proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        # bash= is the interpreter; the script is param1, the path param2.
        # Exec'ing the script directly is what broke "Start now" on 2026-08-26.
        assert "bash='/bin/bash'" in starts[0]
        assert f"param1='{RUN_SCRIPT}'" in starts[0]
        assert "param2='/home/user/proj'" in starts[0]

    def test_start_now_path_in_line(self) -> None:
        entry = _make_entry("proj", manually_runnable=True, path="/home/user/proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "/home/user/proj" in starts[0]

    def test_start_now_has_terminal_false(self) -> None:
        entry = _make_entry("proj", manually_runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "terminal=false" in starts[0]

    def test_start_now_has_refresh_true(self) -> None:
        entry = _make_entry("proj", manually_runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert "refresh=true" in starts[0]

    def test_no_resume_when_runnable(self) -> None:
        entry = _make_entry("proj", manually_runnable=True)
        text = _render(entry)
        assert not _resume_lines(text)

    def test_start_now_is_sub_item(self) -> None:
        entry = _make_entry("proj", manually_runnable=True)
        text = _render(entry)
        starts = _start_now_lines(text)
        assert starts[0].startswith("--")

    def test_start_now_when_manually_runnable_but_not_runnable(self) -> None:
        """AC-2: manually_runnable=True shows Start now even when runnable=False."""
        entry = _make_entry("proj", manually_runnable=True, runnable=False, step="1/4")
        text = _render(entry)
        starts = _start_now_lines(text)
        assert len(starts) == 1
        assert "Start now" in starts[0]


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
            _make_entry("runnable-proj", manually_runnable=True),
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
        entry = _make_entry("proj", manually_runnable=True, path="C:\\Users\\me\\proj")
        text = _render(entry)
        starts = _start_now_lines(text)
        # !r repr doubles backslashes; the project path is param2 now.
        assert "param2='C:\\\\Users\\\\me\\\\proj'" in starts[0]

    def test_resume_uses_entry_path(self) -> None:
        entry = _make_entry("proj", parked=True, blocked=True, path="/home/me/proj")
        text = _render(entry)
        resumes = _resume_lines(text)
        # !r repr: param4='/home/me/proj'
        assert "param4='/home/me/proj'" in resumes[0]


# ---------------------------------------------------------------------------
# AC-5: model label suffix
# ---------------------------------------------------------------------------

class TestModelLabel:
    """AC-5: running row includes 'running on <model>' when model is present."""

    def test_running_row_with_model(self) -> None:
        entry = _make_entry("proj", alive=True, state="running",
                            model="claude-sonnet-4-20250514")
        text = _render(entry)
        # First non-separator line after "---" is the project row.
        lines = text.splitlines()
        project_line = [l for l in lines if l.startswith("* proj")][0]
        assert "running on claude-sonnet-4-20250514" in project_line

    def test_running_row_without_model(self) -> None:
        entry = _make_entry("proj", alive=True, state="running")
        text = _render(entry)
        lines = text.splitlines()
        project_line = [l for l in lines if l.startswith("* proj")][0]
        assert "running on" not in project_line

    def test_idle_row_with_model_no_suffix(self) -> None:
        """An idle entry is hidden entirely (no row to show model suffix on)."""
        entry = _make_entry("proj", alive=False, state="none",
                            model="claude-sonnet-4-20250514")
        text = _render(entry)
        assert "proj" not in text

    def test_blocked_row_with_model_no_suffix(self) -> None:
        """Non-running rows (e.g. blocked) don't show model suffix."""
        entry = _make_entry("proj", blocked=True,
                            model="claude-sonnet-4-20250514")
        text = _render(entry)
        lines = text.splitlines()
        project_line = [l for l in lines if "proj" in l and not l.startswith("--")][0]
        assert "running on" not in project_line


# ---------------------------------------------------------------------------
# Idle-filter: hide pure-idle projects (AC-5 parity)
# ---------------------------------------------------------------------------

class TestIdleFilter:
    """AC-5: render_xbar applies the same idle-filter as render_tray."""

    def test_idle_entry_yields_no_project_line(self) -> None:
        """An idle, not-manually_runnable, not-blocked entry produces
        no project row and no action lines."""
        entry = _make_entry("idle-proj")
        text = _render(entry)
        lines = text.splitlines()
        # No line should reference idle-proj
        assert not any("idle-proj" in l for l in lines), (
            f"idle-proj should be hidden; got: {[l for l in lines if 'idle-proj' in l]}"
        )

    def test_mixed_list_hides_only_idle(self) -> None:
        """Given 1 idle + 1 running + 1 blocked + 1 manually_runnable,
        the idle one is hidden from xbar output."""
        entries = [
            _make_entry("idle-proj"),
            _make_entry("running-proj", alive=True, state="running"),
            _make_entry("blocked-proj", blocked=True),
            _make_entry("runnable-proj", manually_runnable=True, step="1/4"),
        ]
        text = _render(*entries)
        assert "idle-proj" not in text
        assert "running-proj" in text
        assert "blocked-proj" in text
        assert "runnable-proj" in text
