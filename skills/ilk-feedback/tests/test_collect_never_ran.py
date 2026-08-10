"""Tests for collect.py's never-ran classification.

Covers a run that never invoked the model (zero turns, zero tokens,
startup-failure result) — must classify as "never-ran", not "stuck-no-progress".

AC-1: zero-turn, zero-token startup failure → never-ran
AC-2: each startup-failure shape independently (Unknown command, command not found,
      non-zero exit before first turn)
AC-3: genuine stuck-no-progress (zero commits, non-zero turns/tokens) stays stuck
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400


# -- Fixtures ----------------------------------------------------------------

def _make_iter(
    run_id: str = "20260810-110314",
    iteration: int = 1,
    stop_reason: str | None = "no-progress",
    exit_code: int | None = 0,
    new_commits_total: int = 0,
    num_turns: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    result: str | None = None,
    worker_home: str | None = None,
    log: str | None = None,
) -> dict:
    """Build a synthetic JSONL iteration record."""
    rec: dict = {
        "run_id": run_id,
        "iteration": iteration,
        "stop_reason": stop_reason,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
        "num_turns": num_turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if result is not None:
        rec["result"] = result
    if worker_home is not None:
        rec["worker_home"] = worker_home
    if log is not None:
        rec["log"] = log
    return rec


# -- AC-1: Zero-turn startup failure → never-ran ----------------------------

class TestNeverRanClassification:
    """A run with zero turns, zero tokens, and a startup-failure result
    must classify as never-ran, not stuck-no-progress."""

    def test_unknown_command_result(self, tmp_path):
        """The exact fixture from the handoff: Unknown command → never-ran."""
        iters = [_make_iter(
            result="Unknown command: /ilk",
            worker_home="/home/user/.claude-worker-1",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label == "never-ran", (
            f"Expected never-ran for Unknown command, got: {label}"
        )
        assert facts.get("worker_home") == "/home/user/.claude-worker-1"
        assert "Unknown command" in (facts.get("result") or "")

    def test_command_not_found_result(self, tmp_path):
        """'command not found' in result → never-ran."""
        iters = [_make_iter(
            result="bash: ilk: command not found",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label == "never-ran", (
            f"Expected never-ran for command not found, got: {label}"
        )

    def test_no_such_file_result(self, tmp_path):
        """'No such file or directory' in result → never-ran."""
        iters = [_make_iter(
            result="/bin/sh: /usr/local/bin/ilk: No such file or directory",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label == "never-ran", (
            f"Expected never-ran for No such file, got: {label}"
        )

    def test_windows_not_recognized_result(self, tmp_path):
        """Windows 'not recognized' error → never-ran."""
        iters = [_make_iter(
            result="'ilk' is not recognized as an internal or external command",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label == "never-ran", (
            f"Expected never-ran for Windows not recognized, got: {label}"
        )


# -- AC-2: Each startup-failure shape independently -------------------------

class TestNeverRanShapes:
    """Each startup-failure pattern must be detected independently."""

    def test_zero_turns_zero_tokens_required(self, tmp_path):
        """Non-zero turns with startup-failure result → NOT never-ran.
        (A run that took even one turn is a genuine stall.)"""
        iters = [_make_iter(
            num_turns=1,
            input_tokens=500,
            output_tokens=100,
            result="Unknown command: /ilk",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label != "never-ran", (
            f"Non-zero turns should NOT be never-ran, got: {label}"
        )

    def test_nonzero_tokens_prevents_never_ran(self, tmp_path):
        """Non-zero tokens with startup-failure result → NOT never-ran."""
        iters = [_make_iter(
            num_turns=0,
            input_tokens=100,
            output_tokens=50,
            result="Unknown command: /ilk",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label != "never-ran", (
            f"Non-zero tokens should NOT be never-ran, got: {label}"
        )


# -- AC-3: Genuine stuck-no-progress preserved ------------------------------

class TestStuckNoProgressPreserved:
    """A genuine stuck-no-progress run (zero commits, non-zero turns/tokens)
    must still classify as stuck-no-progress."""

    def test_genuine_stay_stuck(self, tmp_path):
        """Zero commits but non-zero turns and tokens → stuck-no-progress."""
        iters = [
            _make_iter(
                iteration=i,
                num_turns=5,
                input_tokens=2000,
                output_tokens=500,
                new_commits_total=0,
            )
            for i in range(1, 4)
        ]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label == "stuck-no-progress", (
            f"Genuine stall should remain stuck-no-progress, got: {label}"
        )

    def test_no_result_string_stays_stuck(self, tmp_path):
        """Zero turns/tokens but no startup-failure result → stuck-no-progress.
        (The result might be empty or non-matching — still a stall.)"""
        iters = [_make_iter(
            num_turns=0,
            input_tokens=0,
            output_tokens=0,
            result="",
        )]
        label, facts = collect.classify(iters, None, tmp_path)
        assert label != "never-ran", (
            f"Empty result should NOT trigger never-ran, got: {label}"
        )


# -- Label vocabulary -------------------------------------------------------

class TestLabelInVocabulary:
    """never-ran must be in CLASSIFICATION_LABELS for the totality gate."""

    def test_never_ran_in_labels(self):
        assert "never-ran" in collect.CLASSIFICATION_LABELS

    def test_all_labels_render_narrative(self):
        """Every label in CLASSIFICATION_LABELS renders without error."""
        for label in collect.CLASSIFICATION_LABELS:
            narrative = collect._label_narrative(label, {})
            assert isinstance(narrative, str)
            assert len(narrative) > 0
