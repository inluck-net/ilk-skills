"""Tests for lint_budget_vs_gate_timeout — budget-vs-gate-timeout warning.

AC-6: warns when a step's summed declared local_checks timeout exceeds a
      configurable fraction of iteration_timeout_min.
AC-7: does NOT warn when no timeouts are declared (absent ≠ zero).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_budget_vs_gate_timeout  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

# Sub-plan with frontmatter local_checks that sum to 1800s (30min) — exceeds
# 80% of the default 30min budget (1440s).
HIGH_TIMEOUT_FM = """\
---
plan: example-high-timeout
status: pending
current_step: 0
local_checks:
  - command: pytest tests/ -q
    timeout: 1200
  - command: bash docs/loop/preflight.sh
    timeout: 600
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.
"""

# Sub-plan with per-step local_checks that sum to 1800s — exceeds threshold.
HIGH_TIMEOUT_STEP = """\
---
plan: example-high-step-timeout
status: pending
current_step: 0
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
```yaml
local_checks:
  - command: pytest tests/ -q
    timeout: 1200
  - command: bash docs/loop/preflight.sh
    timeout: 600
```
- Did the thing.
"""

# Sub-plan with low timeouts — below threshold.
LOW_TIMEOUT = """\
---
plan: example-low-timeout
status: pending
current_step: 0
local_checks:
  - command: pytest tests/test_foo.py -q
    timeout: 60
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.
"""

# Sub-plan with NO timeouts at all — must NOT warn (AC-7).
NO_TIMEOUT = """\
---
plan: example-no-timeout
status: pending
current_step: 0
local_checks:
  - command: pytest tests/test_foo.py -q
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.
"""

# Sub-plan with custom recommended_iteration_timeout_min — higher budget.
CUSTOM_BUDGET = """\
---
plan: example-custom-budget
status: pending
current_step: 0
recommended_iteration_timeout_min: 60
local_checks:
  - command: pytest tests/ -q
    timeout: 1800
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.
"""

# Empty sub-plan — no local_checks at all.
EMPTY = """\
---
plan: example-empty
status: pending
current_step: 0
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.
"""


# ── AC-6: warns when gate timeouts approach the budget ──────────────────────

def test_high_frontmatter_timeout_warns() -> None:
    findings = lint_budget_vs_gate_timeout(HIGH_TIMEOUT_FM, "example-high-timeout")
    assert len(findings) == 1
    assert "1800s" in findings[0]
    assert "80%" in findings[0]


def test_high_step_timeout_warns() -> None:
    findings = lint_budget_vs_gate_timeout(HIGH_TIMEOUT_STEP, "example-high-step-timeout")
    assert len(findings) == 1
    assert "1800s" in findings[0]


def test_low_timeout_no_warning() -> None:
    findings = lint_budget_vs_gate_timeout(LOW_TIMEOUT, "example-low-timeout")
    assert findings == []


def test_custom_budget_no_warning() -> None:
    """1800s timeout against 60min budget (3600s * 0.8 = 2880s) — no warning."""
    findings = lint_budget_vs_gate_timeout(CUSTOM_BUDGET, "example-custom-budget")
    assert findings == []


# ── AC-7: absent is not zero — no warning when no timeouts declared ─────────

def test_no_timeout_no_warning() -> None:
    findings = lint_budget_vs_gate_timeout(NO_TIMEOUT, "example-no-timeout")
    assert findings == []


def test_empty_no_warning() -> None:
    findings = lint_budget_vs_gate_timeout(EMPTY, "example-empty")
    assert findings == []


# ── boundary: exactly at threshold ──────────────────────────────────────────

def test_exactly_at_threshold_no_warning() -> None:
    """1440s = exactly 80% of 30min (1800s) — at the boundary, no warning."""
    text = """\
---
plan: example-boundary
status: pending
current_step: 0
local_checks:
  - command: pytest tests/ -q
    timeout: 1440
---

# Sub-plan

## Steps

### Step 0 — Do the thing
- Did the thing.
"""
    findings = lint_budget_vs_gate_timeout(text, "example-boundary")
    assert findings == []


def test_one_over_threshold_warns() -> None:
    """1441s = just over 80% — should warn."""
    text = """\
---
plan: example-over-boundary
status: pending
current_step: 0
local_checks:
  - command: pytest tests/ -q
    timeout: 1441
---

# Sub-plan

## Steps

### Step 0 — Do the thing
- Did the thing.
"""
    findings = lint_budget_vs_gate_timeout(text, "example-over-boundary")
    assert len(findings) == 1


# --- Per-step semantics (regression guard, 2026-08-10) ----------------------
#
# The budget is per ITERATION and each step runs in its own iteration, so the
# lint must compare each step's gates against the budget -- not the sum of every
# step's gates. The original implementation summed the whole file, which
# overstates cost by the number of steps: 3 of this repo's own 5 sub-plans
# false-warned. Frontmatter gates run at EVERY step, so a step's true cost is
# the frontmatter sum plus that step's own block.

_MANY_CHEAP_STEPS = """\
---
plan: many-cheap-steps
local_checks:
  - command: python3 -m pytest tests/a.py -q
    timeout: 300
---

# Sub-plan: several steps, each cheap

## Steps

### Step 0 - first
```yaml
local_checks:
  - command: python3 -m pytest tests/b.py -q
    timeout: 300
```
- work

### Step 1 - second
```yaml
local_checks:
  - command: python3 -m pytest tests/c.py -q
    timeout: 300
```
- work

### Step 2 - third
```yaml
local_checks:
  - command: python3 -m pytest tests/d.py -q
    timeout: 300
```
- work

### Step 3 - fourth
```yaml
local_checks:
  - command: python3 -m pytest tests/e.py -q
    timeout: 300
```
- work
"""


def test_many_cheap_steps_do_not_warn() -> None:
    """4 steps x 600s each (300 frontmatter + 300 own) must NOT warn.

    Whole-file summing would total 2400s and false-warn against the 1440s
    threshold; per-step the worst step is 600s, comfortably under.
    """
    findings = lint_budget_vs_gate_timeout(_MANY_CHEAP_STEPS, "many-cheap-steps")
    assert findings == [], (
        f"Each step costs 600s, well under 80% of 1800s. A finding here means "
        f"the lint is summing across steps again: {findings}"
    )


def test_warning_names_the_offending_step() -> None:
    """A genuinely expensive step warns, and the message identifies which step."""
    content = _MANY_CHEAP_STEPS.replace(
        """### Step 2 - third
```yaml
local_checks:
  - command: python3 -m pytest tests/d.py -q
    timeout: 300
```""",
        """### Step 2 - third
```yaml
local_checks:
  - command: python3 -m pytest tests/d.py -q
    timeout: 1600
```""",
    )
    findings = lint_budget_vs_gate_timeout(content, "one-hog")
    assert len(findings) == 1, f"Expected one finding for a 1900s step: {findings}"
    assert "step 2" in findings[0], (
        f"Finding must name the offending step so the author knows where to "
        f"look: {findings[0]}"
    )
