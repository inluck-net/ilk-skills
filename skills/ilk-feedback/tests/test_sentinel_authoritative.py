"""Tests for L1: sentinel terminal failure state is authoritative in classify().

Covers the bug from run 20260616-231713: sentinel had
state=local_checks_failed but classify() returned clean-success because
only the last of 3 iters failed and the iter-count heuristic didn't fire.

Acceptance criteria:
  AC-1: state=local_checks_failed, only last iter fails ⇒ local-checks-stuck
  AC-2: state=shipped ⇒ clean-success (no false-positive regression)
  AC-3: agent narrative "all shipped" + state=local_checks_failed ⇒ local-checks-stuck
  AC-4: full feedback test suite remains green (regression sweep, step 2)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the scripts dir so we can import collect
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_iters_last_fails() -> list[dict]:
    """3 iterations: iters 1-2 pass local_checks, iter 3 fails.

    This is the exact scenario from run 20260616-231713: the iter-count
    heuristic requires fail_iters >= 3, but only the last iter failed,
    so the old code fell through to clean-success.
    """
    return [
        {
            "run_id": "20260616-231713",
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 2,
            "duration_sec": 120,
            "local_checks": {"outcome": "pass", "command": "pytest -q"},
        },
        {
            "run_id": "20260616-231713",
            "iteration": 2,
            "exit_code": 0,
            "new_commits_total": 1,
            "duration_sec": 90,
            "local_checks": {"outcome": "pass", "command": "pytest -q"},
        },
        {
            "run_id": "20260616-231713",
            "iteration": 3,
            "exit_code": 0,
            "new_commits_total": 1,
            "duration_sec": 95,
            "stop_reason": "already-shipped",
            "local_checks": {"outcome": "fail", "command": "pytest -q"},
        },
    ]


def _make_clean_iters() -> list[dict]:
    """Single clean iteration that shipped successfully."""
    return [
        {
            "run_id": "20260617-100000",
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 3,
            "stop_reason": "already-shipped",
            "duration_sec": 120,
            "local_checks": {"outcome": "pass", "command": "pytest -q"},
        },
    ]


# ── AC-1: failure sentinel overrides iter-count heuristic ────────────────────


def test_failure_sentinel_overrides_clean_success():
    """state=local_checks_failed with only 1 failing iter ⇒ local-checks-stuck.

    The iter-count heuristic (fail_iters >= 3) does NOT fire because only
    the last of 3 iters failed. Without the sentinel override, classify()
    returns clean-success — this is the bug.
    """
    iters = _make_iters_last_fails()
    sentinel = {
        "state": "local_checks_failed",
        "run_id": "20260616-231713",
        "iteration": 3,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    # After the fix: sentinel state is authoritative
    assert label == "local-checks-stuck", (
        f"Expected local-checks-stuck, got {label}. "
        "The sentinel state=local_checks_failed must override the iter-count heuristic."
    )
    assert facts.get("reason") == "sentinel terminal state", (
        f"Expected reason='sentinel terminal state', got {facts.get('reason')}"
    )


# ── AC-2: success sentinel still classifies clean-success ────────────────────


def test_success_sentinel_classifies_clean_success():
    """state=shipped ⇒ clean-success. No false-positive regression."""
    iters = _make_clean_iters()
    sentinel = {
        "state": "shipped",
        "run_id": "20260617-100000",
        "iteration": 1,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "clean-success", (
        f"Expected clean-success for shipped sentinel, got {label}"
    )


# ── AC-3: agent narrative ignored when sentinel says failure ─────────────────


def test_agent_narrative_ignored_when_sentinel_fails():
    """Agent says "all sub-plans shipped" but sentinel says local_checks_failed.

    The sentinel must win. The agent narrative is NOT authoritative.
    """
    iters = _make_iters_last_fails()
    # The iter already has stop_reason: "already-shipped" (agent narrative).
    # Add explicit agent text claiming success.
    iters[-1]["agent_summary"] = "All sub-plans shipped successfully."

    sentinel = {
        "state": "local_checks_failed",
        "run_id": "20260616-231713",
        "iteration": 3,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "local-checks-stuck", (
        f"Expected local-checks-stuck even with agent success narrative, got {label}. "
        "Sentinel failure state must be authoritative over agent narrative."
    )


# ── AC-3b: no sentinel ⇒ existing behavior preserved ────────────────────────


def test_no_sentinel_preserves_existing_behavior():
    """When read_sentinel returns None, existing iter-based logic applies."""
    iters = _make_iters_last_fails()

    with patch.object(collect, "read_sentinel", return_value=None):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    # Without a sentinel, the iter-count heuristic applies.
    # Only 1 fail out of 3, stop_reason=already-shipped, loop_status_exit=0
    # ⇒ clean-success (existing behavior, no regression)
    assert label == "clean-success", (
        f"Expected clean-success when no sentinel present, got {label}"
    )


# ── Other L1 failure states ─────────────────────────────────────────────────


def test_budget_exhausted_sentinel():
    """state=budget_exhausted ⇒ budget-exhausted."""
    iters = _make_clean_iters()
    sentinel = {
        "state": "budget_exhausted",
        "run_id": "20260617-100000",
        "iteration": 1,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "budget-exhausted", (
        f"Expected budget-exhausted for budget_exhausted sentinel, got {label}"
    )


def test_max_iterations_sentinel():
    """state=max-iterations ⇒ max-iter-bound."""
    iters = _make_clean_iters()
    sentinel = {
        "state": "max-iterations",
        "run_id": "20260617-100000",
        "iteration": 1,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "max-iter-bound", (
        f"Expected max-iter-bound for max-iterations sentinel, got {label}"
    )


def test_interrupted_sentinel():
    """state=interrupted ⇒ interrupted."""
    iters = _make_clean_iters()
    sentinel = {
        "state": "interrupted",
        "run_id": "20260617-100000",
        "iteration": 1,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "interrupted", (
        f"Expected interrupted for interrupted sentinel, got {label}"
    )
