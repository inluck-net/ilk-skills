"""Tests for render_tray BLOCKED (needs-human) row category.

Sub-plan: render-tray-blocked-category  step 0  (RED — blocked fields
not yet implemented in render_tray).

AC-1  Entry with blocked=true yields attention icon, BLOCKED label,
      report_path in action.
AC-2  Tooltip includes "N blocked"; global icon_state is attention when
      >=1 entry is blocked (even if others run).
AC-3  Non-blocked entry renders as before (idle, no BLOCKED label).
AC-4  Mixed input (1 blocked + 1 running + 1 idle) -> len(rows)==3 and
      tooltip counts sum to 3 (count<->rows totality preserved).
AC-5  Labels are ASCII-only (no emoji / non-ASCII chars).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

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
    blocked: bool = False,
    classification: str | None = None,
    report_path: str | None = None,
) -> dict:
    """Build a status_all entry with optional blocked fields."""
    return {
        "project_key": key,
        "path": f"/fake/{key}",
        "active_master": f"MASTER-2026-06-08-{key}.md",
        "next_subplan": next_subplan,
        "step": step,
        "sentinel": {"pid": 123 if alive else 0, "state": state, "alive": alive},
        "last_class": None,
        "blocked": blocked,
        "classification": classification,
        "report_path": report_path,
    }


def _count_in_tooltip(tooltip: str, label: str) -> int:
    """Extract integer count preceding *label* in tooltip, or 0 if absent."""
    m = re.search(rf"(\d+)\s+{re.escape(label)}", tooltip)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# AC-1: blocked entry -> attention icon, BLOCKED label, report_path in action
# ---------------------------------------------------------------------------

class TestBlockedEntry:
    """A blocked entry renders as a red BLOCKED row with report_path."""

    def test_attention_icon(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/report.md",
        )
        view = render_tray([entry])
        row = view["rows"][0]
        assert row["icon_state"] == "attention"

    def test_label_contains_blocked(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/report.md",
        )
        view = render_tray([entry])
        label = view["rows"][0]["label"]
        assert "BLOCKED" in label

    def test_label_contains_classification(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/report.md",
        )
        view = render_tray([entry])
        label = view["rows"][0]["label"]
        assert "local-checks-stuck" in label

    def test_label_contains_ilk_resume(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/report.md",
        )
        view = render_tray([entry])
        label = view["rows"][0]["label"]
        assert "/ilk-resume" in label

    def test_action_carries_report_path(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/report.md",
        )
        view = render_tray([entry])
        action = view["rows"][0]["action"]
        assert action.get("report_path") == "/tmp/report.md"


# ---------------------------------------------------------------------------
# AC-2: tooltip has "N blocked"; global icon_state is attention when blocked
# ---------------------------------------------------------------------------

class TestTooltipAndGlobalIcon:
    """Tooltip shows blocked count; global icon_state is attention when any
    entry is blocked, even if others are running."""

    def test_tooltip_contains_blocked_count(self) -> None:
        entries = [
            _make_entry("blocked-proj", blocked=True,
                        classification="local-checks-stuck",
                        report_path="/tmp/r.md"),
            _make_entry("running-proj", alive=True, state="running"),
        ]
        view = render_tray(entries)
        assert "blocked" in view["tooltip"]
        assert _count_in_tooltip(view["tooltip"], "blocked") == 1

    def test_global_attention_when_blocked_present(self) -> None:
        entries = [
            _make_entry("blocked-proj", blocked=True,
                        classification="local-checks-stuck",
                        report_path="/tmp/r.md"),
            _make_entry("running-proj", alive=True, state="running"),
        ]
        view = render_tray(entries)
        assert view["icon_state"] == "attention"

    def test_blocked_dominates_running_in_global(self) -> None:
        """Even with a running project, a blocked project forces attention."""
        entries = [
            _make_entry("running-proj", alive=True, state="running"),
            _make_entry("blocked-proj", blocked=True,
                        classification="blacklist",
                        report_path="/tmp/r.md"),
        ]
        view = render_tray(entries)
        assert view["icon_state"] == "attention"


# ---------------------------------------------------------------------------
# AC-3: non-blocked entry renders as before (idle, no BLOCKED label)
# ---------------------------------------------------------------------------

class TestNonBlockedUnchanged:
    """A non-blocked entry renders as before — except pure-idle entries are
    now hidden by the idle filter."""

    def test_idle_entry_hidden(self) -> None:
        """Pure-idle entry produces no rows (idle filter)."""
        entry = _make_entry("idle-proj", alive=False, state="none")
        view = render_tray([entry])
        assert view["rows"] == []

    def test_running_entry_no_blocked_label(self) -> None:
        """A running non-blocked entry has no BLOCKED in label."""
        entry = _make_entry("run-proj", alive=True, state="running")
        view = render_tray([entry])
        assert "BLOCKED" not in view["rows"][0]["label"]


# ---------------------------------------------------------------------------
# AC-4: mixed input preserves count<->rows totality
# ---------------------------------------------------------------------------

class TestMixedBlockedTotality:
    """1 blocked + 1 running + 1 idle -> 2 rows (idle hidden), tooltip sums to 3."""

    @pytest.fixture()
    def mixed_view(self) -> dict:
        entries = [
            _make_entry("blocked-proj", blocked=True,
                        classification="local-checks-stuck",
                        report_path="/tmp/r.md"),
            _make_entry("running-proj", alive=True, state="running"),
            _make_entry("idle-proj", alive=False, state="none"),
        ]
        return render_tray(entries)

    def test_row_count_is_two_idle_hidden(self, mixed_view: dict) -> None:
        """Idle entry is hidden; only blocked + running produce rows."""
        assert len(mixed_view["rows"]) == 2

    def test_tooltip_counts_sum_to_three(self, mixed_view: dict) -> None:
        """Tooltip still counts all entries including idle."""
        tooltip = mixed_view["tooltip"]
        blocked = _count_in_tooltip(tooltip, "blocked")
        running = _count_in_tooltip(tooltip, "running")
        stale = _count_in_tooltip(tooltip, "stale")
        error = _count_in_tooltip(tooltip, "error")
        idle = _count_in_tooltip(tooltip, "idle")
        assert blocked + running + stale + error + idle == 3


# ---------------------------------------------------------------------------
# AC-5: labels are ASCII-only
# ---------------------------------------------------------------------------

class TestAsciiOnly:
    """All labels must be ASCII — no emoji or non-ASCII chars."""

    def test_blocked_label_ascii(self) -> None:
        entry = _make_entry(
            "math-blocks", blocked=True,
            classification="local-checks-stuck",
            report_path="/tmp/r.md",
        )
        view = render_tray([entry])
        label = view["rows"][0]["label"]
        assert all(ord(c) < 128 for c in label), f"Non-ASCII in label: {label!r}"

    def test_mixed_labels_ascii(self) -> None:
        entries = [
            _make_entry("blocked-proj", blocked=True,
                        classification="blacklist",
                        report_path="/tmp/r.md"),
            _make_entry("running-proj", alive=True, state="running"),
            _make_entry("idle-proj", alive=False, state="none"),
        ]
        view = render_tray(entries)
        for row in view["rows"]:
            label = row["label"]
            assert all(ord(c) < 128 for c in label), f"Non-ASCII in label: {label!r}"
