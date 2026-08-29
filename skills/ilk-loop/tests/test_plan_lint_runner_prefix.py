"""Tests for runner-prefix handling in _is_whole_suite_command.

F6: a runner prefix hides a whole-suite gate.  These tests pin the correct
behaviour — stripping ``bunx``, ``npx``, ``pnpm dlx``, ``yarn dlx``, and the
``<pm> run <script>`` forms before classifying positional args.

Covers:
  AC-6  the five-row classification table from the sub-plan
  AC-7  each prefix form is stripped
  AC-8  script-form commands (bun run <script>) are BROAD
  bonus a runner prefix with no positional args (only flags) is BROAD
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import _is_whole_suite_command


# ── AC-6: the classification table ──────────────────────────────────────

def test_ac6_classification_table() -> None:
    """Each command from the F6 table classifies as annotated."""
    table = [
        # (command, expected_is_broad, reason)
        (
            "bunx vitest run -c tests/convex-tests/vitest.config.ts "
            "convex/__tests__/issueReports.test.ts",
            False, "single test file → scoped ✓"
        ),
        (
            "bunx vitest run --config=tests/convex-tests/vitest.config.ts",
            True, "no file arg, just config → BROAD ✓"
        ),
        (
            "bun run test:non-ui:convex",
            True, "script form → BROAD ✓"
        ),
        (
            "vitest run -c tests/convex-tests/vitest.config.ts",
            True, "bare vitest with config, no file → BROAD ✓"
        ),
        (
            "pnpm dlx vitest run",
            True, "pnpm dlx prefix, no file → BROAD ✓"
        ),
    ]
    failures = []
    for cmd, expected, reason in table:
        actual = _is_whole_suite_command(cmd)
        if actual is not expected:
            failures.append(
                f"  '{cmd}' — expected {'BROAD' if expected else 'scoped'} "
                f"({reason}), got {'BROAD' if actual else 'scoped'}"
            )
    assert not failures, "Classification table mismatches:\n" + "\n".join(failures)


# ── AC-7: each prefix form is stripped ──────────────────────────────────

def test_ac7_runner_prefixes_stripped() -> None:
    """Runner-prefixed bare vitest with no file arg is BROAD."""
    cmds = [
        "bunx vitest run",
        "npx vitest run",
        "pnpm dlx vitest run",
        "yarn dlx vitest run",
    ]
    failures = []
    for cmd in cmds:
        if _is_whole_suite_command(cmd) is not True:
            failures.append(f"  '{cmd}' should be BROAD (runner prefix must be stripped)")
    assert not failures, "Prefix stripping failures:\n" + "\n".join(failures)


# ── AC-8: script-form commands are BROAD ────────────────────────────────

def test_ac8_script_form_is_broad() -> None:
    """A script-form command (pm run <script>) is BROAD — the script name is
    not a path and cannot scope the run."""
    cmds = [
        "bun run test:non-ui:convex",
        "npm run test:e2e",
        "pnpm run test:integration",
        "yarn run test:unit",
    ]
    failures = []
    for cmd in cmds:
        if _is_whole_suite_command(cmd) is not True:
            failures.append(f"  '{cmd}' should be BROAD (script name is not a path)")
    assert not failures, "Script-form BROAD failures:\n" + "\n".join(failures)


# ── bonus: runner prefix with no positional args (only flags) is BROAD ──

def test_runner_prefix_with_only_flags_is_broad() -> None:
    """A runner-prefixed command with only flags (no file/dir arg) is BROAD.

    ``npx vitest run --reporter=verbose`` has no positional args after the
    runner prefix — the program name ``vitest`` should be stripped, leaving
    only flags.  Current behaviour: ``vitest`` lands in positional (not in
    ``_NON_PATH_TOKENS``), so the command reads as scoped.  After the fix:
    prefix stripped → ``vitest run --reporter=verbose`` → no positional args
    → BROAD.
    """
    cmd = "npx vitest run --reporter=verbose"
    assert _is_whole_suite_command(cmd) is True, (
        f"'{cmd}' should be BROAD (no file/dir arg after runner prefix)"
    )
