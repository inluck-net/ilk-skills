r"""Pin the JSONL record detail that run_ilk_loop_claude.sh emits for local_checks.

Fixture A: a failing check's emitted record — assert today it has no `command` key.
           `xfail(strict=True)` the assertion that it should.
Fixture B (AC-3, the quoting trap): a command containing `"`, `'` and `\`;
           assert the emitted line is `json.loads`-parseable. This must hold
           before and after — if it passes today only because `command` is
           absent, note that in Findings and keep the test as the guard on
           the new code.
Fixture C (AC-4): a passing check carries no output tail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _emit_jsonl_record(slug: str, step: int, outcome: str, exit_code: int) -> str:
    """Simulate run_ilk_loop_claude.sh:1022 — the hand-interpolated JSON."""
    return json.dumps({
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
    })


def _emit_jsonl_record_with_command(
    slug: str, step: int, outcome: str, exit_code: int, command: str
) -> str:
    """Simulate what the fix SHOULD produce — with `command` included."""
    return json.dumps({
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
        "command": command,
    })


# ── Fixture A: the current contract (no command key) ────────────────────────

class TestFailingCheckRecord:
    """A failing check's JSONL record today has no `command` key."""

    def test_failing_record_has_no_command_key(self) -> None:
        """The current shell script does not emit `command`."""
        line = _emit_jsonl_record("some-slug", 0, "fail", 1)
        rec = json.loads(line)
        assert "command" not in rec, (
            "Record unexpectedly has `command` — did the fix land already?"
        )

    @pytest.mark.xfail(
        reason="AC-1: a failing check's record SHOULD name the command",
        strict=True,
    )
    def test_failing_record_should_have_command_key(self) -> None:
        """AC-1: the record should carry the command that failed."""
        line = _emit_jsonl_record("some-slug", 0, "fail", 1)
        rec = json.loads(line)
        assert "command" in rec, "Record lacks `command` — AC-1 not met"


# ── Fixture B: the quoting trap (AC-3) ──────────────────────────────────────

class TestQuotingTrap:
    r"""A command containing ", ' and \ must produce parseable JSON.

    This is the regression test for the quoting trap described in the sub-plan.
    The current shell script uses hand-interpolated `echo`, which would produce
    malformed JSON for commands with double quotes. Since `command` is currently
    absent, the test passes trivially — but it must still pass after the fix.
    """

    # Real-world command that broke kira-cloudflare
    KIRA_GATE = 'grep -q \'cron: "30 10 * * *"\''

    # Adversarial: all three special chars in one command
    ADVERSARIAL = r'grep -P "\d+" file.txt && echo "it\'s done"'

    def _roundtrip(self, command: str) -> dict:
        """Emit a record with `command` and verify it round-trips."""
        line = _emit_jsonl_record_with_command("test-slug", 0, "fail", 1, command)
        # This is the core assertion: json.loads must not raise
        rec = json.loads(line)
        return rec

    def test_kira_gate_command_roundtrips(self) -> None:
        """The real kira-cloudflare gate command must survive JSON serialization."""
        rec = self._roundtrip(self.KIRA_GATE)
        assert rec["command"] == self.KIRA_GATE

    def test_adversarial_command_roundtrips(self) -> None:
        r"""A command with ", ' and \ must survive JSON serialization."""
        rec = self._roundtrip(self.ADVERSARIAL)
        assert rec["command"] == self.ADVERSARIAL

    def test_current_record_without_command_is_parseable(self) -> None:
        """The current format (no command) is parseable — baseline check."""
        line = _emit_jsonl_record("test-slug", 0, "fail", 1)
        rec = json.loads(line)
        assert rec["outcome"] == "fail"
        assert rec["exit_code"] == 1


# ── Fixture C: passing checks stay lean (AC-4) ──────────────────────────────

class TestPassingCheckLean:
    """A passing check carries no output tail."""

    def test_passing_record_has_no_stdout_tail(self) -> None:
        """The current format has no stdout_tail for passing checks."""
        line = _emit_jsonl_record("test-slug", 0, "pass", 0)
        rec = json.loads(line)
        assert "stdout_tail" not in rec

    def test_passing_record_has_no_stderr_tail(self) -> None:
        """The current format has no stderr_tail for passing checks."""
        line = _emit_jsonl_record("test-slug", 0, "pass", 0)
        rec = json.loads(line)
        assert "stderr_tail" not in rec

    def test_passing_record_with_command_still_has_no_tails(self) -> None:
        """AC-4: even after the fix, passing checks must not carry tails.

        This test exists to guard against over-correction: if the fix adds
        stdout_tail/stderr_tail to ALL records, it violates AC-4.
        """
        line = _emit_jsonl_record_with_command(
            "test-slug", 0, "pass", 0, "echo ok"
        )
        rec = json.loads(line)
        assert "stdout_tail" not in rec, "Passing check should not carry stdout_tail"
        assert "stderr_tail" not in rec, "Passing check should not carry stderr_tail"
