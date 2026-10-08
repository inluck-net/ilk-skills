"""Tests: the planner has fresh work when the backlog has it.

Sub-plan: the-planner-has-work (step 0).
Covers AC-1..AC-5: rank admits fresh non-triage rows, triage keeps its
current rule, ordering by source tier, unsourced/handoff excluded,
attempts/blocked still exclude.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_WATCHDOG_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts")

if _SELF_SCRIPTS not in sys.path:
    sys.path.insert(0, _SELF_SCRIPTS)
if _LOOP_SCRIPTS not in sys.path:
    sys.path.insert(0, _LOOP_SCRIPTS)
if _WATCHDOG_SCRIPTS not in sys.path:
    sys.path.insert(0, _WATCHDOG_SCRIPTS)

import autoplan_rails


# ── helpers ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


def _make_candidate(
    *,
    cid: str = "sig-abc123",
    title: str = "Missing feature X",
    gap: str = "No support for X",
    source: str = "triage",
    status: str = "open",
    seen_count: int = 1,
    escalations: int = 0,
    urgent: bool = False,
    autoplan_attempts: int = 0,
    autoplan_blocked: bool = False,
    first_seen: str = "2026-10-03T12:00:00+0800",
    last_seen: str | None = None,
) -> dict:
    """Build a minimal backlog entry."""
    entry = {
        "id": cid,
        "title": title,
        "gap": gap,
        "source": source,
        "status": status,
        "seen_count": seen_count,
        "first_seen": first_seen,
        "relations": {
            "escalations": escalations,
            "urgent": urgent,
            "autoplan_attempts": autoplan_attempts,
            "autoplan_blocked": autoplan_blocked,
        },
        "evidence": [],
        "proposed_fix": "",
        "kind": "toolkit",
        "leverage": "medium",
        "severity": "medium",
    }
    if last_seen is not None:
        entry["last_seen"] = last_seen
    return entry


def _days_ago(days: int) -> str:
    """Return an ISO timestamp *days* before NOW."""
    return (NOW - timedelta(days=days)).isoformat()


# ── AC-1: fresh supervisor rows are eligible; stale / no-date are not ────────


class TestAC1:
    """rank admits fresh supervisor rows, rejects stale and dateless ones."""

    def test_supervisor_seen_3_days_ago_is_eligible(self):
        """An open supervisor row seen 3 days ago is eligible."""
        entry = _make_candidate(
            cid="sup-fresh", source="supervisor",
            first_seen=_days_ago(30), last_seen=_days_ago(3),
        )
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "sup-fresh" in ids

    def test_supervisor_seen_20_days_ago_is_not_eligible(self):
        """An open supervisor row seen 20 days ago is stale — not eligible."""
        entry = _make_candidate(
            cid="sup-stale", source="supervisor",
            first_seen=_days_ago(30), last_seen=_days_ago(20),
        )
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "sup-stale" not in ids

    def test_supervisor_no_dates_is_not_eligible(self):
        """A supervisor row with no parseable dates is not eligible."""
        entry = _make_candidate(
            cid="sup-nodate", source="supervisor",
            first_seen="not-a-date",
        )
        entry.pop("last_seen", None)
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "sup-nodate" not in ids


# ── AC-2: triage rows keep today's rule (no freshness gate) ─────────────────


class TestAC2:
    """Triage rows are eligible regardless of last_seen age."""

    def test_triage_seen_60_days_ago_is_eligible(self):
        """An open triage row seen 60 days ago is still eligible."""
        entry = _make_candidate(
            cid="triage-old", source="triage",
            first_seen=_days_ago(60),
        )
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "triage-old" in ids


# ── AC-3: order — triage before supervisor before feedback ──────────────────


class TestAC3:
    """rank orders by source tier: triage 0, supervisor 1, feedback 2."""

    def test_triage_before_supervisor_before_feedback(self):
        """Triage sorts first, then supervisor, then feedback."""
        entries = [
            _make_candidate(
                cid="feedback-row", source="feedback",
                first_seen=_days_ago(30), last_seen=_days_ago(3),
            ),
            _make_candidate(cid="triage-row", source="triage"),
            _make_candidate(
                cid="supervisor-row", source="supervisor",
                first_seen=_days_ago(30), last_seen=_days_ago(3),
            ),
        ]
        ranked = autoplan_rails.rank(entries, now=NOW)
        ids = [e["id"] for e in ranked]
        assert ids.index("triage-row") < ids.index("supervisor-row") < ids.index("feedback-row")


# ── AC-4: unsourced and handoff-* rows are never eligible ───────────────────


class TestAC4:
    """rank excludes rows with no source or handoff-* source."""

    def test_unsourced_row_not_eligible(self):
        """A row with no source key is never eligible."""
        entry = _make_candidate(cid="no-source", source="unsourced")
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "no-source" not in ids

    def test_handoff_row_not_eligible(self):
        """A handoff-* source row is never eligible."""
        entry = _make_candidate(cid="handoff-row", source="handoff-session-42")
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "handoff-row" not in ids


# ── AC-5: autoplan_attempts >= 2 and autoplan_blocked still exclude ─────────


class TestAC5:
    """rank still drops exhausted and blocked entries."""

    def test_autoplan_attempts_2_excludes(self):
        """An entry with autoplan_attempts >= 2 is excluded."""
        entry = _make_candidate(cid="exhausted", autoplan_attempts=2)
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "exhausted" not in ids

    def test_autoplan_blocked_excludes(self):
        """An entry with autoplan_blocked is excluded."""
        entry = _make_candidate(cid="blocked", autoplan_blocked=True)
        ranked = autoplan_rails.rank([entry], now=NOW)
        ids = [e["id"] for e in ranked]
        assert "blocked" not in ids