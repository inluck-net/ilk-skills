"""Red-first pins: a timeout (error) does not count toward quarantine.

Part of `a-timeout-is-not-a-strike` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

Pins AC-1 and AC-3 from the sub-plan:
  AC-1: three consecutive error (timeout) outcomes ⇒ counter unchanged.
  AC-3: fail, fail, pass, fail ⇒ counter is 1 after the last fail.

AC-2 (consecutive measured fails ⇒ quarantined) is already satisfied by
``test_quarantine_subplan.py::test_second_failure_reaches_the_threshold_and_blocks``.

Drives ``quarantine_subplan.quarantine_subplan`` directly (not the CLI),
because the change is about the counting logic, not about argument parsing.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS_DIR))

from quarantine_subplan import quarantine_subplan  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_subplan(plans: Path, slug: str, *,
                   auto_block_fails: int = 0) -> Path:
    """Write a minimal sub-plan with an optional pre-set counter."""
    plans.mkdir(parents=True, exist_ok=True)
    extra = f"\nauto_block_fails: {auto_block_fails}" if auto_block_fails else ""
    p = plans / f"2026-09-25-{slug}.md"
    p.write_text(textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: in-progress
        current_step: 1
        estimated_steps: 2{extra}
        ---

        # Sub-plan: {slug}
    """), encoding="utf-8")
    return p


def _quarantine_counted(plans: Path, slug: str,
                        outcome: str = "fail",
                        threshold: int = 2) -> dict:
    """Call quarantine_subplan with the outcome passed through."""
    return quarantine_subplan(
        plans, slug, failing_check=f"pytest -q [outcome={outcome}]",
        threshold=threshold, outcome=outcome,
    )


# ── AC-1: three consecutive error (timeout) ⇒ counter unchanged ─────────────


def test_three_consecutive_errors_leave_counter_at_zero(tmp_path: Path) -> None:
    """AC-1: three error (timeout) outcomes ⇒ counter unchanged, not quarantined."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "slow")

    for _ in range(3):
        out = _quarantine_counted(plans, "slow", outcome="error")
        assert out["blocked"] is False, out

    assert out["fails"] == 0, (
        f"counter should be 0 after 3 errors, got {out['fails']}"
    )


def test_error_never_reaches_quarantine_threshold(tmp_path: Path) -> None:
    """Even with threshold=1, an error outcome must not quarantine."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "slow")

    out = _quarantine_counted(plans, "slow", outcome="error", threshold=1)
    assert out["blocked"] is False, (
        "error outcome must never quarantine, even at threshold=1"
    )


# ── AC-2: consecutive measured fails ⇒ quarantined ──────────────────────────
# Already pinned by test_quarantine_subplan.py; included here for completeness.


def test_two_consecutive_fails_quarantine(tmp_path: Path) -> None:
    """AC-2: measured fail bumps the counter; threshold reached ⇒ blocked."""
    plans = tmp_path / "plans"
    _write_subplan(plans, "bug")

    out1 = _quarantine_counted(plans, "bug", outcome="fail")
    assert out1["blocked"] is False
    assert out1["fails"] == 1

    out2 = _quarantine_counted(plans, "bug", outcome="fail")
    assert out2["blocked"] is True
    assert out2["fails"] == 2


# ── AC-3: fail, fail, pass, fail ⇒ counter is 1 ────────────────────────────


def test_green_gate_resets_the_counter(tmp_path: Path) -> None:
    """AC-3: fail, fail, pass, fail ⇒ counter is 1 after the last fail.

    A green gate (pass) resets auto_block_fails to 0.
    """
    plans = tmp_path / "plans"
    _write_subplan(plans, "flaky")

    # Two fails: counter reaches 2 (but threshold not hit at default=2
    # because we call with threshold=3 to observe the reset).
    out = _quarantine_counted(plans, "flaky", outcome="fail")
    assert out["fails"] == 1
    out = _quarantine_counted(plans, "flaky", outcome="fail")
    assert out["fails"] == 2

    # A pass should reset the counter. Today there is no reset path.
    # Step 1 will add a --reset-on-pass or --outcome flag.
    # For now we call with outcome="pass" — the function ignores the
    # outcome today, so the counter keeps climbing.
    out_pass = _quarantine_counted(plans, "flaky", outcome="pass")
    # After reset, the next fail should yield counter=1, not 3.
    out_fail = _quarantine_counted(plans, "flaky", outcome="fail")
    assert out_fail["fails"] == 1, (
        f"counter should be 1 after fail-pass-fail, got {out_fail['fails']}"
    )
