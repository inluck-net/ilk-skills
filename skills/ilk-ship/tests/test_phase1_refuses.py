"""Tests for Phase 1 refusal on unavailable engines.

Sub-plan: phase-1-refuses-rather-than-substitutes, step 0.
Red-first: these tests pin the refusal behaviour before it exists.

Each test builds records in a tmp dir — never reads the operator's real
``~/.ilk-data``.  Uses ``scheduler_sandbox`` where a data home is needed.

The five refusal conditions:
  1. verdict absent      — no batch-gate.json on disk
  2. verdict stale_head  — record sha ≠ current HEAD
  3. verdict stale_invocation — record invocation ≠ ship.suite
  4. verdict fail        — the gate recorded a failing verdict
  5. baseline could_not_compare — no stored baseline for the last tag

Plus the specific trap:
  A ``could_not_compare`` payload that also carries ``regression_count: 0``
  must NOT be read as zero regressions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from batch_gate import (
    BatchGateRecord,
    validate_record,
    validate_record_detail,
    write_record,
)
from baseline_diff import (
    BaselineRef,
    BaselineReport,
    BaselineStatus,
    NodeIdDiff,
    compare,
    run_baseline_diff,
    store_baseline,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_verdict(
    verdict: str = "pass",
    head_sha: str = "abc1234" + "0" * 33,
    invocation: str = "python3 -m pytest",
    timestamp: str = "2026-09-08T00:00:00+08:00",
) -> BatchGateRecord:
    """Build a BatchGateRecord with sensible defaults."""
    return BatchGateRecord(
        verdict=verdict,
        head_sha=head_sha,
        invocation=invocation,
        timestamp=timestamp,
    )


def _write_verdict(runtime_dir: Path, record: BatchGateRecord) -> Path:
    """Write a batch-gate record to disk."""
    return write_record(record, runtime_dir)


# ── Refusal predicate under test ────────────────────────────────────────────

# This import will fail until step 1 implements it.  The tests are written
# first (red-first) so they pin the expected behaviour.
try:
    from phase1_verify import Phase1Verdict, verify_phase1
except ImportError:
    # Step 0: the module does not exist yet.  Define a stub that always
    # returns "proceed" so the tests fail on every assertion — this is the
    # intended red state.
    from dataclasses import dataclass
    from typing import Optional

    @dataclass(frozen=True)
    class Phase1Verdict:
        """Result of Phase 1 verification."""
        action: str  # "proceed" | "refuse"
        reason: str
        engine: str  # "batch_verdict" | "baseline" | ""
        filed: bool  # True if a refusal artifact was filed

    def verify_phase1(
        runtime_dir: Path,
        expected_head_sha: str,
        expected_invocation: str,
        baseline_report: Optional[BaselineReport] = None,
    ) -> Phase1Verdict:
        """Stub: always proceeds.  Step 1 will implement refusal."""
        return Phase1Verdict(action="proceed", reason="", engine="", filed=False)


# ── Refusal on batch-verdict engine ─────────────────────────────────────────

class TestVerdictAbsent:
    """Refuse when no batch-gate.json exists on disk."""

    def test_refuses_on_absent_verdict(self, tmp_path: Path) -> None:
        """Verdict absent → refuse with engine=batch_verdict."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.action == "refuse", (
            f"expected refuse on absent verdict, got {result.action}"
        )
        assert result.engine == "batch_verdict"

    def test_refusal_filed_on_absent(self, tmp_path: Path) -> None:
        """Refusal on absent verdict must file an artifact."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.filed is True, (
            "a refusal that only prints is not a refusal an unattended "
            "pipeline can act on"
        )


class TestVerdictStaleHead:
    """Refuse when the batch-gate record's head_sha differs from HEAD."""

    def test_refuses_on_stale_head(self, tmp_path: Path) -> None:
        """head_sha mismatch → refuse."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict(
            head_sha="deadbee" + "0" * 33,
        ))

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.action == "refuse"
        assert result.engine == "batch_verdict"
        assert "stale" in result.reason.lower() or "head" in result.reason.lower()

    def test_detail_names_both_shas(self, tmp_path: Path) -> None:
        """Refusal reason names both the record sha and current HEAD."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        record_sha = "deadbee" + "0" * 33
        current_sha = "abc1234" + "0" * 33
        _write_verdict(runtime, _make_verdict(head_sha=record_sha))

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha=current_sha,
            expected_invocation="python3 -m pytest",
        )

        # The reason must carry both sides so a reader can see what changed
        assert record_sha[:7] in result.reason or "deadbee" in result.reason
        assert current_sha[:7] in result.reason or "abc1234" in result.reason


class TestVerdictStaleInvocation:
    """Refuse when the batch-gate record's invocation differs from ship.suite."""

    def test_refuses_on_stale_invocation(self, tmp_path: Path) -> None:
        """invocation mismatch → refuse."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict(
            invocation="python3 -m pytest -n 8 --dist loadfile",
        ))

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest --timeout-method=signal",
        )

        assert result.action == "refuse"
        assert result.engine == "batch_verdict"
        assert "invocation" in result.reason.lower() or "stale" in result.reason.lower()


class TestVerdictFail:
    """Refuse when the batch-gate recorded a failing verdict."""

    def test_refuses_on_fail(self, tmp_path: Path) -> None:
        """verdict=fail → refuse."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict(verdict="fail"))

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.action == "refuse"
        assert result.engine == "batch_verdict"
        assert "fail" in result.reason.lower()

    def test_refuses_on_error(self, tmp_path: Path) -> None:
        """verdict=error → refuse (gate code error)."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict(verdict="error"))

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.action == "refuse"
        assert result.engine == "batch_verdict"


# ── Refusal on baseline-diff engine ─────────────────────────────────────────

class TestBaselineCouldNotCompare:
    """Refuse when baseline_diff returns could_not_compare."""

    def test_refuses_on_could_not_compare(self, tmp_path: Path) -> None:
        """Baseline could_not_compare → refuse with engine=baseline."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        # Build a baseline report with could_not_compare
        ref = BaselineRef(
            tag="v0.9.86",
            resolved=True,
            status=BaselineStatus.COULD_NOT_COMPARE,
        )
        diff = NodeIdDiff(
            ref=ref,
            new_failures=frozenset(),
            inherited_failures=frozenset(),
            fixed=frozenset(),
            current_count=0,
            baseline_count=0,
            search_space=0,
            filtered=False,
        )
        report = BaselineReport(
            diff=diff,
            stale_exclusions=(),
            denominator_statement="could not compare — no baseline for v0.9.86",
        )

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
            baseline_report=report,
        )

        assert result.action == "refuse"
        assert result.engine == "baseline"
        assert "could_not_compare" in result.reason or "could not compare" in result.reason.lower()


# ── The specific trap ───────────────────────────────────────────────────────

class TestCouldNotCompareRegressionCountTrap:
    """A could_not_compare payload with regression_count: 0 must NOT be read
    as zero regressions.

    This is the exact defect that occurred: baseline_diff's payload carries
    ``regression_count: 0`` even when ``could_not_compare`` is true.  That
    field is meaningless in that state and is exactly the number an
    unattended caller would read as success.
    """

    def test_could_not_compare_with_zero_regression_count_refuses(
        self, tmp_path: Path,
    ) -> None:
        """The trap: regression_count=0 + could_not_compare → refuse, not proceed."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        ref = BaselineRef(
            tag="v0.9.86",
            resolved=True,
            status=BaselineStatus.COULD_NOT_COMPARE,
        )
        diff = NodeIdDiff(
            ref=ref,
            new_failures=frozenset(),
            inherited_failures=frozenset(),
            fixed=frozenset(),
            current_count=0,
            baseline_count=0,
            search_space=0,
            filtered=False,
        )
        # The trap: regression_count is 0, but could_not_compare is True
        assert diff.regression_count == 0
        assert diff.could_not_compare is True

        report = BaselineReport(
            diff=diff,
            stale_exclusions=(),
            denominator_statement="could not compare — no baseline for v0.9.86",
        )

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
            baseline_report=report,
        )

        assert result.action == "refuse", (
            "A could_not_compare with regression_count=0 must refuse. "
            "Reading regression_count as zero regressions is the exact "
            "defect this sub-plan exists to prevent."
        )

    def test_could_not_compare_never_reads_regression_count(self) -> None:
        """The could_not_compare state makes regression_count meaningless.

        This test documents the invariant: a caller that reads
        ``diff.regression_count == 0`` as "no regressions" when
        ``diff.could_not_compare`` is True is making the same mistake
        that shipped v0.9.86 and v0.9.87 on substitute evidence.
        """
        ref = BaselineRef(
            tag="v0.9.86",
            resolved=True,
            status=BaselineStatus.COULD_NOT_COMPARE,
        )
        diff = NodeIdDiff(
            ref=ref,
            new_failures=frozenset(),
            inherited_failures=frozenset(),
            fixed=frozenset(),
            current_count=0,
            baseline_count=0,
            search_space=0,
            filtered=False,
        )

        # The invariant: could_not_compare is True regardless of regression_count
        assert diff.could_not_compare is True
        assert diff.regression_count == 0

        # A correct caller checks could_not_compare FIRST, not regression_count.
        # This test will pass from step 0 (it's a property assertion); the
        # behavioural test above is the one that fails until step 1.


# ── Fresh verdict + found baseline → proceed ────────────────────────────────

class TestFreshVerdictProceeds:
    """When both engines are available, Phase 1 proceeds."""

    def test_proceeds_on_fresh_verdict_and_found_baseline(
        self, tmp_path: Path,
    ) -> None:
        """Fresh verdict + found baseline → proceed."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        ref = BaselineRef(
            tag="v0.9.86",
            resolved=True,
            status=BaselineStatus.FOUND,
        )
        diff = NodeIdDiff(
            ref=ref,
            new_failures=frozenset(),
            inherited_failures=frozenset(),
            fixed=frozenset(),
            current_count=0,
            baseline_count=0,
            search_space=1846,
            filtered=False,
        )
        report = BaselineReport(
            diff=diff,
            stale_exclusions=(),
            denominator_statement="0 regressions across 1846 collected tests vs v0.9.86",
        )

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
            baseline_report=report,
        )

        assert result.action == "proceed"
        assert result.reason == "" or "fresh" in result.reason.lower()


class TestAbsentBaselineReportIsNotAPass:
    """A baseline engine that was never RUN must not read as one that passed.

    Engine 2 was guarded by ``if baseline_report is not None and
    baseline_report.diff.could_not_compare``, so a caller supplying no report
    at all reached the same ``proceed`` as one whose baseline compared clean.
    Two different states -- "compared, no regressions" and "never compared" --
    with one observable outcome.

    Found 2026-09-09 while running /ilk-ship on this repo: the first
    verification returned ``proceed`` having only run engine 1, and would have
    been reported as a verified Phase 1. That is the v0.9.86 / v0.9.87 failure
    this module exists to prevent, reproduced inside the module itself.

    The fix is fail-closed, matching the batch-verdict engine directly above
    it: no report supplied ⇒ the baseline engine did not run ⇒ refuse and file.
    """

    def test_none_baseline_report_refuses(self, tmp_path: Path) -> None:
        """No baseline report ⇒ refuse, naming the baseline engine."""
        from phase1_verify import verify_phase1

        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
            # No baseline_report: the engine never ran.
        )

        assert result.action == "refuse", (
            "a Phase 1 that never ran the baseline engine must not report "
            f"proceed -- that is a half-run phase claiming a whole one; got "
            f"{result.action!r} (reason: {result.reason!r})"
        )
        assert result.engine == "baseline", (
            f"the refusal must name the engine that did not run; got {result.engine!r}"
        )

    def test_none_baseline_report_files_an_artifact(self, tmp_path: Path) -> None:
        """The refusal is filed, so an unattended pipeline has a file to act on."""
        from phase1_verify import verify_phase1

        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="abc1234" + "0" * 33,
            expected_invocation="python3 -m pytest",
        )

        assert result.filed is True, "refusal must be filed like every other refusal"
        assert (runtime / "phase1-refusal.json").is_file(), (
            "phase1-refusal.json must exist so the pipeline has an artifact"
        )

    def test_batch_verdict_refusal_still_takes_precedence(
        self, tmp_path: Path,
    ) -> None:
        """Engine order is unchanged: a stale verdict still reports engine 1.

        Guards against the fix inverting the order and masking the more
        specific failure behind the new one.
        """
        from phase1_verify import verify_phase1

        runtime = tmp_path / "runtime"
        runtime.mkdir()
        _write_verdict(runtime, _make_verdict())

        result = verify_phase1(
            runtime_dir=runtime,
            expected_head_sha="different" + "0" * 31,
            expected_invocation="python3 -m pytest",
        )

        assert result.action == "refuse"
        assert result.engine == "batch_verdict", (
            "a stale batch verdict must still be reported as the batch_verdict "
            f"engine, not masked by the absent baseline; got {result.engine!r}"
        )
