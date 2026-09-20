"""Tests for lint_step_window_fit — an oversized undeclared step passes today (RED).

AC-1 (red-first): a sub-plan with a 20-item edit surface, no
``recommended_iteration_timeout_min``, under a 30-min window ⇒ HARD
finding.  The same fixture with ``recommended_iteration_timeout_min: 90``
and a stated basis passes.

The lint estimates a step's edit surface from the sub-plan's own declared
numbers (``scope_paths``, ``unit_test_targets``, and per-step file lists)
and compares it against half the effective window (project
``iteration_timeout_min`` or the default 30).

NOTE: this test is RED against the current tree — the lint does not yet
exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402
from plan_lint import lint_step_window_fit  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

def _scope_paths(n: int) -> str:
    """Return YAML list of n scope_paths entries."""
    entries = "\n".join(f"  - src/module_{i}.py" for i in range(n))
    return f"scope_paths:\n{entries}"


OVERSIZED_NO_DECL = f"""\
---
plan: oversized-no-decl
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-09-20
{_scope_paths(20)}
---

# Sub-plan: oversized step with no timeout declaration

## Steps

### Step 0 — Edit all 20 files
- Change every file listed in scope_paths.
"""

OVERSIZED_WITH_DECL = f"""\
---
plan: oversized-with-decl
status: pending
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-09-20
recommended_iteration_timeout_min: 90
{_scope_paths(20)}
---

# Sub-plan: oversized step with explicit timeout declaration

## Steps

### Step 0 — Edit all 20 files
- Change every file listed in scope_paths.

Basis: 20 files measured at ~2-3 min each in the lark batch (retro-2026-09-20).
"""


# ── tests ────────────────────────────────────────────────────────────────────

class TestAC1_WindowFitLint:
    """AC-1: oversized undeclared step ⇒ HARD finding; declared ⇒ passes."""

    def test_oversized_no_declaration_is_hard_finding(self) -> None:
        """20 files, no declaration, under 30-min window ⇒ HARD finding."""
        findings = lint_step_window_fit(OVERSIZED_NO_DECL, "oversized-no-decl")
        assert len(findings) >= 1
        assert any("HARD" in f for f in findings)
        assert any("window" in f.lower() or "timeout" in f.lower() for f in findings)

    def test_oversized_with_declaration_passes(self) -> None:
        """20 files, declared 90-min window ⇒ no finding."""
        findings = lint_step_window_fit(OVERSIZED_WITH_DECL, "oversized-with-decl")
        assert findings == []
