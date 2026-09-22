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

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the scripts dir so we can import collect
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402

_LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

from ilk_paths import external_launcher_dir, project_key  # noqa: E402


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


# ── AC-5: read_sentinel probes liveness (sub-plan a-no-op-run-is-not-a-clean-run)
# ─────────────────────────────────────────────────────────────────────────────


def _dead_pid() -> int:
    """Return a PID that has been reaped — measured dead, not assumed."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_raw_sentinel(project_path: Path, payload: dict) -> None:
    launcher_dir = external_launcher_dir(project_key(project_path))
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-exit.json").write_text(json.dumps(payload))


class TestReadSentinelProbesLiveness:
    """read_sentinel must agree with status_progress's stale-running detection.

    Measured 2026-09-22: a sentinel reading state=running/pid=26627 while
    ps -p 26627 returned nothing (rc=1). status_progress printed
    ⚠ STALE-RUNNING; /ilk-feedback reported clean-success.
    """

    def test_running_with_dead_pid_is_stale(self, tmp_path):
        """state=running + dead pid ⇒ stale True, reported state "unknown"."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            project_path = tmp_path / "repo"
            project_path.mkdir()
            dead = _dead_pid()
            # Precondition: the pid must actually be dead, or the probe under
            # test is not the thing being exercised.
            with pytest.raises(ProcessLookupError):
                os.kill(dead, 0)

            _write_raw_sentinel(project_path, {
                "state": "running",
                "pid": dead,
                "run_id": "20260922-110127",
                "iteration": 0,
                "exit_code": None,
                "generated_at": "2026-09-22T12:00:00+08:00",
            })

            rec = collect.read_sentinel(project_path)
            assert rec is not None, "sentinel file was written; reader returned None"
            assert rec.get("stale") is True, (
                f"state=running with dead pid {dead} must report stale, got {rec}"
            )
            assert rec.get("state") == "unknown", (
                f"a stale sentinel must report state 'unknown' so readers that "
                f"only read state are not misled, got {rec.get('state')!r}"
            )
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)

    def test_running_with_live_runner_pid_is_not_stale(self, tmp_path, live_ilk_pid):
        """AC-6: a live runner pid is NOT reported stale (mirror-image guard).

        The plan phrases this as "the current process id", but the probe is
        command-verified (``pid_health.ilk_pid_alive``): pytest's own pid is
        precisely the unrelated command a recycled PID lands on, so it reads
        as stale — see the test below and ``conftest.py:live_ilk_pid``.  The
        positive control is therefore a live runner-shaped pid.  A probe that
        could not see one would report every sentinel stale — the mirror
        image of the bug this class exists for.
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        project_path = tmp_path / "repo"
        project_path.mkdir()
        with patch.dict(
            os.environ,
            {"HOME": str(fake_home), "ILK_DATA_HOME": str(data_home)},
        ):
            _write_raw_sentinel(project_path, {
                "state": "running",
                "pid": live_ilk_pid,
                "run_id": "20260922-110200",
                "iteration": 1,
                "exit_code": None,
                "generated_at": "2026-09-22T12:00:00+08:00",
            })
            rec = collect.read_sentinel(project_path)

        assert rec is not None, "sentinel file was written; reader returned None"
        assert rec.get("stale") is False, (
            f"a live runner pid {live_ilk_pid} must not read as stale, got {rec}"
        )
        assert rec.get("state") == "running", (
            f"a non-stale sentinel must keep its state, got {rec.get('state')!r}"
        )
        assert "raw_state" not in rec, (
            f"raw_state is only preserved on a stale sentinel, got {rec}"
        )

    def test_current_pid_reads_as_recycled_not_live(self, tmp_path):
        """os.getpid() is NOT a valid live control (AC-6's phrasing, corrected).

        Liveness is command-verified: a sentinel pid that now belongs to an
        unrelated process is the gh-triage recycling bug (2026-08-13, PID
        18920 → ``/bin/zsh -c … pytest``), i.e. stale.  pytest's own pid is
        that unrelated process, so ``state=running`` + ``os.getpid()`` must
        report stale.  Pinned so that "fixing" AC-6 by switching the probe to
        bare ``pid_alive`` — which would make every recycled pid read live —
        goes red.
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        project_path = tmp_path / "repo"
        project_path.mkdir()
        foreign = os.getpid()  # alive, but not an ilk process
        with patch.dict(
            os.environ,
            {"HOME": str(fake_home), "ILK_DATA_HOME": str(data_home)},
        ):
            _write_raw_sentinel(project_path, {
                "state": "running",
                "pid": foreign,
                "run_id": "20260922-110300",
                "iteration": 1,
                "exit_code": None,
                "generated_at": "2026-09-22T12:00:00+08:00",
            })
            rec = collect.read_sentinel(project_path)

        assert rec is not None, "sentinel file was written; reader returned None"
        assert rec.get("stale") is True, (
            f"a foreign live pid {foreign} must read as recycled/stale, got {rec}"
        )
        assert rec.get("state") == "unknown", (
            f"a stale sentinel must report state 'unknown', got {rec.get('state')!r}"
        )
        assert rec.get("raw_state") == "running", (
            f"the original state must survive in raw_state, got {rec.get('raw_state')!r}"
        )


# ── AC-7: a stale sentinel refuses the clean-success claim (sub-plan D3) ──────


class TestStaleSentinelClassification:
    """A stale-running sentinel must never launder into clean-success.

    Measured 2026-09-22: last-exit.json read ``{"state": "running",
    "pid": 26627, ...}`` while ``ps -p 26627`` returned nothing (rc=1,
    positive control confirmed the probe works).  status_progress printed
    ⚠ STALE-RUNNING; /ilk-feedback reported clean-success.
    """

    def test_stale_sentinel_is_not_clean_success(self):
        """AC-7: stale sentinel ⇒ classification is not clean-success.

        The iters alone classify clean-success (see
        ``test_no_sentinel_preserves_existing_behavior``).  A run whose
        sentinel is stale-running died before ``Finalize-Sentinel`` and did
        not stop cleanly, so the postmortem must not claim one.
        """
        iters = _make_clean_iters()
        sentinel = {
            "state": "unknown",
            "raw_state": "running",
            "stale": True,
            "pid": 26627,
            "run_id": "20260617-100000",
            "iteration": 1,
        }

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label != "clean-success", (
            f"A stale-running sentinel (state=running, pid 26627 dead) must not "
            f"classify as clean-success, got {label}: {facts}"
        )
        assert label == "interrupted", (
            f"Expected interrupted for a stale-running sentinel, got {label}. "
            "judgment call: reuse `interrupted` (no new label) because the run "
            "did not reach a natural stop and `interrupted` is already routed "
            "by both watchdogs; wrong if a stale sentinel should park/block "
            "instead — that needs its own label + watchdog arm."
        )
        assert facts.get("sentinel_stale") is True, (
            f"the staleness must be visible in facts even though it does not "
            f"own the label, got {facts}"
        )

    def test_stale_sentinel_keeps_more_specific_labels(self):
        """judgment-call pin: stale only refuses the clean-success claim.

        The measured gh-resolve shape (degenerate already-shipped record +
        stale sentinel) must still label ``already-shipped-noop`` (D1) — the
        stale flag is a fact, not a label override, so the more specific
        action (nothing to carry forward) survives.
        """
        iters = [{
            "run_id": "20260922-110127",
            "iteration": 0,
            "stop_reason": "already-shipped",
            "exit_code": None,
            "new_commits": None,
            "elapsed_sec": None,
            "num_turns": None,
            "result": None,
        }]
        sentinel = {
            "state": "unknown",
            "raw_state": "running",
            "stale": True,
            "pid": 26627,
            "run_id": "20260922-110127",
            "iteration": 0,
        }

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "already-shipped-noop", (
            f"a degenerate already-shipped run keeps its own label under a "
            f"stale sentinel (stale is a fact, not a label override), got "
            f"{label}: {facts}"
        )
        assert facts.get("sentinel_stale") is True, (
            f"the staleness must still be visible in facts, got {facts}"
        )
