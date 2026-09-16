"""Tests for lint_gate_placeholder_unresolved — the unsubstituted-token gate lint.

A rendered sub-plan is not a template. A `local_checks` command still reading
`<record path>` cannot run, and the way it fails is the expensive part: the
shell reports a missing file, which reads as *the artifact the gate checks is
absent* rather than *the command was never finished*.

Measured on gh-resolve 2026-09-15/16. The batch-verification template said
`verify_attribution.py <record path>` and asked the planner to resolve it. The
planner resolved `<skill-root>` and `<batch-slug>` in the same file and left
this one literal, on BOTH batches of that day. Each gate failed as "record not
found", ship_integrity read a red gate, and both sub-plans were reverted from
shipped to in-progress after their suites had run green. The suggested
diagnosis — re-run the suite — could never fix it.

This lint is the class, not the instance: v0.9.105 fixed that one token; any
future template token is the same defect under a new name.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


def _plan(command: str) -> str:
    return (
        "---\n"
        "plan: x\nstatus: pending\ncurrent_step: 0\nlocal_checks: []\n"
        "---\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        f'  - command: "{command}"\n'
        "    timeout: 120\n"
        "```\n"
    )


class TestCatchesTheRealDefect:
    def test_record_path_placeholder_is_flagged(self) -> None:
        f = plan_lint.lint_gate_placeholder_unresolved(
            _plan("python3 /x/verify_attribution.py <record path>"), "s")
        assert len(f) == 1
        assert "<record path>" in f[0]
        assert f[0].startswith("HARD ")

    def test_skill_root_placeholder_is_flagged(self) -> None:
        f = plan_lint.lint_gate_placeholder_unresolved(
            _plan("python3 <skill-root>/ilk-loop/scripts/verify_attribution.py --batch b"), "s")
        assert len(f) == 1 and "<skill-root>" in f[0]

    def test_the_resolved_form_is_clean(self) -> None:
        f = plan_lint.lint_gate_placeholder_unresolved(
            _plan("python3 /Users/x/.claude/skills/ilk-loop/scripts/"
                  "verify_attribution.py --batch batch-2026-09-15c"), "s")
        assert f == []

    def test_each_distinct_token_reported_once(self) -> None:
        f = plan_lint.lint_gate_placeholder_unresolved(
            _plan("cp <record path> <record path> && x <other thing>"), "s")
        assert len(f) == 2


class TestDoesNotFlagRealCommands:
    """Every false positive in a 678-sub-plan sweep was a bare single word."""

    def test_plist_xml_literal(self) -> None:
        assert plan_lint.lint_gate_placeholder_unresolved(
            _plan("grep -l '<key>PATH</key>' provisioning/launchd/x.plist"), "s") == []

    def test_plist_integer_literal(self) -> None:
        assert plan_lint.lint_gate_placeholder_unresolved(
            _plan("grep -q '<integer>5</integer>' x.plist.template"), "s") == []

    def test_commander_cli_signature(self) -> None:
        assert plan_lint.lint_gate_placeholder_unresolved(
            _plan("sed -n '/.command(\\\"update <id>\\\")/p' cli.ts | grep -q category"), "s") == []

    def test_shell_redirection_is_not_a_placeholder(self) -> None:
        assert plan_lint.lint_gate_placeholder_unresolved(
            _plan("sort < in.txt > out.txt 2>&1"), "s") == []

    def test_a_closed_xml_element_with_a_hyphen_is_not_a_placeholder(self) -> None:
        """Belt-and-braces: a hyphenated tag closed in the same command."""
        assert plan_lint.lint_gate_placeholder_unresolved(
            _plan("grep -q '<my-tag>v</my-tag>' f.xml"), "s") == []


class TestRegistered:
    def test_rule_is_in_all_checks(self) -> None:
        assert plan_lint.lint_gate_placeholder_unresolved in plan_lint.ALL_CHECKS
