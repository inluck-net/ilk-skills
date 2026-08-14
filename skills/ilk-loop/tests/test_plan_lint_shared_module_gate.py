"""Tests for the shared-module gate lint: a one-file gate on a module whose
callers depend on it hides integration bugs.

The lint does not exist yet, so positive cases (a) and (b) are
xfail(strict=True).  Flip to pass in step 2 when the lint is implemented.

AC-1: shared module + one-file gate -> finding          (xfail)
AC-2: the real gh-resolve case (return-type change)     (xfail)
AC-3: later step runs a directory -> silent
AC-4: leaf module nothing imports -> silent
AC-5: docs-only sub-plan -> silent
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_file  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

def _run_lint(subplan_text: str, tmp_path: Path,
              project_files: dict[str, str] | None = None) -> list[str]:
    """Write a sub-plan (and optional project files) to *tmp_path*, run lint.

    ``project_files`` maps relative paths to content — simulates a repo
    with source files the caller-aware oracle can inspect.
    """
    if project_files:
        for rel, content in project_files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")

    sp = tmp_path / "test-subplan.md"
    sp.write_text(textwrap.dedent(subplan_text), encoding="utf-8")
    return lint_file(str(sp))


# ── project file fixtures ────────────────────────────────────────────
# These simulate a small repo with a shared module and a caller.

_SHARED_MODULE = """\
def reconcile_from_github() -> set[str]:
    return set()
"""

_CALLER_IMPORTS_MODULE = """\
from shared_module import reconcile_from_github

def watch():
    result = reconcile_from_github()
    return result
"""

_TEST_FILE = """\
from shared_module import reconcile_from_github

def test_reconcile():
    result = reconcile_from_github()
    assert isinstance(result, set)
"""

# gh-resolve shape: return type changed from set to tuple, caller not updated.
_SHARED_MODULE_CHANGED = """\
from typing import Tuple

def reconcile_from_github() -> Tuple[set[str], bool]:
    return set(), True
"""

_CALLER_OLD_SIGNATURE = """\
from shared_module import reconcile_from_github

def watch():
    reconciled = reconcile_from_github()
    if "already-filed" in reconciled:  # always False — reconciled is a tuple
        pass
"""

_WATCH_TEST = """\
from shared_module import reconcile_from_github

def test_watch():
    result = reconcile_from_github()
    assert isinstance(result, tuple)
"""

# Leaf module: nothing imports it.
_LEAF_MODULE = """\
def leaf_func() -> str:
    return "isolated"
"""

_LEAF_TEST = """\
from leaf_module import leaf_func

def test_leaf():
    assert leaf_func() == "isolated"
"""

# Project files for each scenario.
_SHARED_PROJECT = {
    "src/shared_module.py": _SHARED_MODULE,
    "src/caller.py": _CALLER_IMPORTS_MODULE,
    "tests/test_shared_module.py": _TEST_FILE,
}

_GHRESOLVE_PROJECT = {
    "src/shared_module.py": _SHARED_MODULE_CHANGED,
    "src/watch.py": _CALLER_OLD_SIGNATURE,
    "tests/test_watch.py": _WATCH_TEST,
}

_LEAF_PROJECT = {
    "src/leaf_module.py": _LEAF_MODULE,
    "tests/test_leaf_module.py": _LEAF_TEST,
}


# ── sub-plan fixtures ────────────────────────────────────────────────

# AC-1: shared module + one-file gate -> finding.
SHARED_MODULE_ONE_FILE_GATE = """\
---
plan: test-shared-module-gate
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py -q
    timeout: 60
---

# Sub-plan: fix shared_module

Changes `reconcile_from_github` in a module that `caller.py` imports.
Gate runs only `tests/test_shared_module.py` — misses the caller.
"""

# AC-2: the real gh-resolve case — return-type change with production caller.
GHRESOLVE_STEP3 = """\
---
plan: seen-set-is-a-cache
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_watch.py -q
    timeout: 120
---

# Sub-plan: the seen-set-is-a-cache

Step 3 changes `reconcile_from_github` from `-> Set[str]` to
`-> Tuple[Set[str], bool]`.  Gate runs only `tests/test_watch.py`.

The production caller at `src/watch.py:371` was not updated, so
`reconciled` became a tuple that was then membership-tested as a set
— always False.  Reconciliation-based dedup silently stopped working.
"""

# AC-3: later step runs a directory -> silent (compliant).
WIDER_GATE_LATER_STEP = """\
---
plan: test-wider-gate
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py -q
    timeout: 60
---

### Step 0

Change shared_module.

```yaml
local_checks:
  - command: pytest tests/ -q
    timeout: 300
```

### Step 1

Verify by running the full suite.
"""

# AC-4: leaf module nothing imports -> silent.
LEAF_MODULE_GATE = """\
---
plan: test-leaf-module
scope_paths:
  - "src/leaf_module.py"
local_checks:
  - command: pytest tests/test_leaf_module.py -q
    timeout: 60
---

# Sub-plan: new leaf module

Adds `leaf_func()` — nothing imports it yet.  One-file gate is fine.
"""

# AC-5: docs-only sub-plan -> silent.
DOCS_ONLY = """\
---
plan: test-docs-only
scope_paths:
  - "docs/architecture.md"
local_checks:
  - command: echo "docs-only"
    timeout: 10
---

# Sub-plan: architecture docs

Pure documentation, no code change.
"""

# AC-6: finding text must name importing files (verified in step 2).
# Placeholder — tested when the lint is implemented.


# ── tests ────────────────────────────────────────────────────────────

class TestSharedModuleOneFileGate:
    """AC-1: shared module + one-file gate -> finding."""

    @pytest.mark.xfail(
        strict=True,
        reason="shared-module gate lint not implemented yet (step 1-2)",
    )
    def test_finding_for_shared_module_one_file_gate(self, tmp_path: Path) -> None:
        findings = _run_lint(
            SHARED_MODULE_ONE_FILE_GATE, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        assert any("shared_module" in f.lower() or "caller" in f.lower()
                    for f in findings), (
            f"Expected a finding about shared_module having importers.\n"
            f"Findings: {findings}"
        )


class TestGhResolveStep3:
    """AC-2: the real regression case — return-type change with production caller."""

    @pytest.mark.xfail(
        strict=True,
        reason="shared-module gate lint not implemented yet (step 1-2)",
    )
    def test_finding_for_ghresolve_shape(self, tmp_path: Path) -> None:
        findings = _run_lint(
            GHRESOLVE_STEP3, tmp_path,
            project_files=_GHRESOLVE_PROJECT,
        )
        assert any("shared_module" in f.lower() or "caller" in f.lower()
                    or "importer" in f.lower() or "watch" in f.lower()
                    for f in findings), (
            f"Expected a finding about the return-type change with caller.\n"
            f"Findings: {findings}"
        )


class TestWiderGateLaterStep:
    """AC-3: later step runs a directory -> silent (compliant)."""

    def test_no_finding_when_later_step_widens_gate(self, tmp_path: Path) -> None:
        findings = _run_lint(
            WIDER_GATE_LATER_STEP, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        # Must be silent — the sub-plan already widens its gate.
        shared_findings = [f for f in findings
                           if "shared_module" in f.lower()
                           or "caller" in f.lower()
                           or "importer" in f.lower()]
        assert not shared_findings, (
            f"Expected no shared-module finding when later step widens gate.\n"
            f"Findings: {shared_findings}"
        )


class TestLeafModuleGate:
    """AC-4: leaf module nothing imports -> silent."""

    def test_no_finding_for_leaf_module(self, tmp_path: Path) -> None:
        findings = _run_lint(
            LEAF_MODULE_GATE, tmp_path,
            project_files=_LEAF_PROJECT,
        )
        shared_findings = [f for f in findings
                           if "leaf_module" in f.lower()
                           or "importer" in f.lower()]
        assert not shared_findings, (
            f"Expected no finding for leaf module.\n"
            f"Findings: {shared_findings}"
        )


class TestDocsOnlySubplan:
    """AC-5: docs-only sub-plan -> silent."""

    def test_no_finding_for_docs_only(self, tmp_path: Path) -> None:
        findings = _run_lint(DOCS_ONLY, tmp_path)
        assert not findings, (
            f"Expected no finding for docs-only sub-plan.\n"
            f"Findings: {findings}"
        )
