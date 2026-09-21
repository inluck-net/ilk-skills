"""Tests for lint_no_diff_step — a no-diff step must declare --allow-empty.

Regression: gh-resolve handoff-verify-gate-empty reverted 2026-09-22 after
12m of completed work because step 0's deliverable was Findings prose and
the commit line lacked --allow-empty, so `git commit` had nothing to commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_no_diff_step  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

# A step whose ONLY deliverable is Findings prose — no repo diff.
FINDINGS_ONLY_STEP = """\
---
plan: handoff-verify-gate-empty
status: pending
current_step: 0
estimated_steps: 3
---

# Sub-plan: handoff verify gate empty

## Steps

### Step 0 — investigate the gate
- Read the current gate implementation.
- Document findings in `## Findings` below.
- Commit: `docs(handoff): investigate gate emptiness [plan:handoff-verify-gate-empty#step-0]`

## Findings

_(empty — this is where the worker writes diagnostic prose)_
"""

# The SAME step WITH --allow-empty — should pass clean.
FINDINGS_ONLY_STEP_ALLOW_EMPTY = """\
---
plan: handoff-verify-gate-empty
status: pending
current_step: 0
estimated_steps: 3
---

# Sub-plan: handoff verify gate empty

## Steps

### Step 0 — investigate the gate
- Read the current gate implementation.
- Document findings in `## Findings` below.
- Commit: `git commit --allow-empty -m "docs(handoff): investigate gate emptiness [plan:handoff-verify-gate-empty#step-0]"`

## Findings

_(empty — this is where the worker writes diagnostic prose)_
"""

# A step that writes a repo file — unaffected (no finding).
REPO_FILE_STEP = """\
---
plan: fix-the-thing
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: fix the thing

## Steps

### Step 0 — write the fix
- Edit `src/module.py` to add the new function.
- Commit: `feat(module): add new function [plan:fix-the-thing#step-0]`
"""

# A step whose deliverable is a record under ~/.ilk-data (external data root).
EXTERNAL_DATA_STEP = """\
---
plan: record-metrics
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: record metrics

## Steps

### Step 0 — write metrics to external data
- Write metrics to `~/.ilk-data/projects/foo/metrics.json`.
- Commit: `chore(metrics): record baseline [plan:record-metrics#step-0]`
"""

# The SAME external data step WITH --allow-empty — should pass clean.
EXTERNAL_DATA_STEP_ALLOW_EMPTY = """\
---
plan: record-metrics
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: record metrics

## Steps

### Step 0 — write metrics to external data
- Write metrics to `~/.ilk-data/projects/foo/metrics.json`.
- Commit: `git commit --allow-empty -m "chore(metrics): record baseline [plan:record-metrics#step-0]"`
"""

# A step that names both a repo file AND Findings — has a repo diff, so unaffected.
MIXED_STEP = """\
---
plan: mixed-deliverable
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: mixed deliverable

## Steps

### Step 0 — fix and document
- Edit `src/module.py` to add the new function.
- Document findings in `## Findings` below.
- Commit: `feat(module): add function and document [plan:mixed-deliverable#step-0]`
"""


# ── Tests ───────────────────────────────────────────────────────────────────


class TestLintNoDiffStep:
    """Tests for the no-diff-step lint."""

    def test_findings_only_step_flagged(self):
        """AC1: A Findings-only step with a plain commit line is a finding."""
        findings = lint_no_diff_step(FINDINGS_ONLY_STEP, "handoff-verify-gate-empty")
        assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
        assert "allow-empty" in findings[0].lower() or "allow_empty" in findings[0].lower()

    def test_findings_only_step_with_allow_empty_passes(self):
        """AC2: The same step with --allow-empty passes clean."""
        findings = lint_no_diff_step(FINDINGS_ONLY_STEP_ALLOW_EMPTY, "handoff-verify-gate-empty")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_repo_file_step_unaffected(self):
        """AC3: A step that writes a repo file is unaffected."""
        findings = lint_no_diff_step(REPO_FILE_STEP, "fix-the-thing")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_external_data_step_flagged(self):
        """A step whose deliverable is under ~/.ilk-data is flagged."""
        findings = lint_no_diff_step(EXTERNAL_DATA_STEP, "record-metrics")
        assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"

    def test_external_data_step_with_allow_empty_passes(self):
        """An external-data step with --allow-empty passes clean."""
        findings = lint_no_diff_step(EXTERNAL_DATA_STEP_ALLOW_EMPTY, "record-metrics")
        assert findings == [], f"Expected no findings, got: {findings}"

    def test_mixed_step_unaffected(self):
        """A step that names both a repo file and Findings has a diff — unaffected."""
        findings = lint_no_diff_step(MIXED_STEP, "mixed-deliverable")
        assert findings == [], f"Expected no findings, got: {findings}"