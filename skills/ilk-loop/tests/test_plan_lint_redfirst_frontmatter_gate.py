"""A red-first step 0 must not sit under a frontmatter gate that covers its red.

Frontmatter (subplan-scope) ``local_checks`` run at EVERY step, step 0 included.
§8 separately recommends the red-first step-0 pattern — a step whose purpose is
to LAND failing tests. Pair the two so the gate covers those tests and the gate
is red on iteration 1 by construction, before any fix exists.

Consequence measured 2026-08-20: six sub-plans shipped with that pairing, all
six passed plan_lint clean, and the resulting red gate drove the bash runner's
ship-integrity pass to rewrite ``status: shipped`` -> ``in-progress`` on 69 of
150 sub-plan files belonging to prior batches.

The pairing alone is NOT the finding — see ``test_green_scoped_frontmatter_gate_
is_not_flagged``, which pins the legitimate shape this lint must stay silent on.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402


def _subplan(*, fm_checks: list[str], step0_body: str,
             extra_steps: str = "") -> str:
    checks = "\n".join(
        f"  - command: {c}\n    timeout: 300" for c in fm_checks
    ) if fm_checks else "local_checks: []"
    fm_block = f"local_checks:\n{checks}" if fm_checks else checks
    return (
        "---\n"
        "plan: demo-subplan\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-08-20\n"
        f"{fm_block}\n"
        "scope_paths:\n"
        '  - "skills/ilk-loop/scripts/plan_lint.py"\n'
        "---\n"
        "\n# demo\n"
        "\n## Steps\n"
        f"\n### Step 0 — {step0_body}\n"
        f"{extra_steps}\n"
        "\n### Step 1 — Make it green\n"
        "\nDo the fix.\n"
    )


def _run(text: str) -> list[str]:
    return plan_lint.lint_redfirst_step0_under_frontmatter_gate(text, "demo-subplan")


# ── The flagged shapes ──────────────────────────────────────────────────────

def test_wholesuite_frontmatter_gate_over_redfirst_step0_is_flagged():
    """Arm (a): a directory-scoped run necessarily collects step 0's red."""
    text = _subplan(
        fm_checks=["cd \"$ILK_REPO_ROOT\" && python3 -m pytest skills/ilk-loop/tests/ -q"],
        step0_body=(
            "Pin the defect with a red test\n\n"
            "Red-first: land a failing test that reproduces the bug. "
            "Expect 3 failed, 0 passed."
        ),
    )
    findings = _run(text)
    assert findings, "a whole-suite frontmatter gate over a red-first step 0 must be flagged"
    assert "HARD FINDING" in findings[0]
    assert "EVERY step" in findings[0]


def test_frontmatter_gate_naming_step0s_own_test_file_is_flagged():
    """Arm (b): the gate runs the exact file step 0 makes red."""
    text = _subplan(
        fm_checks=[
            "python3 -m pytest skills/ilk-loop/tests/test_new_repro.py -q"
        ],
        step0_body=(
            "Record the failure\n\n"
            "Add `skills/ilk-loop/tests/test_new_repro.py` asserting the broken "
            "behaviour. This step must fail: 2 failed."
        ),
    )
    findings = _run(text)
    assert findings, "a frontmatter gate on step 0's own red file must be flagged"
    assert "test_new_repro.py" in findings[0]
    assert "HARD FINDING" in findings[0]


# ── The shapes that must stay silent (false-positive guards) ────────────────

def test_green_scoped_frontmatter_gate_is_not_flagged():
    """The legitimate pairing: the gate is scoped to already-green files.

    This is the real shape used by this repo's own sub-plans, which carry a
    comment saying the frontmatter files were verified green. Flagging it would
    make the lint unusable, so it is pinned here deliberately.
    """
    text = _subplan(
        fm_checks=[
            "python3 -m pytest skills/ilk-feedback/tests/test_sentinel_authoritative.py -q"
        ],
        step0_body=(
            "Pin the missing detail\n\n"
            "Red-first: land a failing test in "
            "`skills/ilk-loop/tests/test_local_checks_record_detail.py`. "
            "Expect 4 failed."
        ),
    )
    assert _run(text) == [], (
        "a frontmatter gate scoped to files step 0 does not touch is legitimate"
    )


def test_no_frontmatter_gate_is_not_flagged():
    """Per-step gates only — the recommended shape — must stay silent."""
    text = _subplan(
        fm_checks=[],
        step0_body="Red-first: land a failing test. Expect 3 failed.",
    )
    assert _run(text) == []


def test_non_redfirst_step0_with_wholesuite_gate_is_not_flagged():
    """A whole-suite frontmatter gate is fine when step 0 is not red-first.

    That shape is another lint's business (lint_wholesuite_gate_baseline), not
    this one's.
    """
    text = _subplan(
        fm_checks=["python3 -m pytest skills/ilk-loop/tests/ -q"],
        step0_body="Read the existing implementation and write down the contract.",
    )
    assert _run(text) == []


# ── Registration ───────────────────────────────────────────────────────────

def test_check_is_registered_in_all_checks():
    """An unregistered lint never runs — the defect it guards stays open."""
    names = [c.__name__ for c in plan_lint.ALL_CHECKS]
    assert "lint_redfirst_step0_under_frontmatter_gate" in names
