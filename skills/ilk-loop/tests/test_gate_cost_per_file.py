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

def _write_iter(run: Path, name: str, ts: str, commands: list[tuple]) -> None:
    """Write an iteration log from (command, gap_sec[, reported_sec]) tuples.

    *gap_sec* is the transcript gap — the interval between the tool_use record
    and its tool_result record.  *reported_sec* is what pytest printed in its
    own summary line; it defaults to *gap_sec* (an uncontaminated call, where
    the two agree) and is set explicitly by the tests that exercise the case
    where they DIVERGE.  Pass None for it to model a call that produced no
    summary at all — a ceiling-hit or backgrounded call.

    Keeping both is the point of the fixture: the report must read the second
    and never the first.
    """
    run.mkdir(parents=True, exist_ok=True)
    base = datetime.fromisoformat(ts)
    lines = []
    for i, spec in enumerate(commands):
        cmd, gap = spec[0], spec[1]
        reported = spec[2] if len(spec) > 2 else gap
        tid = f"call_{i}"
        body = "" if reported is None else f"1 passed in {reported:.2f}s"
        lines.append(json.dumps({
            "timestamp": (base + timedelta(seconds=i)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": cmd}}]},
        }))
        lines.append(json.dumps({
            "timestamp": (base + timedelta(seconds=i + gap)).isoformat(),
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": body}]},
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


# -- AC-8: the duration is pytest's, not the transcript gap -------------------

class TestDurationSource:
    """A per-file duration comes from pytest's own summary line.

    The transcript gap is an upper bound, not a measurement: the transcript
    serialises, so a call issued while a backgrounded suite is in flight gets
    the background task's elapsed time stamped on it.  Measured on the real
    corpus 2026-09-05: tests/test_no_committed_identities.py was published at
    158.4s from a gap whose pytest summary said 0.06s, and plan_lint's budget
    lint consumed that number.
    """

    @pytest.fixture
    def corpus_contaminated_gap(self, tmp_path: Path) -> Path:
        """A 0.06s test whose result record landed 158.4s late."""
        root = tmp_path / "projects"
        run = root / "proj-c" / "logs" / "runs" / "20260906-100000"
        _write_iter(run, "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00", [
            ("python3 -m pytest tests/test_guard.py -q", 158.4, 0.06),
        ])
        return root

    def test_gap_is_not_the_measurement(self, corpus_contaminated_gap: Path) -> None:
        result = gate_cost.per_file_report(root=corpus_contaminated_gap)
        entry = result["per_project"]["proj-c"]["per_file"][0]
        assert entry["file"] == "tests/test_guard.py"
        assert entry["max_s"] == pytest.approx(0.06), (
            "per-file cost used the transcript gap instead of pytest's own number"
        )

    def test_gap_is_still_reported_for_contrast(self, corpus_contaminated_gap: Path) -> None:
        """The gap is kept and labelled — it is evidence, just not the budget."""
        result = gate_cost.per_file_report(root=corpus_contaminated_gap)
        entry = result["per_project"]["proj-c"]["per_file"][0]
        assert entry["max_gap_s"] == pytest.approx(158.4)

    def test_untimed_call_is_null_not_zero(self, tmp_path: Path) -> None:
        """A call pytest did not time is unmeasured, and says so.

        Silently substituting the gap here is the whole defect; silently
        substituting 0.0 would be worse, because a genuinely slow file would
        read as free.
        """
        root = tmp_path / "projects"
        run = root / "proj-d" / "logs" / "runs" / "20260906-100000"
        _write_iter(run, "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00", [
            ("python3 -m pytest tests/test_slow.py -q", 600.5, None),
        ])
        result = gate_cost.per_file_report(root=root)
        proj = result["per_project"]["proj-d"]
        entry = proj["per_file"][0]
        assert entry["max_s"] is None
        assert entry["invocations"] == 1
        assert entry["measured_invocations"] == 0
        assert proj["unmeasured_invocations"] == 1

    def test_max_is_over_measured_calls_only(self, tmp_path: Path) -> None:
        """One contaminated call must not raise a file's published cost."""
        root = tmp_path / "projects"
        run = root / "proj-e" / "logs" / "runs" / "20260906-100000"
        _write_iter(run, "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00", [
            ("python3 -m pytest tests/test_guard.py -q", 0.3, 0.05),
            ("python3 -m pytest tests/test_guard.py -q", 158.4, 0.06),
            ("python3 -m pytest tests/test_guard.py -q", 0.4, 0.07),
        ])
        result = gate_cost.per_file_report(root=root)
        entry = result["per_project"]["proj-e"]["per_file"][0]
        assert entry["max_s"] == pytest.approx(0.07)
        assert entry["measured_invocations"] == 3


# -- AC-9: only real files reach the per-file report --------------------------

class TestTargetTokenisation:
    """A per-FILE report may only contain things that are files.

    The old parser kept any token that had a slash, started with "test", or
    ended in ".py".  On the real corpus that admitted 44 non-files into
    gh-resolve's 547 entries — a bare directory, an unexpanded glob, a shell
    redirection fragment, and a gate's own output file among them.
    """

    @pytest.mark.parametrize("cmd,expected", [
        # The four shapes observed in the corpus, each previously a "file"
        ("python3 -m pytest tests/ -q", []),
        ("python3 -m pytest tests/test_*.py -q", []),
        ("python3 -m pytest tests/test_a.py -q 2>/dev/null; echo done",
         ["tests/test_a.py"]),
        ("python3 -m pytest tests/test_a.py -q > /tmp/test_results.txt",
         ["tests/test_a.py"]),
        # Quoting was never stripped, so quoted duplicates appeared alongside
        # their unquoted twins
        ('python3 -m pytest "tests/test_a.py::TestX::test_y" -q',
         ["tests/test_a.py::TestX::test_y"]),
        # An unexpanded shell variable is not a path
        ('python3 -m pytest "$D/run.txt" -q', []),
        # A second command after a separator names no pytest target
        ("python3 -m pytest tests/test_a.py -q && cat tests/test_b.py",
         ["tests/test_a.py"]),
        # Node ids and ordinary paths still parse
        ("python3 -m pytest tests/test_a.py::TestX -q --timeout=60",
         ["tests/test_a.py::TestX"]),
        ("python3 -m pytest -k 'slow and not flaky' tests/test_a.py",
         ["tests/test_a.py"]),
    ])
    def test_only_files_are_returned(self, cmd: str, expected: list) -> None:
        assert gate_cost._parse_test_files(cmd) == expected

    def test_non_file_target_still_counts_as_a_target(self) -> None:
        """A directory alongside a file makes the run multi-target.

        Dropping the directory and calling what remains a single-file
        measurement re-creates the attribution bug in a new place: it moved
        test_data_home_sandbox.py from 4.6s to 86.8s by claiming a two-target
        run's time for one file.
        """
        files, other = gate_cost._parse_pytest_targets(
            "python3 -m pytest skills/a/tests/test_x.py skills/b/tests/ -q"
        )
        assert files == ["skills/a/tests/test_x.py"]
        assert other == ["skills/b/tests/"]

    def test_file_plus_directory_is_excluded_from_per_file(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        run = root / "proj-f" / "logs" / "runs" / "20260906-100000"
        _write_iter(run, "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00", [
            ("python3 -m pytest tests/test_a.py other/tests/ -q", 86.8, 86.8),
        ])
        result = gate_cost.per_file_report(root=root)
        proj = result["per_project"]["proj-f"]
        assert proj["per_file"] == [], (
            "a two-target run was attributed to its single .py target"
        )
        assert proj["single_file_invocations"] == 0
        assert proj["total_pytest_invocations"] == 1

    def test_report_contains_no_shell_fragments(self, tmp_path: Path) -> None:
        """The cheap invariant: nothing in per_file may look like shell."""
        import re as _re
        root = tmp_path / "projects"
        run = root / "proj-g" / "logs" / "runs" / "20260906-100000"
        _write_iter(run, "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00", [
            ("python3 -m pytest tests/ -q 2>/dev/null; cat /tmp/test_results.txt", 601.0),
            ("python3 -m pytest tests/test_*.py -q", 546.0),
            ("python3 -m pytest tests/test_real.py -q", 1.2),
        ])
        result = gate_cost.per_file_report(root=root)
        files = [e["file"] for e in result["per_project"]["proj-g"]["per_file"]]
        assert files == ["tests/test_real.py"]
        for f in files:
            assert not _re.search(r"[;&|<>$`*?\[\]]", f), f
            assert not f.endswith("/"), f
            assert f.split("::")[0].endswith(".py"), f


# -- schema ------------------------------------------------------------------

def test_schema_is_2_now_that_max_s_changed_meaning(corpus_multiple_files: Path) -> None:
    """max_s changed from gap seconds to pytest seconds, and can be null.

    A consumer keyed on schema 1 was reading a different quantity under the
    same name; the version is how it can tell.
    """
    result = gate_cost.per_file_report(root=corpus_multiple_files)
    assert result["schema"] == 2


def test_chained_command_uses_the_first_summary() -> None:
    """`pytest a.py && pytest tests/` prints two summaries; a.py owns the first.

    The parsed target is the first pytest in the string, so reading the last
    summary would stamp the chained full suite's time onto one file — the
    same attribution error the gap already made.
    """
    body = (
        "..                                          [100%]\n"
        "2 passed in 0.02s\n"
        "=== full suite ===\n"
        "........................................    [100%]\n"
        "412 passed in 5.78s\n"
    )
    assert gate_cost._pytest_reported_seconds(body) == pytest.approx(0.02)


def test_no_summary_is_none_not_zero() -> None:
    assert gate_cost._pytest_reported_seconds("") is None
    assert gate_cost._pytest_reported_seconds("bash: pytest: command not found") is None


# -- --project scoping --------------------------------------------------------

class TestProjectScoping:
    """--project restricts the scan to one project key.

    Unscoped, gate_cost reads every project's whole run corpus (~12.5s here,
    growing).  plan_lint pays that per subprocess, which is what pushed
    test_plan_lint_supervised_only.py to 132.7s against a 120s budget.  It is
    also an attribution fix: lint_gate_budget merges file_costs across every
    project and falls back to a basename match, so another repo's
    tests/test_drain.py could supply this repo's budget.
    """

    @pytest.fixture
    def two_projects(self, tmp_path: Path) -> Path:
        root = tmp_path / "projects"
        _write_iter(root / "proj-one" / "logs" / "runs" / "20260906-100000",
                    "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00",
                    [("python3 -m pytest tests/test_shared.py -q", 1.0)])
        _write_iter(root / "proj-two" / "logs" / "runs" / "20260906-100000",
                    "iter-01.log.jsonl", "2026-09-06T10:00:00+08:00",
                    [("python3 -m pytest tests/test_shared.py -q", 99.0)])
        return root

    def test_unscoped_sees_both(self, two_projects: Path) -> None:
        result = gate_cost.per_file_report(root=two_projects)
        assert set(result["per_project"]) == {"proj-one", "proj-two"}

    def test_scoped_sees_only_that_project(self, two_projects: Path) -> None:
        result = gate_cost.per_file_report(root=two_projects, project="proj-one")
        assert set(result["per_project"]) == {"proj-one"}
        entry = result["per_project"]["proj-one"]["per_file"][0]
        assert entry["max_s"] == pytest.approx(1.0), (
            "the other project's same-named file supplied the cost"
        )

    def test_scope_is_recorded_in_the_output(self, two_projects: Path) -> None:
        """A scoped report must say so — its denominator is not the corpus."""
        result = gate_cost.per_file_report(root=two_projects, project="proj-one")
        assert result["project"] == "proj-one"
        assert gate_cost.per_file_report(root=two_projects)["project"] is None

    def test_unknown_key_fails_loud(self, two_projects: Path) -> None:
        """An unknown key must not read as an empty corpus.

        Reporting "no measurements" for a typo'd key is the negative-without-
        a-denominator failure: it looks exactly like a project that has never
        run a gate.
        """
        with pytest.raises(SystemExit) as exc:
            gate_cost.per_file_report(root=two_projects, project="proj-typo")
        assert "proj-typo" in str(exc.value)
        assert "2 projects present" in str(exc.value)
