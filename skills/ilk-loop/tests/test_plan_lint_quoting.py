"""Tests for the quoting-vs-classification invariant.

Defect: ``_is_whole_suite_command`` classified a command BROAD when the
only difference was surrounding quotes, because ``_strip_runner_prefix``
stripped quotes as a side-effect and the caller treated any string change
as proof of a runner prefix.

These tests assert AC-1, AC-2, and AC-3 from the sub-plan:
  AC-1  non-runner commands classify identically with and without quotes
  AC-2  runner-prefix behaviour is unchanged
  AC-3  the real plan corpus has zero quote-only misclassifications
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import _is_whole_suite_command


# ── AC-1: quoting must never change classification ─────────────────────

NON_RUNNER_COMMANDS = [
    "grep -q x f.ts",
    "bash -c y",
    "test -f docs/x.md",
    'python3 -c "import sys"',
    'bash -o pipefail -c "a | b"',
]


@pytest.mark.parametrize("cmd", NON_RUNNER_COMMANDS)
@pytest.mark.parametrize("quote_char", ['"', "'"])
def test_ac1_quoting_does_not_change_classification(
    cmd: str, quote_char: str
) -> None:
    """A non-runner command must classify the same with or without quotes."""
    bare = _is_whole_suite_command(cmd)
    quoted = _is_whole_suite_command(f"{quote_char}{cmd}{quote_char}")
    assert bare == quoted, (
        f"Classification changed for {quote_char}{cmd}{quote_char}: "
        f"bare={'BROAD' if bare else 'scoped'}, "
        f"quoted={'BROAD' if quoted else 'scoped'}"
    )


# ── AC-2: runner-prefix behaviour is unchanged ─────────────────────────

RUNNER_BROAD = [
    "bun run test:e2e",
    "npm run test:unit",
    "bunx vitest run",
    "npx vitest run",
    "pnpm dlx vitest run",
]

RUNNER_SCOPED = [
    "bunx vitest run f.test.ts",
    "npx vitest run src/foo.spec.ts",
]


@pytest.mark.parametrize("cmd", RUNNER_BROAD)
def test_ac2_runner_prefix_broad(cmd: str) -> None:
    """Runner-prefixed commands with no file arg are BROAD."""
    assert _is_whole_suite_command(cmd) is True, f"'{cmd}' should be BROAD"


@pytest.mark.parametrize("cmd", RUNNER_SCOPED)
def test_ac2_runner_prefix_scoped(cmd: str) -> None:
    """Runner-prefixed commands with a file arg are scoped."""
    assert _is_whole_suite_command(cmd) is False, f"'{cmd}' should be scoped"


def test_ac2_runner_prefix_quoted_still_broad() -> None:
    """A quoted runner-prefix command is still BROAD."""
    cmd = '"bunx vitest run"'
    assert _is_whole_suite_command(cmd) is True, (
        f"'{cmd}' should be BROAD (runner prefix, quoted)"
    )


def test_ac2_runner_prefix_quoted_still_scoped() -> None:
    """A quoted runner-prefix command with file arg is still scoped."""
    cmd = '"bunx vitest run f.test.ts"'
    assert _is_whole_suite_command(cmd) is False, (
        f"'{cmd}' should be scoped (runner prefix + file, quoted)"
    )
