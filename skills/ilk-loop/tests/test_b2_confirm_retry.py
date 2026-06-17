"""RED tests for confirm_b2_block — the B2 confirm-before-block decision helper.

The runner's B2 gate currently trusts the first pass of local_checks.  A
transient `error` (flaky exit, missing shell builtin) blocks the loop even
though the work shipped cleanly — re-running every gate is green.  The fix:
before committing to `local_checks_failed`, re-run the *blocking* checks once
and call `confirm_b2_block` to decide whether the stop is real.

AC-1: NOT-blocked when a check errors on the first pass but passes on re-run.
AC-2: blocked when a check fails/errored on BOTH passes.
AC-3: confirm-retry only re-runs the blocking checks (no re-run ⇒ not blocked).

These tests use in-memory result dicts — no subprocess, no file I/O.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Will be implemented in step 1 — until then, every test here is RED.
from run_local_checks import confirm_b2_block  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def _r(command: str, outcome: str) -> dict:
    """Build a minimal check-result dict (the shape run_one produces)."""
    return {"command": command, "outcome": outcome}


# ── AC-1: error → pass on re-run means NOT blocked ─────────────────────────

def test_error_on_first_pass_cleared_on_rerun_is_not_blocked() -> None:
    """A check that errors transiently then passes is NOT a stop."""
    first = [_r("pytest -q", "error")]
    rerun = [_r("pytest -q", "pass")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is False, "transient error cleared on re-run should not block"


def test_multiple_errors_all_cleared_on_rerun() -> None:
    """Several transient errors, all pass on re-run → not blocked."""
    first = [_r("pytest -q", "error"), _r("lint.sh", "error")]
    rerun = [_r("pytest -q", "pass"), _r("lint.sh", "pass")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is False


# ── AC-2: fail/error on BOTH passes means blocked ──────────────────────────

def test_fail_on_both_passes_is_blocked() -> None:
    """A genuine assertion failure that persists is a real stop."""
    first = [_r("pytest -q", "fail")]
    rerun = [_r("pytest -q", "fail")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is True


def test_error_on_both_passes_is_blocked() -> None:
    """A command that can't execute cleanly twice is a real stop."""
    first = [_r("broken-cmd", "error")]
    rerun = [_r("broken-cmd", "error")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is True


def test_fail_on_first_error_on_rerun_is_blocked() -> None:
    """fail→error still means something is wrong — block."""
    first = [_r("pytest -q", "fail")]
    rerun = [_r("pytest -q", "error")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is True


def test_error_on_first_fail_on_rerun_is_blocked() -> None:
    """error→fail — the re-run surfaced a real failure — block."""
    first = [_r("pytest -q", "error")]
    rerun = [_r("pytest -q", "fail")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is True


# ── AC-3: clean first pass ⇒ no re-run needed, not blocked ─────────────────

def test_all_pass_on_first_no_rerun_is_not_blocked() -> None:
    """No blocking outcome on first pass → no re-run needed."""
    first = [_r("pytest -q", "pass"), _r("lint.sh", "pass")]
    rerun: list[dict] = []
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is False


def test_clean_first_pass_with_empty_lists() -> None:
    """Edge: empty first pass (no checks) → not blocked."""
    result = confirm_b2_block([], [])
    assert result["blocked"] is False


# ── mixed outcomes: some pass, some error ───────────────────────────────────

def test_mixed_first_pass_only_blocking_checks_rerun() -> None:
    """Only the error check is re-run; the passing one is left alone."""
    first = [_r("pytest -q", "pass"), _r("lint.sh", "error")]
    rerun = [_r("lint.sh", "pass")]  # only the blocking one re-ran
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is False


def test_mixed_first_pass_rerun_still_fails() -> None:
    """The blocking check fails again on re-run → blocked."""
    first = [_r("pytest -q", "pass"), _r("lint.sh", "fail")]
    rerun = [_r("lint.sh", "fail")]
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is True


# ── skipped outcome: skipped is not blocking ────────────────────────────────

def test_skipped_is_not_blocking() -> None:
    """A skipped check should not trigger a re-run or block."""
    first = [_r("pytest -q", "pass"), _r("optional-check", "skipped")]
    rerun: list[dict] = []
    result = confirm_b2_block(first, rerun)
    assert result["blocked"] is False


# ── AC-1/AC-2 detail: result includes which checks were confirmed ──────────

def test_result_contains_blocking_checks_detail() -> None:
    """The result should list which checks were blocking for diagnostics."""
    first = [_r("pytest -q", "error"), _r("lint.sh", "pass")]
    rerun = [_r("pytest -q", "pass")]
    result = confirm_b2_block(first, rerun)
    # The result should carry enough detail for the runner to log what happened
    assert "confirmed_blocking" in result or "details" in result
