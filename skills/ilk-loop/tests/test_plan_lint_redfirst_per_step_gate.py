"""Red-first pin: a per-step gate on a red-first step 0 must not demand green.

A red-first step 0 writes a failing test.  If the same step's per-step
``local_checks`` runs that test file with plain exit-0 semantics, the step is
required to fail: the driver exits ``local_checks_failed``, ``collect.py``
classifies the run ``local-checks-stuck``, and the scheduler blacklists the
project — on correct, committed work.

Measured across this project's run log: 11 step-0 gate failures in 380
recorded gate outcomes; 9 of the 11 are exactly this shape.  The rule was
documented 2026-08-18 (``aa1e8fc``); all 9 are on or after that date.

Step 0 writes the pins as ``xfail(strict=True)`` so the gate can be green
while the lint does not exist.  Step 1 lands ``lint_redfirst_step0_per_step_gate_demands_green``
and DELETES those markers: with the fix in place they would XPASS and
``strict=True`` would fail the gate.

The negative controls never carry a marker.  That is deliberate — if they were
xfail, a lint that wrongly fires on a healthy gate would be reported as an
expected failure and the fix could ship broken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


def _subplan(
    step0_heading: str = "Do the thing",
    step0_prose: str = "",
    step0_gate: str = "",
    *,
    name: str = "demo-subplan",
) -> str:
    """A minimal sub-plan whose step 0 carries the given heading, prose and gate."""
    gate_block = ""
    if step0_gate:
        gate_block = f"\n```yaml\n{step0_gate}\n```\n"
    prose = f"\n{step0_prose}\n" if step0_prose else ""
    return (
        "---\n"
        f"plan: {name}\n"
        "status: pending\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-09-23\n"
        "verification_tier: loop-verified\n"
        "regression_for:\n"
        "depends_on: []\n"
        "data_prereqs: []\n"
        "env_prereqs: []\n"
        "local_checks: []\n"
        "scope_paths: []\n"
        "unit_test_targets: []\n"
        "e2e_test_targets: []\n"
        "must_add_tests: true\n"
        "ci_required: false\n"
        "ci_status_endpoint: github\n"
        "extra_dangerous_paths: []\n"
        "allow_dangerous_paths: []\n"
        "expected_entities:\n"
        "  migrations: []\n"
        "  api_endpoints: []\n"
        "  db_tables: []\n"
        "---\n"
        "\n# Sub-plan: demo\n"
        "\nA fixture for the red-first per-step gate lint.\n"
        "\n## Steps\n"
        f"\n### Step 0 — {step0_heading}\n"
        f"{prose}"
        f"{gate_block}"
        "\n### Step 1 — Fix the bug\n"
        "\nDo the fix.\n"
    )


def _lint(text: str, tmp_path: Path, *, name: str = "demo-subplan") -> list[str]:
    """Run the full plan_lint over *text* and return every finding.

    ``lint_file`` is the surface the planner actually invokes, so a finding
    that only exists on a directly-called helper would not protect a plan.
    """
    p = tmp_path / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return plan_lint.lint_file(p)


def _demands_green_findings(findings: list[str]) -> list[str]:
    return [f for f in findings if "demands green" in f]


# ── Pins: the finding must fire ─────────────────────────────────────────────
#
# Red-first in step 0 as xfail(strict=True); step 1 landed the lint and
# deleted the markers.


@pytest.mark.xfail(strict=True, reason="lint does not exist yet")
def test_redfirst_step0_plain_pytest_gate_is_a_hard_finding(tmp_path: Path) -> None:
    """A red-first step 0 whose per-step gate is ``pytest <file> -q``."""
    findings = _lint(
        _subplan(
            step0_heading="Pin the bug, red-first",
            step0_prose=(
                "Red-first: land a failing test that reproduces the bug. "
                "Expect 1 failed."
            ),
            step0_gate=(
                "local_checks:\n"
                '  - command: "python3 -m pytest tests/test_x.py -q"\n'
                "    timeout: 180\n"
            ),
        ),
        tmp_path,
        name="2026-09-23-redfirst-plain-pytest",
    )
    gf = _demands_green_findings(findings)
    assert len(gf) == 1, (
        "expected exactly one HARD finding for a red-first step 0 with a "
        f"plain pytest gate, got {len(gf)}: {gf!r} (all: {findings!r})"
    )
    assert any("HARD" in f for f in gf), f"finding must be HARD, got {gf!r}"


# ── Negative controls: must stay silent ─────────────────────────────────────
#
# No xfail here.  These pass today (the check does not exist, so nothing fires)
# and must still pass when step 1 lands — if the lint wrongly fires on a
# healthy gate, this test goes red and step 1 cannot ship it.


def test_xfail_strict_instruction_is_not_a_finding(tmp_path: Path) -> None:
    """A red-first step 0 that instructs xfail(strict=True) is exempt."""
    findings = _lint(
        _subplan(
            step0_heading="Pin the bug, red-first",
            step0_prose=(
                "Red-first: land a failing test that reproduces the bug. "
                "Write pins as `@pytest.mark.xfail(strict=True, reason=\"...\")`."
            ),
            step0_gate=(
                "local_checks:\n"
                '  - command: "python3 -m pytest tests/test_x.py -q"\n'
                "    timeout: 180\n"
            ),
        ),
        tmp_path,
        name="2026-09-23-redfirst-xfail-strict",
    )
    gf = _demands_green_findings(findings)
    assert not gf, (
        "a red-first step 0 that instructs xfail(strict=True) must not be "
        f"flagged, got {gf!r} (all: {findings!r})"
    )


def test_red_count_gate_is_not_a_finding(tmp_path: Path) -> None:
    """A red-first step 0 whose gate asserts a failure count is fine."""
    findings = _lint(
        _subplan(
            step0_heading="Pin the bug, red-first",
            step0_prose=(
                "Red-first: land a failing test. Expect 4 failed."
            ),
            step0_gate=(
                "local_checks:\n"
                '  - command: "python3 -m pytest tests/test_x.py -q 2>&1 | tail -5 | grep -q \'4 failed\'"\n'
                "    timeout: 180\n"
            ),
        ),
        tmp_path,
        name="2026-09-23-redfirst-red-count",
    )
    gf = _demands_green_findings(findings)
    assert not gf, (
        "a red-first step 0 with a red-count grep gate must not be flagged, "
        f"got {gf!r} (all: {findings!r})"
    )


def test_non_redfirst_step0_is_not_a_finding(tmp_path: Path) -> None:
    """A non-red-first step 0 with a plain pytest gate is fine."""
    findings = _lint(
        _subplan(
            step0_heading="Read the implementation",
            step0_prose="Read the existing code and document the contract.",
            step0_gate=(
                "local_checks:\n"
                '  - command: "python3 -m pytest tests/test_x.py -q"\n'
                "    timeout: 180\n"
            ),
        ),
        tmp_path,
        name="2026-09-23-non-redfirst",
    )
    gf = _demands_green_findings(findings)
    assert not gf, (
        "a non-red-first step 0 must not be flagged, "
        f"got {gf!r} (all: {findings!r})"
    )


# ── Registration ───────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="lint does not exist yet")
def test_check_is_registered_in_all_checks() -> None:
    """An unregistered lint never runs — the defect it guards stays open."""
    names = [c.__name__ for c in plan_lint.ALL_CHECKS]
    assert "lint_redfirst_step0_per_step_gate_demands_green" in names
