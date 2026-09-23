"""Red-first pin: a ``gate_first: true`` step must declare a gate.

A ``gate_first`` step whose gate is already green is the fast path that costs
no model turn (``attempt_gate_first_fast_path``).  A ``gate_first`` step with
no ``local_checks`` has nothing to run — the marker promises a gate that does
the work, and an empty one is an authoring shape that must be impossible, not
merely discouraged.

The marker's exact spelling and placement is whatever sub-plan #1 implemented:
``run_ilk_loop_claude.sh:1519-1552``'s ``step_declares_gate_first`` matches
``gate_first: true|yes|1`` on its own line **inside the step's yaml fence**.
A marker in frontmatter is NOT read — the unit is the step.  These fixtures
put the marker there, beside its ``local_checks``.

"Has a gate" is decided by ``run_local_checks.parse_local_checks_block`` — the
same runtime parser the driver uses, and the oracle ``lint_gate_extractable``
already trusts.  Empty, absent, and ``local_checks: []`` all extract 0 commands
and are therefore all findings; a command with no ``timeout:`` is a real gate
(gh-resolve's ``handoff.py:422-499`` omits ``timeout:`` when it has no basis —
omission is honest, a default is a lie) and must stay silent.

**Red-first under a per-step gate.**  This file is step 0's own gate, and the
check does not exist yet.  Every pin that asserts the finding fires is marked
``xfail(strict=True)`` so the gate is green while the defect stands.  When
step 1 lands the lint, those pins XPASS, ``strict=True`` turns that into a
failure, and step 1's gate forces the marker off — the pin cannot silently
outlive its purpose.  Step 1 must DELETE the marker, not loosen it.

The negative controls carry no marker: they must pass today and after.  That
is deliberate — if they were xfail, a lint that wrongly fires on a healthy
gate would be reported as an expected failure and step 1 could ship broken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


def _subplan(step_yaml: str, *, name: str = "demo-subplan") -> str:
    """A minimal sub-plan whose step 0 fence carries *step_yaml* verbatim."""
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
        "\nA fixture for the gate_first lint.\n"
        "\n## Steps\n"
        "\n### Step 0 — Run the gate before the agent\n"
        "\n```yaml\n"
        f"{step_yaml}"
        "```\n"
        "\n### Step 1 — Fix what the gate names\n"
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


def _gate_first_findings(findings: list[str]) -> list[str]:
    return [f for f in findings if "gate_first" in f.lower()]


# ── Red-first pins: the finding must fire ───────────────────────────────────
#
# Both shapes the sub-plan names — empty AND absent — are findings.  Marked
# xfail(strict=True) because the check does not exist yet; step 1 removes the
# marker.


@pytest.mark.xfail(
    strict=True,
    reason=(
        "plan_lint has no gate_first-requires-a-gate check — "
        "step 1 of the-verification-template-declares-gate-first removes this marker"
    ),
)
def test_gate_first_with_absent_local_checks_is_a_hard_finding(tmp_path: Path) -> None:
    """A step declaring ``gate_first: true`` and no ``local_checks`` key at all."""
    findings = _lint(
        _subplan("gate_first: true\n"),
        tmp_path,
        name="2026-09-23-demo-absent-gate",
    )
    gf = _gate_first_findings(findings)
    assert gf, (
        "expected a HARD finding for a gate_first step with absent local_checks, "
        f"got {findings!r}"
    )
    assert any("HARD" in f for f in gf), f"finding must be HARD, got {gf!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "plan_lint has no gate_first-requires-a-gate check — "
        "step 1 of the-verification-template-declares-gate-first removes this marker"
    ),
)
def test_gate_first_with_empty_local_checks_is_a_hard_finding(tmp_path: Path) -> None:
    """A step declaring ``gate_first: true`` beside ``local_checks: []``."""
    findings = _lint(
        _subplan("gate_first: true\nlocal_checks: []\n"),
        tmp_path,
        name="2026-09-23-demo-empty-gate",
    )
    gf = _gate_first_findings(findings)
    assert gf, (
        "expected a HARD finding for a gate_first step with local_checks: [], "
        f"got {findings!r}"
    )
    assert any("HARD" in f for f in gf), f"finding must be HARD, got {gf!r}"


# ── Negative controls: must stay silent ─────────────────────────────────────
#
# No xfail here.  These pass today (the check does not exist, so nothing fires)
# and must still pass when step 1 lands — if the lint wrongly fires on a
# healthy gate, this test goes red and step 1 cannot ship it.


def test_gate_first_with_a_real_gate_is_not_a_finding(tmp_path: Path) -> None:
    """``gate_first: true`` beside a gate that actually names a command."""
    findings = _lint(
        _subplan(
            "gate_first: true\n"
            "local_checks:\n"
            '  - command: "python3 -c \'print(1)\'"\n'
            "    timeout: 30\n"
        ),
        tmp_path,
        name="2026-09-23-demo-real-gate",
    )
    gf = _gate_first_findings(findings)
    assert not gf, (
        "a gate_first step with a real local_checks command must not be "
        f"flagged, got {gf!r} (all findings: {findings!r})"
    )


def test_gate_first_with_a_timeout_less_gate_is_not_a_finding(tmp_path: Path) -> None:
    """A gate with a command but no ``timeout:`` is still a gate.

    gh-resolve's ``handoff.py:422-499`` generates gate timeouts from measured
    wallclock and omits ``timeout:`` entirely when it has no basis.  The lint
    keys on ``gate_first: true`` with absent/empty ``local_checks``, so a
    timeout-less gate must pass.  If the lint does fire here, fix the lint; do
    not ask the generator to invent a timeout.
    """
    findings = _lint(
        _subplan(
            "gate_first: true\n"
            "local_checks:\n"
            '  - command: "true"\n'
        ),
        tmp_path,
        name="2026-09-23-demo-timeout-less-gate",
    )
    gf = _gate_first_findings(findings)
    assert not gf, (
        "a timeout-less gate is a gate — the lint must not fire on it, "
        f"got {gf!r} (all findings: {findings!r})"
    )


# ── Positive control: the oracle is actually running ────────────────────────
#
# The negative controls above are only meaningful if ``lint_file`` is really
# running checks.  Without this, a harness that returned [] for every input
# would satisfy every other assertion in the file.


def test_lint_file_is_actually_running_checks(tmp_path: Path) -> None:
    """A duplicate frontmatter key is a known HARD finding — prove the oracle runs."""
    text = (
        "---\n"
        "plan: demo-subplan\n"
        "status: pending\n"
        "status: shipped\n"
        "---\n"
        "\n# Sub-plan: demo\n"
        "\nA fixture with a duplicate frontmatter key.\n"
        "\n## Steps\n"
        "\n### Step 0 — Do the thing\n"
        "\nDo it.\n"
    )
    findings = _lint(text, tmp_path, name="2026-09-23-demo-duplicate-key")
    assert any("HARD" in f and "duplicate" in f.lower() for f in findings), (
        "lint_file must report the duplicate frontmatter key (HARD) — if this "
        f"fails the oracle is not running and every 'no finding' assertion "
        f"above is vacuous. Got {findings!r}"
    )
