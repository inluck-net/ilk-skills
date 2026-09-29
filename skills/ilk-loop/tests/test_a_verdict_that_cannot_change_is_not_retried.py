"""Red-first: a deterministic or environment red blocks at once, without a re-run.

V3, measured on gh-resolve 28c:
- Run 073402: root-area gate was 535 passed, 0 failed.  The only red was
  conftest D-70 guard on stderr.  It ran twice, ~3 min each, before blocking.
- Run 083918: step 1 ``verify_attribution.py`` failed twice from 09:10:25 to
  09:14:49.  It reads the verification record, so the second attempt re-derived
  the same answer (~2 min).

Acceptance criteria:
1. Deterministic checks skip the confirm re-run.
2. Environment reds skip the re-run and are labelled.
3. Environment reds are not quarantine strikes.
4. Unchanged: a normal red is re-run once, and a transient pass clears it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_REPO = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))

import run_local_checks as rlc  # noqa: E402


# ── AC-1: deterministic checks skip the confirm re-run ────────────────────────

@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_runner_skips_deterministic_check_in_b2_rerun():
    """The runner's B2 block must skip re-running a check with ``retry: false``.

    This is a runner-level pin.  The check should appear in the confirmed
    blocking list without ever being re-run.
    """
    runner = (_SCRIPTS / "run_ilk_loop_claude.sh").read_text(encoding="utf-8")
    # Find the B2 confirm block (around line 5150).
    # The runner must have logic to skip deterministic checks in the B2 rerun.
    # Check specifically in the B2 area, not anywhere in the file.
    b2_area_start = runner.find("# missing shell builtin) must be CONFIRMED")
    assert b2_area_start != -1, "B2 confirm block not found in runner"
    b2_area = runner[b2_area_start:b2_area_start + 3000]
    assert "retry" in b2_area or "deterministic" in b2_area, \
        "runner B2 block has no logic to skip deterministic checks"


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_confirm_b2_skips_deterministic_even_when_rerun_present():
    """``confirm_b2_block`` must skip a deterministic check even if the rerun
    accidentally includes it (the runner re-ran it before realising)."""
    first = [
        {"command": "pytest -q", "outcome": "fail", "retry": False},
    ]
    rerun = [
        {"command": "pytest -q", "outcome": "pass"},
    ]
    result = rlc.confirm_b2_block(first, rerun)
    # Must be blocked despite the transient pass — the verdict cannot change.
    assert result["blocked"] is True
    assert result["transient_cleared"] == []


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_verify_attribution_without_remeasure_is_deterministic():
    """``verify_attribution.py`` without ``--remeasure-if-stale`` is deterministic."""
    first = [
        {"command": "python3 verify_attribution.py --check", "outcome": "fail"},
    ]
    rerun = [
        {"command": "python3 verify_attribution.py --check", "outcome": "pass"},
    ]
    result = rlc.confirm_b2_block(first, rerun)
    # Must be blocked despite transient pass — the record doesn't change.
    assert result["blocked"] is True
    assert result["transient_cleared"] == []


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_log_line_names_verdict_cannot_change():
    """The runner logs ``no re-run — verdict cannot change`` for deterministic checks."""
    runner = (_SCRIPTS / "run_ilk_loop_claude.sh").read_text(encoding="utf-8")
    # Check specifically in the B2 area.
    b2_area_start = runner.find("# missing shell builtin) must be CONFIRMED")
    assert b2_area_start != -1
    b2_area = runner[b2_area_start:b2_area_start + 3000]
    assert "verdict cannot change" in b2_area, \
        "runner B2 block does not log 'verdict cannot change' for deterministic checks"


# ── AC-2: environment reds skip the re-run and are labelled ───────────────────

_ENVIRONMENT_STDERR_PYTEST = (
    "D-70 VIOLATION: the real ledger grew\n"
    "=== 1 passed, 0 failed, 0 errors in 0.17s ===\n"
)

_ENVIRONMENT_STDERR_VITEST = (
    "guard check failed: unexpected stderr\n"
    " ✓ 1 passed\n"
    " ✗ 0 failed\n"
)

# A real failure (1 failed) must NOT be classified as environment red.
_REAL_FAILURE_STDERR = (
    "FAILED tests/test_x.py::test_foo - assert 1 == 0\n"
    "=== 1 passed, 1 failed in 0.17s ===\n"
)


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_environment_red_pytest_format():
    """A pytest summary with >=1 passed and 0 failed is an environment red."""
    outcome, reason = rlc._classify_check(1, "", _ENVIRONMENT_STDERR_PYTEST)
    assert outcome == "error"
    assert reason is not None
    assert reason.startswith("environment:")
    assert "D-70" in reason


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_environment_red_vitest_format():
    """Vitest equivalent: >=1 passed, 0 failed on stderr."""
    outcome, reason = rlc._classify_check(1, "", _ENVIRONMENT_STDERR_VITEST)
    assert outcome == "error"
    assert reason is not None
    assert reason.startswith("environment:")
    assert "guard" in reason


def test_real_failure_is_still_fail():
    """A pytest summary with >=1 failed is a real failure, not environment."""
    outcome, reason = rlc._classify_check(1, "", _REAL_FAILURE_STDERR)
    assert outcome == "fail"
    assert reason is None


@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_environment_red_skips_confirm_rerun():
    """An environment red is blocked immediately, even if the rerun passes."""
    first = [
        {"command": "pytest -q", "outcome": "error",
         "reason": "environment: D-70 VIOLATION"},
    ]
    # The runner re-ran it (accidentally) and it passed this time.
    rerun = [
        {"command": "pytest -q", "outcome": "pass"},
    ]
    result = rlc.confirm_b2_block(first, rerun)
    # Must be blocked despite transient pass — the environment red is not
    # a transient failure of the check itself.
    assert result["blocked"] is True
    assert result["transient_cleared"] == []


# ── AC-3: environment reds are not quarantine strikes ─────────────────────────

@pytest.mark.xfail(strict=True, reason="deterministic/environment reds still retried")
def test_quarantine_skips_environment_errors():
    """``quarantine_subplan.py`` must not count ``environment:`` errors as strikes.

    The quarantine counter counts only ``fail``, and ``error`` rows whose
    reason doesn't start with ``environment:``.
    """
    quarantine = _SCRIPTS / "quarantine_subplan.py"
    assert quarantine.exists(), "quarantine_subplan.py not found"

    src = quarantine.read_text(encoding="utf-8")
    # The quarantine module must explicitly exclude environment errors.
    assert "environment" in src, \
        "quarantine_subplan.py has no logic to exclude environment errors"


# ── AC-4: unchanged — a normal red is re-run once, transient pass clears it ───

def test_normal_red_is_rerun_once_and_transient_clears():
    """A normal ``fail`` that passes on re-run is transient (existing behavior)."""
    first = [
        {"command": "pytest -q", "outcome": "fail"},
    ]
    rerun = [
        {"command": "pytest -q", "outcome": "pass"},
    ]
    result = rlc.confirm_b2_block(first, rerun)
    assert result["blocked"] is False
    assert "pytest -q" in result["transient_cleared"]


def test_normal_red_that_stays_red_is_blocked():
    """A normal ``fail`` that fails again on re-run is confirmed blocked."""
    first = [
        {"command": "pytest -q", "outcome": "fail"},
    ]
    rerun = [
        {"command": "pytest -q", "outcome": "fail"},
    ]
    result = rlc.confirm_b2_block(first, rerun)
    assert result["blocked"] is True
    assert len(result["blocking_checks"]) == 1
    assert result["blocking_checks"][0]["rerun_outcome"] == "fail"
