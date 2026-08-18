"""Tests for baseline_diff.py — node-id diff against a named tag baseline.

Step 0 focuses on:
- AC-1: comparison by node id, not count (same-count fixture proves it)
- AC-2: ref is resolved via git describe, never assumed
- AC-3: missing baseline is "could not compare", distinct from "zero regressions"
- AC-8: stale baseline_red entries reported
- AC-9: baselines keyed by (tag, suite-invocation flags)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from baseline_diff import (
    BaselineReport,
    BaselineStatus,
    CollectionError,
    CollectionFloor,
    RunVerdict,
    StaleExclusion,
    Verdict,
    baseline_key,
    check_stale_exclusions,
    compare,
    diff_by_node_id,
    format_denominator,
    load_baseline,
    make_ref,
    parse_collection_output,
    resolve_last_tag,
    run_baseline_diff,
    run_with_timeout,
    store_baseline,
)


# ── AC-1: node-id diff, not count ───────────────────────────────────────────

class TestNodeIdDiff:
    """AC-1: same failure count, different node ids must NOT report zero regressions."""

    def test_same_count_different_ids_detects_regressions(self) -> None:
        """The core AC-1 fixture: counts match, ids differ.

        If the implementation used counts, this would report 0 regressions.
        """
        # 3 failures in each, but completely different node ids
        baseline = frozenset({
            "tests/test_alpha.py::test_one",
            "tests/test_alpha.py::test_two",
            "tests/test_alpha.py::test_three",
        })
        current = frozenset({
            "tests/test_beta.py::test_x",
            "tests/test_beta.py::test_y",
            "tests/test_beta.py::test_z",
        })
        new, inherited, fixed = diff_by_node_id(current, baseline)

        assert len(new) == 3, "all 3 current failures are new"
        assert len(inherited) == 0, "none are inherited"
        assert len(fixed) == 3, "all 3 baseline failures are fixed"
        assert new == current

    def test_partial_overlap(self) -> None:
        """Some overlap, some new, some fixed."""
        baseline = frozenset({"a", "b", "c"})
        current = frozenset({"b", "c", "d"})
        new, inherited, fixed = diff_by_node_id(current, baseline)

        assert new == frozenset({"d"})
        assert inherited == frozenset({"b", "c"})
        assert fixed == frozenset({"a"})

    def test_identical_sets(self) -> None:
        """No regressions when sets match exactly."""
        ids = frozenset({"a", "b"})
        new, inherited, fixed = diff_by_node_id(ids, ids)

        assert len(new) == 0
        assert inherited == ids
        assert len(fixed) == 0

    def test_empty_baseline(self) -> None:
        """Empty baseline: everything current is new."""
        current = frozenset({"a", "b"})
        new, inherited, fixed = diff_by_node_id(current, frozenset())

        assert new == current
        assert len(inherited) == 0
        assert len(fixed) == 0

    def test_empty_current(self) -> None:
        """Empty current: everything baseline is fixed."""
        baseline = frozenset({"a", "b"})
        new, inherited, fixed = diff_by_node_id(frozenset(), baseline)

        assert len(new) == 0
        assert len(inherited) == 0
        assert fixed == baseline

    def test_full_compare_same_count_fixture(self) -> None:
        """Full compare() with same count, different ids — AC-1 end-to-end."""
        from baseline_diff import BaselineRef

        baseline = frozenset({"a::1", "a::2", "a::3"})
        current = frozenset({"b::1", "b::2", "b::3"})
        ref = BaselineRef(tag="v1.0", resolved=True, status=BaselineStatus.FOUND)

        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
        )

        assert diff.regression_count == 3
        assert diff.current_count == 3
        assert diff.baseline_count == 3
        assert diff.new_failures == current


# ── AC-2: ref resolution ────────────────────────────────────────────────────

class TestRefResolution:
    """AC-2: the ref is resolved (git describe --tags --abbrev=0), never assumed."""

    def test_resolve_last_tag_returns_string(self) -> None:
        """In a repo with tags, returns a non-None string."""
        tag = resolve_last_tag()
        # This repo has tags (v0.9.66 at time of writing)
        assert tag is not None
        assert tag.startswith("v")

    def test_make_ref_resolved(self) -> None:
        """A resolved tag gets FOUND status."""
        ref = make_ref("v0.9.66")
        assert ref.tag == "v0.9.66"
        assert ref.resolved is True
        assert ref.status == BaselineStatus.FOUND

    def test_make_ref_none(self) -> None:
        """None tag (no tags in repo) gets COULD_NOT_COMPARE."""
        ref = make_ref(None)
        assert ref.tag == "<no-tag>"
        assert ref.resolved is False
        assert ref.status == BaselineStatus.COULD_NOT_COMPARE


# ── AC-3: missing baseline ≠ zero ───────────────────────────────────────────

class TestMissingBaseline:
    """AC-3: missing baseline is 'could not compare', not 'zero regressions'."""

    def test_missing_baseline_could_not_compare(self) -> None:
        """When baseline_failures is None, result is could_not_compare."""
        from baseline_diff import BaselineRef

        ref = BaselineRef(tag="v9.9.9", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=frozenset({"a"}),
            search_space=50,
            filtered=False,
            baseline_failures=None,  # no stored baseline
            baseline_search_space=0,
            ref=ref,
        )

        assert diff.could_not_compare is True
        assert diff.regression_count == 0
        # The ref status is updated to COULD_NOT_COMPARE
        assert diff.ref.status == BaselineStatus.COULD_NOT_COMPARE

    def test_missing_baseline_distinct_from_zero(self) -> None:
        """Zero regressions (found baseline, no new failures) is NOT could_not_compare."""
        from baseline_diff import BaselineRef

        ref = BaselineRef(tag="v1.0", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=frozenset({"a"}),
            search_space=50,
            filtered=False,
            baseline_failures=frozenset({"a"}),  # found, same failures
            baseline_search_space=50,
            ref=ref,
        )

        assert diff.could_not_compare is False
        assert diff.regression_count == 0
        assert diff.ref.status == BaselineStatus.FOUND

    def test_missing_baseline_in_full_pipeline(self) -> None:
        """run_baseline_diff with no stored baseline returns could_not_compare."""
        report = run_baseline_diff(
            current_failures=frozenset({"a"}),
            search_space=50,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=Path("/nonexistent"),
            tag_override="v99.99.99",  # no stored baseline for this
        )

        assert report.diff.could_not_compare is True
        assert "could not compare" in report.denominator_statement


# ── AC-7: denominator statement ─────────────────────────────────────────────

class TestDenominator:
    """AC-7: every negative carries its denominator."""

    def test_zero_regressions_with_space(self) -> None:
        """0 regressions across N collected tests vs vX.Y.Z."""
        from baseline_diff import BaselineRef

        ref = BaselineRef(tag="v1.0", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=frozenset(),
            search_space=698,
            filtered=False,
            baseline_failures=frozenset(),
            baseline_search_space=698,
            ref=ref,
        )
        stmt = format_denominator(diff, filtered=False)

        assert "0 regressions" in stmt
        assert "698" in stmt
        assert "v1.0" in stmt

    def test_filtered_run_says_not_fully_searched(self) -> None:
        """A filtered run must say the suite was not fully searched."""
        from baseline_diff import BaselineRef

        ref = BaselineRef(tag="v1.0", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=frozenset(),
            search_space=42,
            filtered=True,
            baseline_failures=frozenset(),
            baseline_search_space=42,
            ref=ref,
        )
        stmt = format_denominator(diff, filtered=True)

        assert "not fully searched" in stmt
        assert "42" in stmt

    def test_could_not_compare_statement(self) -> None:
        """Could not compare names the tag."""
        from baseline_diff import BaselineRef

        ref = BaselineRef(tag="v9.9", resolved=True, status=BaselineStatus.COULD_NOT_COMPARE)
        diff = compare(
            current_failures=frozenset(),
            search_space=0,
            filtered=False,
            baseline_failures=None,
            baseline_search_space=0,
            ref=ref,
        )
        stmt = format_denominator(diff, filtered=False)

        assert "could not compare" in stmt
        assert "v9.9" in stmt


# ── AC-8: stale exclusions ──────────────────────────────────────────────────

class TestStaleExclusions:
    """AC-8: baseline_red entries that no longer fail are reported as stale."""

    def test_stale_entry_detected(self) -> None:
        """An entry in baseline_red that is NOT in current failures is stale."""
        entries = [
            {"node_id": "tests/test_old.py::test_x", "reason": "was broken", "as_of": "2026-08-14"},
            {"node_id": "tests/test_still.py::test_y", "reason": "still broken", "as_of": "2026-08-14"},
        ]
        current = frozenset({"tests/test_still.py::test_y"})  # only test_y still fails

        stale = check_stale_exclusions(entries, current)

        assert len(stale) == 1
        assert stale[0].node_id == "tests/test_old.py::test_x"
        assert stale[0].reason == "was broken"

    def test_no_stale_when_all_still_fail(self) -> None:
        """No stale entries when all baseline_red entries still fail."""
        entries = [{"node_id": "a", "reason": "", "as_of": ""}]
        current = frozenset({"a"})

        stale = check_stale_exclusions(entries, current)
        assert len(stale) == 0

    def test_empty_baseline_red(self) -> None:
        """Empty baseline_red list → no stale entries."""
        stale = check_stale_exclusions([], frozenset({"a"}))
        assert len(stale) == 0


# ── AC-4: collection floor ──────────────────────────────────────────────────

class TestCollectionFloor:
    """AC-4: a collection error is a distinct, loud outcome that voids every other result."""

    def test_parse_no_errors(self) -> None:
        """Clean --collect-only output: no errors."""
        output = "collected 100 items\n"
        floor = parse_collection_output(output)

        assert floor.has_errors is False
        assert floor.voids_run is False
        assert floor.collected_count == 100

    def test_parse_collection_error(self) -> None:
        """ERROR collecting line detected."""
        output = (
            "collected 0 items / 1 error\n"
            "========================= ERRORS =========================\n"
            "_ ERROR collecting tests/test_bad.py _\n"
            "tests/test_bad.py:20: in <module>\n"
            "    def f(x: list[str] | None) -> str:\n"
            "E   TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'\n"
            "Interrupted: 1 error during collection\n"
        )
        floor = parse_collection_output(output)

        assert floor.has_errors is True
        assert floor.voids_run is True
        assert len(floor.errors) == 1
        assert "test_bad.py" in floor.errors[0].file_path
        assert floor.errors[0].error_type == "TypeError"

    def test_parse_multiple_errors(self) -> None:
        """Multiple collection errors all detected."""
        output = (
            "_ ERROR collecting tests/a.py _\n"
            "E   ImportError: No module named 'foo'\n"
            "_ ERROR collecting tests/b.py _\n"
            "E   SyntaxError: invalid syntax\n"
        )
        floor = parse_collection_output(output)

        assert floor.has_errors is True
        assert len(floor.errors) == 2

    def test_voids_run_property(self) -> None:
        """AC-4: voids_run is True when any error exists."""
        floor = CollectionFloor(
            errors=(CollectionError(
                file_path="x.py", error_type="TypeError", message="boom"
            ),),
            collected_count=0,
            has_errors=True,
        )
        assert floor.voids_run is True

    def test_real_fixture_causes_collection_error(self) -> None:
        """AC-4: the committed fixture actually fails to collect.

        This is a positive experiment — if the fixture is ever fixed
        (e.g. by adding `from __future__ import annotations`), this test
        will fail, which is exactly the signal we want.
        """
        fixture_dir = Path(__file__).resolve().parent / "fixtures" / "collection_error"
        if not fixture_dir.exists():
            pytest.skip("fixture directory not found")

        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(fixture_dir), "--collect-only"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + "\n" + result.stderr
        floor = parse_collection_output(output)

        assert floor.has_errors is True, (
            f"Fixture should cause collection error, got: {output}"
        )

    def test_collection_floor_in_report(self, tmp_path: Path) -> None:
        """Collection floor appears in the full report."""
        store_baseline(tmp_path, "v1.0", "pytest -q", frozenset(), 100)

        coll = CollectionFloor(
            errors=(),
            collected_count=100,
            has_errors=False,
        )

        report = run_baseline_diff(
            current_failures=frozenset(),
            search_space=100,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=tmp_path,
            tag_override="v1.0",
            collection_floor=coll,
        )

        assert report.collection_floor is not None
        assert report.collection_floor.has_errors is False
        d = report.to_dict()
        assert "collection_floor" in d

    def test_collection_floor_voids_report(self, tmp_path: Path) -> None:
        """Collection error in report: voids_run is True."""
        store_baseline(tmp_path, "v1.0", "pytest -q", frozenset(), 100)

        coll = CollectionFloor(
            errors=(CollectionError(
                file_path="test_bad.py", error_type="TypeError", message="boom"
            ),),
            collected_count=0,
            has_errors=True,
        )

        report = run_baseline_diff(
            current_failures=frozenset(),
            search_space=0,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=tmp_path,
            tag_override="v1.0",
            collection_floor=coll,
        )

        assert report.collection_floor is not None
        assert report.collection_floor.voids_run is True


# ── AC-6: timeout → inconclusive ─────────────────────────────────────────────

class TestRunVerdict:
    """AC-6: a timeout names its bound — never pass, never fail."""

    def test_pass_verdict(self) -> None:
        """Exit 0 → pass."""
        v, output = run_with_timeout("true")
        assert v.verdict == Verdict.PASS
        assert v.bound_seconds is None

    def test_fail_verdict(self) -> None:
        """Exit 1 → fail."""
        v, output = run_with_timeout("false")
        assert v.verdict == Verdict.FAIL

    def test_timeout_verdict_names_bound(self) -> None:
        """AC-6: a timeout → inconclusive, naming the bound.

        A timeout is a fact about the bound, not about the code.
        """
        v, output = run_with_timeout("sleep 10", timeout=1)
        assert v.verdict == Verdict.INCONCLUSIVE
        assert v.bound_seconds == 1
        assert "1s" in v.message

    def test_inconclusive_is_not_pass_or_fail(self) -> None:
        """AC-6: inconclusive is distinct from pass and fail."""
        v, _ = run_with_timeout("sleep 10", timeout=1)
        assert v.is_inconclusive is True
        assert v.verdict != Verdict.PASS
        assert v.verdict != Verdict.FAIL

    def test_verdict_to_dict(self) -> None:
        """Verdict serializes cleanly."""
        v = RunVerdict(verdict=Verdict.INCONCLUSIVE, bound_seconds=120, message="hit 120s")
        d = v.to_dict()
        assert d["verdict"] == "inconclusive"
        assert d["bound_seconds"] == 120
        assert "120s" in d["message"]

    def test_exit_124_is_inconclusive(self) -> None:
        """Exit 124 (gtimeout's kill code) → inconclusive."""
        # Use a shell command that exits 124
        v, _ = run_with_timeout("exit 124")
        assert v.verdict == Verdict.INCONCLUSIVE
        assert v.bound_seconds is not None

    def test_error_command_not_found(self) -> None:
        """Command not found → error, not fail.

        Exit 127 = command not found, 126 = permission denied.
        """
        v, _ = run_with_timeout("nonexistent_command_xyz_42")
        assert v.verdict == Verdict.ERROR
        assert "not found" in v.message


# ── AC-9: baseline keying ───────────────────────────────────────────────────

class TestBaselineKeying:
    """AC-9: baselines keyed by (tag, suite-invocation flags)."""

    def test_different_flags_different_keys(self) -> None:
        """--timeout-method=thread vs signal must produce different keys."""
        key_thread = baseline_key("v1.0", "pytest --timeout-method=thread")
        key_signal = baseline_key("v1.0", "pytest --timeout-method=signal")

        assert key_thread != key_signal

    def test_same_flags_same_key(self) -> None:
        """Same tag + same invocation → same key."""
        k1 = baseline_key("v1.0", "pytest -q")
        k2 = baseline_key("v1.0", "pytest -q")

        assert k1 == k2

    def test_different_tags_different_keys(self) -> None:
        """Different tags → different keys even with same flags."""
        k1 = baseline_key("v1.0", "pytest -q")
        k2 = baseline_key("v2.0", "pytest -q")

        assert k1 != k2


# ── Baseline storage round-trip ─────────────────────────────────────────────

class TestBaselineStorage:
    """Store and load a baseline — round-trip fidelity."""

    def test_store_and_load(self, tmp_path: Path) -> None:
        """Store a baseline, load it back, verify fidelity."""
        node_ids = frozenset({"a::1", "b::2"})
        store_baseline(tmp_path, "v1.0", "pytest -q", node_ids, 100)

        loaded = load_baseline(tmp_path, "v1.0", "pytest -q")
        assert loaded is not None
        assert loaded[0] == node_ids
        assert loaded[1] == 100

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        """Loading a non-existent baseline returns None."""
        loaded = load_baseline(tmp_path, "v99.0", "pytest -q")
        assert loaded is None

    def test_different_invocation_different_file(self, tmp_path: Path) -> None:
        """Same tag, different invocation → different stored file."""
        store_baseline(tmp_path, "v1.0", "pytest --thread", frozenset({"a"}), 50)
        store_baseline(tmp_path, "v1.0", "pytest --signal", frozenset({"a"}), 50)

        loaded_thread = load_baseline(tmp_path, "v1.0", "pytest --thread")
        loaded_signal = load_baseline(tmp_path, "v1.0", "pytest --signal")

        assert loaded_thread is not None
        assert loaded_signal is not None
        # Both stored successfully with same node_ids
        assert loaded_thread[0] == loaded_signal[0] == frozenset({"a"})


# ── Full pipeline ────────────────────────────────────────────────────────────

class TestFullPipeline:
    """run_baseline_diff end-to-end."""

    def test_with_stored_baseline(self, tmp_path: Path) -> None:
        """Full pipeline with a stored baseline: detect new + inherited + fixed."""
        # Store baseline: a, b fail
        store_baseline(tmp_path, "v1.0", "pytest -q", frozenset({"a", "b"}), 50)

        # Current: b, c fail (b inherited, c new, a fixed)
        report = run_baseline_diff(
            current_failures=frozenset({"b", "c"}),
            search_space=50,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=tmp_path,
            tag_override="v1.0",
        )

        assert report.diff.regression_count == 1
        assert report.diff.new_failures == frozenset({"c"})
        assert report.diff.inherited_failures == frozenset({"b"})
        assert report.diff.fixed == frozenset({"a"})
        assert report.diff.ref.tag == "v1.0"
        assert "1 regressions" in report.denominator_statement

    def test_same_count_different_ids_e2e(self, tmp_path: Path) -> None:
        """AC-1 end-to-end: same count, different ids → regressions detected."""
        store_baseline(
            tmp_path, "v1.0", "pytest -q",
            frozenset({"test_a::x", "test_a::y", "test_a::z"}),
            100,
        )

        report = run_baseline_diff(
            current_failures=frozenset({"test_b::x", "test_b::y", "test_b::z"}),
            search_space=100,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=tmp_path,
            tag_override="v1.0",
        )

        # Counts are both 3, but ids are completely different
        assert report.diff.current_count == 3
        assert report.diff.baseline_count == 3
        assert report.diff.regression_count == 3
        assert report.diff.new_failures == frozenset({"test_b::x", "test_b::y", "test_b::z"})

    def test_stale_exclusions_e2e(self, tmp_path: Path) -> None:
        """AC-8 end-to-end: stale baseline_red entries reported."""
        store_baseline(tmp_path, "v1.0", "pytest -q", frozenset({"a", "b"}), 50)

        baseline_red = [
            {"node_id": "a", "reason": "known broken", "as_of": "2026-08-14"},
            {"node_id": "old_thing", "reason": "was broken", "as_of": "2026-08-14"},
        ]

        report = run_baseline_diff(
            current_failures=frozenset({"a", "b"}),
            search_space=50,
            filtered=False,
            suite_invocation="pytest -q",
            baseline_red_entries=baseline_red,
            project_root=tmp_path,
            tag_override="v1.0",
        )

        assert len(report.stale_exclusions) == 1
        assert report.stale_exclusions[0].node_id == "old_thing"

    def test_to_dict_json_serializable(self, tmp_path: Path) -> None:
        """Report.to_dict() is JSON-serializable."""
        store_baseline(tmp_path, "v1.0", "pytest -q", frozenset(), 100)

        report = run_baseline_diff(
            current_failures=frozenset(),
            search_space=100,
            filtered=False,
            suite_invocation="pytest -q",
            project_root=tmp_path,
            tag_override="v1.0",
        )

        # Must not raise
        serialized = json.dumps(report.to_dict())
        assert isinstance(serialized, str)
