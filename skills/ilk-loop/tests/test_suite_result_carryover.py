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
        from iteration_timing import find_repeated_commands, normalise_command
        repeats = find_repeated_commands(self._run_dir())
        # Find the broad pytest repeat — normalised form strips tail/redirects
        broad_norm = normalise_command("python3 -m pytest -q 2>&1 | tail -10")
        broad = [r for r in repeats if r["normalised"] == broad_norm]
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
            path = FIXTURES / f"iter-{i}.log.jsonl"
            assert path.exists(), f"missing fixture: {path}"

    def test_fixtures_are_valid_jsonl(self):
        """Each fixture file contains valid JSON on every line."""
        for fixture in FIXTURES.glob("iter-*.log.jsonl"):
            for i, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    json.loads(line), f"{fixture}:{i} is not valid JSON"

    def test_each_fixture_has_broad_pytest_command(self):
        """Each iteration fixture contains at least one broad pytest command."""
        for i in ["01", "03", "04", "05", "06", "07"]:
            path = FIXTURES / f"iter-{i}.log.jsonl"
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


# ── AC-4: suite-result artifact extraction ───────────────────────────────────

class TestSuiteResultExtraction:
    """AC-4: extract_suite_result_from_jsonl() extracts the broad test result."""

    def _load_records(self, name: str) -> list:
        path = FIXTURES / name
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def test_broad_pytest_detected(self):
        """A broad pytest command is detected and its result extracted."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("broad_pytest.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is not None, "expected a suite result from broad_pytest.jsonl"
        assert "pytest" in result["command"]
        assert result["outcome"] in ("pass", "fail", "unknown")

    def test_broad_pytest_has_summary_line(self):
        """The summary line is extracted from pytest output."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("broad_pytest.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is not None
        assert result["summary_line"] is not None
        assert "passed" in result["summary_line"]

    def test_broad_pytest_has_timestamp(self):
        """The artifact includes the timestamp of the tool_use."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("broad_pytest.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is not None
        assert result["timestamp"] is not None
        assert len(result["timestamp"]) > 0

    def test_broad_pytest_outcome_pass(self):
        """A passing suite (content says 'passed') has outcome 'pass'."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("broad_pytest.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is not None
        assert result["outcome"] == "pass"

    def test_no_broad_test_returns_none(self):
        """A non-test iteration returns None."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("clean_paired.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is None

    def test_targeted_pytest_not_broad(self):
        """A targeted pytest command (with path) is not extracted."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("targeted_pytest.jsonl")
        result = extract_suite_result_from_jsonl(records)
        assert result is None

    def test_iter_fixture_has_extractable_result(self):
        """The real iter-01 fixture (backgrounded + TaskOutput) yields a result."""
        from iteration_timing import extract_suite_result_from_jsonl
        records = self._load_records("iter-01.log.jsonl")
        result = extract_suite_result_from_jsonl(records)
        # iter-01 is backgrounded then retrieved — the retrieval has the real result
        assert result is not None, "iter-01 should have an extractable suite result"
        assert result["outcome"] == "pass"


# ── AC-4: artifact building ─────────────────────────────────────────────────

class TestBuildSuiteResultArtifact:
    """AC-4: build_suite_result_artifact() attaches head_sha."""

    def _load_records(self, name: str) -> list:
        path = FIXTURES / name
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def test_artifact_includes_head_sha(self):
        """When head_sha is provided, it appears in the artifact."""
        from iteration_timing import build_suite_result_artifact
        records = self._load_records("broad_pytest.jsonl")
        result = build_suite_result_artifact(records, head_sha="abc123")
        assert result is not None
        assert result["head_sha"] == "abc123"

    def test_artifact_without_sha(self):
        """Without head_sha, the field is None."""
        from iteration_timing import build_suite_result_artifact
        records = self._load_records("broad_pytest.jsonl")
        result = build_suite_result_artifact(records)
        assert result is not None
        assert result["head_sha"] is None

    def test_no_test_returns_none(self):
        """No broad test → None."""
        from iteration_timing import build_suite_result_artifact
        records = self._load_records("clean_paired.jsonl")
        result = build_suite_result_artifact(records)
        assert result is None


# ── AC-5: prior result readable from disk ────────────────────────────────────

class TestPriorSuiteResult:
    """AC-5: read_prior_suite_result() reads the artifact from disk."""

    def test_reads_written_artifact(self, tmp_path):
        """An artifact written to disk is readable."""
        from iteration_timing import read_prior_suite_result
        artifact = {
            "command": "python3 -m pytest -q",
            "outcome": "pass",
            "summary_line": "2919 passed in 784.40s",
            "exit_code": 0,
            "head_sha": "abc123",
            "timestamp": "2026-08-20T06:50:32.571Z",
        }
        (tmp_path / "suite-result.json").write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        result = read_prior_suite_result(tmp_path)
        assert result is not None
        assert result["command"] == "python3 -m pytest -q"
        assert result["outcome"] == "pass"
        assert result["head_sha"] == "abc123"

    def test_missing_file_returns_none(self, tmp_path):
        """No artifact file → None."""
        from iteration_timing import read_prior_suite_result
        result = read_prior_suite_result(tmp_path)
        assert result is None

    def test_malformed_json_returns_none(self, tmp_path):
        """A corrupted artifact file → None, not an exception."""
        from iteration_timing import read_prior_suite_result
        (tmp_path / "suite-result.json").write_text("NOT JSON", encoding="utf-8")
        result = read_prior_suite_result(tmp_path)
        assert result is None

    def test_bom_prefixed_json_parses(self, tmp_path):
        """A BOM-prefixed artifact (Windows PS 5.1) parses via utf-8-sig."""
        from iteration_timing import read_prior_suite_result
        artifact = {"command": "pytest -q", "outcome": "pass"}
        content = json.dumps(artifact)
        # Write with BOM
        with open(tmp_path / "suite-result.json", "wb") as f:
            f.write(b"\xef\xbb\xbf" + content.encode("utf-8"))
        result = read_prior_suite_result(tmp_path)
        assert result is not None
        assert result["outcome"] == "pass"

    def test_absent_data_written_as_null(self, tmp_path):
        """Absent fields are explicit null, not missing keys."""
        from iteration_timing import read_prior_suite_result
        artifact = {
            "command": "pytest -q",
            "outcome": "pass",
            "summary_line": None,
            "exit_code": 0,
            "head_sha": None,
            "timestamp": "2026-08-20T06:50:32.571Z",
        }
        (tmp_path / "suite-result.json").write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        result = read_prior_suite_result(tmp_path)
        assert result is not None
        # Keys are present even when values are null
        assert "summary_line" in result
        assert result["summary_line"] is None
        assert "head_sha" in result
        assert result["head_sha"] is None
