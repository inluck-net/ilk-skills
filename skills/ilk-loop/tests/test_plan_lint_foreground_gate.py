"""Tests for lint_foreground_whole_suite_gate — foreground gate beyond ceiling.

AC-1: a sub-plan declaring ``command: python3 -m pytest -q`` with
      ``timeout: 1200`` produces a finding.  The finding text contains the
      command, the declared timeout, and the ceiling value.
AC-2: a sub-plan declaring the same command with a timeout BELOW the ceiling
      produces NO finding from this lint.
AC-3: a scoped gate (``pytest tests/test_foo.py -q``) produces no finding at
      any timeout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_foreground_whole_suite_gate  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_subplan(command: str, timeout: int) -> str:
    """Return a minimal sub-plan with one per-step local_checks gate."""
    return (
        "---\n"
        "plan: test-slug\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n"
        "\n# Sub-plan: test\n"
        "\n### Step 0 — Do the thing\n"
        "```yaml\n"
        "local_checks:\n"
        f"  - command: {command}\n"
        f"    timeout: {timeout}\n"
        "```\n"
    )


# ── AC-1: bare pytest -q with timeout above ceiling → finding ────────────────


class TestAboveCeiling:
    """A whole-suite gate declared beyond the harness ceiling is a finding."""

    def test_bare_pytest_q_timeout_1200(self):
        """The exact gh-resolve shape: bare ``pytest -q``, ``timeout: 1200``."""
        text = _make_subplan("python3 -m pytest -q", 1200)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert len(findings) == 1, f"Expected 1 finding, got: {findings}"
        # Finding text must name the command, the declared timeout, and the ceiling.
        msg = findings[0]
        assert "python3 -m pytest -q" in msg, f"Missing command in: {msg}"
        assert "1200" in msg, f"Missing declared timeout in: {msg}"
        # The ceiling value (600) must appear.
        assert "600" in msg, f"Missing ceiling value in: {msg}"

    def test_pytest_q_timeout_900(self):
        """Timeout above ceiling with bare ``pytest -q``."""
        text = _make_subplan("pytest -q", 900)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert len(findings) == 1

    def test_pytest_directory_timeout_800(self):
        """Directory gate with timeout above ceiling."""
        text = _make_subplan("python3 -m pytest tests/ -q", 800)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert len(findings) == 1


# ── AC-2: same command, timeout below ceiling → no finding ───────────────────


class TestBelowCeiling:
    """A whole-suite gate with timeout below the ceiling is not flagged."""

    def test_bare_pytest_q_timeout_300(self):
        """Same command as AC-1 but timeout well below the 600s ceiling."""
        text = _make_subplan("python3 -m pytest -q", 300)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_bare_pytest_q_timeout_599(self):
        """Just below the ceiling — still no finding."""
        text = _make_subplan("python3 -m pytest -q", 599)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_bare_pytest_q_timeout_600(self):
        """Exactly at the ceiling — should fire (>= not just >)."""
        text = _make_subplan("python3 -m pytest -q", 600)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        # At the ceiling is still beyond (>=), so this should produce a finding.
        assert len(findings) == 1, f"Expected 1 finding at ceiling, got: {findings}"


# ── AC-3: scoped gate at any timeout → no finding ────────────────────────────


class TestScopedGate:
    """A scoped gate (single file) produces no finding at any timeout."""

    def test_scoped_pytest_timeout_1200(self):
        """Scoped file gate even with a very high timeout — no finding."""
        text = _make_subplan("python3 -m pytest tests/test_foo.py -q", 1200)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_scoped_pytest_timeout_300(self):
        """Scoped file gate with moderate timeout — no finding."""
        text = _make_subplan("python3 -m pytest tests/test_foo.py -q", 300)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_selector_scoped_timeout_1200(self):
        """A -k selector scopes the run — no finding."""
        text = _make_subplan("python3 -m pytest -k test_writeback -q", 1200)
        findings = lint_foreground_whole_suite_gate(text, "test-slug")
        assert findings == [], f"Expected no findings, got: {findings}"
