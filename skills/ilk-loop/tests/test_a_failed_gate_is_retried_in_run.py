"""Sub-plan ``a-failed-gate-is-retried-in-run`` step 0 — pin it red-first.

The contract (from the sub-plan's "The contract" section):

1. On the FIRST confirmed-red post-iteration gate for a given (slug, step)
   within a run, the driver does NOT set the stop reason.  It writes the
   failing checks' tail to ``<run dir>/gate-red-<iteration>.txt``, appends one
   line to the next iteration's prompt, and continues the loop (the iteration
   counts toward ``--max-iterations``).
2. The SECOND consecutive red on the same (slug, step) in the same run takes
   today's path unchanged (quarantine count + ``local_checks_failed`` exit).
3. ``environment_fault`` and ``no_commits`` variants (:6485, :6488) keep
   today's behaviour.
4. No timeout, iteration budget or threshold value changes.

Test structure:
- **Unit tests** call ``gate_retry.decide_gate_retry`` directly.  These
  verify the decision logic and pass at base (the function is correct).
- **Integration tests** verify the runner's gate-failure block calls the
  helper and honours its return value.  At base the runner does NOT call
  ``decide_gate_retry``, so these are ``xfail(strict=True)``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gate_retry import GateRetryDecision, decide_gate_retry  # noqa: E402

_DRIVER = SCRIPTS / "run_ilk_loop_claude.sh"


# ── helpers ─────────────────────────────────────────────────────────────────

def _assert_decision(
    decision: GateRetryDecision,
    *,
    should_stop: bool,
    stop_reason: str | None = None,
    write_gate_red: bool = False,
    append_prompt_line: bool = False,
) -> None:
    """Assert all fields of a GateRetryDecision at once."""
    assert decision.should_stop is should_stop, (
        f"should_stop: expected {should_stop}, got {decision.should_stop}"
    )
    assert decision.stop_reason == stop_reason, (
        f"stop_reason: expected {stop_reason!r}, got {decision.stop_reason!r}"
    )
    assert decision.write_gate_red is write_gate_red, (
        f"write_gate_red: expected {write_gate_red}, got {decision.write_gate_red}"
    )
    assert decision.append_prompt_line is append_prompt_line, (
        f"append_prompt_line: expected {append_prompt_line}, "
        f"got {decision.append_prompt_line}"
    )


# ── Unit tests: decide_gate_retry ───────────────────────────────────────────

class TestDecideGateRetryUnit:
    """Unit tests for the decision helper.  These pass at base."""

    # -- Bullet 1: first red → retry (don't stop) --------------------------

    def test_first_red_does_not_stop(self) -> None:
        d = decide_gate_retry(red_count=1)
        _assert_decision(
            d,
            should_stop=False,
            stop_reason=None,
            write_gate_red=True,
            append_prompt_line=True,
        )

    def test_first_red_writes_gate_red_file(self) -> None:
        d = decide_gate_retry(red_count=1)
        assert d.write_gate_red is True

    def test_first_red_appends_prompt_line(self) -> None:
        d = decide_gate_retry(red_count=1)
        assert d.append_prompt_line is True

    # -- Bullet 2: second red → stop (today's path) ------------------------

    def test_second_red_sets_local_checks_failed(self) -> None:
        d = decide_gate_retry(red_count=2)
        _assert_decision(
            d,
            should_stop=True,
            stop_reason="local_checks_failed",
        )

    def test_second_red_does_not_write_gate_red(self) -> None:
        d = decide_gate_retry(red_count=2)
        assert d.write_gate_red is False

    def test_third_red_also_stops(self) -> None:
        d = decide_gate_retry(red_count=3)
        _assert_decision(
            d, should_stop=True, stop_reason="local_checks_failed"
        )

    # -- Bullet 3: environment_fault and no_commits keep today's behaviour --

    def test_environment_fault_stops_on_first_red(self) -> None:
        d = decide_gate_retry(red_count=1, is_environment_fault=True)
        _assert_decision(
            d,
            should_stop=True,
            stop_reason="local_checks_environment_fault",
        )

    def test_no_commits_stops_on_first_red(self) -> None:
        d = decide_gate_retry(red_count=1, is_no_commits=True)
        _assert_decision(
            d,
            should_stop=True,
            stop_reason="local_checks_failed_no_commits",
        )

    def test_environment_fault_stops_on_second_red(self) -> None:
        d = decide_gate_retry(red_count=2, is_environment_fault=True)
        _assert_decision(
            d,
            should_stop=True,
            stop_reason="local_checks_environment_fault",
        )

    def test_no_commits_stops_on_second_red(self) -> None:
        d = decide_gate_retry(red_count=2, is_no_commits=True)
        _assert_decision(
            d,
            should_stop=True,
            stop_reason="local_checks_failed_no_commits",
        )

    # -- Bullet 4: no value changes in the helper itself --------------------

    def test_helper_defines_no_timeout_or_budget_values(self) -> None:
        """The helper is pure decision logic — no constants."""
        src = (SCRIPTS / "gate_retry.py").read_text(encoding="utf-8")
        # Check that no assignment lines contain timeout/budget keywords.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "=" in stripped:
                for kw in ("TIMEOUT", "BUDGET", "THRESHOLD"):
                    assert kw not in stripped, (
                        f"gate_retry.py defines {kw} on line: {stripped!r}"
                    )


# ── Integration tests: runner must call the helper ─────────────────────────

class TestRunnerCallsGateRetry:
    """At base, the runner does NOT call decide_gate_retry.  xfail."""

    @pytest.mark.xfail(
        strict=True,
        reason="base runner sets iter_stop_reason=local_checks_failed on "
               "first red and breaks (run_ilk_loop_claude.sh:6491, :7017) "
               "without calling decide_gate_retry",
    )
    def test_driver_calls_decide_gate_retry(self) -> None:
        """AC: the runner's gate-failure block calls decide_gate_retry."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "decide_gate_retry" in src, (
            "run_ilk_loop_claude.sh does not call decide_gate_retry — "
            "the runner must call it in its gate-failure block"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="base runner does not write gate-red file on first red",
    )
    def test_driver_writes_gate_red_on_retry(self) -> None:
        """AC: the runner writes gate-red-<iteration>.txt when helper says retry."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "gate-red-" in src, (
            "run_ilk_loop_claude.sh does not write gate-red file — "
            "the runner must write failing checks' tail on first red"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="base runner does not append prompt line on first red",
    )
    def test_driver_appends_prompt_on_retry(self) -> None:
        """AC: the runner appends a prompt line for the next iteration."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        # The prompt line should reference the gate-red file.
        assert "Your previous iteration failed" in src, (
            "run_ilk_loop_claude.sh does not append prompt line — "
            "the runner must tell the next iteration about the red gate"
        )

    def test_driver_has_gate_failure_block(self) -> None:
        """Control: the runner's gate-failure block exists at base."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert 'iter_stop_reason="local_checks_failed"' in src, (
            "runner's gate-failure block not found"
        )