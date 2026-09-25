"""Tests for import-graph test discovery in the shared-module gate lint.

The lint currently resolves callers' tests by filename
(``tests/test_{importer}.py``).  When a test file has a different name but
imports the importer, the lint misses it.  These tests pin the new behaviour:
walk the import graph (one level of production importers + test files that
import them) to discover test files the gate should cover.

AC-1: module ``claim``, production importer ``reconcile``, test file
      ``tests/test_waits_for_runner.py`` (imports ``reconcile``).  Gate runs
      only ``tests/test_claim.py`` ⇒ the finding names
      ``tests/test_waits_for_runner.py``.

AC-2: gate includes ``tests/test_waits_for_runner.py`` ⇒ no finding for it.

AC-3: a test that mentions the module only in a string or comment is not
      counted.
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
from plan_lint import lint_file  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

def _slug_aligned_name(filename: str, content: str) -> str:
    """Filename whose derived slug matches the fixture's own ``plan:`` field."""
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
        return lint_file(str(sp))
    finally:
        _pl._PROJECT_ROOT = old_root


def _shared_module_findings(findings: list[str]) -> list[str]:
    """Filter findings to only shared-module-gate ones.

    Matches on the finding's stable phrases ("callers' integration",
    "callers' tests", "imported by") rather than module names that could
    appear in unrelated findings.
    """
    keywords = ("callers' integration", "callers' tests", "imported by",
                "misses")
    return [f for f in findings if any(kw in f.lower() for kw in keywords)]


# ── project file fixtures ────────────────────────────────────────────
#
# Shape:  claim.py  ←  reconcile.py  ←  tests/test_waits_for_runner.py
# The gate covers tests/test_claim.py only.

_CLAIM_MODULE = """\
def process_claim(data: dict) -> dict:
    return {"status": "processed", **data}
"""

_RECONCILE_IMPORTS_CLAIM = """\
from claim import process_claim

def reconcile_from_github(raw: dict) -> dict:
    return process_claim(raw)
"""

# The test that imports reconcile — has a different name than test_claim.py.
_TEST_WAITS_FOR_RUNNER = """\
from reconcile import reconcile_from_github

def test_reconcile_waits_for_the_shared_runner():
    result = reconcile_from_github({"id": 1})
    assert result["status"] == "processed"
"""

_TEST_CLAIM = """\
from claim import process_claim

def test_process_claim():
    result = process_claim({"id": 1})
    assert result["status"] == "processed"
"""

# A test that mentions "claim" only in a string or comment — must NOT count.
_TEST_STRING_MENTION = """\
# This test does not import claim at all.
def test_unrelated():
    # "claim" appears in this comment but is not an import.
    assert "claim" in "a claim was filed"
"""

_PROJECT_FULL = {
    "src/claim.py": _CLAIM_MODULE,
    "src/reconcile.py": _RECONCILE_IMPORTS_CLAIM,
    "tests/test_claim.py": _TEST_CLAIM,
    "tests/test_waits_for_runner.py": _TEST_WAITS_FOR_RUNNER,
}

_PROJECT_WITH_STRING_MENTION = {
    **_PROJECT_FULL,
    "tests/test_string_mention.py": _TEST_STRING_MENTION,
}


# ── sub-plan fixtures ────────────────────────────────────────────────

# AC-1: gate covers only tests/test_claim.py.  The lint should find that
# tests/test_waits_for_runner.py (which imports reconcile, which imports
# claim) is missing from the gate.
AC1_GATE_COVERS_ONLY_MODULE = """\
---
plan: test-import-walk-ac1
scope_paths:
  - "src/claim.py"
local_checks:
  - command: python3 -m pytest tests/test_claim.py -q
    timeout: 60
---

# Sub-plan: fix claim

Changes `process_claim` in a module that `reconcile.py` imports.
Gate runs only `tests/test_claim.py` — misses the test that exercises
the claim→reconcile integration path.
"""

# AC-2 (control): gate covers both tests/test_claim.py AND
# tests/test_waits_for_runner.py.  No finding expected.
AC2_GATE_COVERS_MODULE_AND_WALKED_TEST = """\
---
plan: test-import-walk-ac2
scope_paths:
  - "src/claim.py"
local_checks:
  - command: python3 -m pytest tests/test_claim.py tests/test_waits_for_runner.py -q
    timeout: 120
---

# Sub-plan: fix claim with integration test

Gate covers both `tests/test_claim.py` (the module) and
`tests/test_waits_for_runner.py` (discovered via import walk).
No finding expected.
"""


# ── tests ────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
class TestImportWalkFindsWalkedTest:
    """AC-1: module claim, importer reconcile, test imports reconcile.

    Gate covers only tests/test_claim.py ⇒ the finding must name
    tests/test_waits_for_runner.py.
    """

    def test_finding_names_the_walked_test(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC1_GATE_COVERS_ONLY_MODULE, tmp_path,
            project_files=_PROJECT_FULL,
        )
        sm = _shared_module_findings(findings)
        assert sm, (
            f"Expected a finding about tests/test_waits_for_runner.py "
            f"being missing from the gate.\nFindings: {findings}"
        )
        # The finding must name the specific test file.
        assert any("waits_for_runner" in f.lower() for f in sm), (
            f"Expected finding to name 'test_waits_for_runner'.\nFindings: {sm}"
        )


@pytest.mark.xfail(strict=True, reason="red-first")
class TestGateIncludesWalkedTest:
    """AC-2: gate includes the walked test file ⇒ no finding.

    Today the lint resolves callers' tests by filename (``tests/test_reconcile.py``),
    so it cannot recognise that ``tests/test_waits_for_runner.py`` covers
    ``reconcile``.  After step 1 implements import-walk discovery, the lint
    will see that the gate already covers the walked test and stay silent.
    """

    def test_no_finding_when_gate_covers_walked_test(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC2_GATE_COVERS_MODULE_AND_WALKED_TEST, tmp_path,
            project_files=_PROJECT_FULL,
        )
        sm = _shared_module_findings(findings)
        assert not sm, (
            f"Expected no shared-module finding when gate covers the walked test.\n"
            f"Findings: {sm}"
        )


class TestStringMentionNotCounted:
    """AC-3: a test that mentions the module only in a string or comment
    must NOT be counted as an importer.

    The lint must use ``ast`` parsing (not grep) to detect real imports.
    A file that contains ``"claim"`` in a string or comment but never
    ``import claim`` or ``from claim import …`` must not appear in the
    finding.
    """

    def test_string_mention_is_not_an_import(self, tmp_path: Path) -> None:
        findings = _run_lint(
            AC1_GATE_COVERS_ONLY_MODULE, tmp_path,
            project_files=_PROJECT_WITH_STRING_MENTION,
        )
        # No finding should mention test_string_mention — it has no real import.
        assert not any("string_mention" in f.lower() for f in findings), (
            f"test_string_mention should not be counted as an importer "
            f"(it only mentions 'claim' in a string/comment).\nFindings: {findings}"
        )