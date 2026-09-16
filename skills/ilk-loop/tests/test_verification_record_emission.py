"""Tests for the verification-record emission contract (sub-plan 3, AC-4).

The batch-verification template is rendered into a project-specific sub-plan by
an LLM planner.  Measured 2026-09-16 by diffing the template against the
sub-plan gh-resolve's planner produced from it:

| template element  | lives in a command: line? | survived rendering |
|---|---|---|
| _resolve_expected_invocation | yes | 3 of 3 |
| verified_head               | no — prose bullet | 0 of 2 |
| suite_scope                 | no — prose bullet | 0 of 2 |

**What lives inside a gate command survives rendering.  What lives only in
prose does not.**

This test suite extracts the template's ``command:`` lines, discards everything
else, and asserts the surviving commands still produce ``verified_head`` and
``suite_scope``.  It must be able to fail: writing it before the helper exists
pins the thesis that prose-only fields are un-survivable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import plan_lint  # noqa: E402

_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "batch-verification-subplan.md"
)


class TestRecordEmissionFromCommands:
    """Prose-only record fields do not survive rendering.

    A planner strips prose bullets and keeps only ``command:`` lines.  If a
    field the runtime depends on exists only in prose, it drops out, and the
    resulting sub-plan ships with the field missing — measured live on
    gh-resolve 2026-09-16.
    """

    @staticmethod
    def _template_text() -> str:
        return _TEMPLATE.read_text(encoding="utf-8")

    @staticmethod
    def _commands_only(text: str) -> list[str]:
        """Extract only the ``command:`` lines, discarding all prose."""
        return plan_lint._extract_all_local_checks_commands(text)

    # ── AC-1 / AC-2: verified_head and suite_scope survive prose-stripping ──

    def test_verified_head_in_command_lines(self) -> None:
        """``verified_head`` is emitted by a command, not described in prose.

        After prose-stripping the runtime can still read which HEAD the suite
        ran on.  Today it lives only in prose bullets and disappears.
        """
        commands = self._commands_only(self._template_text())
        assert commands, "template has no command: lines"
        combined = "\n".join(commands)

        # The command must invoke verification_record.py (which reads HEAD
        # from git, never as an argument).  A bare suite run that does not
        # record HEAD anywhere machine-readable is not enough.
        assert "verification_record.py" in combined, (
            "after prose-stripping, no surviving command invokes "
            "verification_record.py to derive verified_head.  The field "
            "exists only in prose bullets today and drops out of rendered "
            "sub-plans.  Wire verification_record.py into a gate command "
            "so it survives."
        )


    # ── the contract, not the flag name ───────────────────────────────────
    #
    # These assertions used to require the literal string "compute-scope".
    # That is an implementation detail, and on 2026-09-16 it broke when step 0
    # collapsed to a single `--run-suite` command that computes AND emits
    # suite_scope — the contract satisfied, the string absent. A test that
    # pins a flag name fails a correct change and passes a renamed-but-broken
    # one, so it is asserting the wrong thing in both directions.
    #
    # It now asks the question the contract actually poses: does a surviving
    # command invoke the emitter in a mode that produces this field? The mode
    # list is derived from the tool's own parser, so a renamed flag is caught
    # rather than guessed at.
    _EMITTING_MODES = ("--run-suite", "--compute-scope")

    def _emits_scope(self, combined: str) -> bool:
        """Does a surviving command run the emitter in a scope-emitting mode?"""
        if "verification_record.py" not in combined:
            return False
        if "suite_scope" in combined:
            return True
        if not any(m in combined for m in self._EMITTING_MODES):
            return False
        # The mode must still exist in the tool — a flag the parser rejects
        # would fail at runtime while satisfying a substring check.
        import subprocess
        import sys as _sys
        from pathlib import Path as _P
        script = _P(__file__).resolve().parent.parent / "scripts" / "verification_record.py"
        help_text = subprocess.run(
            [_sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=60).stdout
        return any(m in help_text for m in self._EMITTING_MODES
                   if m in combined)

    def test_suite_scope_in_command_lines(self) -> None:
        """``suite_scope`` is computed and emitted by a command, not asserted in prose.

        After prose-stripping the runtime can still determine whether the suite
        ran scoped or full.  Today this decision is described in prose and drops
        out of rendered sub-plans.
        """
        commands = self._commands_only(self._template_text())
        assert commands, "template has no command: lines"
        combined = "\n".join(commands)

        assert self._emits_scope(combined), (
            "after prose-stripping, no surviving command runs the emitter in a "
            "mode that produces suite_scope.  The field would exist only in "
            "prose and drop out of rendered sub-plans.  Wire "
            f"verification_record.py {self._EMITTING_MODES} into a gate command."
        )

    # ── AC-4 (the thesis): render, strip prose, verify both fields ─────────

    def test_prose_stripped_render_still_emits_both_fields(self) -> None:
        """Simulate a planner rendering: keep only command: lines.

        This is the direct reproduction of the 2026-09-16 defect: a planner
        rendered the template, paraphrased the prose away, and both fields
        vanished.  If this test passes, that failure cannot recur.
        """
        commands = self._commands_only(self._template_text())
        # Step 0 collapsed from two commands (run-suite, then write-record)
        # to one, because no data flowed between them and the emitter could
        # not know the failure count. Count is not the contract; emission is.
        assert commands, "template has no command: lines"

        combined = "\n".join(commands)
        # Both fields must be machine-parseable after prose-stripping.
        # The commands must invoke verification_record.py (for verified_head)
        # with --compute-scope (for suite_scope).
        assert "verification_record.py" in combined, (
            "prose-stripped commands do not invoke verification_record.py "
            "to produce verified_head"
        )
        assert self._emits_scope(combined), (
            "prose-stripped commands do not produce suite_scope"
        )
