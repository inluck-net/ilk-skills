"""Tests for gate_cost --by-test-file — per-test-file wall-clock measurement.

These pin the contract that SP4 (plan_lint budget finding) depends on:

  AC-1  Per project: each test file gets invocation count, total seconds, max.
  AC-2  The report states its denominator (usable single-file invocations).
  AC-3  Multi-file invocations contribute to NO per-file total; separate section.
  AC-4  --json emits a stable object with schema version.
  AC-5  Zero measurements prints "no measurements" with window + run count.
  AC-6  --since / --after / --before filter this mode.
  AC-7  Existing gate_cost output and tests are unchanged (covered by test_gate_cost.py).

Fixtures are synthetic — never reads real ~/.ilk-data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gate_cost  # noqa: E402


# -- helpers ------------------------------------------------------------------

def _write_iter(run: Path, name: str, ts: str, commands: list[tuple[str, float]]) -> None:
    """Write an iteration log with *commands* as (command, duration_sec) pairs."""
    run.mkdir(parents=True, exist_ok=True)
    base = datetime.fromisoformat(ts)
    lines = []
    for i, (cmd, dur) in enumerate(commands):
        tid = f"call_{i}"
        lines.append(json.dumps({
            "timestamp": (base + timedelta(seconds=i)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": cmd}}]},
        }))
        lines.append(json.dumps({
            "timestamp": (base + timedelta(seconds=i + dur)).isoformat(),
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": ""}]},
        }))
    (run / name).write_text("\n".join(lines) + "\n")


# -- fixtures -----------------------------------------------------------------

@pytest.fixture
def corpus_single_and_multi(tmp_path: Path) -> Path:
    """One single-file call and one two-file call over the same file.

    The two-file call must NOT contribute to the per-file total (AC-3).
    """
    root = tmp_path / "projects"
    run = root / "proj-a" / "logs" / "runs" / "20260825-100000"
    _write_iter(run, "iter-01.log.jsonl", "2026-08-25T10:00:00+08:00", [
        # Single-file: should count toward tests/test_alpha.py
        ("python3 -m pytest tests/test_alpha.py -q", 45.0),
        # Multi-file: should NOT count toward any per-file total
        ("python3 -m pytest tests/test_alpha.py tests/test_beta.py -q", 120.0),
    ])
    return root


@pytest.fixture
def corpus_multiple_files(tmp_path: Path) -> Path:
    """Three test files across two iterations, one of them slow."""
    root = tmp_path / "projects"
    run = root / "proj-b" / "logs" / "runs" / "20260825-100000"
    _write_iter(run, "iter-01.log.jsonl", "2026-08-25T10:00:00+08:00", [
        ("python3 -m pytest tests/test_drain.py -q", 199.0),
        ("python3 -m pytest tests/test_pass.py -q", 225.0),
    ])
    _write_iter(run, "iter-02.log.jsonl", "2026-08-25T10:30:00+08:00", [
        ("python3 -m pytest tests/test_drain.py -q", 205.0),
        ("python3 -m pytest tests/test_guard.py -q", 197.0),
    ])
    return root


@pytest.fixture
def corpus_empty(tmp_path: Path) -> Path:
    """A project with runs but no pytest invocations at all."""
    root = tmp_path / "projects"
    run = root / "proj-empty" / "logs" / "runs" / "20260825-100000"
    _write_iter(run, "iter-01.log.jsonl", "2026-08-25T10:00:00+08:00", [
        ("echo hello", 0.1),
    ])
    return root


# -- AC-1: per-file stats -----------------------------------------------------

class TestPerFileStats:
    """Each test file gets count, total seconds, and max (AC-1)."""

    def test_single_file_has_count_total_max(self, corpus_multiple_files: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_multiple_files)
        drain = None
        for entry in result["per_project"]["proj-b"]["per_file"]:
            if entry["file"] == "tests/test_drain.py":
                drain = entry
                break
        assert drain is not None, "test_drain.py missing from per_file"
        assert drain["invocations"] == 2
        assert drain["total_s"] == pytest.approx(404.0)  # 199 + 205
        assert drain["max_s"] == pytest.approx(205.0)

    def test_single_invocation_file(self, corpus_multiple_files: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_multiple_files)
        guard = None
        for entry in result["per_project"]["proj-b"]["per_file"]:
            if entry["file"] == "tests/test_guard.py":
                guard = entry
                break
        assert guard is not None
        assert guard["invocations"] == 1
        assert guard["total_s"] == pytest.approx(197.0)
        assert guard["max_s"] == pytest.approx(197.0)


# -- AC-2: denominator --------------------------------------------------------

class TestDenominator:
    """The report states how many of total pytest invocations were usable (AC-2)."""

    def test_denominator_printed(self, corpus_single_and_multi: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_single_and_multi)
        proj = result["per_project"]["proj-a"]
        # 2 total pytest invocations, but only 1 is single-file
        assert proj["total_pytest_invocations"] == 2
        assert proj["single_file_invocations"] == 1

    def test_denominator_in_json(self, corpus_single_and_multi: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_single_and_multi, as_json=True)
        proj = result["per_project"]["proj-a"]
        assert proj["total_pytest_invocations"] == 2
        assert proj["single_file_invocations"] == 1


# -- AC-3: multi-file excluded ------------------------------------------------

class TestMultiFileExcluded:
    """Multi-file invocations contribute to NO per-file total (AC-3)."""

    def test_multi_file_not_in_per_file(self, corpus_single_and_multi: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_single_and_multi)
        proj = result["per_project"]["proj-a"]
        # Only the single-file call (45s) should appear; the 120s two-file call must not
        alpha = None
        for entry in proj["per_file"]:
            if entry["file"] == "tests/test_alpha.py":
                alpha = entry
                break
        assert alpha is not None
        assert alpha["total_s"] == pytest.approx(45.0), (
            "multi-file invocation leaked into per-file total"
        )

    def test_multi_file_appear_in_separate_section(self, corpus_single_and_multi: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_single_and_multi)
        proj = result["per_project"]["proj-a"]
        multi = proj.get("multi_file", [])
        assert len(multi) >= 1, "multi-file invocation missing from multi_file section"
        # The multi-file section is keyed by the file set
        found = any(
            set(m["files"]) == {"tests/test_alpha.py", "tests/test_beta.py"}
            for m in multi
        )
        assert found, "expected {test_alpha, test_beta} in multi_file section"


# -- AC-4: JSON with schema version -------------------------------------------

class TestJsonOutput:
    """--json emits a stable object with schema version (AC-4)."""

    def test_json_has_schema_version(self, corpus_multiple_files: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_multiple_files, as_json=True)
        assert "schema" in result, "JSON output must have a 'schema' field"
        assert isinstance(result["schema"], int)
        assert result["schema"] >= 1

    def test_json_has_per_project(self, corpus_multiple_files: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_multiple_files, as_json=True)
        assert "per_project" in result
        assert "proj-b" in result["per_project"]


# -- AC-5: zero measurements --------------------------------------------------

class TestZeroMeasurements:
    """Zero measurements prints 'no measurements' with window + run count (AC-5)."""

    def test_no_pytest_invocations(self, corpus_empty: Path, capsys) -> None:
        gate_cost.per_file_report(root=corpus_empty)
        out = capsys.readouterr().out
        assert "no measurements" in out.lower() or "no measurements" in out

    def test_no_pytest_invocations_names_window(self, corpus_empty: Path, capsys) -> None:
        gate_cost.per_file_report(root=corpus_empty, since="20260825")
        out = capsys.readouterr().out
        # Must mention the window searched so the reader knows the denominator
        assert "20260825" in out or "since" in out.lower()

    def test_no_pytest_invocations_names_run_count(self, corpus_empty: Path, capsys) -> None:
        gate_cost.per_file_report(root=corpus_empty)
        out = capsys.readouterr().out
        # Must mention how many runs were searched
        assert "run" in out.lower() or "iteration" in out.lower()


# -- AC-6: --since / --after / --before filtering ----------------------------

class TestTimeFilters:
    """--since / --after / --before filter this mode (AC-6)."""

    def test_since_filters_runs(self, corpus_multiple_files: Path) -> None:
        # With a since date after all runs, nothing should be found
        result = gate_cost.per_file_report(root=corpus_multiple_files, since="20260826")
        assert result["per_project"] == {}

    def test_after_filters_iterations(self, corpus_multiple_files: Path) -> None:
        # iter-01 starts at 10:00, iter-02 at 10:30
        after = datetime.fromisoformat("2026-08-25T10:15:00+08:00")
        result = gate_cost.per_file_report(root=corpus_multiple_files, after=after)
        proj = result["per_project"].get("proj-b", {})
        # Only iter-02's files should appear
        files = {e["file"] for e in proj.get("per_file", [])}
        assert "tests/test_drain.py" in files  # from iter-02
        assert "tests/test_guard.py" in files  # from iter-02
        # test_pass.py was only in iter-01, should be excluded
        assert "tests/test_pass.py" not in files

    def test_before_filters_iterations(self, corpus_multiple_files: Path) -> None:
        before = datetime.fromisoformat("2026-08-25T10:15:00+08:00")
        result = gate_cost.per_file_report(root=corpus_multiple_files, before=before)
        proj = result["per_project"].get("proj-b", {})
        files = {e["file"] for e in proj.get("per_file", [])}
        # Only iter-01's files should appear
        assert "tests/test_pass.py" in files
        assert "tests/test_guard.py" not in files
