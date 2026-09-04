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

from plan_lint import _extract_all_local_checks_commands, _is_whole_suite_command


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


# ── AC-3: real corpus has zero quote-only misclassifications ────────────

def _strip_surrounding_quotes(s: str) -> str:
    """Strip one layer of surrounding single or double quotes."""
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return s


def test_ac3_corpus_no_quote_only_misclassifications() -> None:
    """Scan the real plan corpus and assert no command's classification
    depends only on surrounding quotes.

    Before the fix (at ``5cb36f7``): 194 misclassified across 86 files.
    After: expected 0.

    Skips cleanly when ``~/.ilk-data/projects/`` holds no plan files.
    """
    import os

    ilk_data = Path(os.path.expanduser("~")) / ".ilk-data" / "projects"
    if not ilk_data.is_dir():
        pytest.skip("No ~/.ilk-data/projects/ directory on this host")

    plan_files = sorted(ilk_data.glob("*/plans/*.md"))
    if not plan_files:
        pytest.skip("No plan files found in ~/.ilk-data/projects/*/plans/")

    misclassified: list[str] = []
    total_cmds = 0

    for plan_file in plan_files:
        text = plan_file.read_text(encoding="utf-8", errors="replace")
        commands = _extract_all_local_checks_commands(text)
        for cmd in commands:
            total_cmds += 1
            stripped = _strip_surrounding_quotes(cmd)
            if stripped == cmd:
                continue  # no surrounding quotes — nothing to test
            as_is = _is_whole_suite_command(cmd)
            unquoted = _is_whole_suite_command(stripped)
            if as_is != unquoted:
                misclassified.append(
                    f"  {plan_file.name}: '{cmd[:60]}' "
                    f"as_is={'BROAD' if as_is else 'scoped'} "
                    f"unquoted={'BROAD' if unquoted else 'scoped'}"
                )

    assert not misclassified, (
        f"{len(misclassified)} commands across the corpus have quote-dependent "
        f"classification (of {total_cmds} total):\n" + "\n".join(misclassified[:20])
    )
