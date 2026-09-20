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


# ── AC-3: the lint cannot certify a file it cannot read ──────────────
#
# Added 2026-09-20 after the FM-0003 remediation pass (586df97, dabf946)
# left 35 of 370 tracked .py files with `SyntaxError: keyword argument
# repeated` and the step-2 gate — `lint_subprocess_encoding.py --scan
# skills tools` — still exited 0.  The two facts are the same fact:
# `lint_source` swallowed SyntaxError and returned [], so every edit that
# broke a file also removed that file from the check meant to verify it.
# An empty answer that is indistinguishable from a clean answer is not a
# result; these two tests make the empty case unconstructible.

_UNPARSEABLE = """\
import subprocess
subprocess.run(["git"], capture_output=True, text=True, encoding="utf-8", text=True)
"""


def test_unparseable_file_is_a_violation() -> None:
    """AC-3a: a .py that does not parse must be reported, never reported clean."""
    result = _run_lint_on_snippet(_UNPARSEABLE)
    assert result.returncode != 0, (
        "the linter exited 0 on a file that does not parse. A file it cannot "
        "read is a file it cannot certify — returning 'no violations' here is "
        "what let 35 broken files through a green --scan gate on 2026-09-20.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "parse" in result.stdout.lower(), (
        "the violation must say the file did not parse, otherwise the operator "
        f"reads it as an encoding finding.\nstdout={result.stdout}"
    )


def test_every_tracked_python_file_parses() -> None:
    """AC-3b: every tracked .py in the toolkit compiles.

    The denominator is `git ls-files '*.py'` — the whole tracked set, not a
    sampled or scoped subset — so a zero here is a statement about the repo
    and not about where the test happened to look.
    """
    toolkit_root = Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=toolkit_root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=True,
    )
    files = [f for f in listing.stdout.splitlines() if f.strip()]
    assert files, "git ls-files returned no .py files — wrong cwd, not a clean repo"

    broken = []
    for rel in files:
        path = toolkit_root / rel
        try:
            compile(path.read_text(encoding="utf-8", errors="replace"), rel, "exec")
        except SyntaxError as exc:
            broken.append(f"{rel}:{exc.lineno}: {exc.msg}")

    assert not broken, (
        f"{len(broken)} of {len(files)} tracked .py files do not parse:\n  "
        + "\n  ".join(broken)
    )
