"""Tests for plan_preflight — validate a batch with the readers that will judge it.

Regression: a master with backticked filenames in the registry table lints
clean (plan_lint exits 0) while extract_subplan_files parses 0 sub-plans,
so the loop reads "no runnable sub-plans" and halts.  The preflight must
catch this parity failure and refuse release.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# The module under test does not exist yet — this is a red test.
from plan_preflight import preflight_batch  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

# A master whose registry table uses backticked filenames.
# extract_subplan_files parses 0 sub-plans from this (the backtick is not
# in _REF_LEAD's lookbehind set), but plan_lint sees "clean" because it
# uses its own regex.  The preflight must detect the parity mismatch.
BACKTICKED_MASTER = """\
---
master_plan: 2026-09-22-execution
batch_date: 2026-09-22
status: draft
current_subplan: 2026-09-22-alpha
---

# MASTER plan: test batch

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | `2026-09-22-alpha.md` | pending |
| 2 | `2026-09-22-beta.md` | pending |
"""

# The same master with bare filenames — must parse cleanly (2 sub-plans).
BARE_FILENAME_MASTER = """\
---
master_plan: 2026-09-22-execution
batch_date: 2026-09-22
status: draft
current_subplan: 2026-09-22-alpha
---

# MASTER plan: test batch

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | 2026-09-22-alpha.md | pending |
| 2 | 2026-09-22-beta.md | pending |
"""

# A sub-plan that declares a test path that does not exist on disk.
INVENTED_TEST_PATH_SUBPLAN = """\
---
plan: alpha
status: pending
current_step: 0
estimated_steps: 2
unit_test_targets:
  - "tests/test_admission_gates.py"
---

# Sub-plan: alpha

## Steps

### Step 0 — add the gate
- Implement the admission gate in `src/gate.py`.
- Commit: `feat(gate): add admission gate [plan:alpha#step-0]`

### Step 1 — verify
```yaml
local_checks:
  - command: "python3 -m pytest tests/test_admission_gates.py -q"
    timeout: 120
```
- Commit: `test(gate): verify admission gate [plan:alpha#step-1]`
"""

# A sub-plan whose step 0 has neither a repo artifact nor --allow-empty.
# Step 0 is purely investigative prose; step 1 has a repo artifact.
NO_ARTIFACT_NO_ALLOW_EMPTY = """\
---
plan: findings-only
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: findings only

## Steps

### Step 0 — investigate
- Read the current implementation and document findings in `## Findings` below.
- Commit: `docs(investigate): document findings [plan:findings-only#step-0]`

### Step 1 — fix
- Edit `src/module.py` to apply the fix.
- Commit: `fix(module): apply fix [plan:findings-only#step-1]`

## Findings

_(empty)_
"""

# A sub-plan that is clean — has repo artifacts and valid test paths.
CLEAN_SUBPLAN = """\
---
plan: beta
status: pending
current_step: 0
estimated_steps: 2
unit_test_targets:
  - "skills/ilk-loop/tests/test_plan_lint.py"
---

# Sub-plan: beta

## Steps

### Step 0 — implement
- Edit `skills/ilk-loop/scripts/plan_lint.py` to add the new check.
- Commit: `feat(lint): add new check [plan:beta#step-0]`

### Step 1 — verify
```yaml
local_checks:
  - command: "python3 -m pytest skills/ilk-loop/tests/test_plan_lint.py -q"
    timeout: 120
```
- Commit: `test(lint): verify new check [plan:beta#step-1]`
"""


# ── Tests ───────────────────────────────────────────────────────────────────


class TestPreflightRegistryParity:
    """AC1: registry parity — extract_subplan_files must agree with the table."""

    def test_backticked_master_fails_parity(self):
        """The anchor case: backticked filenames → 0 parsed, table says 2."""
        # Create a temporary plans dir with the sub-plan files missing.
        result = preflight_batch(
            master_text=BACKTICKED_MASTER,
            plans_dir=Path("/nonexistent"),  # files don't exist
            project_root=Path("/nonexistent"),
        )
        assert result.has_failures, "Expected parity failure for backticked master"
        assert any("parity" in f.lower() or "registry" in f.lower()
                    for f in result.failures), (
            f"Expected a parity/registry finding, got: {result.failures}"
        )

    def test_bare_filename_master_passes_parity(self):
        """Bare filenames → 2 parsed, matching the table."""
        result = preflight_batch(
            master_text=BARE_FILENAME_MASTER,
            plans_dir=Path("/nonexistent"),
            project_root=Path("/nonexistent"),
        )
        parity_failures = [f for f in result.failures
                           if "parity" in f.lower() or "registry" in f.lower()]
        assert parity_failures == [], (
            f"Expected no parity failure for bare-filename master, got: {parity_failures}"
        )


class TestPreflightDeclaredPaths:
    """AC2: declared test paths must exist on disk."""

    def test_invented_test_path_fails(self):
        """A unit_test_targets entry that does not exist on disk is a finding."""
        result = preflight_batch(
            master_text=BARE_FILENAME_MASTER,
            plans_dir=Path("/nonexistent"),
            project_root=Path("/nonexistent"),
            subplan_texts={"2026-09-22-alpha.md": INVENTED_TEST_PATH_SUBPLAN},
        )
        path_failures = [f for f in result.failures
                         if "path" in f.lower() or "exist" in f.lower()
                         or "test_admission_gates" in f.lower()]
        assert path_failures != [], (
            f"Expected a path-existence finding, got: {result.failures}"
        )


class TestPreflightStepCommitFeasibility:
    """AC3: a step must name a repo artifact or declare --allow-empty."""

    def test_no_artifact_no_allow_empty_fails(self):
        """A step with neither repo artifact nor --allow-empty is a finding."""
        result = preflight_batch(
            master_text=BARE_FILENAME_MASTER,
            plans_dir=Path("/nonexistent"),
            project_root=Path("/nonexistent"),
            subplan_texts={"2026-09-22-findings-only.md": NO_ARTIFACT_NO_ALLOW_EMPTY},
        )
        commit_failures = [f for f in result.failures
                           if "allow-empty" in f.lower()
                           or "commit" in f.lower()
                           or "artifact" in f.lower()
                           or "diff" in f.lower()]
        assert commit_failures != [], (
            f"Expected a commit-feasibility finding, got: {result.failures}"
        )


class TestPreflightCleanBatch:
    """A clean batch must pass the preflight with no failures."""

    def test_clean_subplan_passes(self):
        """A sub-plan with repo artifacts and valid paths passes."""
        # Use a real plans dir so the test path can resolve.
        real_tests_dir = Path(__file__).resolve().parent
        real_project_root = real_tests_dir.parent.parent.parent  # ilk-skills root
        real_plans_dir = real_project_root / "docs" / "plans"
        if not real_plans_dir.exists():
            real_plans_dir = real_project_root  # fallback

        result = preflight_batch(
            master_text=BARE_FILENAME_MASTER,
            plans_dir=real_plans_dir,
            project_root=real_project_root,
            subplan_texts={"2026-09-22-beta.md": CLEAN_SUBPLAN},
        )
        assert not result.has_failures, (
            f"Expected clean preflight, got failures: {result.failures}"
        )