"""Tests for suite_timing — outcome-set equality gates wall-clock.

Sub-plan: suite-timing-measures-before-it-parallelises, step 0.
Red-first: these tests pin the comparator behaviour before it exists.

Modelled on real numbers from gate-timing-2026-08-27.md:
  serial  263.31s  clean (0 flips)
  -n 2    616.66s  5 flips (PASSED→FAILED)
  -n 4    430.52s  5 flips
  -n auto 404.12s  4 flips

The rule: outcome-set equality gates wall-clock.  A config that flips any
test is disqualified regardless of speed.  Only among configs with identical
outcome sets does wall-clock decide.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SHIP_SCRIPTS))


# ── Domain types (injected from suite_timing.py when it exists) ─────────────

# These stubs mirror what suite_timing.py will export.  Step 0 writes tests
# against them; step 1 implements the real module.  If the import succeeds,
# we use the real types; otherwise we define stubs that always fail.

try:
    from suite_timing import (
        ConfigResult,
        RunOutcome,
        compare_configs,
        recommend,
    )
except ImportError:
    # Step 0: the module does not exist yet.  Define stubs so every test
    # fails on assertion — this is the intended red state.

    @dataclass(frozen=True)
    class RunOutcome:
        """Result of a single test node under a configuration."""
        node_id: str
        passed: bool  # True=PASSED, False=FAILED/SKIPPED/ERROR

    @dataclass(frozen=True)
    class ConfigResult:
        """Result of running a suite under one configuration."""
        name: str              # e.g. "serial", "-n 4"
        wall_clock: float      # seconds
        outcomes: frozenset[tuple[str, bool]]  # (node_id, passed) pairs
        invocations: tuple[str, ...]  # the pytest invocations used

        @classmethod
        def from_outcomes(
            cls,
            name: str,
            wall_clock: float,
            outcomes: Sequence[RunOutcome],
            invocations: Sequence[str] = (),
        ) -> "ConfigResult":
            return cls(
                name=name,
                wall_clock=wall_clock,
                outcomes=frozenset((o.node_id, o.passed) for o in outcomes),
                invocations=tuple(invocations),
            )

    @dataclass(frozen=True)
    class Comparison:
        """Result of comparing two configurations."""
        faster: str
        slower: str
        faster_wall_clock: float
        slower_wall_clock: float
        outcome_sets_equal: bool
        flips: frozenset[str]  # node-ids that differ in pass/fail

    def compare_configs(a: ConfigResult, b: ConfigResult) -> Comparison:
        """Stub: always returns equal outcomes.  Step 1 will implement."""
        return Comparison(
            faster=a.name if a.wall_clock <= b.wall_clock else b.name,
            slower=b.name if a.wall_clock <= b.wall_clock else a.name,
            faster_wall_clock=min(a.wall_clock, b.wall_clock),
            slower_wall_clock=max(a.wall_clock, b.wall_clock),
            outcome_sets_equal=True,
            flips=frozenset(),
        )

    def recommend(configs: Sequence[ConfigResult]) -> ConfigResult:
        """Stub: always returns the fastest.  Step 1 will disqualify flips."""
        return min(configs, key=lambda c: c.wall_clock)


# ── Fixtures: real-shaped data ──────────────────────────────────────────────

# 2423 node ids, but we model only the ones that matter — the 5 that flip.
# The rest are identical across all configs.

_SERIAL_OUTCOMES = [
    RunOutcome("tests/test_alpha.py::test_one", True),
    RunOutcome("tests/test_alpha.py::test_two", True),
    RunOutcome("tests/test_alpha.py::test_three", True),
    RunOutcome("skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox", True),
    RunOutcome("skills/ilk-loop/tests/test_plan_lint_e2e_env_prereq.py::test_existing_plan_lint_tests_still_pass", True),
    RunOutcome("skills/ilk-loop/tests/test_plan_lint_escaped_bug.py::test_existing_plan_lint_tests_still_pass", True),
    RunOutcome("skills/ilk-loop/tests/test_plan_lint_frontmatter_path.py::test_existing_plan_lint_tests_still_pass", True),
    RunOutcome("skills/ilk-loop/tests/test_plan_lint_one_branch.py::TestSupervisedOnlyUnaffected::test_supervised_only_tests_still_pass", True),
]

# The 5 tests that flip under xdist (PASSED → FAILED).
_FLIPPED_NODE_IDS = {
    "skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox",
    "skills/ilk-loop/tests/test_plan_lint_e2e_env_prereq.py::test_existing_plan_lint_tests_still_pass",
    "skills/ilk-loop/tests/test_plan_lint_escaped_bug.py::test_existing_plan_lint_tests_still_pass",
    "skills/ilk-loop/tests/test_plan_lint_frontmatter_path.py::test_existing_plan_lint_tests_still_pass",
    "skills/ilk-loop/tests/test_plan_lint_one_branch.py::TestSupervisedOnlyUnaffected::test_supervised_only_tests_still_pass",
}


def _make_serial_outcomes() -> list[RunOutcome]:
    """Serial run: all pass."""
    return list(_SERIAL_OUTCOMES)


def _make_parallel_outcomes(flips: set[str]) -> list[RunOutcome]:
    """Parallel run: some tests flip from PASSED to FAILED."""
    outcomes = []
    for o in _SERIAL_OUTCOMES:
        if o.node_id in flips:
            outcomes.append(RunOutcome(o.node_id, False))
        else:
            outcomes.append(o)
    return outcomes


# ── Decisive test 1: faster but flips → disqualified ────────────────────────

class TestFasterButFlipsIsDisqualified:
    """A config that is faster but flips a test must be disqualified.

    Modelled on the real data: imagine a hypothetical "-n fast" that runs in
    200s but flips the same 5 tests.  It must NOT be recommended over serial.
    """

    def test_faster_with_flips_not_recommended(self) -> None:
        """The decisive test from the sub-plan."""
        serial = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal"],
        )
        # Hypothetical fast-but-broken config
        fast_parallel = ConfigResult.from_outcomes(
            name="-n fast",
            wall_clock=200.0,  # faster!
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal -n 4"],
        )

        result = recommend([serial, fast_parallel])

        assert result.config.name == "serial", (
            f"serial must be recommended over a faster config that flips tests, "
            f"got {result.config.name}"
        )

    def test_slower_with_flips_also_disqualified(self) -> None:
        """Real numbers: -n 4 is slower AND flips tests → disqualified."""
        serial = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal"],
        )
        n4 = ConfigResult.from_outcomes(
            name="-n 4",
            wall_clock=430.52,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal -n 4"],
        )

        result = recommend([serial, n4])

        assert result.config.name == "serial", (
            f"serial must be recommended over -n 4 (slower + flips), got {result.config.name}"
        )


# ── Decisive test 2: identical outcome sets → fastest wins ──────────────────

class TestIdenticalOutcomesFasterWins:
    """Among configs with identical outcome sets, the fastest wins."""

    def test_identical_outcomes_faster_wins(self) -> None:
        """Two configs with same outcomes → faster one recommended."""
        config_a = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal"],
        )
        # Same outcomes, slower
        config_b = ConfigResult.from_outcomes(
            name="-n 1",
            wall_clock=280.0,
            outcomes=_make_serial_outcomes(),
            invocations=["python3 -m pytest --timeout=60 --timeout-method=signal -n 1"],
        )

        result = recommend([config_a, config_b])

        assert result.config.name == "serial", (
            f"among identical outcome sets, fastest must win, got {result.config.name}"
        )

    def test_three_identical_outcomes_fastest_wins(self) -> None:
        """Three configs, all same outcomes → fastest of the three."""
        slow = ConfigResult.from_outcomes(
            name="slow",
            wall_clock=300.0,
            outcomes=_make_serial_outcomes(),
        )
        medium = ConfigResult.from_outcomes(
            name="medium",
            wall_clock=270.0,
            outcomes=_make_serial_outcomes(),
        )
        fast = ConfigResult.from_outcomes(
            name="fast",
            wall_clock=250.0,
            outcomes=_make_serial_outcomes(),
        )

        result = recommend([slow, medium, fast])

        assert result.config.name == "fast", (
            f"fastest of three identical-outcome configs must win, got {result.config.name}"
        )


# ── Outcome-set comparison ──────────────────────────────────────────────────

class TestOutcomeSetEquality:
    """Outcome-set comparison is the primary gate."""

    def test_identical_outcomes_detected(self) -> None:
        """Two configs with identical outcome sets are equal."""
        a = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
        )
        b = ConfigResult.from_outcomes(
            name="-n 1",
            wall_clock=280.0,
            outcomes=_make_serial_outcomes(),
        )

        comp = compare_configs(a, b)

        assert comp.outcome_sets_equal is True
        assert len(comp.flips) == 0

    def test_flips_detected(self) -> None:
        """Configs with different outcomes report the differing node-ids."""
        serial = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
        )
        n4 = ConfigResult.from_outcomes(
            name="-n 4",
            wall_clock=430.52,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
        )

        comp = compare_configs(serial, n4)

        assert comp.outcome_sets_equal is False
        assert comp.flips == _FLIPPED_NODE_IDS

    def test_subset_flips_detected(self) -> None:
        """A config that flips fewer tests is still unequal."""
        serial = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
        )
        # Only flip 2 of the 5
        partial_flips = {
            "skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox",
            "skills/ilk-loop/tests/test_plan_lint_e2e_env_prereq.py::test_existing_plan_lint_tests_still_pass",
        }
        n_auto = ConfigResult.from_outcomes(
            name="-n auto",
            wall_clock=404.12,
            outcomes=_make_parallel_outcomes(partial_flips),
        )

        comp = compare_configs(serial, n_auto)

        assert comp.outcome_sets_equal is False
        assert comp.flips == partial_flips


# ── Recommendation with mixed configs ───────────────────────────────────────

class TestRecommendMixed:
    """recommend() must handle a mix of clean and dirty configs."""

    def test_all_dirty_picks_consensus_fastest(self) -> None:
        """If every config flips tests and no serial baseline is present,
        pick the fastest among the most common outcome set (consensus).

        Without serial in the list, the algorithm can't determine absolute
        flip counts — it uses the most common outcome set as the consensus
        and picks the fastest member of that group.
        """
        n2 = ConfigResult.from_outcomes(
            name="-n 2",
            wall_clock=616.66,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
        )
        n4 = ConfigResult.from_outcomes(
            name="-n 4",
            wall_clock=430.52,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
        )
        n_auto = ConfigResult.from_outcomes(
            name="-n auto",
            wall_clock=404.12,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS - {
                "skills/ilk-loop/tests/test_data_home_sandbox.py::TestSchedulerWritesToSandbox::test_pidfile_in_sandbox",
            }),
        )

        result = recommend([n2, n4, n_auto])

        # n2 and n4 share the most common outcome set (identical flips).
        # Among them, n4 is faster (430.52 vs 616.66).
        assert result.config.name == "-n 4", (
            f"expected -n 4 (fastest in consensus group), got {result.config.name}"
        )

    def test_mixed_clean_and_dirty_picks_clean(self) -> None:
        """A clean config beats a faster dirty one."""
        serial = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
        )
        n_auto = ConfigResult.from_outcomes(
            name="-n auto",
            wall_clock=404.12,
            outcomes=_make_parallel_outcomes(_FLIPPED_NODE_IDS),
        )

        result = recommend([serial, n_auto])

        assert result.config.name == "serial"
