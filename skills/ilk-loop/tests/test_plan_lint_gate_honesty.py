#!/usr/bin/env python3
"""Tests for plan_lint gate-honesty checks (2026-06-28 drawing-worker run).

Lint A: whole-suite-gate baseline (backlog 5a5092ff) — step 0.
Lint B: POSIX-only test-assertion (backlog 602e2039) — step 1.
Lint C: network-tool mock-only gate (draw.py escaped-bug) — step 2.

Part of sub-plan plan-lint-gate-honesty.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_wholesuite_gate_baseline  # noqa: E402

_PLAN_LINT = SCRIPTS_DIR / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── Lint A: whole-suite-gate baseline ────────────────────────────────

# BAD: bare pytest (no file arg), no baseline-green note.
_WHOLE_SUITE_BARE_PYTEST = """\
---
plan: test-whole-suite-pytest
local_checks:
  - command: python -m pytest -q
    timeout: 180
---

# Sub-plan: test

A sub-plan that runs the full pytest suite with no baseline note.
"""

# BAD: bash tests/*.sh (glob on test dir), no baseline-green note.
_WHOLE_SUITE_BASH_TESTS = """\
---
plan: test-whole-suite-bash
local_checks:
  - command: bash tests/*.sh
    timeout: 60
---

# Sub-plan: test

Runs the full pre-existing test suite via shell glob.
"""

# GOOD: same pytest but with baseline-green note.
_WHOLE_SUITE_WITH_BASELINE = """\
---
plan: test-whole-suite-baseline
local_checks:
  - command: python -m pytest -q
    timeout: 180
---

# Sub-plan: test

Baseline-green on Windows 2026-06-28. Full pytest suite as gate.
"""

# GOOD: scoped pytest (file arg) → not a whole-suite gate.
_SCOPED_PYTEST = """\
---
plan: test-scoped-pytest
local_checks:
  - command: python -m pytest tests/test_foo.py -q
    timeout: 60
---

# Sub-plan: test

Scoped pytest run — only exercises the changed file.
"""


class TestWholeSuiteGateBaseline:
    def test_bare_pytest_flagged(self):
        f = lint_wholesuite_gate_baseline(_WHOLE_SUITE_BARE_PYTEST, "test-whole")
        assert len(f) == 1, f
        assert "baseline-green" in f[0]

    def test_bash_tests_flagged(self):
        f = lint_wholesuite_gate_baseline(_WHOLE_SUITE_BASH_TESTS, "test-bash")
        assert len(f) == 1, f

    def test_with_baseline_note_not_flagged(self):
        assert lint_wholesuite_gate_baseline(_WHOLE_SUITE_WITH_BASELINE, "test-baseline") == []

    def test_scoped_pytest_not_flagged(self):
        assert lint_wholesuite_gate_baseline(_SCOPED_PYTEST, "test-scoped") == []
