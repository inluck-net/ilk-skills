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

from plan_lint import (  # noqa: E402
    lint_wholesuite_gate_baseline,
    lint_posix_only_test_assertion,
    lint_network_tool_mock_only_gate,
)

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
  - command: python3 -m pytest -q
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
  - command: python3 -m pytest -q
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
  - command: python3 -m pytest tests/test_foo.py -q
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


# ── Lint B: POSIX-only test assertions ───────────────────────────────

# BAD: chmod/perm check with no uname guard.
_POSIX_ONLY_NO_GUARD = """\
---
plan: test-posix-no-guard
local_checks:
  - command: bash tests/test_perms.sh
    timeout: 30
---

# Sub-plan: test

Tests file permissions via stat -c %A and chmod 600.
"""

# GOOD: same check but guarded by uname.
_POSIX_ONLY_WITH_GUARD = """\
---
plan: test-posix-guarded
local_checks:
  - command: bash tests/test_perms.sh
    timeout: 30
---

# Sub-plan: test

On Linux: tests file permissions via stat -c %A.
uname guards the POSIX-only assertions.
"""


class TestPosixOnlyAssertion:
    def test_posix_no_guard_flagged(self):
        f = lint_posix_only_test_assertion(_POSIX_ONLY_NO_GUARD, "test-posix")
        assert len(f) == 1, f
        assert "POSIX" in f[0] or "posix" in f[0].lower()

    def test_posix_with_guard_not_flagged(self):
        assert lint_posix_only_test_assertion(_POSIX_ONLY_WITH_GUARD, "test-guarded") == []


# ── Lint C: network-tool mock-only gate ──────────────────────────────

# BAD: sub-plan mentions urllib/requests/_post, only gate mocks the network.
_MOCK_ONLY_GATE = """\
---
plan: test-mock-only
local_checks:
  - command: python3 -m pytest tests/test_draw.py -q
    timeout: 60
---

# Sub-plan: draw.py _load_minimax_token

Adds a new HTTP tool using urllib to post to api.minimax.chat.
Tests mock the _post function with injected fakes.
"""

# GOOD: has an integration/import-resolve check alongside mocks.
_INTEGRATION_GATE = """\
---
plan: test-integration-gate
local_checks:
  - command: python3 -c "from draw import _load_minimax_token; _load_minimax_token()"
    timeout: 30
  - command: python3 -m pytest tests/test_draw.py -q
    timeout: 60
---

# Sub-plan: draw.py _load_minimax_token

Adds a new HTTP tool using urllib to post to api.minimax.chat.
Import-resolve smoke + unit tests with mocks.
"""

# GOOD: not a network tool at all.
_NON_NETWORK_TOOL = """\
---
plan: test-non-network
local_checks:
  - command: python3 -m pytest tests/test_parser.py -q
    timeout: 60
---

# Sub-plan: parser refactor

Refactors the CSV parser. No network involvement.
"""


class TestNetworkToolMockOnlyGate:
    def test_mock_only_flagged(self):
        f = lint_network_tool_mock_only_gate(_MOCK_ONLY_GATE, "test-mock")
        assert len(f) == 1, f
        assert "mock" in f[0].lower()

    def test_with_integration_gate_not_flagged(self):
        assert lint_network_tool_mock_only_gate(_INTEGRATION_GATE, "test-integ") == []

    def test_non_network_tool_not_flagged(self):
        assert lint_network_tool_mock_only_gate(_NON_NETWORK_TOOL, "test-non-net") == []


# ── main() surfaces all three findings ───────────────────────────────

class TestMainEntrypoint:
    """Verify main() emits the three new finding classes via the CLI."""

    def test_main_surfaces_wholesuite_baseline(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _WHOLE_SUITE_BARE_PYTEST)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "baseline-green" in result.stdout

    def test_main_surfaces_posix_assertion(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _POSIX_ONLY_NO_GUARD)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "POSIX" in result.stdout or "posix" in result.stdout.lower()

    def test_main_surfaces_mock_only_gate(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _MOCK_ONLY_GATE)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "mock" in result.stdout.lower()

    def test_main_clean_on_good_subplan(self, tmp_path):
        result = _run_lint(tmp_path, "good.md", _SCOPED_PYTEST)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout
