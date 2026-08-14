r"""Test the JSONL record detail that emit_jsonl_record.py produces for local_checks.

Fixture A: a failing check's emitted record — now carries `command`.
Fixture B (AC-3, the quoting trap): a command containing `"`, `'` and `\`;
           assert the emitted line is `json.loads`-parseable.
Fixture C (AC-4): a passing check carries no output tail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import emit_jsonl_record as ejr  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_record(
    slug: str,
    step: int | None,
    outcome: str,
    exit_code: int,
    failing_check: dict | None = None,
) -> dict:
    """Build a record using the actual module."""
    return ejr.build_record(slug, step, outcome, exit_code, failing_check)


# ── Fixture A: the new contract (command key present for failures) ───────────

class TestFailingCheckRecord:
    """A failing check's JSONL record now carries `command`."""

    def test_failing_record_has_command_key(self) -> None:
        """AC-1: the record carries the command that failed."""
        failing_check = {
            "command": "grep -q 'hello' file.txt",
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 1,
            "duration_sec": 0.001,
            "passed": False,
            "stdout_tail": "",
            "stderr_tail": "",
            "error": "",
        }
        rec = _build_record("some-slug", 0, "fail", 1, failing_check)
        assert "command" in rec, "Record lacks `command` — AC-1 not met"
        assert rec["command"] == "grep -q 'hello' file.txt"

    def test_failing_record_without_check_has_no_command(self) -> None:
        """If no failing check is available, command is absent."""
        rec = _build_record("some-slug", 0, "fail", 1, None)
        assert "command" not in rec

    def test_passing_record_has_no_command(self) -> None:
        """AC-4: passing checks don't carry command."""
        rec = _build_record("some-slug", 0, "pass", 0, None)
        assert "command" not in rec


# ── Fixture B: the quoting trap (AC-3) ──────────────────────────────────────

class TestQuotingTrap:
    r"""A command containing ", ' and \ must produce parseable JSON.

    This is the regression test for the quoting trap described in the sub-plan.
    The old shell script used hand-interpolated `echo`, which would produce
    malformed JSON for commands with double quotes.
    """

    # Real-world command that broke kira-cloudflare
    KIRA_GATE = 'grep -q \'cron: "30 10 * * *"\''

    # Adversarial: all three special chars in one command
    ADVERSARIAL = r'grep -P "\d+" file.txt && echo "it\'s done"'

    def _roundtrip(self, command: str) -> dict:
        """Build a record with `command` and verify it round-trips through JSON."""
        failing_check = {
            "command": command,
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 1,
            "duration_sec": 0.001,
            "passed": False,
            "stdout_tail": "",
            "stderr_tail": "",
            "error": "",
        }
        rec = _build_record("test-slug", 0, "fail", 1, failing_check)
        # Serialize and parse — the core assertion
        line = json.dumps(rec, ensure_ascii=False)
        return json.loads(line)

    def test_kira_gate_command_roundtrips(self) -> None:
        """The real kira-cloudflare gate command must survive JSON serialization."""
        rec = self._roundtrip(self.KIRA_GATE)
        assert rec["command"] == self.KIRA_GATE

    def test_adversarial_command_roundtrips(self) -> None:
        r"""A command with ", ' and \ must survive JSON serialization."""
        rec = self._roundtrip(self.ADVERSARIAL)
        assert rec["command"] == self.ADVERSARIAL

    def test_record_without_command_is_parseable(self) -> None:
        """The old format (no command) is still parseable — baseline check."""
        rec = _build_record("test-slug", 0, "fail", 1, None)
        line = json.dumps(rec, ensure_ascii=False)
        parsed = json.loads(line)
        assert parsed["outcome"] == "fail"
        assert parsed["exit_code"] == 1


# ── Fixture C: passing checks stay lean (AC-4) ──────────────────────────────

class TestPassingCheckLean:
    """A passing check carries no output tail."""

    def test_passing_record_has_no_stdout_tail(self) -> None:
        """Passing checks have no stdout_tail."""
        rec = _build_record("test-slug", 0, "pass", 0, None)
        assert "stdout_tail" not in rec

    def test_passing_record_has_no_stderr_tail(self) -> None:
        """Passing checks have no stderr_tail."""
        rec = _build_record("test-slug", 0, "pass", 0, None)
        assert "stderr_tail" not in rec

    def test_passing_record_with_command_still_has_no_tails(self) -> None:
        """AC-4: even with a command, passing checks must not carry tails."""
        # This guards against over-correction: if the fix adds tails to ALL
        # records, it violates AC-4.
        failing_check = {
            "command": "echo ok",
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 0,
            "duration_sec": 0.001,
            "passed": True,
            "stdout_tail": "ok\n",
            "stderr_tail": "",
            "error": "",
        }
        rec = _build_record("test-slug", 0, "pass", 0, failing_check)
        assert "stdout_tail" not in rec, "Passing check should not carry stdout_tail"
        assert "stderr_tail" not in rec, "Passing check should not carry stderr_tail"


# ── AC-2: bounded tails ─────────────────────────────────────────────────────

class TestBoundedTails:
    """AC-2: stdout_tail/stderr_tail are capped at 4KB."""

    def test_stdout_tail_capped_at_4kb(self) -> None:
        """A long stdout_tail is truncated to the last 4KB."""
        long_output = "x" * 10000
        failing_check = {
            "command": "failing-cmd",
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 1,
            "duration_sec": 0.001,
            "passed": False,
            "stdout_tail": long_output,
            "stderr_tail": "",
            "error": "",
        }
        rec = _build_record("test-slug", 0, "fail", 1, failing_check)
        assert len(rec["stdout_tail"]) == 4096
        assert rec["stdout_tail"].endswith("x" * 10)  # tail preserved

    def test_stderr_tail_capped_at_4kb(self) -> None:
        """A long stderr_tail is truncated to the last 4KB."""
        long_output = "y" * 10000
        failing_check = {
            "command": "failing-cmd",
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 1,
            "duration_sec": 0.001,
            "passed": False,
            "stdout_tail": "",
            "stderr_tail": long_output,
            "error": "",
        }
        rec = _build_record("test-slug", 0, "fail", 1, failing_check)
        assert len(rec["stderr_tail"]) == 4096
        assert rec["stderr_tail"].endswith("y" * 10)  # tail preserved

    def test_short_tails_pass_through(self) -> None:
        """Short tails are not truncated."""
        failing_check = {
            "command": "failing-cmd",
            "scope": "subplan",
            "timeout": 60,
            "exit_code": 1,
            "duration_sec": 0.001,
            "passed": False,
            "stdout_tail": "short output",
            "stderr_tail": "short error",
            "error": "",
        }
        rec = _build_record("test-slug", 0, "fail", 1, failing_check)
        assert rec["stdout_tail"] == "short output"
        assert rec["stderr_tail"] == "short error"
