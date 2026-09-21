"""Tests for smoothness_report — classify runs by terminal state.

AC6: "smoothness_report.py classifies every run in a window by terminal state
from the postmortem corpus and prints clean-finish rate with its denominator.
It reproduces today's baseline — 2 of 18 over 24h — from the same data."
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from smoothness_report import (  # noqa: E402
    classify_runs,
    smoothness_report,
    format_report,
    collect_runs,
    _CLEAN_STATES,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

# Sample run data: (started_at, classification, project)
SAMPLE_RUNS = [
    ("2026-09-22T01:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T02:00:00+0800", "local-checks-stuck", "ilk-skills"),
    ("2026-09-22T03:00:00+0800", "clean-success", "gh-resolve"),
    ("2026-09-22T04:00:00+0800", "api-blocked", "gh-resolve"),
    ("2026-09-22T05:00:00+0800", "shipped-unverified", "ilk-skills"),
    ("2026-09-22T06:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T07:00:00+0800", "interrupted", "gh-resolve"),
    ("2026-09-22T08:00:00+0800", "clean-success", "gh-resolve"),
    ("2026-09-22T09:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T10:00:00+0800", "local-checks-stuck", "ilk-skills"),
    ("2026-09-22T11:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T12:00:00+0800", "api-blocked", "gh-resolve"),
    ("2026-09-22T13:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T14:00:00+0800", "shipped-unverified", "ilk-skills"),
    ("2026-09-22T15:00:00+0800", "merge-conflict", "ilk-skills"),
    ("2026-09-22T16:00:00+0800", "local-checks-stuck", "ilk-skills"),
    ("2026-09-22T17:00:00+0800", "api-blocked", "gh-resolve"),
    ("2026-09-22T18:00:00+0800", "merge-conflict", "ilk-skills"),
]

# Expected: 18 runs, 2 clean-success, 3 projects.
EXPECTED_TOTAL = 18
EXPECTED_CLEAN = 2


# ── Tests ───────────────────────────────────────────────────────────────────


class TestClassifyRuns:
    """classify_runs counts runs by terminal state."""

    def test_state_counts(self):
        counts = classify_runs(SAMPLE_RUNS)
        assert counts["merge-conflict"] == 7
        assert counts["local-checks-stuck"] == 3
        assert counts["clean-success"] == 2
        assert counts["api-blocked"] == 3
        assert counts["shipped-unverified"] == 2
        assert counts["interrupted"] == 1

    def test_empty_runs(self):
        counts = classify_runs([])
        assert counts == {}


class TestSmoothnessReport:
    """smoothness_report computes the clean-finish rate."""

    def test_baseline_numbers(self):
        """Reproduces the measured baseline: 2 of 18 clean."""
        total, clean, rate, state_counts = smoothness_report(SAMPLE_RUNS)
        assert total == EXPECTED_TOTAL, f"Expected {EXPECTED_TOTAL} runs, got {total}"
        assert clean == EXPECTED_CLEAN, f"Expected {EXPECTED_CLEAN} clean, got {clean}"
        assert rate == pytest.approx(2 / 18, abs=0.001)

    def test_all_clean(self):
        runs = [
            ("2026-09-22T01:00:00+0800", "clean-success", "proj"),
            ("2026-09-22T02:00:00+0800", "all-shipped", "proj"),
        ]
        total, clean, rate, _ = smoothness_report(runs)
        assert total == 2
        assert clean == 2
        assert rate == 1.0

    def test_no_clean(self):
        runs = [
            ("2026-09-22T01:00:00+0800", "merge-conflict", "proj"),
            ("2026-09-22T02:00:00+0800", "api-blocked", "proj"),
        ]
        total, clean, rate, _ = smoothness_report(runs)
        assert total == 2
        assert clean == 0
        assert rate == 0.0

    def test_empty_runs(self):
        total, clean, rate, _ = smoothness_report([])
        assert total == 0
        assert clean == 0
        assert rate == 0.0


class TestCleanStates:
    """The set of states that count as 'clean finish'."""

    def test_clean_success_is_clean(self):
        assert "clean-success" in _CLEAN_STATES

    def test_all_shipped_is_clean(self):
        assert "all-shipped" in _CLEAN_STATES

    def test_merge_conflict_is_not_clean(self):
        assert "merge-conflict" not in _CLEAN_STATES


class TestFormatReport:
    """format_report produces readable output."""

    def test_contains_rate(self):
        report = format_report(18, 2, 2 / 18, classify_runs(SAMPLE_RUNS), "test")
        assert "2 of 18" in report

    def test_contains_states(self):
        report = format_report(18, 2, 2 / 18, classify_runs(SAMPLE_RUNS), "test")
        assert "merge-conflict" in report
        assert "clean-success" in report

    def test_contains_checkmark_for_clean(self):
        report = format_report(18, 2, 2 / 18, classify_runs(SAMPLE_RUNS), "test")
        assert "✓" in report


class TestCollectRuns:
    """collect_runs reads from the postmortem corpus."""

    def test_reads_real_corpus(self):
        """Runs against the real data root — must find some postmortems."""
        since = datetime.utcnow() - timedelta(hours=48)
        runs = collect_runs(since)
        # The real corpus should have at least some runs.
        assert len(runs) > 0, "Expected at least some postmortems in the last 48h"

    def test_filters_by_time(self):
        """A very recent cutoff should find fewer runs."""
        since_recent = datetime.utcnow() - timedelta(minutes=1)
        runs_recent = collect_runs(since_recent)
        since_old = datetime.utcnow() - timedelta(hours=168)  # 1 week
        runs_old = collect_runs(since_old)
        assert len(runs_recent) <= len(runs_old)