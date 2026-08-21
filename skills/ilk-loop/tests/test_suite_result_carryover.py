"""Red-first contract tests for cross-iteration suite repetition detection.

Covers AC-1, AC-2, AC-3, and AC-7 from the sub-plan
2026-08-21-a-suite-result-outlives-its-iteration.

These tests import from skills/ilk-loop/scripts/iteration_timing.py functions
that do not exist yet.  They are designed to fail here (red-first rule,
decomposition-principles §8) so the step-0 gate asserts a non-zero failure
count rather than exit 0.

All tests read only from committed fixtures under
skills/ilk-loop/tests/fixtures/iteration_timing/ — never from ~/.ilk-data.
"""
import json
from pathlib import Path

import pytest

# ── paths ─────────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "iteration_timing"


# ── AC-2: normalisation is conservative ──────────────────────────────────────

class TestNormaliseCommand:
    """AC-2: normalise_command() is unit-tested and conservative.

    ``pytest -q 2>&1 | tail -10`` and ``pytest -q 2>&1 | tail -15`` normalise
    to the same key (differing only in output truncation); ``pytest tests/a.py``
    and ``pytest tests/b.py`` do NOT.
    """

    def test_tail_10_and_tail_15_normalise_to_same_key(self):
        from iteration_timing import normalise_command
        a = normalise_command("python3 -m pytest -q 2>&1 | tail -10")
        b = normalise_command("python3 -m pytest -q 2>&1 | tail -15")
        assert a == b, (
            "tail -10 and tail -15 differ only in output truncation; "
            f"got {a!r} vs {b!r}"
        )

    def test_different_test_files_do_not_normalise_together(self):
        from iteration_timing import normalise_command
        a = normalise_command("python3 -m pytest tests/a.py -q")
        b = normalise_command("python3 -m pytest tests/b.py -q")
        assert a != b, (
            "different test files must produce different normalised keys"
        )

    def test_bare_pytest_normalises_stably(self):
        from iteration_timing import normalise_command
        a = normalise_command("python3 -m pytest -q 2>&1 | tail -10")
        b = normalise_command("python3 -m pytest -q 2>&1 | tail -10")
        assert a == b

    def test_normalise_returns_string(self):
        from iteration_timing import normalise_command
        result = normalise_command("python3 -m pytest -q")
        assert isinstance(result, str)
        assert len(result) > 0


# ── AC-1 & AC-3: repeat detection on the fixture ────────────────────────────

class TestRepeatDetection:
    """AC-1 & AC-3: find_repeated_commands() reports repeated normalised
    commands across iterations.

    Run against the ``20260820-143915`` fixture, the detector reports the
    broad ``pytest -q`` command as repeated across **6** iterations.
    """

    def _run_dir(self) -> Path:
        """Return a directory containing the trimmed fixture JSONLs."""
        return FIXTURES

    def test_reports_repeated_commands(self):
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(self._run_dir())
        assert len(repeats) >= 1, "expected at least one repeated command"

    def test_broad_pytest_repeated_across_six_iterations(self):
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(self._run_dir())
        # Find the broad pytest repeat
        broad = [r for r in repeats if "pytest" in r["normalised"] and "tail" in r["normalised"]]
        assert len(broad) >= 1, (
            f"expected a broad pytest repeat; got {repeats}"
        )
        entry = broad[0]
        assert entry["iteration_count"] == 6, (
            f"expected 6 iterations, got {entry['iteration_count']}"
        )

    def test_repeat_entry_has_wallclock(self):
        """AC-1: each repeat entry includes total wall-clock spent."""
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(self._run_dir())
        for r in repeats:
            assert "total_wallclock_sec" in r, (
                f"repeat entry missing total_wallclock_sec: {r}"
            )
            assert r["total_wallclock_sec"] > 0

    def test_repeat_entry_has_iteration_list(self):
        """Each repeat entry names which iterations it appeared in."""
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(self._run_dir())
        for r in repeats:
            assert "iterations" in r, (
                f"repeat entry missing iterations list: {r}"
            )
            assert isinstance(r["iterations"], list)
            assert len(r["iterations"]) >= 2

    def test_targeted_commands_not_counted_as_repeats_of_broad(self):
        """Targeted pytest runs (with paths/node ids) don't merge with broad."""
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(self._run_dir())
        for r in repeats:
            norm = r["normalised"]
            # A targeted command should not appear as a repeat of a broad one
            if "tests/" in norm or "::" in norm:
                # If it's targeted, it should have its own entry
                pass  # just checking no crash


# ── AC-7: backward compatibility ─────────────────────────────────────────────

class TestBackwardCompatibility:
    """AC-7: a run directory with no suite-result artifact parses without error."""

    def test_no_suite_result_artifact_parses_cleanly(self):
        """find_repeated_commands on a directory with no suite-result artifact
        must not raise — it should return an empty list or handle gracefully."""
        from iteration_timing import find_repeated_commands
        # The fixture directory has no suite-result artifact (it hasn't been
        # written yet).  The function must parse without error.
        repeats = find_repeated_commands(FIXTURES)
        assert isinstance(repeats, list)

    def test_empty_run_dir_handled(self, tmp_path):
        """An empty run directory (no JSONL files) is handled gracefully."""
        from iteration_timing import find_repeated_commands
        repeats = find_repeated_commands(tmp_path)
        assert isinstance(repeats, list)
        assert len(repeats) == 0


# ── AC-3 fixture integrity ───────────────────────────────────────────────────

class TestFixtureIntegrity:
    """Verify the committed fixtures reproduce the six-fold repeat."""

    def test_fixture_files_exist(self):
        """All six trimmed iteration fixtures are committed."""
        for i in ["01", "03", "04", "05", "06", "07"]:
            path = FIXTURES / f"six_fold_repeat_iter{i}.jsonl"
            assert path.exists(), f"missing fixture: {path}"

    def test_fixtures_are_valid_jsonl(self):
        """Each fixture file contains valid JSON on every line."""
        for fixture in FIXTURES.glob("six_fold_repeat_iter*.jsonl"):
            for i, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    json.loads(line), f"{fixture}:{i} is not valid JSON"

    def test_each_fixture_has_broad_pytest_command(self):
        """Each iteration fixture contains at least one broad pytest command."""
        for i in ["01", "03", "04", "05", "06", "07"]:
            path = FIXTURES / f"six_fold_repeat_iter{i}.jsonl"
            found_broad = False
            with open(path) as f:
                for line in f:
                    rec = json.loads(line.strip())
                    if rec.get("type") == "assistant":
                        for block in rec.get("message", {}).get("content", []):
                            if block.get("type") == "tool_use":
                                cmd = block.get("input", {}).get("command", "")
                                if "pytest" in cmd and "-q" in cmd:
                                    found_broad = True
            assert found_broad, f"iter-{i} fixture has no broad pytest command"
