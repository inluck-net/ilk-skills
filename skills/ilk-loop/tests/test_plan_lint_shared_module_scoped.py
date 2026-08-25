"""Red-first tests for the change-scoped shared-module gate rule.

After the rule change (sub-plan step 1), `lint_shared_module_gate` accepts a
gate that covers the changed module AND its resolved callers' tests — a
whole-suite gate is no longer required.  These tests encode that new rule.

AC-4 (both directions):
  - module + callers gate → lint does NOT fire  (RED today — the lint
    currently clears only on a whole-suite command)
  - module-only gate      → lint still fires    (GREEN today — the lint
    already fires on this shape)

AC-6: §12's old "LAST step must run the FULL test suite" phrasing is gone.
  (RED today — step 2 has not rewritten §12 yet)

Total expected-red: ≥2 (AC-4 pass direction + AC-6).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint as _pl  # noqa: E402
from plan_lint import lint_file  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

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
            p.write_text(content, encoding="utf-8")

    sp = tmp_path / "test-subplan.md"
    sp.write_text(subplan_text, encoding="utf-8")

    old_root = _pl._PROJECT_ROOT
    try:
        _pl._PROJECT_ROOT = tmp_path
        return lint_file(str(sp))
    finally:
        _pl._PROJECT_ROOT = old_root


def _shared_module_findings(findings: list[str]) -> list[str]:
    """Filter findings to only shared-module-gate ones."""
    keywords = ("shared_module", "importer", "caller")
    return [f for f in findings if any(kw in f.lower() for kw in keywords)]


# ── project file fixtures ────────────────────────────────────────────

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

_TEST_SHARED_MODULE = """\
from shared_module import reconcile_from_github

def test_reconcile():
    result = reconcile_from_github()
    assert isinstance(result, set)
"""

_TEST_CALLER = """\
from caller import watch

def test_watch():
    result = watch()
    assert result is not None
"""

_SHARED_PROJECT = {
    "src/shared_module.py": _SHARED_MODULE,
    "src/caller.py": _CALLER_IMPORTS_MODULE,
    "tests/test_shared_module.py": _TEST_SHARED_MODULE,
    "tests/test_caller.py": _TEST_CALLER,
}


# ── sub-plan fixtures ────────────────────────────────────────────────

# AC-4 PASS direction: gate covers the module AND its callers' tests.
# After the rule change, this should satisfy the lint.  Today, the lint
# fires because it only clears on a whole-suite command → RED.
MODULE_PLUS_CALLERS_GATE = """\
---
plan: test-change-scoped-pass
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py tests/test_caller.py -q
    timeout: 120
---

# Sub-plan: fix shared_module

Gate covers `tests/test_shared_module.py` (the module) AND
`tests/test_caller.py` (a resolved caller).  The change-scoped rule
should accept this without requiring a whole-suite gate.
"""

# AC-4 FAIL direction: gate covers only the module's own tests.
# After the rule change, this should still be flagged.  Today the lint
# fires too, so this test passes today → GREEN.
MODULE_ONLY_GATE = """\
---
plan: test-change-scoped-fail
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py -q
    timeout: 60
---

# Sub-plan: fix shared_module

Gate covers only `tests/test_shared_module.py`.  The caller at
`src/caller.py` imports `reconcile_from_github` and its integration
is never exercised.
"""


# ── AC-4 tests ───────────────────────────────────────────────────────

class TestModulePlusCallersGatePasses:
    """AC-4 PASS: module + callers gate → lint does NOT fire."""

    def test_no_finding_when_gate_covers_module_and_callers(
        self, tmp_path: Path,
    ) -> None:
        findings = _run_lint(
            MODULE_PLUS_CALLERS_GATE, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert not sm, (
            f"Lint should accept a gate covering module + resolved callers.\n"
            f"Findings: {sm}"
        )


class TestModuleOnlyGateStillFails:
    """AC-4 FAIL: module-only gate → lint still fires."""

    def test_finding_when_gate_covers_only_module(self, tmp_path: Path) -> None:
        findings = _run_lint(
            MODULE_ONLY_GATE, tmp_path,
            project_files=_SHARED_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert sm, (
            f"Lint should still flag a gate that covers only the module.\n"
            f"Findings: {sm}"
        )


# ── AC-6 test ────────────────────────────────────────────────────────

class TestOldPhrasingGone:
    """AC-6: §12's old 'LAST step must run the FULL test suite' is gone."""

    def test_decomposition_principles_no_old_shared_module_rule(self) -> None:
        principles = (
            Path(__file__).resolve().parent.parent
            / "references" / "decomposition-principles.md"
        )
        text = principles.read_text(encoding="utf-8")
        assert "LAST step must run the FULL test suite" not in text, (
            "§12 still contains the old 'LAST step must run the FULL test suite' "
            "phrasing.  Step 2 should have replaced it with the batch-gate "
            "escalation language."
        )
