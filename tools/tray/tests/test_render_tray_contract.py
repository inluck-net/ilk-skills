"""L3 contract tests: tooltip count↔rows totality for render_tray.

Part of sub-plan tray-panel-contract (step 0).  These tests LOCK the
invariant that the tooltip's running/attention/idle counts equal the
number of corresponding rows in the view-spec, so the two surfaces
can never silently diverge.

AC-1  Single alive project → len(rows) >= 1, running row present,
      tooltip contains "1 running" with count == number of running rows.
AC-2  Mixed input (1 alive + 1 stale + 1 idle) → exactly 3 rows and
      tooltip counts sum to 3 — count↔rows totality.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_tray import render_tray  # noqa: E402


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


def _count_in_tooltip(tooltip: str, label: str) -> int:
    """Extract the integer count preceding *label* in the tooltip string.

    Returns 0 if the label is absent (count implicitly zero).
    """
    m = re.search(rf"(\d+)\s+{re.escape(label)}", tooltip)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# AC-1: single alive project — running row present, count matches
# ---------------------------------------------------------------------------

class TestSingleAliveProject:
    """AC-1: one alive project produces ≥1 row, a running row, and
    tooltip '1 running' where the count equals the number of running rows."""

    def test_at_least_one_row(self) -> None:
        entries = [_make_entry("proj-a", alive=True, state="running")]
        view = render_tray(entries)
        assert len(view["rows"]) >= 1

    def test_running_row_present(self) -> None:
        entries = [_make_entry("proj-a", alive=True, state="running")]
        view = render_tray(entries)
        running_rows = [r for r in view["rows"] if r["icon_state"] == "running"]
        assert len(running_rows) >= 1

    def test_tooltip_running_count_matches_rows(self) -> None:
        entries = [_make_entry("proj-a", alive=True, state="running")]
        view = render_tray(entries)
        running_rows = [r for r in view["rows"] if r["icon_state"] == "running"]
        tooltip_running = _count_in_tooltip(view["tooltip"], "running")
        assert tooltip_running == len(running_rows)


# ---------------------------------------------------------------------------
# AC-2: mixed input — count↔rows totality
# ---------------------------------------------------------------------------

class TestMixedInputTotality:
    """AC-2: 1 alive + 1 stale + 1 idle → exactly 3 rows and tooltip
    counts sum to 3 (count↔rows totality)."""

    @pytest.fixture()
    def mixed_view(self) -> dict:
        entries = [
            _make_entry("alive-proj", alive=True, state="running"),
            _make_entry("stale-proj", alive=False, state="running"),
            _make_entry("idle-proj", alive=False, state="none"),
        ]
        return render_tray(entries)

    def test_row_count_equals_three(self, mixed_view: dict) -> None:
        assert len(mixed_view["rows"]) == 3

    def test_tooltip_counts_sum_to_three(self, mixed_view: dict) -> None:
        tooltip = mixed_view["tooltip"]
        running = _count_in_tooltip(tooltip, "running")
        stale = _count_in_tooltip(tooltip, "stale")
        error = _count_in_tooltip(tooltip, "error")
        idle = _count_in_tooltip(tooltip, "idle")
        assert running + stale + error + idle == 3

    def test_running_count_matches_running_rows(self, mixed_view: dict) -> None:
        running_rows = [r for r in mixed_view["rows"] if r["icon_state"] == "running"]
        tooltip_running = _count_in_tooltip(mixed_view["tooltip"], "running")
        assert tooltip_running == len(running_rows)

    def test_attention_count_matches_attention_rows(self, mixed_view: dict) -> None:
        attention_rows = [r for r in mixed_view["rows"] if r["icon_state"] == "attention"]
        tooltip_stale = _count_in_tooltip(mixed_view["tooltip"], "stale")
        tooltip_error = _count_in_tooltip(mixed_view["tooltip"], "error")
        assert tooltip_stale + tooltip_error == len(attention_rows)

    def test_idle_count_matches_idle_rows(self, mixed_view: dict) -> None:
        idle_rows = [r for r in mixed_view["rows"] if r["icon_state"] == "idle"]
        tooltip_idle = _count_in_tooltip(mixed_view["tooltip"], "idle")
        assert tooltip_idle == len(idle_rows)
