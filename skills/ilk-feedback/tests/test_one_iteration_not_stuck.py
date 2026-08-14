"""Tests for one-iteration gate-failure mislabelling.

Pins the defect where a single-iteration run whose sentinel records
``local_checks_failed`` is labelled ``local-checks-stuck`` — pointing
triage at the work when the cause was the plan file (unrunnable gate).

Sub-plan: a-one-iteration-gate-failure-is-not-stuck
Step 0 — pin the mislabel; step 1 flips the xfail.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_single_iter(exit_code: int = 0, new_commits: int = 1) -> list[dict]:
    """One iteration with given exit_code and new_commits."""
    return [{
        "run_id": "20260813-173420",
        "iteration": 1,
        "exit_code": exit_code,
        "duration_sec": 120,
        "new_commits_total": new_commits,
        "local_checks": {
            "outcome": "fail",
            "command": "pytest -q",
            "exit_code": 1,
            "stderr_tail": "FAILED tests/test_gate.py::test_yaml - assert 1 == 2",
        },
    }]


def _make_three_failing_iters() -> list[dict]:
    """Three iterations, all with failing local_checks (≥3 threshold)."""
    return [
        {
            "run_id": "20260813-173420",
            "iteration": i,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 1,
            "local_checks": {
                "outcome": "fail",
                "command": "pytest -q",
                "exit_code": 1,
                "stderr_tail": "FAILED tests/test_gate.py::test_yaml",
            },
        }
        for i in range(1, 4)
    ]


def _sentinel(state: str = "local_checks_failed", iteration: int = 1) -> dict:
    """Sentinel with given state and iteration."""
    return {
        "state": state,
        "run_id": "20260813-173420",
        "iteration": iteration,
    }


# ── Fixture A: one-iteration gate failure is mislabelled ─────────────────────


class TestOneIterationGateFailure:
    """A single-iteration run with sentinel=local_checks_failed.

    Today (step 0) this maps unconditionally to local-checks-stuck via the
    L1 sentinel map at collect.py:1262.  The label's recommended action
    points at the work (AC may be wrong), but the real cause is the plan
    file (unrunnable gate — exit_code 0, new_commits > 0).

    Step 1 will guard the L1 map by iteration count so this becomes
    local-checks-broken.  The xfail(strict=True) pins today's wrong
    label and flips when the fix lands.
    """

    def test_today_labels_it_stuck(self):
        """AC-1 pin: 1-iter sentinel=local_checks_failed → local-checks-stuck (today).

        xfail because this is the WRONG label — it should point at the plan
        file, not the work.  Step 1 removes the xfail.
        """
        iters = _make_single_iter(exit_code=0, new_commits=1)
        sentinel = _sentinel()

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        # Today: wrong label.  After step 1: local-checks-broken.
        assert label == "local-checks-stuck", (
            f"Expected local-checks-stuck (today's behaviour), got {label}"
        )

        # This is the assertion that should become local-checks-broken after step 1.
        # Mark xfail so the test passes today and fails when the fix lands,
        # reminding us to remove the xfail.
        pytest.xfail(
            "Step 0 pins the mislabel; step 1 will change this to local-checks-broken"
        )
        assert label == "local-checks-broken", (
            f"After fix: expected local-checks-broken, got {label}"
        )


# ── Fixture B: ≥3 iterations still gets local-checks-stuck ───────────────────


class TestThreeIterationsStillStuck:
    """AC-3 guard: ≥3 failing iterations + sentinel=local_checks_failed
    → local-checks-stuck, both today and after step 1.

    The iter-count heuristic already covers this case correctly.
    """

    def test_three_iters_remains_stuck(self):
        iters = _make_three_failing_iters()
        sentinel = _sentinel(iteration=3)

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-stuck", (
            f"Expected local-checks-stuck for ≥3 iters, got {label}"
        )


# ── Fixture C: sentinel overrides agent success narrative ────────────────────


class TestSentinelOverridesNarrative:
    """AC-4 guard: terminal-failure sentinel + success narrative → failure label.

    The L1 invariant (sentinel is authoritative) must survive the iteration-
    count narrowing.  Even with a single iteration whose exit_code=0 and
    new_commits>0, the sentinel's terminal failure state takes precedence
    over any agent narrative.
    """

    def test_sentinel_wins_over_success_narrative(self):
        """Sentinel says local_checks_failed, agent says shipped.

        After step 1, with 1 iter, this should become local-checks-broken
        (unrunnable gate).  The sentinel still wins — just a different label.
        """
        iters = _make_single_iter(exit_code=0, new_commits=1)
        iters[0]["stop_reason"] = "already-shipped"
        iters[0]["agent_summary"] = "All sub-plans shipped successfully."
        sentinel = _sentinel()

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        # The sentinel must produce a failure label — not clean-success.
        assert label in ("local-checks-stuck", "local-checks-broken"), (
            f"Sentinel failure must override agent narrative, got {label}"
        )
        assert facts.get("reason") == "sentinel terminal state", (
            f"Expected reason='sentinel terminal state', got {facts.get('reason')}"
        )
