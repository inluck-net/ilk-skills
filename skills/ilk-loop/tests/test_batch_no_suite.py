"""Tests for "a batch with no suite says so" — the coverage-regression fix.

AC-1: plan_lint --master reports a finding when a batch declares no broad-suite
      gate AND the project resolves to NotConfigured.
AC-2: no finding when the project declares a ship.suite.
AC-3: no finding when a sub-plan still declares a broad-suite gate.
AC-5: at batch end, a not_configured verdict is reported with config path.
AC-7: the real gh-resolve config is the fixture for AC-1 and AC-5.

These are RED-FIRST tests — the lint function and reporting do not exist yet.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GH_RESOLVE_CONFIG = FIXTURES / "gh-resolve" / ".ilk-launch.json"

# The function under test does not exist yet — import will fail.
# The tests import it inside each test body so pytest can collect and report
# them as failures rather than aborting at module-import time.
lint_batch_has_no_suite = None  # populated by _import_lint()


def _import_lint():
    """Import lint_batch_has_no_suite, or return None if it doesn't exist yet."""
    try:
        from plan_lint import lint_batch_has_no_suite as fn
        return fn
    except (ImportError, AttributeError):
        return None


# ── Fixture batches ─────────────────────────────────────────────────────

# A batch with NO broad-suite gate in any sub-plan (the gh-resolve shape).
BATCH_NO_BROAD_GATE = textwrap.dedent("""\
    ---
    master_plan: 2026-08-25-execution
    batch_date: 2026-08-25
    status: active
    total_tickets: 7
    ---

    # MASTER plan

    ## Sub-plan registry

    | # | Order | Slug | Items | Steps | Status |
    |---|---|---|---|---|---|
    | 1 | 1 | [2026-08-25-sp1.md](./2026-08-25-sp1.md) | fix A | 4 | shipped |
    | 2 | 2 | [2026-08-25-sp2.md](./2026-08-25-sp2.md) | fix B | 4 | shipped |
""")

# A sub-plan with NO broad-suite gate (scoped pytest only).
SUBPLAN_NO_BROAD_GATE = textwrap.dedent("""\
    ---
    plan: sp1
    status: shipped
    current_step: 4
    estimated_steps: 4
    ---
    # Sub-plan: fix A
    ### Step 0 — Test
    ```yaml
    local_checks:
      - command: python3 -m pytest skills/ilk-loop/tests/test_sp1.py -q --timeout=60 --timeout-method=signal
        timeout: 120
    ```
""")

# A sub-plan WITH a broad-suite gate (the old shape — no finding expected).
SUBPLAN_WITH_BROAD_GATE = textwrap.dedent("""\
    ---
    plan: sp-broad
    status: shipped
    current_step: 4
    estimated_steps: 4
    ---
    # Sub-plan: broad gate
    ### Step 0 — Test
    ```yaml
    local_checks:
      - command: python3 -m pytest -q --timeout=60 --timeout-method=signal
        timeout: 300
    ```
""")


# ── AC-1: no broad gates + NotConfigured → finding ──────────────────────


class TestAc1NoBroadGatesNotConfigured:
    """The gh-resolve shape: 0 broad gates, NotConfigured → finding."""

    def test_finding_present(self, tmp_path: Path) -> None:
        """AC-1: plan_lint --master reports a finding for a batch with no suite."""
        lint = _import_lint()
        assert lint is not None, (
            "lint_batch_has_no_suite does not exist yet — "
            "this is a red-first test. Implement it in plan_lint.py."
        )

        # Write the gh-resolve fixture as the project's .ilk-launch.json
        config_dir = tmp_path / ".ilk-launch.json"
        config_dir.write_text(GH_RESOLVE_CONFIG.read_text(encoding="utf-8"),
                              encoding="utf-8")

        # The lint needs: master text, list of sub-plan texts, project root.
        # Exact signature TBD — this test will drive it.
        findings = lint(
            master_text=BATCH_NO_BROAD_GATE,
            subplan_texts=[SUBPLAN_NO_BROAD_GATE],
            project_root=tmp_path,
        )
        assert len(findings) >= 1, (
            f"Expected at least one finding for a batch with no suite "
            f"and NotConfigured project. Got: {findings}"
        )
        # Must name the config path.
        assert ".ilk-launch.json" in findings[0], (
            f"Finding must name the config path. Got: {findings[0]}"
        )
        # Must state the consequence.
        assert "not_configured" in findings[0].lower() or "will not run" in findings[0].lower(), (
            f"Finding must state the consequence (not_configured / will not run). "
            f"Got: {findings[0]}"
        )


# ── AC-2: ship.suite declared → no finding ─────────────────────────────


class TestAc2ShipSuiteDeclared:
    """When the project has ship.suite, the lint must stay silent."""

    def test_no_finding_with_ship_suite(self, tmp_path: Path) -> None:
        """AC-2: ship.suite configured → no finding."""
        lint = _import_lint()
        assert lint is not None, (
            "lint_batch_has_no_suite does not exist yet — "
            "this is a red-first test. Implement it in plan_lint.py."
        )

        # Write a config WITH ship.suite.
        config = {
            "version": 1,
            "launch": {"command": "python3 run.py"},
            "worker": {"model": "sonnet"},
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "flags": ["--timeout=60", "--timeout-method=signal"],
                }
            },
        }
        config_path = tmp_path / ".ilk-launch.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        findings = lint(
            master_text=BATCH_NO_BROAD_GATE,
            subplan_texts=[SUBPLAN_NO_BROAD_GATE],
            project_root=tmp_path,
        )
        assert findings == [], (
            f"Expected no findings when ship.suite is declared. Got: {findings}"
        )


# ── AC-3: a broad gate present → no finding ────────────────────────────


class TestAc3BroadGatePresent:
    """When a sub-plan declares a broad gate, the old route covers it."""

    def test_no_finding_with_broad_gate(self, tmp_path: Path) -> None:
        """AC-3: a sub-plan with a broad-suite gate → no finding."""
        lint = _import_lint()
        assert lint is not None, (
            "lint_batch_has_no_suite does not exist yet — "
            "this is a red-first test. Implement it in plan_lint.py."
        )

        # Write the gh-resolve config (NotConfigured) — but the batch HAS
        # a broad gate, so coverage exists by the old route.
        config_dir = tmp_path / ".ilk-launch.json"
        config_dir.write_text(GH_RESOLVE_CONFIG.read_text(encoding="utf-8"),
                              encoding="utf-8")

        findings = lint(
            master_text=BATCH_NO_BROAD_GATE,
            subplan_texts=[SUBPLAN_WITH_BROAD_GATE],
            project_root=tmp_path,
        )
        assert findings == [], (
            f"Expected no findings when a sub-plan declares a broad gate. "
            f"Got: {findings}"
        )


# ── AC-5: batch-end not_configured verdict reported ────────────────────


class TestAc5BatchEndReport:
    """At batch end, a not_configured verdict names what did not run."""

    def test_not_configured_reported_with_config_path(self, tmp_path: Path) -> None:
        """AC-5: batch_gate output names the config path for not_configured."""
        # Import batch_gate — it already exists.
        from batch_gate import run_batch_gate

        # Write the gh-resolve fixture as the project's .ilk-launch.json
        config_path = tmp_path / ".ilk-launch.json"
        config_path.write_text(GH_RESOLVE_CONFIG.read_text(encoding="utf-8"),
                               encoding="utf-8")

        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir()

        rec = run_batch_gate(
            project_path=tmp_path,
            runtime_dir=runtime_dir,
        )
        assert rec is not None, "run_batch_gate returned None"
        assert rec.verdict == "not_configured", (
            f"Expected verdict 'not_configured', got '{rec.verdict}'"
        )
        # The current implementation records an empty invocation for
        # not_configured. AC-5 requires the config path to be surfaced.
        # This is the RED part — the invocation field is empty today.
        assert rec.invocation and ".ilk-launch" in rec.invocation, (
            f"AC-5: not_configured verdict must name the config path in "
            f"invocation. Got empty: '{rec.invocation}'"
        )


# ── AC-7: gh-resolve fixture exists ────────────────────────────────────


class TestAc7FixtureExists:
    """The real gh-resolve .ilk-launch.json is committed as a fixture."""

    def test_fixture_file_exists(self) -> None:
        """AC-7: gh-resolve fixture is present."""
        assert GH_RESOLVE_CONFIG.is_file(), (
            f"Fixture not found: {GH_RESOLVE_CONFIG}"
        )

    def test_fixture_has_three_keys_no_ship(self) -> None:
        """AC-7: fixture has 3 top-level keys and no 'ship' key."""
        data = json.loads(GH_RESOLVE_CONFIG.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) == 3, f"Expected 3 keys, got {len(data)}: {list(data)}"
        assert "ship" not in data, f"Fixture must NOT have 'ship' key: {list(data)}"
