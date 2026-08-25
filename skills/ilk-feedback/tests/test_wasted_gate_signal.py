"""Red-first tests for within-iteration repeats and ceiling waste signals.

Part of sub-plan a-wasted-gate-is-named.

Covers AC-1 (within-iteration repeat on a fixture), AC-3 (ceiling signal
on hang_600s.jsonl), and AC-4 (zero on broad_pytest.jsonl /
targeted_pytest.jsonl).

These tests reference functions/fields that do not yet exist in
iteration_timing.py:
- find_within_iteration_repeats() — step 1 will add it
- ceiling_hit_no_output field — step 2 will rename it from hang_suspected

The import of find_within_iteration_repeats is deferred to each test
method so that collection succeeds and each test fails individually
(red-first rule, decomposition-principles §8).

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

from iteration_timing import analyze_iteration, normalise_command  # noqa: E402


# ── AC-1: within-iteration repeat detection on a real fixture ─────────────────


class TestWithinIterationRepeat:
    """AC-1: find_within_iteration_repeats() detects commands repeated
    within a single iteration file.

    Uses the committed iter-07.log.jsonl fixture, which contains 3
    executions of `python3 -m pytest -q` (after normalisation) within
    one iteration — a real run from gh-resolve 2026-08-20.
    """

    def test_detects_repeated_command(self):
        """The iter-07 fixture has `python3 -m pytest -q` repeated 3 times.

        find_within_iteration_repeats must return an entry with count >= 2,
        total_wallclock_sec > 0, and the correct normalised command.
        """
        from iteration_timing import find_within_iteration_repeats
        result = find_within_iteration_repeats(FIXTURES / "iter-07.log.jsonl")
        assert isinstance(result, list), "must return a list"

        # Filter to pytest entries (exclude poll commands that also match
        # is_test_command due to 'pytest' in the log path).
        pytest_entries = [
            e for e in result
            if "pytest" in e.get("normalised", "")
            and "grep" not in e.get("normalised", "")
        ]
        assert len(pytest_entries) >= 1, (
            "iter-07.log.jsonl contains 3 executions of `python3 -m pytest -q`; "
            "find_within_iteration_repeats must detect at least 1 repeat entry"
        )

        entry = pytest_entries[0]
        assert entry.get("count", 0) >= 2, (
            f"expected count >= 2 for repeated pytest, got {entry.get('count')}"
        )
        assert "total_wallclock_sec" in entry, (
            "repeat entry must include total_wallclock_sec"
        )
        assert entry["total_wallclock_sec"] > 0
        assert entry.get("normalised") == normalise_command("python3 -m pytest -q")


# ── AC-3: ceiling signal on hang_600s.jsonl ──────────────────────────────────


class TestCeilingSignal:
    """AC-3: analyze_iteration() reports ceiling_hit_no_output instead of
    hang_suspected. The detection condition is unchanged — broad command,
    duration at the ceiling, no captured output — but the reported meaning
    must not say 'hang'.
    """

    def test_ceiling_signal_replaces_hang(self):
        """analyze_iteration() must return ceiling_hit_no_output (not
        hang_suspected) with exactly one entry for the hang_600s fixture.
        """
        result = analyze_iteration(FIXTURES / "hang_600s.jsonl")

        # The old field must be gone.
        assert "hang_suspected" not in result, (
            "hang_suspected must be renamed to ceiling_hit_no_output; "
            "the 600s calls were not hangs (test_no_worktree_left_behind "
            "passes in 2.52s)"
        )

        # The new field must exist and contain exactly one entry.
        assert "ceiling_hit_no_output" in result, (
            "analyze_iteration() must return a ceiling_hit_no_output key; "
            "the current hang_suspected name misrepresents what is observed"
        )
        ceiling = result["ceiling_hit_no_output"]
        assert len(ceiling) == 1, (
            f"expected 1 ceiling hit, got {len(ceiling)} — "
            "the 600.5s backgrounded broad call must be detected"
        )

        entry = ceiling[0]
        assert "command" in entry, "ceiling entry must include the command"
        assert "python3 -m pytest" in entry["command"]
        assert "duration_sec" in entry, "ceiling entry must include duration_sec"
        assert entry["duration_sec"] >= 599.0


# ── AC-4: no false positives on clean fixtures ───────────────────────────────


class TestNoFalsePositives:
    """AC-4: broad_pytest.jsonl and targeted_pytest.jsonl report zero for
    both the within-iteration repeat and ceiling signals.
    """

    def test_broad_pytest_reports_zero(self):
        """broad_pytest.jsonl triggers neither signal."""
        from iteration_timing import find_within_iteration_repeats

        # Within-iteration repeats: 1 test command, no repeats.
        repeats = find_within_iteration_repeats(FIXTURES / "broad_pytest.jsonl")
        assert repeats == [], (
            f"broad_pytest.jsonl must produce 0 within-iteration repeats, "
            f"got {len(repeats)}"
        )

        # Ceiling signal: 120.5s run, well under 600s ceiling.
        result = analyze_iteration(FIXTURES / "broad_pytest.jsonl")
        assert "ceiling_hit_no_output" in result, (
            "analyze_iteration() must return ceiling_hit_no_output field "
            "even when empty — its absence means the rename hasn't happened"
        )
        assert result["ceiling_hit_no_output"] == [], (
            "broad_pytest.jsonl must produce 0 ceiling hits — "
            "a completed 120.5s run does not hit the 600s ceiling"
        )

    def test_targeted_pytest_reports_zero(self):
        """targeted_pytest.jsonl triggers neither signal."""
        from iteration_timing import find_within_iteration_repeats

        # Within-iteration repeats: 1 test command, no repeats.
        repeats = find_within_iteration_repeats(FIXTURES / "targeted_pytest.jsonl")
        assert repeats == [], (
            f"targeted_pytest.jsonl must produce 0 within-iteration repeats, "
            f"got {len(repeats)}"
        )

        # Ceiling signal: 3.2s targeted run, well under 600s ceiling.
        result = analyze_iteration(FIXTURES / "targeted_pytest.jsonl")
        assert "ceiling_hit_no_output" in result, (
            "analyze_iteration() must return ceiling_hit_no_output field "
            "even when empty — its absence means the rename hasn't happened"
        )
        assert result["ceiling_hit_no_output"] == [], (
            "targeted_pytest.jsonl must produce 0 ceiling hits — "
            "a targeted 3.2s run does not hit the 600s ceiling"
        )
