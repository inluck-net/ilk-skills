"""Tests for sentinel classification label trigger — sub-plan a-label-matches-its-own-trigger.

Pins the defect where a 1-iteration local_checks_failed sentinel is classified
as local-checks-broken based on iteration count and runner exit code, rather
than consulting _is_broken_gate_result (the predicate the label claims to use).

Three acceptance criteria, all expected to FAIL until the sentinel branch
consults the predicate.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
Both HOME and ILK_DATA_HOME are pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400

_LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

from ilk_paths import external_launcher_dir, project_key  # noqa: E402


# -- helpers ------------------------------------------------------------------


def _write_sentinel(project_path: Path, state: str, iteration: int) -> None:
    """Write a minimal sentinel file in the external launcher dir."""
    launcher_dir = external_launcher_dir(project_key(project_path))
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "pid": 0,
        "run_id": "20260915-112812",
        "iteration": iteration,
        "exit_code": None,
        "generated_at": "2026-09-15T12:00:00+08:00",
    }
    (launcher_dir / "last-exit.json").write_text(json.dumps(sentinel))


def _make_single_iter_kira() -> list[dict]:
    """One iteration reproducing the kira shape: runner exit 0, gate exit 1
    with a genuine assertion failure. Built from the recorded postmortem values."""
    return [{
        "run_id": "20260915-112812",
        "iteration": 1,
        "exit_code": 0,
        "duration_sec": 51.2 * 60,
        "new_commits_total": 7,
        "local_checks": {
            "outcome": "fail",
            "command": "npx vitest run --reporter=verbose",
            "exit_code": 1,
            "stderr_tail": "AssertionError: expected undefined to be 'kira_verify_patient'",
        },
    }]


def _make_single_iter_unrunnable() -> list[dict]:
    """One iteration with a genuine unrunnable gate: exit 127, command not found."""
    return [{
        "run_id": "20260915-112812",
        "iteration": 1,
        "exit_code": 0,
        "duration_sec": 10,
        "new_commits_total": 1,
        "local_checks": {
            "outcome": "fail",
            "command": "bunx eslint-staged",
            "exit_code": 127,
            "stderr_tail": "bunx: command not found",
        },
    }]


# -- AC-1: kira shape (exit 1 assertion) → local-checks-stuck -----------------


class TestKiraShapeIsStuck:
    """Sentinel local_checks_failed, 1 iteration, runner exit 0, gate exit 1
    with an AssertionError stderr must yield local-checks-stuck (not broken)."""

    def test_exit1_assertion_is_stuck(self, tmp_path):
        """The kira fixture: gate exited 1 from a clean vitest run.
        This is a real test failure, not a broken gate command."""
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
            _write_sentinel(project_path, "local_checks_failed", 1)

            iters = _make_single_iter_kira()
            label, facts = collect.classify(iters, None, project_path)
            assert label == "local-checks-stuck", (
                f"exit-1 assertion failure should be local-checks-stuck, "
                f"got {label}: {facts}"
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


# -- AC-3: stderr arm → local-checks-broken -----------------------------------


class TestStderrArmIsBroken:
    """Sentinel local_checks_failed, 1 iteration, runner exit 0, gate exit 127
    with a 'command not found' stderr must yield local-checks-broken."""

    def test_exit127_command_not_found_is_broken(self, tmp_path):
        """Genuine unrunnable gate: bunx: command not found."""
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
            _write_sentinel(project_path, "local_checks_failed", 1)

            iters = _make_single_iter_unrunnable()
            label, facts = collect.classify(iters, None, project_path)
            assert label == "local-checks-broken", (
                f"exit-127 command-not-found should be local-checks-broken, "
                f"got {label}: {facts}"
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


# -- AC-6: rationale emitted only under the predicate -------------------------


class TestRationaleRequiresPredicate:
    """The local-checks-broken rationale (claiming 'product code is NOT the
    issue' and 'a blind resume re-fails identically') must be emitted only
    when _is_broken_gate_result held for at least one recorded check."""

    def test_exit1_assertion_no_broken_rationale(self, tmp_path):
        """Exit-1 assertion failure: the broken rationale must NOT appear."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            iters = _make_single_iter_kira()
            _, _, rationale = collect.recommend_params("local-checks-broken", iters, None)
            assert "re-fails" not in rationale, (
                "exit-1 assertion failure should not get the 're-fails identically' "
                "rationale — that claim requires _is_broken_gate_result"
            )
            assert "NOT implicated" not in rationale, (
                "exit-1 assertion failure should not get the 'NOT implicated' "
                "rationale — product code IS the issue"
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


# -- AC-1: a degenerate already-shipped run is not clean-success ---------------
# (sub-plan a-no-op-run-is-not-a-clean-run)


class TestDegenerateAlreadyShipped:
    """A run that never ran must not inherit the label of a run that worked.

    The exact record from gh-resolve run 20260922-110127: one iteration-0
    row, stop_reason already-shipped, every measurement null. The
    already-shipped branch returns clean-success with no guard on iteration
    count, elapsed, turns or commits.
    """

    def test_noop_already_shipped_is_not_clean_success(self, tmp_path):
        """The measured record classifies as already-shipped-noop."""
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
            label, facts = collect.classify(iters, None, project_path)
            assert label == "already-shipped-noop", (
                f"a degenerate already-shipped run must not be clean-success, "
                f"got {label}: {facts}"
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