"""Tests for the shared-module gate lint: the last step's gate must cover
the changed module's resolved callers' tests (HARD finding).

AC-1: step 0 gates everything, step 1 (last) gates only test_new → HARD
AC-2: last step gates test_new + test_shared, no caller test → HARD
AC-3: 3-step, only step 2 (last) gates everything → no finding (control or xfail)
AC-4: ## Findings holds an old gate; last step's own fence is the judge → HARD
AC-5 (control): frontmatter-only gates, no ### Step → unprefixed finding
AC-6 (control): whole-suite gate on any step → no finding
AC-7 (control): leaf module, no importer → no finding

AC-1, AC-2, AC-4 are xfail(strict=True) until the lint changes (step 1).
AC-3 is xfail only if it fails on current code; otherwise a control.
AC-5, AC-6, AC-7 are plain tests that pass on the current code.
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint as _pl  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

def _slug_aligned_name(filename: str, content: str) -> str:
    """Filename whose derived slug matches the fixture's own `plan:` field."""
    if filename.startswith("MASTER"):
        return filename
    m = re.search(r"^\s*plan:\s*(\S+)\s*$", content, re.M)
    return (m.group(1).strip("'\"") + ".md") if m else filename


def _run_lint(subplan_text: str, tmp_path: Path,
              project_files: dict[str, str] | None = None) -> list[str]:
    """Write a sub-plan (and optional project files) to *tmp_path*, run lint.

    Sets ``_PROJECT_ROOT`` to *tmp_path* so the importer oracle searches
    the fixture tree.  Restores the original value afterwards.
    """
    if project_files:
        for rel, content in project_files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")

    sp = tmp_path / _slug_aligned_name("test-subplan.md", subplan_text)
    sp.write_text(textwrap.dedent(subplan_text), encoding="utf-8")

    old_root = _pl._PROJECT_ROOT
    try:
        _pl._PROJECT_ROOT = tmp_path
        return _pl.lint_file(str(sp))
    finally:
        _pl._PROJECT_ROOT = old_root


# ── project file fixtures ────────────────────────────────────────────

_SHARED_MODULE = """\
def reconcile_from_github() -> set[str]:
    return set()
"""

_CALLER_IMPORTS_MODULE = """\
from shared import reconcile_from_github

def watch():
    result = reconcile_from_github()
    return result
"""

_TEST_SHARED = """\
from shared import reconcile_from_github

def test_reconcile():
    result = reconcile_from_github()
    assert isinstance(result, set)
"""

_TEST_CALLER = """\
from caller import watch

def test_watch():
    result = watch()
    assert isinstance(result, set)
"""

_TEST_NEW = """\
def test_new_feature():
    assert True
"""

# Project files: a shared module, a caller, and their test files.
# The module name "shared" is derived from "skills/x/scripts/shared.py"
# by _resolve_module_name, so the import must be "from shared import".
_SHARED_PROJECT = {
    "skills/x/scripts/shared.py": _SHARED_MODULE,
    "skills/x/scripts/caller.py": _CALLER_IMPORTS_MODULE,
    "skills/x/tests/test_shared.py": _TEST_SHARED,
    "skills/x/tests/test_caller.py": _TEST_CALLER,
    "skills/x/tests/test_new.py": _TEST_NEW,
}

# Leaf project: a module with no importers.
_LEAF_MODULE = """\
def leaf_func() -> str:
    return "isolated"
"""

_LEAF_TEST = """\
from leaf_module import leaf_func

def test_leaf():
    assert leaf_func() == "isolated"
"""

_LEAF_PROJECT = {
    "skills/x/scripts/leaf_module.py": _LEAF_MODULE,
    "skills/x/tests/test_leaf_module.py": _LEAF_TEST,
}


# ── sub-plan fixtures ────────────────────────────────────────────────

# AC-1: step 0 gates test_new + test_shared + test_caller, step 1 (last)
# gates only test_new.  The last step's gate misses test_caller → HARD.
# Today the union covers it, so there is no finding (xfail).
AC1_STEP0_COVERS_LAST_DOES_NOT = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
---

# Sub-plan: last step gate must cover importers

## Steps

### Step 0 — change the shared module
- Edit `skills/x/scripts/shared.py` per spec.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py skills/x/tests/test_caller.py -q
    timeout: 60
```

### Step 1 — final verification
- Verify the change.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py -q
    timeout: 60
```
"""

# AC-2: last step gates test_new + test_shared (no caller test).
# The last step's gate misses test_caller → HARD.
# Today the finding has no HARD prefix (xfail).
AC2_LAST_STEP_MISSING_CALLER = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
---

# Sub-plan: last step gate must cover importers

## Steps

### Step 0 — change the shared module
- Edit `skills/x/scripts/shared.py` per spec.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py -q
    timeout: 60
```

### Step 1 — final verification
- Verify the change.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py -q
    timeout: 60
```
"""

# AC-3: 3-step sub-plan, only step 2 (last) gates everything.
# Steps 0 and 1 gate only test_new.  The last step's gate covers all → no finding.
# Control if it passes today; xfail if it fails.
AC3_ONLY_LAST_STEP_COVERS = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
---

# Sub-plan: last step gate covers everything

## Steps

### Step 0 — initial change
- Edit `skills/x/scripts/shared.py`.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py -q
    timeout: 60
```

### Step 1 — secondary change
- More edits.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py -q
    timeout: 60
```

### Step 2 — final verification
- Verify everything.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py skills/x/tests/test_caller.py -q
    timeout: 60
```
"""

# AC-4: ## Findings holds an old #### block with a yaml fence that gates
# test_caller.py.  The last step's own fence (step 1) gates only test_new.
# The Findings fence must not count → HARD on the last step.
AC4_FINDINGS_HAS_OLD_GATE = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
---

# Sub-plan: last step gate with Findings tail

## Steps

### Step 0 — change the shared module
- Edit `skills/x/scripts/shared.py` per spec.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py skills/x/tests/test_caller.py -q
    timeout: 60
```

### Step 1 — final verification
- Verify the change.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py -q
    timeout: 60
```

## Findings

#### Old gate from a previous iteration

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_caller.py -q
    timeout: 60
```
"""

# AC-5 (control): frontmatter-only gates (no ### Step) missing test_caller.
# Today's union rule applies → unprefixed finding (no HARD).
AC5_FRONTMATTER_ONLY = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py -q
    timeout: 60
---

# Sub-plan: frontmatter-only gate

No per-step gates.  The frontmatter gate misses test_caller.
"""

# AC-6 (control): whole-suite gate on any step → no finding.
AC6_WHOLE_SUITE_GATE = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/shared.py"
---

# Sub-plan: whole-suite gate

## Steps

### Step 0 — change the shared module
- Edit `skills/x/scripts/shared.py`.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_new.py skills/x/tests/test_shared.py skills/x/tests/test_caller.py -q
    timeout: 60
```

### Step 1 — full suite verification
- Run the full suite.

```yaml
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
```
"""

# AC-7 (control): leaf module (no importer) → no finding.
AC7_LEAF_MODULE = """\
---
plan: test-last-step-gate
scope_paths:
  - "skills/x/scripts/leaf_module.py"
---

# Sub-plan: leaf module

## Steps

### Step 0 — add leaf module
- Edit `skills/x/scripts/leaf_module.py`.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_leaf_module.py -q
    timeout: 60
```

### Step 1 — verify
- Run the test.

```yaml
local_checks:
  - command: python3 -m pytest skills/x/tests/test_leaf_module.py -q
    timeout: 60
```
"""


# ── tests ────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="HARD finding for last step gate (step 1)")
class TestAc1LastStepGateMissesCaller:
    """AC-1: step 0 gates everything, step 1 (last) gates only test_new.

    The last step's gate misses test_caller → HARD finding.
    Today the union covers it (no finding) — xfail until step 1.
    """

    def test_hard_finding_for_last_step_missing_caller(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC1_STEP0_COVERS_LAST_DOES_NOT, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert len(hard_findings) == 1, (
            f"Expected exactly one HARD finding, got {len(hard_findings)}.\n"
            f"All findings: {findings}"
        )
        assert "test_caller" in hard_findings[0], (
            f"HARD finding must name test_caller.py.\n"
            f"Got: {hard_findings[0]}"
        )
        assert "### Step 1" in hard_findings[0], (
            f"HARD finding must name the last step heading.\n"
            f"Got: {hard_findings[0]}"
        )


@pytest.mark.xfail(strict=True, reason="HARD prefix for last step gate (step 1)")
class TestAc2LastStepMissingCallerNoPrefix:
    """AC-2: last step gates test_new + test_shared, no caller test.

    The last step's gate misses test_caller → HARD finding.
    Today the finding has no HARD prefix — xfail until step 1.
    """

    def test_hard_prefix_for_last_step_missing_caller(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC2_LAST_STEP_MISSING_CALLER, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert len(hard_findings) == 1, (
            f"Expected exactly one HARD finding, got {len(hard_findings)}.\n"
            f"All findings: {findings}"
        )
        assert "test_caller" in hard_findings[0]


class TestAc3OnlyLastStepCovers:
    """AC-3: 3-step sub-plan, only step 2 (last) gates everything.

    Steps 0 and 1 gate only test_new, but step 2 (the last) gates all.
    No finding expected — the last step's gate is compliant.
    """

    def test_no_finding_when_last_step_covers_all(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC3_ONLY_LAST_STEP_COVERS, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        shared_findings = [f for f in findings
                           if "shared_module" in f.lower()
                           or "caller" in f.lower()
                           or "importer" in f.lower()
                           or "test_caller" in f.lower()]
        assert not shared_findings, (
            f"Expected no finding when last step covers all tests.\n"
            f"Findings: {shared_findings}"
        )


@pytest.mark.xfail(strict=True, reason="Findings gate must not count (step 1)")
class TestAc4FindingsGateDoesNotCount:
    """AC-4: ## Findings holds an old gate; last step's own fence is the judge.

    The Findings section's yaml fence gates test_caller, but the last step's
    own fence (step 1) gates only test_new.  The Findings fence must not
    count → HARD finding on the last step.
    """

    def test_hard_finding_ignoring_findings_gate(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC4_FINDINGS_HAS_OLD_GATE, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert len(hard_findings) == 1, (
            f"Expected exactly one HARD finding (Findings gate must not count).\n"
            f"All findings: {findings}"
        )
        assert "test_caller" in hard_findings[0]


class TestAc5FrontmatterOnlyGates:
    """AC-5 (control): frontmatter-only gates (no ### Step) missing test_caller.

    Today's union rule applies → unprefixed finding (no HARD prefix).
    """

    def test_unprefixed_finding_for_frontmatter_gates(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC5_FRONTMATTER_ONLY, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        shared_findings = [f for f in findings
                           if "shared_module" in f.lower()
                           or "caller" in f.lower()
                           or "importer" in f.lower()
                           or "test_caller" in f.lower()]
        assert len(shared_findings) >= 1, (
            f"Expected at least one finding for frontmatter-only gates.\n"
            f"All findings: {findings}"
        )
        # Must NOT have HARD prefix (frontmatter-only uses the old union rule).
        for f in shared_findings:
            assert not f.startswith("HARD"), (
                f"Frontmatter-only finding must not be HARD.\n"
                f"Got: {f}"
            )


class TestAc6WholeSuiteGate:
    """AC-6 (control): whole-suite gate on any step → no finding."""

    def test_no_finding_for_whole_suite_gate(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC6_WHOLE_SUITE_GATE, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        shared_findings = [f for f in findings
                           if "shared_module" in f.lower()
                           or "caller" in f.lower()
                           or "importer" in f.lower()
                           or "test_caller" in f.lower()]
        assert not shared_findings, (
            f"Expected no finding for whole-suite gate.\n"
            f"Findings: {shared_findings}"
        )


class TestAc7LeafModule:
    """AC-7 (control): leaf module (no importer) → no finding."""

    def test_no_finding_for_leaf_module(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC7_LEAF_MODULE, tmp_path,
            project_files=_LEAF_PROJECT,
        )
        shared_findings = [f for f in findings
                           if "leaf_module" in f.lower()
                           or "importer" in f.lower()]
        assert not shared_findings, (
            f"Expected no finding for leaf module.\n"
            f"Findings: {shared_findings}"
        )