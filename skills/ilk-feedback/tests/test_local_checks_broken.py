"""Tests for local-checks-broken classification (gate couldn't execute).

Covers AC-1..AC-5 from sub-plan feedback-local-checks-broken step 0:

  AC-1 (classifier split): exit_code in {4,5,127} or "couldn't execute" stderr
        → local-checks-broken; exit_code 1 + assertions → local-checks-stuck
  AC-2 (diagnosis): postmortem narrative names the gate command as the issue
  AC-3 (candidate): local-checks-broken emits kind=toolchain candidate
  AC-4 (watchdog parity): tested in step 1 (test_label_action_totality.py)
  AC-5 (additive): existing feedback tests still pass

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_iters_with_failing_checks(
    check_results: list[dict],
    n_iters: int = 5,
) -> list[dict]:
    """Build synthetic JSONL iterations where the last N have failing local_checks.

    check_results: list of dicts with at least 'outcome', 'command', and
    optionally 'exit_code', 'stderr_tail' — one per failing check.
    """
    iters = []
    for i in range(1, n_iters + 1):
        is_failing = i > n_iters - len(check_results)
        check_idx = i - (n_iters - len(check_results)) - 1
        if is_failing and check_idx >= 0:
            lc = check_results[check_idx]
        else:
            lc = {"outcome": "pass", "command": "pytest -q"}
        iters.append({
            "run_id": "20260619-120000",
            "iteration": i,
            "exit_code": 0 if not is_failing else 1,
            "duration_sec": 120,
            "new_commits_total": 1,
            "local_checks": lc,
        })
    return iters


def _make_single_failing_iter(check: dict) -> list[dict]:
    """Single iteration with a failing local_checks result."""
    return [{
        "run_id": "20260619-120000",
        "iteration": 1,
        "exit_code": 1,
        "duration_sec": 120,
        "new_commits_total": 0,
        "stop_reason": "no-progress",
        "local_checks": check,
    }]


# ── AC-1: classifier split ──────────────────────────────────────────────────


class TestClassifierSplit:
    """AC-1: exit_code {4,5,127} or 'couldn't execute' stderr → local-checks-broken."""

    def test_exit_code_4_is_broken(self):
        """pytest exit 4 (collection error) → local-checks-broken."""
        check = {
            "outcome": "fail",
            "command": "pytest tools/xbar/tests/ -q",
            "exit_code": 4,
            "stderr_tail": "ERROR: found no tests in ...",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken", (
            f"Expected local-checks-broken for exit_code 4, got {label}"
        )

    def test_exit_code_5_is_broken(self):
        """pytest exit 5 (no tests collected) → local-checks-broken."""
        check = {
            "outcome": "fail",
            "command": "pytest -q",
            "exit_code": 5,
            "stderr_tail": "no tests ran",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"

    def test_exit_code_127_is_broken(self):
        """exit 127 (command not found) → local-checks-broken."""
        check = {
            "outcome": "error",
            "command": "nonexistent-tool --check",
            "exit_code": 127,
            "stderr_tail": "bash: nonexistent-tool: command not found",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"

    def test_stderr_no_such_file_is_broken(self):
        """exit 1 + 'No such file or directory' → local-checks-broken."""
        check = {
            "outcome": "fail",
            "command": "pytest tools/xbar/tests/ -q",
            "exit_code": 1,
            "stderr_tail": "pytest: error: file or directory not found: tools/xbar/tests/",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"

    def test_stderr_no_module_named_is_broken(self):
        """exit 1 + 'No module named' → local-checks-broken."""
        check = {
            "outcome": "fail",
            "command": "python -m pytest -q",
            "exit_code": 1,
            "stderr_tail": "ModuleNotFoundError: No module named 'my_missing_pkg'",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"

    def test_stderr_syntax_error_is_broken(self):
        """exit 1 + 'SyntaxError' → local-checks-broken."""
        check = {
            "outcome": "fail",
            "command": "python -c 'import bad_module'",
            "exit_code": 1,
            "stderr_tail": "SyntaxError: invalid syntax",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"

    def test_exit_code_1_assertions_is_stuck(self):
        """exit 1 + assertion output (not 'couldn't execute') → local-checks-stuck."""
        check = {
            "outcome": "fail",
            "command": "pytest -q",
            "exit_code": 1,
            "stderr_tail": "FAILED tests/test_foo.py::test_bar - assert 1 == 2",
        }
        iters = _make_iters_with_failing_checks([check] * 3)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-stuck", (
            f"Expected local-checks-stuck for exit_code 1 + assertions, got {label}"
        )

    def test_mixed_exit_codes_picks_broken(self):
        """If ANY failing check is broken, the whole classification is broken."""
        checks = [
            {"outcome": "fail", "command": "pytest -q", "exit_code": 1,
             "stderr_tail": "FAILED test_bar"},
            {"outcome": "fail", "command": "pytest tools/xbar/tests/ -q", "exit_code": 4,
             "stderr_tail": "ERROR: found no tests"},
            {"outcome": "fail", "command": "pytest -q", "exit_code": 1,
             "stderr_tail": "FAILED test_baz"},
        ]
        iters = _make_iters_with_failing_checks(checks)

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"


# ── AC-1b: sentinel path still works ────────────────────────────────────────


class TestSentinelPathUnchanged:
    """Sentinel-derived local-checks-broken for 1-iter unrunnable gate."""

    def test_sentinel_one_iter_exit_0_is_broken(self):
        """state=local_checks_failed, 1 iter, exit_code=0 → local-checks-broken.

        A single iteration whose sentinel records local_checks_failed with
        exit_code=0 is an unrunnable gate, not a stuck agent.  The L1
        sentinel path now distinguishes this from the ≥3-iter case.
        """
        iters = [{
            "run_id": "20260619-120000",
            "iteration": 1,
            "exit_code": 0,
            "duration_sec": 120,
            "new_commits_total": 1,
            "local_checks": {"outcome": "pass", "command": "pytest -q"},
        }]
        sentinel = {"state": "local_checks_failed", "run_id": "20260619-120000", "iteration": 1}

        with patch.object(collect, "read_sentinel", return_value=sentinel):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "local-checks-broken"


# ── AC-2: diagnosis narrative ───────────────────────────────────────────────


class TestDiagnosisNarrative:
    """AC-2: postmortem narrative names the gate as broken, not the code."""

    def test_broken_narrative_mentions_gate_command(self):
        """_label_narrative for local-checks-broken mentions gate COMMAND."""
        facts = {
            "fail_iters_in_window": 3,
            "pass_iters_in_window": 0,
            "window_size": 3,
            "iter_at_stop": 5,
        }
        narrative = collect._label_narrative("local-checks-broken", facts)

        assert "gate COMMAND" in narrative or "gate config" in narrative
        assert "NOT" in narrative or "not" in narrative.lower()

    def test_broken_narrative_mentions_no_auto_relaunch(self):
        """Narrative warns against blind auto-relaunch."""
        facts = {"fail_iters_in_window": 3, "window_size": 3, "pass_iters_in_window": 0}
        narrative = collect._label_narrative("local-checks-broken", facts)

        assert "auto-relaunch" in narrative.lower() or "blind" in narrative.lower()

    def test_stuck_narrative_unchanged(self):
        """local-checks-stuck narrative is unchanged (no regression)."""
        facts = {
            "fail_iters_in_window": 3,
            "pass_iters_in_window": 0,
            "window_size": 5,
            "iter_at_stop": 5,
        }
        narrative = collect._label_narrative("local-checks-stuck", facts)

        assert "acceptance criteria" in narrative.lower() or "AC" in narrative


# ── AC-3: upstream candidate emission ───────────────────────────────────────


class TestCandidateEmission:
    """AC-3: local-checks-broken emits kind=toolchain candidate."""

    def test_broken_emits_toolchain_candidate(self, tmp_path):
        """local-checks-broken emits kind='toolchain' candidate with
        command + exit_code evidence."""
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        os.environ["ILK_DATA_HOME"] = str(data_home)

        try:
            import importlib
            import improvement_backlog as mod
            importlib.reload(mod)

            facts = {
                "fail_iters_in_window": 3,
                "pass_iters_in_window": 0,
                "window_size": 3,
            }
            iters = [{
                "run_id": "20260619-120000",
                "iteration": 3,
                "local_checks": {
                    "outcome": "fail",
                    "command": "pytest tools/xbar/tests/ -q",
                    "exit_code": 4,
                    "stderr_tail": "ERROR: found no tests",
                },
            }]

            collect.maybe_emit_upstream_candidate(
                "local-checks-broken", facts, Path("/tmp/proj"), "20260619-120000", iters,
            )

            entries = mod.load(backlog_dir=data_home / "ilk-skills-improvements")
            assert len(entries) >= 1, "no candidate emitted for local-checks-broken"
            assert entries[0].kind == "toolchain", (
                f"Expected kind='toolchain', got '{entries[0].kind}'"
            )
            assert "failing_checks" in entries[0].evidence
            assert "exit_codes" in entries[0].evidence
        finally:
            os.environ.pop("ILK_DATA_HOME", None)

    def test_stuck_emits_toolkit_candidate(self, tmp_path):
        """local-checks-stuck still emits kind='toolkit' (no regression)."""
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        os.environ["ILK_DATA_HOME"] = str(data_home)

        try:
            import importlib
            import improvement_backlog as mod
            importlib.reload(mod)

            facts = {
                "fail_iters_in_window": 3,
                "pass_iters_in_window": 0,
                "window_size": 3,
            }
            iters = [{
                "run_id": "20260619-120000",
                "iteration": 3,
                "local_checks": {
                    "outcome": "fail",
                    "command": "pytest -q",
                    "exit_code": 1,
                    "stderr_tail": "FAILED test_bar",
                },
            }]

            collect.maybe_emit_upstream_candidate(
                "local-checks-stuck", facts, Path("/tmp/proj"), "20260619-120000", iters,
            )

            entries = mod.load(backlog_dir=data_home / "ilk-skills-improvements")
            assert len(entries) >= 1
            assert entries[0].kind == "toolkit", (
                f"Expected kind='toolkit' for stuck, got '{entries[0].kind}'"
            )
        finally:
            os.environ.pop("ILK_DATA_HOME", None)


# ── AC-5: additive — existing labels still work ─────────────────────────────


class TestAdditive:
    """AC-5: the new label is additive; existing classification paths unchanged."""

    def test_clean_success_unchanged(self):
        """clean-success classification is unchanged."""
        iters = [{
            "run_id": "20260619-120000",
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 3,
            "stop_reason": "already-shipped",
            "duration_sec": 120,
            "local_checks": {"outcome": "pass", "command": "pytest -q"},
        }]

        with patch.object(collect, "read_sentinel", return_value=None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, _ = collect.classify(iters, None, Path("/tmp/fake-project"))

        assert label == "clean-success"

    def test_label_in_classification_labels(self):
        """local-checks-broken is in CLASSIFICATION_LABELS."""
        assert "local-checks-broken" in collect.CLASSIFICATION_LABELS

    def test_label_in_toolkit_signal_labels(self):
        """local-checks-broken is in _TOOLKIT_SIGNAL_LABELS."""
        assert "local-checks-broken" in collect._TOOLKIT_SIGNAL_LABELS
