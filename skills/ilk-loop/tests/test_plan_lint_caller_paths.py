"""Red-first tests for caller-lint path resolution.

The current ``_resolve_test_paths`` constructs ``tests/test_<module>.py``
relative to the project root.  This repo keeps its tests at
``skills/<skill>/tests/test_<module>.py``, so the resolver returns empty
and ``lint_shared_module_gate`` fires no matter what a sub-plan gates.

AC-1: resolve ship_audit / loop_status / batch_gate to their real paths.
AC-2: a module+caller gate clears the lint (fixture uses real layout).
AC-3: a module-only gate still fires (regression net).
AC-4: a top-level ``tests/`` layout still resolves (other projects).
AC-5: unresolvable ⇒ report nothing, not a finding.
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
    """Write a sub-plan (and optional project files) to *tmp_path*, run lint."""
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


# ── AC-1: resolve real paths for this repo's layout ──────────────────

class TestResolveRealPaths:
    """AC-1: modules under skills/<skill>/scripts/ resolve to their real
    test paths under skills/<skill>/tests/."""

    @pytest.mark.parametrize("module_name", ["ship_audit", "loop_status", "batch_gate"])
    def test_resolves_skills_layout(self, module_name: str, tmp_path: Path) -> None:
        """The resolver must find skills/ilk-loop/tests/test_<module>.py."""
        # Build a fixture tree mimicking this repo's layout.
        skill_dir = tmp_path / "skills" / "ilk-loop"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "scripts" / f"{module_name}.py").write_text(
            f"# {module_name}\n", encoding="utf-8",
        )
        (skill_dir / "tests").mkdir(parents=True)
        test_file = skill_dir / "tests" / f"test_{module_name}.py"
        test_file.write_text(f"# test {module_name}\n", encoding="utf-8")

        resolved = _pl._resolve_test_paths(
            [f"tests/test_{module_name}.py"], tmp_path,
        )
        assert str(test_file) in resolved, (
            f"_resolve_test_paths should find {test_file}, got {resolved}"
        )


# ── AC-2: module+caller gate clears ──────────────────────────────────

_SKILLS_LAYOUT_PROJECT = {
    "skills/ilk-loop/scripts/shared_module.py": "def reconcile(): return set()\n",
    "skills/ilk-loop/scripts/caller.py": (
        "from shared_module import reconcile\n"
        "def watch(): return reconcile()\n"
    ),
    "skills/ilk-loop/tests/test_shared_module.py": (
        "def test_reconcile(): pass\n"
    ),
    "skills/ilk-loop/tests/test_caller.py": (
        "def test_watch(): pass\n"
    ),
}

_MODULE_PLUS_CALLERS_GATE_SKILLS = """\
---
plan: test-skills-layout-pass
scope_paths:
  - "skills/ilk-loop/scripts/shared_module.py"
local_checks:
  - command: pytest skills/ilk-loop/tests/test_shared_module.py skills/ilk-loop/tests/test_caller.py -q
    timeout: 120
---

# Sub-plan: fix shared_module

Gate covers the module AND the caller's tests in the skills/ layout.
"""


class TestModulePlusCallersGateClearsSkillsLayout:
    """AC-2: module+caller gate clears the lint in the skills/ layout."""

    def test_no_finding(self, tmp_path: Path) -> None:
        findings = _run_lint(
            _MODULE_PLUS_CALLERS_GATE_SKILLS, tmp_path,
            project_files=_SKILLS_LAYOUT_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert not sm, (
            f"Lint should accept a gate covering module + callers in skills/ layout.\n"
            f"Findings: {sm}"
        )


# ── AC-3: module-only gate still fires ───────────────────────────────

_MODULE_ONLY_GATE_SKILLS = """\
---
plan: test-skills-layout-fail
scope_paths:
  - "skills/ilk-loop/scripts/shared_module.py"
local_checks:
  - command: pytest skills/ilk-loop/tests/test_shared_module.py -q
    timeout: 60
---

# Sub-plan: fix shared_module

Gate covers only the module's own tests.
"""


class TestModuleOnlyGateStillFires:
    """AC-3: module-only gate still fires (regression net)."""

    def test_finding_present(self, tmp_path: Path) -> None:
        findings = _run_lint(
            _MODULE_ONLY_GATE_SKILLS, tmp_path,
            project_files=_SKILLS_LAYOUT_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert sm, (
            f"Lint should still flag a module-only gate.\n"
            f"Findings: {sm}"
        )


# ── AC-4: top-level tests/ layout still resolves ─────────────────────

_TOPLEVEL_PROJECT = {
    "src/shared_module.py": "def reconcile(): return set()\n",
    "src/caller.py": (
        "from shared_module import reconcile\n"
        "def watch(): return reconcile()\n"
    ),
    "tests/test_shared_module.py": "def test_reconcile(): pass\n",
    "tests/test_caller.py": "def test_watch(): pass\n",
}

_MODULE_PLUS_CALLERS_GATE_TOPLEVEL = """\
---
plan: test-toplevel-pass
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py tests/test_caller.py -q
    timeout: 120
---

# Sub-plan: fix shared_module

Gate covers the module AND the caller's tests in the top-level layout.
"""


class TestTopLevelLayoutStillResolves:
    """AC-4: a top-level tests/ layout still resolves (other projects)."""

    def test_no_finding(self, tmp_path: Path) -> None:
        findings = _run_lint(
            _MODULE_PLUS_CALLERS_GATE_TOPLEVEL, tmp_path,
            project_files=_TOPLEVEL_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert not sm, (
            f"Lint should accept a gate in top-level tests/ layout.\n"
            f"Findings: {sm}"
        )


# ── AC-5: unresolvable ⇒ report nothing ──────────────────────────────

_UNRESOLVABLE_PROJECT = {
    "src/shared_module.py": "def reconcile(): return set()\n",
    "src/caller.py": (
        "from shared_module import reconcile\n"
        "def watch(): return reconcile()\n"
    ),
    # No test files at all.
}

_GATE_FOR_UNRESOLVABLE = """\
---
plan: test-unresolvable
scope_paths:
  - "src/shared_module.py"
local_checks:
  - command: pytest tests/test_shared_module.py -q
    timeout: 60
---

# Sub-plan: fix shared_module

Gate references tests that don't exist anywhere.
"""


class TestUnresolvableReportsNothing:
    """AC-5: when tests cannot be located, the lint reports nothing."""

    def test_no_finding_for_unresolvable(self, tmp_path: Path) -> None:
        findings = _run_lint(
            _GATE_FOR_UNRESOLVABLE, tmp_path,
            project_files=_UNRESOLVABLE_PROJECT,
        )
        sm = _shared_module_findings(findings)
        assert not sm, (
            f"Lint should not fire when tests cannot be located.\n"
            f"Findings: {sm}"
        )


# ── AC-6: existing suites stay green ─────────────────────────────────

class TestExistingSuitesGreen:
    """AC-6: the existing plan_lint suites must stay green."""

    def test_import_plan_lint(self) -> None:
        """Smoke: plan_lint imports without error."""
        assert hasattr(_pl, "lint_file")
        assert hasattr(_pl, "_resolve_test_paths")
        assert hasattr(_pl, "_gate_covers_module_and_callers")
