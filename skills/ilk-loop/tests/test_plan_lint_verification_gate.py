#!/usr/bin/env python3
"""RED test — verification-gate and at-base-quality gates.

Tests that plan_lint.py catches two defects in batch-verification sub-plans:

Defect A (AC-1): a ``batch_verification: true`` sub-plan whose declared gates
  are ALL compile-only (typecheck, not a suite).  The batch-wide obligation
  cannot be discharged by a typecheck alone.

Defect B (AC-2/3/4): a ``batch_verification: true`` sub-plan whose
  ``## At-base rerun`` section is present but only carries the template's
  own example rows (``tests/test_foo.py::test_bar``,
  ``tests/test_baz.py::test_qux``) — a placeholder, not a filled table.

AC-5 (false-positive guard): a real filled table is not flagged, and a
  sub-plan without ``batch_verification: true`` is not touched by AC-1.

Part of sub-plan a-verification-gate-must-verify (step 0).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
_PLAN_LINT = _SCRIPTS / "plan_lint.py"
_TEMPLATE = _HERE.parent / "templates" / "batch-verification-subplan.md"

sys.path.insert(0, str(_SCRIPTS))
import plan_lint  # noqa: E402


def _findings_for(tmp_path: Path, filename: str, content: str) -> list[str]:
    """Write a sub-plan, lint it via the Python API, and return finding messages.

    Uses the Python API (``plan_lint.lint_file``) so findings are returned as
    plain message strings with the slug embedded by the linter, not by the CLI.
    """
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return plan_lint.lint_file(p)


def _template_example_node_ids() -> list[str]:
    """Extract the template's example node ids from batch-verification-subplan.md.

    These are the placeholder rows the template uses as illustration.
    A sub-plan that carries only these rows has not been filled.
    """
    text = _TEMPLATE.read_text(encoding="utf-8")
    import re
    return re.findall(r"\|\s*((?:tests/|test/)[^\s|]+::\w+)\s*\|", text)


# ── Defect A: compile-only verification gate ─────────────────────────────
#
# The fixture carries a real at-base table with the worktree mechanism so
# ``lint_verification_attribution_unmeasured`` passes.  The gate is a
# compile-only typecheck (``mypy --strict``), which is not a test suite.
#
# NOTE: the fixture's body contains ``pytest`` (in the at-base example),
# which triggers ``lint_verification_subplan_hardcodes_suite`` — that is a
# pre-existing lint and unrelated to the compile-only defect.  The test
# checks that a finding about the *gate being compile-only* exists, not that
# every finding is about it.

_SUBPLAN_COMPILE_ONLY_GATE = """\
---
plan: test-bv-gate-only
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks:
  - command: "mypy --strict src/"
    timeout: 120
---

# Sub-plan: batch verification — gate runs a checker, not a suite

Part of [MASTER-2026-09-15-execution-plan](./MASTER-2026-09-15-execution-plan.md).
**Order #2** — runs the batch-wide verification.

## At-base rerun

Base: abc123 · worktree: detached · command: pytest failing_ids

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" abc123
( cd "$WT" && pytest tests/test_example.py::test_something )

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_example.py::test_something | passed | no | YES |
"""


class TestDefectACompileOnlyGate:
    """Defect A: a batch-verification gate that is only a typecheck."""

    def test_compile_only_gate_is_hard_finding(self, tmp_path: Path):
        """A batch_verification sub-plan with only a compile-only gate is flagged."""
        findings = _findings_for(tmp_path, "bv-gate-only.md", _SUBPLAN_COMPILE_ONLY_GATE)
        hard = [f for f in findings if "HARD" in f]
        assert hard, (
            f"Expected at least one HARD finding for compile-only gate. "
            f"Findings: {findings}"
        )
        # The finding must describe the gate as compile-only.  The slug
        # ``bv-gate-only`` does not contain compile/typecheck keywords, so
        # a match here means the finding text itself describes the defect.
        gate_findings = [
            f for f in hard
            if "compile" in f.lower()
            or "typecheck" in f.lower()
            or "not a suite" in f.lower()
            or "not run" in f.lower()
            or "no suite" in f.lower()
        ]
        assert gate_findings, (
            f"HARD finding must describe a compile-only gate. "
            f"Findings: {hard}"
        )


# ── Defect B: template example rows verbatim ─────────────────────────────
#
# The fixture carries the template's own placeholder node ids and the
# worktree mechanism so the existing lints pass.  The only defect is the
# unfilled example rows.  The gate is a real suite invocation (not
# compile-only) so AC-1 does not fire.

_SUBPLAN_TEMPLATE_EXAMPLE_ROWS = """\
---
plan: test-bv-at-base-rows
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks:
  - command: "python3 -m pytest --timeout=60 --timeout-method=signal"
    timeout: 300
---

# Sub-plan: batch verification — at-base table not filled

Part of [MASTER-2026-09-15-execution-plan](./MASTER-2026-09-15-execution-plan.md).

## At-base rerun

Base: abc123 · worktree: detached · command: pytest

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" abc123
( cd "$WT" && pytest tests/test_foo.py::test_bar tests/test_baz.py::test_qux )

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_foo.py::test_bar | passed | no | YES |
| tests/test_baz.py::test_qux | failed | no | no |
"""


class TestDefectBTemplateExampleRows:
    """Defect B: an at-base section carrying only the template's example rows."""

    def test_example_rows_is_hard_finding(self, tmp_path: Path):
        """A table with only the template's example rows is flagged as unfilled."""
        findings = _findings_for(tmp_path, "bv-at-base-rows.md", _SUBPLAN_TEMPLATE_EXAMPLE_ROWS)
        hard = [f for f in findings if "HARD" in f]
        assert hard, (
            f"Expected at least one HARD finding for example rows. "
            f"Findings: {findings}"
        )
        # The finding must describe the table as unfilled/placeholder/example.
        example_findings = [
            f for f in hard
            if "example" in f.lower()
            or "template" in f.lower()
            or "placeholder" in f.lower()
            or "unfilled" in f.lower()
        ]
        assert example_findings, (
            f"HARD finding must describe the table as unfilled. "
            f"Findings: {hard}"
        )

    def test_finding_names_the_placeholder_rows(self, tmp_path: Path):
        """The finding names the unfilled example node ids."""
        findings = _findings_for(tmp_path, "bv-at-base-rows.md", _SUBPLAN_TEMPLATE_EXAMPLE_ROWS)
        all_text = " ".join(findings)
        node_ids = _template_example_node_ids()
        assert len(node_ids) >= 2, f"Expected at least 2 template node ids, got {node_ids}"
        for nid in node_ids:
            assert nid in all_text, (
                f"Finding should name the placeholder row '{nid}'. Findings: {findings}"
            )


# ── Defect B, variant: empty table, no suite gate ────────────────────────

_SUBPLAN_EMPTY_TABLE_NO_SUITE = """\
---
plan: test-bv-table-rows
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks: []
---

# Sub-plan: batch verification — at-base section with only header

## At-base rerun

Base: abc123 · worktree: detached · command: pytest

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" abc123

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
"""


class TestDefectBEmptyTableNoSuite:
    """Defect B variant: at-base section present but zero data rows, no suite gate."""

    def test_empty_table_no_suite_is_hard_finding(self, tmp_path: Path):
        """An empty at-base table with no suite gate is flagged."""
        findings = _findings_for(tmp_path, "bv-table-rows.md", _SUBPLAN_EMPTY_TABLE_NO_SUITE)
        hard = [f for f in findings if "HARD" in f]
        assert hard, (
            f"Expected at least one HARD finding for empty table with no suite gate. "
            f"Findings: {findings}"
        )
        # The finding must describe the table as empty/unfilled.
        empty_findings = [
            f for f in hard
            if "empty" in f.lower()
            or "zero" in f.lower()
            or "no data" in f.lower()
            or "unfilled" in f.lower()
        ]
        assert empty_findings, (
            f"HARD finding must describe the table as empty. "
            f"Findings: {hard}"
        )


# ── AC-5: false-positive guard ───────────────────────────────────────────

_SUBPLAN_REAL_FILLED_TABLE = """\
---
plan: test-real-at-base-table
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks:
  - command: "python3 -m pytest --timeout=60"
    timeout: 300
---

# Sub-plan: batch verification — real filled table

## At-base rerun

Base: def456 · worktree: detached · command: pytest tests/test_auth.py::test_login

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" def456
( cd "$WT" && pytest tests/test_auth.py::test_login )

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_auth.py::test_login | passed | no | YES |
"""

_SUBPLAN_NOT_BATCH_VERIFICATION = """\
---
plan: test-not-batch-verification
status: pending
current_step: 0
priority: P0
estimated_steps: 3
last_updated: 2026-09-15
verification_tier: loop-verified
must_add_tests: true
scope_paths:
  - "src/foo.py"
local_checks:
  - command: "python3 -m pytest src/foo.py -q --timeout=60"
    timeout: 60
---

# Sub-plan: implement feature X

Not a batch-verification sub-plan; no verification obligation.
"""


class TestAC5FalsePositiveGuard:
    """AC-5: real filled tables pass; non-batch sub-plans unaffected."""

    def test_real_filled_table_has_no_example_finding(self, tmp_path: Path):
        """A real filled at-base table is not flagged by the example-rows check."""
        findings = _findings_for(tmp_path, "real-filled.md", _SUBPLAN_REAL_FILLED_TABLE)
        # No finding should mention example/template/placeholder.
        example_findings = [
            f for f in findings
            if "example" in f.lower()
            or "template" in f.lower()
            or "placeholder" in f.lower()
            or "unfilled" in f.lower()
        ]
        assert not example_findings, (
            f"Real filled table should not trigger example-rows finding. "
            f"Got: {example_findings}"
        )

    def test_non_batch_subplan_not_affected_by_compile_only(self, tmp_path: Path):
        """A sub-plan without batch_verification: true is not touched by AC-1."""
        findings = _findings_for(tmp_path, "not-batch.md", _SUBPLAN_NOT_BATCH_VERIFICATION)
        compile_findings = [
            f for f in findings
            if "HARD" in f
            and ("compile" in f.lower() or "typecheck" in f.lower())
        ]
        assert not compile_findings, (
            f"Non-batch sub-plan should not trigger compile-only finding. "
            f"Got: {compile_findings}"
        )

    def test_template_example_node_ids_extracted(self):
        """The template's example node ids can be extracted programmatically."""
        node_ids = _template_example_node_ids()
        assert len(node_ids) >= 2, (
            f"Expected at least 2 template example node ids, got: {node_ids}"
        )
        # They should be the known placeholders.
        assert any("test_foo" in nid for nid in node_ids), (
            f"Expected test_foo placeholder, got: {node_ids}"
        )
        assert any("test_baz" in nid for nid in node_ids), (
            f"Expected test_baz placeholder, got: {node_ids}"
        )
