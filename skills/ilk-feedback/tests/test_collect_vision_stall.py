"""Regression tests for vision-stall vs true-unreachable classification.

Covers the lumenpath misdiagnosis (20260701-013349): chrome-devtools calls
succeeded, the model saved a PNG, then Read(@png) returned blank source,
zero commits — collect.py mislabeled it dependency-unreachable.

Tests use synthetic JSONL + per-iter log fixtures mirroring the existing
test_collect_* pattern.

AC-1: MCP calls succeed + blank image + no commits → NOT dependency-unreachable,
      label is model-incapability.
AC-2: True unreachable (MCP call errors / "not connected") → dependency-unreachable.
AC-3: Existing dependency-unreachable behavior preserved when MCP genuinely fails.
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
    run_id: str = "20260701-013349",
    iteration: int = 1,
    stop_reason: str | None = "no-progress",
    exit_code: int | None = 0,
    new_commits_total: int = 0,
    log: str | None = None,
) -> dict:
    """Build a synthetic JSONL iteration record."""
    rec: dict = {
        "run_id": run_id,
        "iteration": iteration,
        "stop_reason": stop_reason,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
    }
    if log is not None:
        rec["log"] = log
    return rec


# -- AC-1: Vision stall → model-incapability ---------------------------------


class TestVisionStallModelIncapability:
    """When MCP tool calls succeed but the model can't process the output,
    the classification should be model-incapability, not dependency-unreachable."""

    def test_dep_signal_with_mcp_success_and_blank_image(self, tmp_path):
        """Lumenpath shape: dependency signal present BUT chrome-devtools calls
        succeeded and Read returned blank image source → model-incapability.

        This is the actual misdiagnosis case: the log contained a dependency
        signal (which triggered DEPENDENCY_RE), but the MCP was actually
        working — the model just couldn't process the image output.
        """
        iter_log = tmp_path / "iter-1.log"
        iter_log.write_text(
            "[2026-07-01 01:34:00] mcp__chrome-devtools__take_screenshot called\n"
            "[2026-07-01 01:34:01] Tool ran without output or errors\n"
            "[2026-07-01 01:34:02] mcp__chrome-devtools__click called\n"
            "[2026-07-01 01:34:03] Tool ran without output or errors\n"
            "[2026-07-01 01:34:04] Read(screenshot.png) result: @{type=image; source=}\n"
            "[2026-07-01 01:34:05] blocked: dependency unreachable\n"
            "[2026-07-01 01:34:06] No commits made this iteration\n",
            encoding="utf-8",
        )

        iters = [_make_iter(log=str(iter_log))]
        label, facts = collect.classify(iters, None, tmp_path)

        assert label != "dependency-unreachable", (
            f"Vision stall should NOT be classified as dependency-unreachable, got: {label}"
        )
        assert label == "model-incapability", (
            f"Expected model-incapability, got: {label}"
        )

    def test_dep_signal_with_mcp_success_no_blank_image(self, tmp_path):
        """Dependency signal present, MCP calls succeed, no blank image →
        model-incapability (MCP IS reachable, so not dependency-unreachable)."""
        iter_log = tmp_path / "iter-1.log"
        iter_log.write_text(
            "[2026-07-01 01:34:00] mcp__chrome-devtools__take_screenshot called\n"
            "[2026-07-01 01:34:01] Tool ran without output or errors\n"
            "[2026-07-01 01:34:02] blocked: dependency unreachable\n",
            encoding="utf-8",
        )

        iters = [_make_iter(log=str(iter_log))]
        label, facts = collect.classify(iters, None, tmp_path)

        # MCP success → not dependency-unreachable
        assert label != "dependency-unreachable", (
            f"MCP success evidence should prevent dependency-unreachable, got: {label}"
        )
        assert label == "model-incapability", (
            f"Expected model-incapability when MCP calls succeed, got: {label}"
        )


# -- AC-2 + AC-3: True unreachable preserved ---------------------------------


class TestTrueUnreachablePreserved:
    """When the MCP genuinely fails (not connected, errors), the existing
    dependency-unreachable classification must be preserved."""

    def test_mcp_not_connected(self, tmp_path):
        """MCP not connected signal → dependency-unreachable (unchanged)."""
        iter_log = tmp_path / "iter-1.log"
        iter_log.write_text(
            "[2026-07-01 01:34:00] Error: figma MCP not connected\n"
            "[2026-07-01 01:34:01] Cannot proceed without figma\n",
            encoding="utf-8",
        )

        iters = [_make_iter(log=str(iter_log))]
        label, facts = collect.classify(iters, None, tmp_path)

        assert label == "dependency-unreachable", (
            f"True unreachable should remain dependency-unreachable, got: {label}"
        )
        assert facts.get("missing_dependency") is not None

    def test_env_prereq_failed(self, tmp_path):
        """env_prereq failed signal → dependency-unreachable (unchanged)."""
        iter_log = tmp_path / "iter-1.log"
        iter_log.write_text(
            "[2026-07-01 01:34:00] env_prereq: chrome-devtools unreachable\n"
            "[2026-07-01 01:34:01] blocked: dependency unreachable\n",
            encoding="utf-8",
        )

        iters = [_make_iter(log=str(iter_log))]
        label, facts = collect.classify(iters, None, tmp_path)

        assert label == "dependency-unreachable", (
            f"env_prereq failure should remain dependency-unreachable, got: {label}"
        )

    def test_no_mcp_calls_at_all(self, tmp_path):
        """DEPENDENCY_RE signal with no MCP calls → dependency-unreachable.
        (The dependency was never reached, so it's genuinely unreachable.)"""
        iter_log = tmp_path / "iter-1.log"
        iter_log.write_text(
            "[2026-07-01 01:34:00] blocked: dependency unreachable\n"
            "[2026-07-01 01:34:01] Cannot proceed\n",
            encoding="utf-8",
        )

        iters = [_make_iter(log=str(iter_log))]
        label, facts = collect.classify(iters, None, tmp_path)

        assert label == "dependency-unreachable", (
            f"No MCP calls + dependency signal → dependency-unreachable, got: {label}"
        )


# -- Label vocabulary ---------------------------------------------------------


class TestLabelInVocabulary:
    """model-incapability must be in CLASSIFICATION_LABELS for the totality gate."""

    def test_model_incapability_in_labels(self):
        assert "model-incapability" in collect.CLASSIFICATION_LABELS

    def test_all_labels_render_narrative(self):
        """Every label in CLASSIFICATION_LABELS renders without error."""
        for label in collect.CLASSIFICATION_LABELS:
            narrative = collect._label_narrative(label, {})
            assert isinstance(narrative, str)
            assert len(narrative) > 0
