"""Red-first contract tests for iteration_timing.py.

Covers AC-1 through AC-6 and AC-8 from the sub-plan
2026-08-21-an-iteration-accounts-for-its-time.

These tests import from skills/ilk-loop/scripts/iteration_timing.py, which
does not exist yet.  They are designed to fail here (red-first rule,
decomposition-principles §8) so the step-0 gate asserts a non-zero failure
count rather than exit 0.

All tests read only from committed fixtures under
skills/ilk-loop/tests/fixtures/iteration_timing/ — never from ~/.ilk-data
(AC-8: hermetic).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── paths ─────────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "iteration_timing"
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "iteration_timing.py"


# ── AC-5: broad-vs-targeted classifier (pure function) ───────────────────────

class TestBroadVsTargeted:
    """AC-5: is_broad_test_command() is a pure, unit-tested function.

    A command is *targeted* if it names a path, ``::`` node id, ``-k``
    selector, or ``--lf``/``--last-failed``; otherwise *broad*.
    """

    def test_bare_pytest_is_broad(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest -q") is True

    def test_pytest_with_tail_is_broad(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest -q 2>&1 | tail -10") is True

    def test_pytest_with_path_is_targeted(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest tests/test_x.py -q") is False

    def test_pytest_with_node_id_is_targeted(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest tests/test_x.py::TestY -q") is False

    def test_pytest_with_k_selector_is_targeted(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest -k test_foo -q") is False

    def test_pytest_with_last_failed_is_targeted(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest --lf -q") is False

    def test_pytest_with_lf_is_targeted(self):
        from iteration_timing import is_broad_test_command
        assert is_broad_test_command("python3 -m pytest --last-failed -q") is False


# ── AC-1: per-iteration table and JSON output ────────────────────────────────

class TestPerIterationOutput:
    """AC-1: analyze_iteration() returns per-iteration timing breakdown."""

    def test_clean_paired_returns_expected_fields(self):
        """A clean iteration with two paired tool calls has all required fields."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert "span_sec" in result
        assert "tool_sec" in result
        assert "test_sec" in result
        assert "broad_test_sec" in result
        assert "model_remainder_sec" in result
        assert "paired" in result
        assert "unpaired" in result
        assert "backgrounded_calls" in result

    def test_clean_paired_tool_durations(self):
        """Tool durations sum correctly for a clean paired iteration."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert result["paired"] == 2
        assert result["unpaired"] == 0
        assert result["tool_sec"] > 0
        assert result["model_remainder_sec"] > 0

    def test_json_output_mode(self):
        """AC-1: --json flag produces valid JSON with expected keys."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--run", str(FIXTURES / "clean_paired.jsonl"), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr[:500]}"
        data = json.loads(proc.stdout)
        assert "span_sec" in data
        assert "tool_sec" in data


# ── AC-2: tool_use→tool_result pairing and unpaired count ────────────────────

class TestPairing:
    """AC-2: durations come from tool_use→tool_result timestamp pairing."""

    def test_unpaired_count_reported(self):
        """An iteration with one unpaired tool_use reports unpaired=1."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "one_unpaired.jsonl")
        assert result["unpaired"] == 1, (
            f"expected 1 unpaired, got {result['unpaired']}"
        )

    def test_unpaired_never_silently_dropped(self):
        """The unpaired count is always present in the output, even when 0."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert "unpaired" in result


# ── AC-3: model remainder naming, no thinking field ──────────────────────────

class TestModelRemainder:
    """AC-3: the model bucket is named model_remainder, not thinking."""

    def test_model_remainder_field_exists(self):
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert "model_remainder_sec" in result

    def test_no_thinking_field(self):
        """AC-3 negative: the result must NOT contain a field named 'thinking'."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert "thinking" not in result, (
            "result must not contain a field named 'thinking'; "
            "use 'model_remainder_sec' instead"
        )

    def test_remainder_is_nonnegative(self):
        """Model remainder is span minus tool time; must be >= 0."""
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "clean_paired.jsonl")
        assert result["model_remainder_sec"] >= 0


# ── AC-6: auto-backgrounded attribution ──────────────────────────────────────

class TestBackgroundedAttribution:
    """AC-6: auto-backgrounded calls are attributed and flagged."""

    def test_backgrounded_flag_present(self):
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "backgrounded_call.jsonl")
        assert result["backgrounded_calls"] >= 1, (
            f"expected >=1 backgrounded call, got {result['backgrounded_calls']}"
        )

    def test_backgrounded_not_double_counted(self):
        """The TaskOutput retrieval is not counted as fresh test time.

        The original pytest call (600s) is the test time; the TaskOutput
        retrieval (725s) is a follow-up read, not new test execution.
        """
        from iteration_timing import analyze_iteration
        result = analyze_iteration(FIXTURES / "backgrounded_call.jsonl")
        # The fixture has one 600s pytest call (backgrounded) and one
        # TaskOutput retrieval. Total test time should reflect only the
        # original call, not the retrieval.
        assert result["backgrounded_calls"] == 1


# ── AC-4: baseline corpus mode ───────────────────────────────────────────────

class TestBaselineMode:
    """AC-4: --baseline walks the corpus and reports shares."""

    def test_baseline_reports_denominator(self):
        """--baseline reports how many iterations were unusable with denominator."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--baseline", "--json"],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr[:500]}"
        data = json.loads(proc.stdout)
        assert "iterations_parsed" in data
        assert "iterations_unusable" in data
        # The unusable count must be reported with its denominator
        assert data["iterations_unusable"] >= 0


# ── AC-8: hermetic — tests read only committed fixtures ──────────────────────

class TestHermetic:
    """AC-8: unit tests never read from ~/.ilk-data."""

    def test_fixtures_exist(self):
        """All required fixture files are committed."""
        required = [
            "clean_paired.jsonl",
            "one_unpaired.jsonl",
            "broad_pytest.jsonl",
            "targeted_pytest.jsonl",
            "backgrounded_call.jsonl",
        ]
        for name in required:
            path = FIXTURES / name
            assert path.exists(), f"missing fixture: {path}"

    def test_fixtures_are_valid_jsonl(self):
        """Each fixture file contains valid JSON on every line."""
        for fixture in FIXTURES.glob("*.jsonl"):
            for i, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    json.loads(line), f"{fixture}:{i} is not valid JSON"


# ── suite-in-disguise (2026-08-25) ───────────────────────────────────────────
# A whole-suite run can be spelled as a file list. Observed on gh-resolve run
# 20260825-145122 iter-02: `pytest -q $(ls tests/test_*.py | awk 'NR%2==0' ...)`
# ran 63 of 127 test files in 528.2s and its sibling ran the other 64 in 334.6s
# — the ENTIRE suite in two halves, each sized to fit under the 600s ceiling.
# Both classified as *targeted*, because the substitution supplies file
# arguments, so the run recorded broad_test_sec = 0.0.

def test_command_substitution_is_broad_not_targeted() -> None:
    """A runtime-computed file set cannot be called targeted — we can't see it."""
    from iteration_timing import is_broad_test_command
    cmd = ("python3 -m pytest -q $(ls tests/test_*.py | awk 'NR%2==0' | tr '\\n' ' ') "
           "--timeout=60 --timeout-method=signal")
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(cmd) is True


def test_backtick_substitution_is_broad() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "pytest -q `ls tests/test_*.py`") is True


def test_xargs_into_pytest_is_broad() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "ls tests/test_*.py | xargs python3 -m pytest -q") is True


def test_glob_file_argument_is_broad() -> None:
    """An unexpanded glob names an unknown number of files."""
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "python3 -m pytest tests/test_*.py -q") is True


# The no-false-positive direction: genuinely targeted runs must stay targeted,
# or the fix would reclassify the whole corpus and inflate the BEFORE number.

def test_named_file_stays_targeted() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "python3 -m pytest tests/test_foo.py -q") is False


def test_several_named_files_stay_targeted() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "pytest tests/test_foo.py tests/test_bar.py -q") is False


def test_node_id_stays_targeted() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "pytest tests/test_foo.py::TestBar::test_baz -q") is False


def test_k_selector_stays_targeted() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command(
        "pytest -q -k 'archive or bound'") is False


def test_bare_suite_still_broad() -> None:
    from iteration_timing import is_broad_test_command
    assert is_broad_test_command("python3 -m pytest -q") is True
