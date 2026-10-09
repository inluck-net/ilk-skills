"""Pins: /ilk-file names only flags the CLI accepts, and carries the rules.

Sub-plan: a-session-can-find-the-filing-command (step 0 pins; step 1 writes
the command file and removes the xfail markers).

AC-1: ``commands/ilk-file.md`` exists and contains
    ``improvement_backlog.py" add``.
AC-2: every ``--[a-z][a-z-]+`` token in the file is accepted by the CLI.
    The token set must be a superset of
    ``{--title, --gap, --kind, --proposed-fix, --file, --line, --run-id,
    --relation, --no-fix-yet}``.
AC-3: each of the five refusal reason codes (``title-empty``,
    ``title-placeholder``, ``title-too-short``, ``no-evidence-anchor``,
    ``no-proposed-fix``) appears in the file AND in
    ``improvement_backlog.py``'s source text.
AC-4: the file contains ``scheduler.log``,
    ``retro-2026-10-09-a-refused-train-read-as-a-train-never-offered``,
    ``/gh-resolve-file``, ``_inbox.md`` and ``overlaps=``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_COMMAND = _REPO / "commands" / "ilk-file.md"
_SCRIPT = _REPO / "skills" / "ilk-feedback" / "scripts" / "improvement_backlog.py"

_REASON_CODES = [
    "title-empty",
    "title-placeholder",
    "title-too-short",
    "no-evidence-anchor",
    "no-proposed-fix",
]

_REQUIRED_FLAGS = {
    "--title",
    "--gap",
    "--kind",
    "--proposed-fix",
    "--file",
    "--line",
    "--run-id",
    "--relation",
    "--no-fix-yet",
}


def _cli_accepted_flags() -> set[str]:
    """Run ``improvement_backlog.py add --help`` and parse the flags."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "add", "--help"],
        capture_output=True,
        text=True,
    )
    return set(re.findall(r"(--[a-z][a-z-]+)", proc.stdout))


# ── AC-1 ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="commands/ilk-file.md not written")
def test_ac1_command_exists_and_names_the_cli():
    assert _COMMAND.exists(), f"{_COMMAND} missing"
    text = _COMMAND.read_text(encoding="utf-8")
    assert 'improvement_backlog.py" add' in text


# ── AC-2 ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="commands/ilk-file.md not written")
def test_ac2_all_flags_in_command_are_accepted_by_cli():
    text = _COMMAND.read_text(encoding="utf-8")
    flags_in_doc = set(re.findall(r"(--[a-z][a-z-]+)", text))
    accepted = _cli_accepted_flags()
    unexpected = flags_in_doc - accepted
    assert not unexpected, f"flags in doc not accepted by CLI: {unexpected}"
    missing = _REQUIRED_FLAGS - flags_in_doc
    assert not missing, f"required flags missing from doc: {missing}"


# ── AC-3 ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="commands/ilk-file.md not written")
def test_ac3_reason_codes_in_doc_and_source():
    doc_text = _COMMAND.read_text(encoding="utf-8")
    source_text = _SCRIPT.read_text(encoding="utf-8")
    for code in _REASON_CODES:
        assert code in doc_text, f"{code!r} missing from command doc"
        assert code in source_text, f"{code!r} missing from improvement_backlog.py"


# ── AC-4 ─────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="commands/ilk-file.md not written")
def test_ac4_rules_and_references_present():
    text = _COMMAND.read_text(encoding="utf-8")
    required = [
        "scheduler.log",
        "retro-2026-10-09-a-refused-train-read-as-a-train-never-offered",
        "/gh-resolve-file",
        "_inbox.md",
        "overlaps=",
    ]
    for token in required:
        assert token in text, f"{token!r} missing from command doc"