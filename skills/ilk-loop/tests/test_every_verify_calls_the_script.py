"""Pins for sub-plan: every verify calls the script (2026-09-29b).

AC-1: New HARD lint ``lint_verification_subplan_runs_the_script``.
A ``batch_verification: true`` sub-plan passes only if:
  - step 0's yaml gate invokes ``verification_record.py`` with ``--run-suite``;
  - step 1's yaml gate invokes ``verify_attribution.py``;
  - both steps are ``gate_first: true``.

AC-2: The template's step 0 and step 1 gates use
``--base-sha auto --master <MASTER .md path>``.

AC-3: ``commands/ilk-plan.md``'s verify paragraph says the same and names
``--base-sha auto``.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
_PLAN_LINT = _SCRIPTS / "plan_lint.py"
_TEMPLATE = _HERE.parent / "templates" / "batch-verification-subplan.md"
_COMMANDS = _HERE.parent.parent.parent / "commands" / "ilk-plan.md"

sys.path.insert(0, str(_SCRIPTS))


# ── Fixtures ─────────────────────────────────────────────────────────────

# 08b-shaped hand-run step 0: no gate, no verification_record.py.
HAND_RUN_STEP_0 = textwrap.dedent("""\
    ---
    plan: 08b-verify-state-ownership
    status: pending
    current_step: 0
    batch_verification: true
    ---

    # Sub-plan: verify state-ownership

    ### Step 0 — Run the full suite

    Run the full test suite and record the results:

    ```bash
    python3 -m pytest --timeout=60 --timeout-method=signal -q
    ```

    ### Step 1 — Fix attributed failures

    Fix every attributed failure until zero remain.
    """)

# Template-shaped gate-first pair: verification_record.py + verify_attribution.py.
TEMPLATE_SHAPED_PAIR = textwrap.dedent("""\
    ---
    plan: test-verify
    status: pending
    current_step: 0
    batch_verification: true
    ---

    # Sub-plan: test verify

    ### Step 0 — Run the full suite, record the result

    ```yaml
    gate_first: true
    local_checks:
      - command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py --project . --batch <batch-slug> --base-sha auto --master <MASTER> --run-suite --scope auto"
        timeout: 3660
    ```

    ### Step 1 — Fix every attributed failure

    ```yaml
    gate_first: true
    local_checks:
      - command: "python3 <skill-root>/ilk-loop/scripts/verify_attribution.py --batch <batch-slug> --remeasure-if-stale"
        timeout: 3660
    ```
    """)


# ── AC-1: lint_verification_subplan_runs_the_script ──────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="lint_verification_subplan_runs_the_script not implemented",
)
class TestAC1LintExists:
    """The HARD lint flags 08b-shaped hand-run step 0, passes template-shaped pair."""

    def test_function_defined(self):
        """The lint function exists in plan_lint.py."""
        import importlib
        mod = importlib.import_module("plan_lint")
        fn = getattr(mod, "lint_verification_subplan_runs_the_script", None)
        assert fn is not None, (
            "lint_verification_subplan_runs_the_script not found in plan_lint.py"
        )

    def test_flags_hand_run_step_0(self):
        """08b-shaped hand-run step 0 is flagged as HARD finding."""
        import importlib
        mod = importlib.import_module("plan_lint")
        fn = mod.lint_verification_subplan_runs_the_script
        findings = fn(HAND_RUN_STEP_0, "08b-verify-state-ownership")
        assert len(findings) == 1, (
            f"Expected 1 finding for hand-run step 0, got {len(findings)}: {findings}"
        )
        assert findings[0].startswith("HARD"), (
            f"Finding must be HARD, got: {findings[0]}"
        )

    def test_passes_template_shaped_pair(self):
        """Template-shaped gate-first pair passes with 0 findings."""
        import importlib
        mod = importlib.import_module("plan_lint")
        fn = mod.lint_verification_subplan_runs_the_script
        findings = fn(TEMPLATE_SHAPED_PAIR, "test-verify")
        assert findings == [], (
            f"Expected 0 findings for template-shaped pair, got {findings}"
        )

    def test_registered_in_all_checks(self):
        """The lint is registered in ALL_CHECKS."""
        import importlib
        mod = importlib.import_module("plan_lint")
        checks = mod.ALL_CHECKS
        fn = mod.lint_verification_subplan_runs_the_script
        assert fn in checks, (
            "lint_verification_subplan_runs_the_script not in ALL_CHECKS"
        )


# ── AC-2: template uses --base-sha auto --master ─────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="template does not yet use --base-sha auto --master",
)
def test_template_step0_uses_auto_base():
    """Step 0 gate invokes verification_record.py with --base-sha auto --master."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert "--base-sha auto --master" in text, (
        "Template step 0 must use --base-sha auto --master"
    )


def test_template_step0_has_run_suite():
    """Step 0 gate invokes verification_record.py with --run-suite."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert "--run-suite" in text, (
        "Template step 0 must use --run-suite"
    )


# ── AC-3: commands/ilk-plan.md names --base-sha auto ─────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="commands/ilk-plan.md does not yet mention --base-sha auto",
)
class TestACIlkPlanMentionsAutoBase:
    """commands/ilk-plan.md verify paragraph names --base-sha auto."""

    def test_verify_paragraph_mentions_auto_base(self):
        """The verify paragraph in ilk-plan.md mentions --base-sha auto."""
        text = _COMMANDS.read_text(encoding="utf-8")
        assert "--base-sha auto" in text, (
            "commands/ilk-plan.md verify paragraph must mention --base-sha auto"
        )