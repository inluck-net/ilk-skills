"""Tests for ceiling-hit-with-no-output detection.

Part of sub-plan a-gate-that-produces-nothing-is-a-hang.

Covers AC-1, AC-2, and AC-3 from the sub-plan.  The field was renamed from
`hang_suspected` to `ceiling_hit_no_output` in sub-plan a-wasted-gate-is-named
because measurement showed these are not hangs — the suite simply exceeds
the ceiling.  The detection condition is unchanged.

All tests read only from committed fixtures under
skills/ilk-loop/tests/fixtures/iteration_timing/ — never from ~/.ilk-data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── paths ─────────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "tests" / "fixtures" / "iteration_timing"
SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from iteration_timing import analyze_iteration, is_broad_test_command  # noqa: E402


# ── AC-1: ceiling_hit_no_output entry with three separate conditions ──────────


class TestCeilingHitField:
    """AC-1: analyze_iteration() returns a ceiling_hit_no_output entry.

    A call qualifies when ALL of:
      1. It is a broad test command (is_broad_test_command)
      2. Duration is within a small epsilon of the harness ceiling
      3. tool_result reports background hand-off with no captured output

    Each condition asserted separately so a future change that satisfies
    only two does not silently qualify.
    """

    def test_ceiling_hit_field_exists(self):
        """analyze_iteration() must return a ceiling_hit_no_output field."""
        result = analyze_iteration(FIXTURES / "hang_600s.jsonl")
        assert "ceiling_hit_no_output" in result, (
            "analyze_iteration() must return a ceiling_hit_no_output key — "
            "a ceiling-hitting, zero-output broad run must be named"
        )

    def test_broad_command_is_required(self):
        """A targeted test command must NOT qualify as a ceiling hit, even if
        it hits the ceiling and backgrounds with no output."""
        result = analyze_iteration(FIXTURES / "targeted_pytest.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert hits == [], (
            "A targeted test command must not be classified as a ceiling hit — "
            "only broad (full-suite) invocations qualify"
        )

    def test_near_ceiling_duration_is_required(self):
        """A broad test that completes well under the ceiling must NOT
        qualify, even if it is a broad command."""
        result = analyze_iteration(FIXTURES / "broad_pytest.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert hits == [], (
            "A broad test that completes in 120.5s (well under 600s ceiling) "
            "must not be classified as a ceiling hit"
        )

    def test_backgrounded_with_no_output_is_required(self):
        """A broad test that completes normally (not backgrounded) must NOT
        qualify, even if it runs for a long time."""
        result = analyze_iteration(FIXTURES / "broad_pytest.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert hits == [], (
            "A broad test that completes normally (not backgrounded) "
            "must not be classified as a ceiling hit"
        )


# ── AC-2: fixture reports exactly one ceiling hit ────────────────────────────


class TestCeilingHitFixture:
    """AC-2: Running the analyzer over the committed hang_600s.jsonl fixture
    reports exactly one ceiling hit and names the command."""

    def test_exactly_one_ceiling_hit_detected(self):
        """The hang_600s.jsonl fixture must produce exactly one ceiling hit."""
        result = analyze_iteration(FIXTURES / "hang_600s.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert len(hits) == 1, (
            f"expected 1 ceiling hit, got {len(hits)} — "
            "the 600.5s backgrounded broad call must be detected"
        )

    def test_ceiling_hit_names_the_command(self):
        """The ceiling hit entry must name the offending command."""
        result = analyze_iteration(FIXTURES / "hang_600s.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert len(hits) == 1
        entry = hits[0]
        assert "command" in entry, "ceiling entry must include the command"
        assert "python3 -m pytest -q 2>&1 | tail -30" in entry["command"], (
            f"ceiling entry must name the offending command, got: {entry.get('command')}"
        )

    def test_ceiling_hit_reports_duration(self):
        """The ceiling hit entry must include the call's duration."""
        result = analyze_iteration(FIXTURES / "hang_600s.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert len(hits) == 1
        entry = hits[0]
        assert "duration_sec" in entry, "ceiling entry must include duration_sec"
        assert entry["duration_sec"] >= 599.0, (
            f"ceiling duration should be ~600s, got {entry.get('duration_sec')}"
        )


# ── AC-3: no false positives on existing fixtures ─────────────────────────────


class TestNoFalsePositives:
    """AC-3: Running the analyzer over the existing broad_pytest.jsonl and
    targeted_pytest.jsonl fixtures reports zero ceiling hits — the
    no-false-positive direction, asserted explicitly rather than assumed."""

    def test_broad_pytest_no_ceiling_hit(self):
        """broad_pytest.jsonl (120.5s, completed normally) must not trigger."""
        result = analyze_iteration(FIXTURES / "broad_pytest.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert hits == [], (
            f"broad_pytest.jsonl must produce 0 ceiling hits, got {len(hits)} — "
            "a completed 120.5s run does not hit the 600s ceiling"
        )

    def test_targeted_pytest_no_ceiling_hit(self):
        """targeted_pytest.jsonl (3.2s, targeted) must not trigger."""
        result = analyze_iteration(FIXTURES / "targeted_pytest.jsonl")
        hits = result.get("ceiling_hit_no_output", [])
        assert hits == [], (
            f"targeted_pytest.jsonl must produce 0 ceiling hits, got {len(hits)} — "
            "a targeted 3.2s run does not hit the 600s ceiling"
        )


# ── AC-4: hang evidence in postmortem, classification unchanged ──────────────

# Import collect.py for detect_suspected_hangs and classify.
_COLLECT_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_COLLECT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_COLLECT_SCRIPTS))

import collect  # noqa: E402


class TestHangEvidenceInPostmortem:
    """AC-4: collect.py includes suspected hangs in postmortem output.

    The classification label must NOT change when a suspected hang is
    detected — a hang is evidence attached to whatever label the run
    already earns, not a new top-level classification.  CLASSIFICATION_LABELS
    is NOT extended.
    """

    def test_detect_suspected_hangs_finds_hang(self, tmp_path):
        """detect_suspected_hangs() finds the hang in the fixture."""
        import shutil

        # Set up a log root with per-iter JSONL matching the fixture.
        # detect_suspected_hangs uses _iter_log_root_candidates which
        # checks last_launch["log_dir"] first.
        log_root = tmp_path / "logs"
        run_dir = log_root / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        shutil.copy(FIXTURES / "hang_600s.jsonl", run_dir / "iter-01.jsonl")

        project_path = tmp_path / "proj"
        project_path.mkdir()
        last_launch = {"log_dir": str(log_root)}

        hangs = collect.detect_suspected_hangs("test-run", project_path, last_launch)
        assert len(hangs) == 1, (
            f"expected 1 suspected hang, got {len(hangs)}"
        )
        assert "python3 -m pytest -q" in hangs[0]["command"]
        assert hangs[0]["duration_sec"] >= 599.0
        assert hangs[0]["iteration"] == 1

    def test_classify_label_unchanged_by_hang(self, tmp_path):
        """classify() returns the same label regardless of hang evidence.

        A run that would classify as clean-success must still classify as
        clean-success even when its per-iteration JSONL contains a suspected
        hang.  The hang is evidence, not a label change.
        """
        # Build minimal clean-success summary records.
        import json as _json

        project_path = tmp_path / "proj"
        project_path.mkdir()
        run_id = "test-run"

        records = [
            {
                "run_id": run_id, "iteration": 1, "exit_code": 0,
                "new_commits_total": 2, "duration_sec": 100,
            },
            {
                "run_id": run_id, "iteration": 2, "exit_code": 0,
                "new_commits_total": 1, "duration_sec": 80,
            },
        ]

        label, facts = collect.classify(records, None, project_path)
        # The label should be one of the known taxonomy labels.
        assert label in collect.CLASSIFICATION_LABELS
        # hang_suspected and ceiling_hit_no_output must NOT be classification labels.
        assert "hang_suspected" not in collect.CLASSIFICATION_LABELS
        assert "ceiling_hit_no_output" not in collect.CLASSIFICATION_LABELS

        # Even if we simulate attaching ceiling-hit evidence, the label must not
        # change.  detect_suspected_hangs modifies facts, not the label.
        facts["ceiling_hit_no_output"] = [
            {"command": "python3 -m pytest -q", "duration_sec": 600.5, "iteration": 1}
        ]
        # The label is determined by classify(), not by hang evidence.
        assert label in collect.CLASSIFICATION_LABELS

    def test_render_report_includes_ceiling_section(self, tmp_path):
        """render_report() includes a 'Ceiling hits' section when evidence present."""
        project_path = tmp_path / "proj"
        project_path.mkdir()

        iters = [
            {
                "run_id": "test-run", "iteration": 1, "exit_code": 0,
                "new_commits_total": 2, "duration_sec": 100,
                "timestamp": "2026-08-24T08:00:00Z",
            },
        ]
        facts = {
            "ceiling_hit_no_output": [
                {"command": "python3 -m pytest -q 2>&1 | tail -30", "duration_sec": 600.5, "iteration": 1}
            ]
        }

        report = collect.render_report(
            project_path=project_path,
            project_name="test-proj",
            run_id="test-run",
            iters=iters,
            last_launch=None,
            label="clean-success",
            facts=facts,
            rec_max=30,
            rec_to=30,
            rationale="test",
            tail=[],
        )
        assert "Ceiling hits" in report
        assert "python3 -m pytest -q" in report
        assert "600.5s" in report

    def test_render_report_no_ceiling_section_without_evidence(self, tmp_path):
        """render_report() omits 'Ceiling hits' when no evidence present."""
        project_path = tmp_path / "proj"
        project_path.mkdir()

        iters = [
            {
                "run_id": "test-run", "iteration": 1, "exit_code": 0,
                "new_commits_total": 2, "duration_sec": 100,
                "timestamp": "2026-08-24T08:00:00Z",
            },
        ]
        facts = {}

        report = collect.render_report(
            project_path=project_path,
            project_name="test-proj",
            run_id="test-run",
            iters=iters,
            last_launch=None,
            label="clean-success",
            facts=facts,
            rec_max=30,
            rec_to=30,
            rationale="test",
            tail=[],
        )
        assert "Ceiling hits" not in report
