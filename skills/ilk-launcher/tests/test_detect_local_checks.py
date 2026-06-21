"""Regression tests for _detect_local_checks._has_local_checks.

Escaped bug 26ff5e2e: the detector originally scanned ONLY frontmatter, so a
sub-plan authored in the canonical style (frontmatter ``local_checks: []`` plus
per-step fenced ``yaml`` blocks in the body) was silently reported as having no
gates → the loop launched gates OFF.  Commit 469a18c fixed it by also scanning
the body below the frontmatter.

These tests lock the fix in place.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import _detect_local_checks as d  # noqa: E402


# ── AC-1: per-step body local_checks → True ─────────────────────────────────

PER_STEP_PLAN = """\
---
plan: example
status: pending
current_step: 0
local_checks: []
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
```yaml
local_checks:
  - command: bash some/test.sh
    timeout: 60
```
- Do the thing.

### Step 1 — Verify
```yaml
local_checks:
  - command: bash some/test.sh
    timeout: 60
```
- Verify the thing.
"""


def test_per_step_body_local_checks_detected():
    """AC-1: per-step yaml blocks with local_checks → True."""
    assert d._has_local_checks(PER_STEP_PLAN) is True


# ── AC-2: no local_checks anywhere → False ──────────────────────────────────

NO_CHECKS_PLAN = """\
---
plan: example
status: pending
current_step: 0
local_checks: []
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Do the thing.

### Step 1 — Verify
- Verify the thing.
"""


def test_no_local_checks_anywhere():
    """AC-2: empty frontmatter + no body local_checks → False."""
    assert d._has_local_checks(NO_CHECKS_PLAN) is False


# ── AC-3: frontmatter non-empty local_checks → True (back-compat) ──────────

FRONTMATTER_CHECKS_PLAN = """\
---
plan: example
status: pending
current_step: 0
local_checks:
  - command: bash run_tests.sh
    timeout: 120
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Do the thing.
"""


def test_frontmatter_local_checks_detected():
    """AC-3: non-empty frontmatter local_checks → True (back-compat)."""
    assert d._has_local_checks(FRONTMATTER_CHECKS_PLAN) is True
