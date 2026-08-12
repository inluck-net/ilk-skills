#!/usr/bin/env python3
"""Tests for plan_lint per-step local_checks extraction.

Pins the PLANLINT-PERSTEP-BLIND defect: gate lints that use
``_extract_local_checks_commands`` cannot see per-step ``local_checks``
blocks, only frontmatter.

Fixtures:
  A  frontmatter gate ``python3 -m pytest -q`` → 1 whole-suite finding (control)
  B  identical gate in a per-step block → 1 whole-suite finding (FAILS today)
  C  per-step check referencing a later-created path → 0 frontmatter-path findings (AC-3 guard)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import (  # noqa: E402
    lint_wholesuite_gate_baseline,
    lint_frontmatter_path_created_later,
)


# ── Fixture A: frontmatter gate (control — passes today) ──────────────────────

FRONTMATTER_GATE = """\
---
plan: test-fm-gate
status: pending
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
---

# Sub-plan: frontmatter whole-suite gate

A bare pytest in frontmatter with no baseline-green note.
"""


def test_frontmatter_gate_flagged():
    """Fixture A: frontmatter whole-suite gate → 1 finding (control)."""
    findings = lint_wholesuite_gate_baseline(FRONTMATTER_GATE, "test-fm-gate")
    assert len(findings) == 1, (
        f"Expected 1 whole-suite finding for frontmatter gate, got {len(findings)}: {findings}"
    )
    assert "whole suite" in findings[0].lower() or "baseline" in findings[0].lower()


# ── Fixture B: per-step gate (FAILS today — xfail until step 1) ───────────────

PERSTEP_GATE = """\
---
plan: test-perstep-gate
status: pending
---

# Sub-plan: per-step whole-suite gate

### Step 0 — Do the thing
```yaml
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
```
"""


def test_perstep_gate_flagged():
    """Fixture B: per-step whole-suite gate → 1 finding (FAILS today)."""
    findings = lint_wholesuite_gate_baseline(PERSTEP_GATE, "test-perstep-gate")
    assert len(findings) == 1, (
        f"Expected 1 whole-suite finding for per-step gate, got {len(findings)}: {findings}"
    )


# ── Fixture C: AC-3 guard — per-step check for later-created path ─────────────

PERSTEP_CREATED_LATER = """\
---
plan: test-perstep-created-later
status: pending
scope_paths:
  - "tools/new_module/tests/"
---

# Sub-plan: per-step check for later-created path

### Step 0 — Create the module
```yaml
local_checks:
  - command: python3 -m pytest tools/new_module/tests/ -q
    timeout: 60
```
"""


def test_perstep_created_later_no_false_positive():
    """Fixture C (AC-3): per-step check for later-created path → 0 frontmatter-path findings."""
    findings = lint_frontmatter_path_created_later(PERSTEP_CREATED_LATER, "test-perstep-created-later")
    assert findings == [], (
        f"Expected 0 frontmatter-path findings for per-step check, got {len(findings)}: {findings}"
    )
