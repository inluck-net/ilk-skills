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

import subprocess

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


# ── Environment fields in artifact ──────────────────────────────────────────

class TestArtifactEnvironment:
    """The artifact must carry host, ncpu, pytest/xdist versions, HEAD,
    and load average at start AND end of each run."""

    def test_config_result_carries_load_fields(self) -> None:
        """ConfigResult has load_start and load_end fields."""
        cfg = ConfigResult.from_outcomes(
            name="serial",
            wall_clock=263.31,
            outcomes=_make_serial_outcomes(),
            load_start={"1m": 1.5, "5m": 2.0, "15m": 1.8},
            load_end={"1m": 3.0, "5m": 2.5, "15m": 2.0},
        )
        assert cfg.load_start is not None
        assert cfg.load_end is not None
        assert cfg.load_start["1m"] == 1.5
        assert cfg.load_end["1m"] == 3.0

    def test_artifact_contains_required_fields(self, tmp_path: Path) -> None:
        """write_artifact produces output with all required environment fields."""
        from suite_timing import write_artifact, Recommendation

        results = [
            ConfigResult.from_outcomes(
                name="serial",
                wall_clock=263.31,
                outcomes=_make_serial_outcomes(),
                load_start={"1m": 1.5, "5m": 2.0, "15m": 1.8},
                load_end={"1m": 3.0, "5m": 2.5, "15m": 2.0},
            ),
        ]
        rec = Recommendation(
            config=results[0],
            reason="only config",
            disqualified=(),
        )
        env = {
            "host": "test-host",
            "platform": "Darwin 25.6.0",
            "ncpu": 10,
            "python": "3.9.6",
            "pytest_version": "8.4.2",
            "xdist_version": "3.8.0",
            "head": "abc1234",
            "load_avg": {"1m": 1.5, "5m": 2.0, "15m": 1.8},
        }

        output = tmp_path / "timing.md"
        write_artifact(results, rec, env, output)

        content = output.read_text()
        assert "host" in content
        assert "ncpu" in content
        assert "pytest" in content
        assert "HEAD" in content
        assert "load start" in content
        assert "load end" in content
        assert "1.5/2.0/1.8" in content  # load_start values
        assert "3.0/2.5/2.0" in content  # load_end values


# ── Idle-box check ──────────────────────────────────────────────────────────

class TestIdleBoxCheck:
    """The script refuses to measure on a busy box."""

    def test_refuses_on_high_load(self) -> None:
        """Load above 2.0 × ncpu → BusyBoxError."""
        from suite_timing import check_idle, BusyBoxError

        # Monkey-patch _capture_load to return high load
        import suite_timing
        original = suite_timing._capture_load
        suite_timing._capture_load = lambda: {"1m": 25.0, "5m": 20.0, "15m": 15.0}
        try:
            with pytest.raises(BusyBoxError) as exc_info:
                check_idle(ncpu=10)
            assert "25.0" in str(exc_info.value)
            assert "threshold" in str(exc_info.value).lower()
        finally:
            suite_timing._capture_load = original

    def test_refuses_on_loop_process(self) -> None:
        """Live loop process → BusyBoxError."""
        from suite_timing import check_idle, BusyBoxError, LOOP_PROCESS_NAMES

        # Monkey-patch _capture_load to return low load (so load check passes)
        import suite_timing
        original_load = suite_timing._capture_load
        suite_timing._capture_load = lambda: {"1m": 1.5, "5m": 1.0, "15m": 0.8}

        # Monkey-patch subprocess.run to return a ps output with a loop process
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["ps", "-eo"]:
                # Return a fake ps output with a loop process
                class FakeResult:
                    returncode = 0
                    stdout = "  PID COMM\n 1234 run_ilk_loop_claude.sh\n"
                return FakeResult()
            return original_run(cmd, **kwargs)

        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            with pytest.raises(BusyBoxError) as exc_info:
                check_idle(ncpu=10)
            assert "1234" in str(exc_info.value)
            assert "loop" in str(exc_info.value).lower()
        finally:
            suite_timing._capture_load = original_load
            subprocess.run = original_run  # type: ignore[assignment]

    def test_passes_on_idle_box(self) -> None:
        """Low load and no loop processes → no error."""
        from suite_timing import check_idle

        # Monkey-patch _capture_load to return low load
        import suite_timing
        original_load = suite_timing._capture_load
        suite_timing._capture_load = lambda: {"1m": 1.5, "5m": 1.0, "15m": 0.8}

        # Monkey-patch subprocess.run to return empty ps output
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["ps", "-eo"]:
                class FakeResult:
                    returncode = 0
                    stdout = "  PID COMM\n"
                return FakeResult()
            return original_run(cmd, **kwargs)

        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            # Should not raise
            check_idle(ncpu=10)
        finally:
            suite_timing._capture_load = original_load
            subprocess.run = original_run  # type: ignore[assignment]

    def test_busy_box_error_carrying_context(self) -> None:
        """BusyBoxError carries load, ncpu, and loop_pids."""
        from suite_timing import BusyBoxError

        err = BusyBoxError(
            reason="test",
            load={"1m": 25.0, "5m": 20.0, "15m": 15.0},
            ncpu=10,
            loop_pids=[1234, 5678],
        )
        assert err.load == {"1m": 25.0, "5m": 20.0, "15m": 15.0}
        assert err.ncpu == 10
        assert err.loop_pids == [1234, 5678]
