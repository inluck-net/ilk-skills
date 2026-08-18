"""Test that emit_jsonl_record.py records command for ALL outcomes.

AC-1: A passing gate's command is recorded in the JSONL.
AC-2: Readers tolerate the absent field in historical records (34 existing
      passing records have no command — they are not rewritten).

The old behavior: command was only emitted for fail/error outcomes (line 42
of emit_jsonl_record.py). A passing gate was indistinguishable from a gate
that never ran — both showed no command. This made the "already-run
complement" uncomputable: you cannot subtract what you cannot see.

The fix: always include the command when data.results[0] provides it.
The 4KB-capped stdout_tail/stderr_tail/error fields remain fail/error only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import emit_jsonl_record as ejr  # noqa: E402


# ── AC-1: command recorded for every outcome ────────────────────────────────

class TestPassOutcomeRecordsCommand:
    """A passing gate records its command so the complement is computable."""

    def test_pass_with_data_has_command(self) -> None:
        """AC-1: pass outcome + data.results[0].command → record has command."""
        data = {
            "results": [
                {"command": "pytest skills/ilk-loop/tests/ -q", "passed": True, "exit_code": 0},
            ]
        }
        rec = ejr.build_record("test-slug", 0, "pass", 0, None, data=data)
        assert rec.get("command") == "pytest skills/ilk-loop/tests/ -q"

    def test_pass_without_data_has_no_command(self) -> None:
        """Back-compat: pass outcome without data dict → no command (tolerated)."""
        rec = ejr.build_record("test-slug", 0, "pass", 0, None)
        assert "command" not in rec

    def test_pass_with_empty_results_has_no_command(self) -> None:
        """Edge case: data with empty results list → no command."""
        data = {"results": []}
        rec = ejr.build_record("test-slug", 0, "pass", 0, None, data=data)
        assert "command" not in rec

    def test_fail_still_records_command_from_failing_check(self) -> None:
        """No regression: fail outcome still records command from failing_check."""
        failing_check = {"command": "grep -q 'expected' file.txt", "passed": False}
        rec = ejr.build_record("test-slug", 0, "fail", 1, failing_check)
        assert rec.get("command") == "grep -q 'expected' file.txt"

    def test_error_still_records_command(self) -> None:
        """No regression: error outcome records command."""
        failing_check = {"command": "pytest --bad-flag", "passed": False}
        rec = ejr.build_record("test-slug", 0, "error", 2, failing_check)
        assert rec.get("command") == "pytest --bad-flag"

    def test_inconclusive_with_data_has_command(self) -> None:
        """Inconclusive (gtimeout exit 124) also records command when data is present."""
        data = {
            "results": [
                {"command": "pytest skills/ilk-loop/tests/ -q --timeout=180", "passed": False, "exit_code": 124},
            ]
        }
        rec = ejr.build_record("test-slug", 0, "inconclusive", 124, None, data=data)
        assert rec.get("command") == "pytest skills/ilk-loop/tests/ -q --timeout=180"


# ── AC-2: readers tolerate absent command in historical records ─────────────

class TestAbsentFieldTolerance:
    """Historical passing records (34 of them) have no command field.

    Readers must tolerate this — .get("command", "") returns "".
    This test verifies the contract by simulating what a reader does.
    """

    def test_historical_pass_record_parses(self) -> None:
        """A record matching the old format (no command) still parses."""
        old_format = {"slug": "test-slug", "step": 0, "outcome": "pass", "exit_code": 0}
        line = json.dumps(old_format)
        parsed = json.loads(line)
        # Reader pattern from collect.py:1367
        cmd = parsed.get("command", "")
        assert cmd == ""

    def test_new_pass_record_with_command_parses(self) -> None:
        """A record with the new command field also parses."""
        new_format = {
            "slug": "test-slug",
            "step": 0,
            "outcome": "pass",
            "exit_code": 0,
            "command": "pytest skills/ilk-loop/tests/ -q",
        }
        line = json.dumps(new_format)
        parsed = json.loads(line)
        cmd = parsed.get("command", "")
        assert cmd == "pytest skills/ilk-loop/tests/ -q"

    def test_mixed_historical_and_new_records_readable(self) -> None:
        """A log with both old (no command) and new (with command) records is readable."""
        records = [
            {"slug": "a", "step": 0, "outcome": "pass", "exit_code": 0},  # old
            {"slug": "b", "step": 0, "outcome": "pass", "exit_code": 0, "command": "exit 0"},  # new
            {"slug": "c", "step": 0, "outcome": "fail", "exit_code": 1, "command": "grep x"},  # old fail
        ]
        commands = []
        for rec in records:
            cmd = rec.get("command", "")
            if cmd:
                commands.append(cmd)
        assert commands == ["exit 0", "grep x"]
