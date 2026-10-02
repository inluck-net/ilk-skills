"""Tests pinning: gates are read only from the Steps section.

Part of sub-plan ``gates-are-read-only-from-steps`` (MASTER-2026-10-02b).

AC-1: a ``### Step 1:`` note under Findings ⇒ step 1's real gate is still
      extracted.
AC-2: a Findings note with a yaml ``local_checks`` block ⇒ no extra gate.
AC-3: no ``## Steps`` section ⇒ unchanged legacy behaviour.

Red-first pins — AC-1 and AC-2 are xfail(strict) until the implementation
scopes the extractors to ``## Steps``.
"""
from __future__ import annotations

import textwrap

import pytest

from run_local_checks import collect_declared_local_checks, extract_step_local_checks
from ship_audit import count_authored_steps


# ── Fixtures ──────────────────────────────────────────────────────────────────

# A minimal 2-step sub-plan body with a real gate under Step 1 in ## Steps,
# plus a spurious ``### Step 1:`` note under ## Findings.
_BODY_FINDINGS_STEP_HEADING = textwrap.dedent("""\
    ## Steps

    ### Step 0 — no gate

    Do something.

    ### Step 1 — real gate

    ```yaml
    local_checks:
      - command: "python3 -m pytest tests/test_foo.py -q"
        timeout: 120
    ```

    Do something else.

    ## Findings

    ### Step 1: worker note

    This is just a prose note documenting what happened during step 1.
    It should NOT be parsed as a step heading for gate extraction.

    ## Reference reading

    - some-doc.md
""")


# Same structure but the Findings note includes a yaml block with local_checks.
_BODY_FINDINGS_YAML_BLOCK = textwrap.dedent("""\
    ## Steps

    ### Step 0 — no gate

    Do something.

    ### Step 1 — real gate

    ```yaml
    local_checks:
      - command: "python3 -m pytest tests/test_foo.py -q"
        timeout: 120
    ```

    Do something else.

    ## Findings

    ### Step 1: worker note

    Here is what I found:

    ```yaml
    local_checks:
      - command: "echo phantom"
        timeout: 30
    ```

    ## Reference reading

    - some-doc.md
""")


# A body with NO ## Steps section (legacy fallback).
_BODY_NO_STEPS_SECTION = textwrap.dedent("""\
    ## Overview

    ### Step 0 — first step

    ```yaml
    local_checks:
      - command: "echo hello"
        timeout: 60
    ```

    ### Step 1 — second step

    ```yaml
    local_checks:
      - command: "echo world"
        timeout: 60
    ```
""")


# ── AC-1: Findings heading does not hide the real gate ────────────────────────

def test_ac1_findings_heading_does_not_hide_real_gate() -> None:
    """A ``### Step 1:`` note under Findings must not interfere with
    extracting step 1's real gate from the Steps section."""
    # Step 1 has a real gate in ## Steps — it should still be found
    checks = extract_step_local_checks(_BODY_FINDINGS_STEP_HEADING, 1)
    assert len(checks) == 1, (
        f"Expected 1 gate for step 1, got {len(checks)} — "
        "the Findings heading may have interfered"
    )
    assert "pytest" in checks[0].get("command", "")


def test_ac1_real_gate_in_steps_section_is_found() -> None:
    """After the fix, the real gate under ## Steps > ### Step 1 is found."""
    checks = extract_step_local_checks(_BODY_FINDINGS_STEP_HEADING, 1)
    assert len(checks) == 1, (
        f"Expected 1 gate for step 1, got {len(checks)}"
    )
    assert "pytest" in checks[0].get("command", "")


# ── AC-2: Findings yaml block does not add a phantom gate ─────────────────────

def test_ac2_findings_yaml_block_no_phantom_gate() -> None:
    """A yaml ``local_checks`` block under Findings must NOT be parsed as
    an additional gate for step 1."""
    checks = extract_step_local_checks(_BODY_FINDINGS_YAML_BLOCK, 1)
    commands = [c.get("command", "") for c in checks]
    phantom = [c for c in commands if "phantom" in c]
    assert len(phantom) == 0, (
        f"Found phantom gate(s) from Findings: {phantom}"
    )


def test_ac2_only_real_gate_counted() -> None:
    """After the fix, only the gate from ## Steps is found."""
    checks = extract_step_local_checks(_BODY_FINDINGS_YAML_BLOCK, 1)
    commands = [c.get("command", "") for c in checks]
    phantom = [c for c in commands if "phantom" in c]
    assert len(phantom) == 0, (
        f"Found phantom gate(s) from Findings: {phantom}"
    )
    assert any("pytest" in c for c in commands)


# ── AC-2b: collect_declared_local_checks also scoped ──────────────────────────

def test_ac2b_collect_declared_not_polluted_by_findings() -> None:
    """``collect_declared_local_checks`` should not pick up gates from
    headings outside ## Steps."""
    fm_text = "local_checks: []"
    checks = collect_declared_local_checks(fm_text, _BODY_FINDINGS_YAML_BLOCK)
    commands = [c.get("command", "") for c in checks]
    phantom = [c for c in commands if "phantom" in c]
    assert len(phantom) == 0, (
        f"collect_declared_local_checks found phantom: {phantom}"
    )


# ── AC-3: no ## Steps section ⇒ legacy behaviour (whole body) ─────────────────

def test_ac3_no_steps_section_legacy_whole_body() -> None:
    """When there is no ## Steps heading, the whole body is scanned
    (legacy fallback)."""
    checks = extract_step_local_checks(_BODY_NO_STEPS_SECTION, 0)
    assert len(checks) == 1
    assert "echo hello" in checks[0].get("command", "")


def test_ac3_no_steps_section_collect_legacy() -> None:
    """``collect_declared_local_checks`` legacy fallback when no ## Steps."""
    fm_text = "local_checks: []"
    checks = collect_declared_local_checks(fm_text, _BODY_NO_STEPS_SECTION)
    assert len(checks) == 2


# ── count_authored_steps is already scoped (baseline) ─────────────────────────

def test_count_authored_steps_scoped_to_steps_section() -> None:
    """``count_authored_steps`` already scopes to ## Steps — baseline."""
    steps = count_authored_steps(_BODY_FINDINGS_STEP_HEADING)
    # Should see steps 0 and 1 from ## Steps, NOT the Findings heading
    assert steps == [0, 1], (
        f"count_authored_steps returned {steps}, expected [0, 1]"
    )