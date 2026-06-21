"""Tests for render_tray.py — the pure tray view-spec renderer.

Covers AC-2..AC-5 from the tray-renderer sub-plan:
  AC-2  empty input → idle, empty rows
  AC-3  tooltip ≤127 chars always
  AC-4  CLI --json-from and stdin both work (tested via subprocess)
  AC-5  cases: empty, all-idle, ≥1 running, stale-running→attention, tooltip cap
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test — mirror test_verification_tier.py pattern.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_tray import render_tray, _MAX_TOOLTIP  # noqa: E402


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
) -> dict:
    """Build a single status_all entry dict."""
    return {
        "project_key": key,
        "path": f"/fake/{key}",
        "active_master": f"MASTER-2026-06-08-{key}.md",
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": {"pid": 123 if alive else 0, "state": state, "alive": alive},
        "last_class": None,
    }


# ---------------------------------------------------------------------------
# AC-2: empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_icon_state_idle(self) -> None:
        view = render_tray([])
        assert view["icon_state"] == "idle"

    def test_tooltip_no_projects(self) -> None:
        view = render_tray([])
        assert view["tooltip"] == "ilk: no projects"

    def test_rows_empty(self) -> None:
        view = render_tray([])
        assert view["rows"] == []


# ---------------------------------------------------------------------------
# All-idle projects
# ---------------------------------------------------------------------------

class TestAllIdle:
    def test_icon_state_idle(self) -> None:
        entries = [_make_entry("a"), _make_entry("b")]
        view = render_tray(entries)
        assert view["icon_state"] == "idle"

    def test_tooltip_shows_idle_count(self) -> None:
        entries = [_make_entry("a"), _make_entry("b")]
        view = render_tray(entries)
        assert "2 idle" in view["tooltip"]

    def test_rows_count_zero_for_idle(self) -> None:
        """Pure-idle entries are hidden from the row list (idle filter)."""
        entries = [_make_entry("a"), _make_entry("b")]
        view = render_tray(entries)
        assert len(view["rows"]) == 0

    def test_no_rows_for_idle_entry(self) -> None:
        entries = [_make_entry("a")]
        view = render_tray(entries)
        assert view["rows"] == []


# ---------------------------------------------------------------------------
# ≥1 running project
# ---------------------------------------------------------------------------

class TestRunning:
    def test_icon_state_running(self) -> None:
        entries = [_make_entry("a", alive=True, state="running")]
        view = render_tray(entries)
        assert view["icon_state"] == "running"

    def test_tooltip_shows_running_count(self) -> None:
        entries = [_make_entry("a", alive=True, state="running")]
        view = render_tray(entries)
        assert "1 running" in view["tooltip"]

    def test_mixed_running_and_idle(self) -> None:
        """Running entry shows; idle entry is hidden by the idle filter."""
        entries = [
            _make_entry("a", alive=True, state="running", step="2/5", next_subplan="auth"),
            _make_entry("b"),
        ]
        view = render_tray(entries)
        assert view["icon_state"] == "running"
        assert len(view["rows"]) == 1
        assert view["rows"][0]["icon_state"] == "running"

    def test_row_label_includes_step_and_subplan(self) -> None:
        entries = [_make_entry("my-app", alive=True, state="running", step="2/5", next_subplan="auth-module")]
        view = render_tray(entries)
        label = view["rows"][0]["label"]
        assert "my-app" in label
        assert "2/5" in label
        assert "auth-module" in label

    def test_running_row_label_has_no_idle_suffix(self) -> None:
        # A running row must NOT be tagged (idle)/(stale) — the icon conveys it.
        entries = [_make_entry("my-app", alive=True, state="running", step="2/5")]
        label = render_tray(entries)["rows"][0]["label"]
        assert "(idle)" not in label and "(stale)" not in label

    def test_idle_entry_hidden_by_filter(self) -> None:
        """An idle entry (even with step info) is hidden by the idle filter."""
        entries = [_make_entry("my-app", step="1/4", next_subplan="server-wake")]
        view = render_tray(entries)
        # Pure idle (not manually_runnable, not blocked) → no rows
        assert view["rows"] == []

    def test_row_action_structure(self) -> None:
        entries = [_make_entry("proj-x", alive=True, state="running")]
        view = render_tray(entries)
        action = view["rows"][0]["action"]
        assert action["kind"] == "status"
        assert action["project_key"] == "proj-x"


# ---------------------------------------------------------------------------
# Stale-running → attention (sentinel says running but process dead)
# ---------------------------------------------------------------------------

class TestStaleRunning:
    def test_icon_state_attention(self) -> None:
        """Stale: state=='running' but alive==False."""
        entries = [_make_entry("stale-proj", alive=False, state="running")]
        view = render_tray(entries)
        assert view["icon_state"] == "attention"

    def test_row_icon_state_attention(self) -> None:
        entries = [_make_entry("stale-proj", alive=False, state="running")]
        view = render_tray(entries)
        assert view["rows"][0]["icon_state"] == "attention"

    def test_tooltip_shows_stale_count(self) -> None:
        entries = [_make_entry("stale-proj", alive=False, state="running")]
        view = render_tray(entries)
        assert "1 stale" in view["tooltip"]

    def test_stale_row_label_marked_stale(self) -> None:
        # Stale (sentinel says running, process dead) must be tagged (stale) —
        # previously its label had no suffix and looked like a running row.
        entries = [_make_entry("stale-proj", alive=False, state="running", step="3/7")]
        label = render_tray(entries)["rows"][0]["label"]
        assert "(stale)" in label


# ---------------------------------------------------------------------------
# Error state → attention
# ---------------------------------------------------------------------------

class TestErrorState:
    def test_error_icon_state_attention(self) -> None:
        entries = [_make_entry("err-proj", alive=False, state="error")]
        view = render_tray(entries)
        assert view["icon_state"] == "attention"
        assert view["rows"][0]["icon_state"] == "attention"

    def test_errored_variant(self) -> None:
        entries = [_make_entry("err2", alive=False, state="errored")]
        view = render_tray(entries)
        assert view["rows"][0]["icon_state"] == "attention"


# ---------------------------------------------------------------------------
# AC-3: tooltip ≤127 chars always
# ---------------------------------------------------------------------------

class TestTooltipLength:
    def test_many_projects_tooltip_capped(self) -> None:
        """With many projects, tooltip must never exceed _MAX_TOOLTIP."""
        entries = [_make_entry(f"project-{i}", alive=(i % 2 == 0), state="running") for i in range(50)]
        view = render_tray(entries)
        assert len(view["tooltip"]) <= _MAX_TOOLTIP

    def test_single_project_tooltip_within_limit(self) -> None:
        entries = [_make_entry("x", alive=True, state="running")]
        view = render_tray(entries)
        assert len(view["tooltip"]) <= _MAX_TOOLTIP

    def test_tooltip_truncation_with_ellipsis(self) -> None:
        """When truncated, tooltip ends with '...'."""
        # Craft entries with long project_key names to force overflow.
        entries = [_make_entry(f"very-long-project-name-{i}" * 5, alive=(i % 2 == 0)) for i in range(20)]
        view = render_tray(entries)
        assert len(view["tooltip"]) <= _MAX_TOOLTIP
        if len(view["tooltip"]) == _MAX_TOOLTIP:
            assert view["tooltip"].endswith("...")


# ---------------------------------------------------------------------------
# AC-4: CLI --json-from and stdin
# ---------------------------------------------------------------------------

class TestCLI:
    def _run_cli(self, args: list[str], input_data: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent / "render_tray.py")] + args
        return subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8", errors="replace",
        )

    def test_json_from_file(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample.json"
        sample.write_text(json.dumps([_make_entry("a", alive=True, state="running")]), encoding="utf-8")
        result = self._run_cli(["--json-from", str(sample)])
        assert result.returncode == 0
        view = json.loads(result.stdout)
        assert view["icon_state"] == "running"

    def test_stdin(self) -> None:
        data = json.dumps([_make_entry("b")])
        result = self._run_cli([], input_data=data)
        assert result.returncode == 0
        view = json.loads(result.stdout)
        assert view["icon_state"] == "idle"

    def test_empty_json_from_file(self, tmp_path: Path) -> None:
        sample = tmp_path / "empty.json"
        sample.write_text("[]", encoding="utf-8")
        result = self._run_cli(["--json-from", str(sample)])
        assert result.returncode == 0
        view = json.loads(result.stdout)
        assert view["icon_state"] == "idle"
        assert view["tooltip"] == "ilk: no projects"

    def test_output_is_ascii_safe(self) -> None:
        """CLI stdout must survive GBK encoding (zh-CN console)."""
        data = json.dumps([_make_entry("test", alive=True, state="running")])
        result = self._run_cli([], input_data=data)
        assert result.returncode == 0
        # Must not raise UnicodeEncodeError on GBK console.
        result.stdout.encode("gbk")
        result.stdout.encode("ascii")
