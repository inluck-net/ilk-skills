"""Tests for lint_unbounded_broad_suite — unbounded project detection.

AC-3: plan_lint grows a finding when a sub-plan declares a broad-suite
local_check and the project has neither ship.suite nor a pytest timeout bound.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plan_lint import lint_unbounded_broad_suite


def _make_subplan_text(local_checks_block: str) -> str:
    """Return a minimal sub-plan with the given local_checks yaml block."""
    return textwrap.dedent(f"""\
        ---
        plan: test-slug
        status: in-progress
        current_step: 0
        estimated_steps: 1
        ---
        # Test sub-plan
        ### Step 0 — Test
        ```yaml
        {local_checks_block}
        ```
    """)


# ── No finding when bound is present ──────────────────────────────────────


class TestBoundPresent:
    """No finding when the project has a timeout bound."""

    def test_pytest_ini_has_timeout(self, tmp_path: Path) -> None:
        """pytest.ini with --timeout in addopts → no finding."""
        pytest_ini = tmp_path / "pytest.ini"
        pytest_ini.write_text("[pytest]\naddopts = --timeout=60 --timeout-method=signal\n")
        # Ship config not needed — timeout alone suffices if no broad gate
        # But with a broad gate, we need at least one of the two.
        # Actually the lint fires when BOTH are missing. Let me re-check...
        # The lint fires when the project has neither ship.suite nor pytest timeout.
        # If pytest timeout is present, no finding even without ship.suite.
        text = _make_subplan_text(
            "local_checks:\n"
            "  - command: python3 -m pytest -q\n"
            "    timeout: 120"
        )
        import plan_lint
        old_root = plan_lint._PROJECT_ROOT
        try:
            plan_lint._PROJECT_ROOT = tmp_path
            findings = lint_unbounded_broad_suite(text, "test-slug")
        finally:
            plan_lint._PROJECT_ROOT = old_root
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_ship_suite_present(self, tmp_path: Path) -> None:
        """ship.suite configured → no finding even without pytest timeout."""
        launch = tmp_path / ".ilk-launch.json"
        launch.write_text('{"ship": {"suite": {"command": "python3 -m pytest", "flags": ["--timeout=60"]}}}')
        text = _make_subplan_text(
            "local_checks:\n"
            "  - command: python3 -m pytest -q\n"
            "    timeout: 120"
        )
        import plan_lint
        old_root = plan_lint._PROJECT_ROOT
        try:
            plan_lint._PROJECT_ROOT = tmp_path
            findings = lint_unbounded_broad_suite(text, "test-slug")
        finally:
            plan_lint._PROJECT_ROOT = old_root
        assert findings == [], f"Expected no findings, got: {findings}"


# ── Finding when neither is present ───────────────────────────────────────


class TestNeitherPresent:
    """Finding fires when the project has no bound and no ship.suite."""

    def test_neither_bound_nor_suite(self, tmp_path: Path) -> None:
        """No pytest timeout and no ship.suite → finding."""
        text = _make_subplan_text(
            "local_checks:\n"
            "  - command: python3 -m pytest -q\n"
            "    timeout: 120"
        )
        import plan_lint
        old_root = plan_lint._PROJECT_ROOT
        try:
            plan_lint._PROJECT_ROOT = tmp_path
            findings = lint_unbounded_broad_suite(text, "test-slug")
        finally:
            plan_lint._PROJECT_ROOT = old_root
        assert len(findings) == 1
        assert "unbounded" in findings[0]
        assert "test-slug" in findings[0]
        # Must name which is missing and the path checked
        assert "no ship.suite" in findings[0]
        assert "no --timeout" in findings[0]


# ── No finding when gate is scoped (not broad) ────────────────────────────


class TestScopedGate:
    """No finding when the gate is scoped to specific files."""

    def test_scoped_pytest_command(self, tmp_path: Path) -> None:
        """pytest with a file argument → scoped, no finding."""
        text = _make_subplan_text(
            "local_checks:\n"
            "  - command: python3 -m pytest tests/test_foo.py -q\n"
            "    timeout: 120"
        )
        import plan_lint
        old_root = plan_lint._PROJECT_ROOT
        try:
            plan_lint._PROJECT_ROOT = tmp_path
            findings = lint_unbounded_broad_suite(text, "test-slug")
        finally:
            plan_lint._PROJECT_ROOT = old_root
        assert findings == [], f"Expected no findings, got: {findings}"


# ── No finding when no broad gate at all ───────────────────────────────────


class TestNoBroadGate:
    """No finding when no broad-suite command exists."""

    def test_no_local_checks(self, tmp_path: Path) -> None:
        """No local_checks at all → no finding."""
        text = textwrap.dedent("""\
            ---
            plan: test-slug
            status: in-progress
            current_step: 0
            estimated_steps: 1
            ---
            # Test sub-plan
            ### Step 0 — Test
            Do the work.
        """)
        import plan_lint
        old_root = plan_lint._PROJECT_ROOT
        try:
            plan_lint._PROJECT_ROOT = tmp_path
            findings = lint_unbounded_broad_suite(text, "test-slug")
        finally:
            plan_lint._PROJECT_ROOT = old_root
        assert findings == []
