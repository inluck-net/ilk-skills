#!/usr/bin/env python3
"""RED test — subprocess capture-encoding lint (FM-0003 guard).

Tests that lint_subprocess_encoding.py flags any subprocess.run / Popen
call that captures output without pinning an explicit encoding=.

Part of sub-plan subprocess-encoding-lint (step 0).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LINT = _HERE.parent / "skills" / "ilk-loop" / "scripts" / "lint_subprocess_encoding.py"


def _run_lint_on_snippet(snippet: str) -> subprocess.CompletedProcess:
    """Run the linter against a temp .py file containing *snippet*."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(snippet))
        path = f.name
    try:
        return subprocess.run(
            [sys.executable, str(_LINT), path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        os.unlink(path)


# ── AC-1 / AC-3: bad snippets — must be flagged ──────────────────────

BAD_CAPTURE_OUTPUT_TRUE = """\
import subprocess

def run_gate(cmd):
    subprocess.run(cmd, capture_output=True, text=True)
"""

BAD_STDOUT_PIPE = """\
import subprocess

def run_gate(cmd):
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
"""

BAD_POPEN_CAPTURE = """\
import subprocess

def run_gate(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate()
"""

BAD_MULTI_LINE_CALL = """\
import subprocess

def run_gate(cmd):
    subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
"""


@pytest.mark.parametrize(
    "snippet,label",
    [
        (BAD_CAPTURE_OUTPUT_TRUE, "capture_output=True + text=True, no encoding"),
        (BAD_STDOUT_PIPE, "stdout=PIPE, no encoding"),
        (BAD_POPEN_CAPTURE, "Popen + PIPE + text=True, no encoding"),
        (BAD_MULTI_LINE_CALL, "multi-line call, no encoding"),
    ],
    ids=["capture_output", "stdout_pipe", "popen", "multiline"],
)
def test_bad_snippet_flagged(snippet: str, label: str) -> None:
    """AC-1 / AC-3: a capture call without encoding= must be flagged."""
    result = _run_lint_on_snippet(snippet)
    assert result.returncode != 0, (
        f"Expected violation for: {label}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# ── AC-1: good snippets — must NOT be flagged ────────────────────────

GOOD_EXPLICIT_ENCODING = """\
import subprocess

def run_gate(cmd):
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
"""

GOOD_NO_CAPTURE = """\
import subprocess

def run_gate(cmd):
    subprocess.run(cmd, check=True)
"""

GOOD_POPEN_WITH_ENCODING = """\
import subprocess

def run_gate(cmd):
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    out, _ = p.communicate()
"""


@pytest.mark.parametrize(
    "snippet,label",
    [
        (GOOD_EXPLICIT_ENCODING, "capture_output + text + encoding=utf-8"),
        (GOOD_NO_CAPTURE, "no output capture at all"),
        (GOOD_POPEN_WITH_ENCODING, "Popen + encoding=utf-8"),
    ],
    ids=["explicit_encoding", "no_capture", "popen_with_encoding"],
)
def test_good_snippet_not_flagged(snippet: str, label: str) -> None:
    """AC-1: a well-formed call must not be flagged."""
    result = _run_lint_on_snippet(snippet)
    assert result.returncode == 0, (
        f"Expected clean pass for: {label}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# ── AC-2: toolkit-wide scan — zero violations ────────────────────────

def test_toolkit_scan_clean() -> None:
    """AC-2: scanning skills/**/*.py + tools/**/*.py reports 0 violations."""
    toolkit_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(_LINT), "--scan",
         str(toolkit_root / "skills"),
         str(toolkit_root / "tools")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Toolkit scan found violations (expected 0).\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
