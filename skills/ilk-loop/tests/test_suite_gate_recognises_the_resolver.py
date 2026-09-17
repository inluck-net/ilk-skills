#!/usr/bin/env python3
"""RED tests — _has_suite_gate must recognise the verification_record resolver.

Sub-plan: the-linter-sees-the-suite-it-mandates (step 0).

The defect: ``plan_lint._SUITE_GATE_RE`` matches only literal test-runner
names (pytest, vitest, jest, …).  The batch-verification template mandates
``verification_record.py --run-suite``, which *resolves* the pytest invocation
from ``.ilk-launch.json`` via ``ship_audit._resolve_expected_invocation`` — the
literal string ``pytest`` never appears in the plan, and ``_has_suite_gate``
returns False.

Acceptance criteria:

  AC-1  A verification sub-plan whose only gate is
        ``verification_record.py ... --run-suite`` is recognised as declaring
        a suite gate.
  AC-2  The same file without ``--run-suite`` is NOT recognised — the flag,
        not the script name, is the promise.
  AC-3  A verification sub-plan with an empty at-base table and no gate at all
        still produces the HARD finding (the check's original purpose survives).
  AC-4  Linting this batch's own
        ``2026-09-17-state-truth-after-a-red-gate-verify.md`` yields 0 HARD
        findings (fixture copy under ``tmp_path``, not the live plans dir).
  AC-5  The existing literal runners (``pytest``, ``vitest``, ``jest``, …)
        are still recognised.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
_PLANS_DIR = Path.home() / ".ilk-data" / "projects" / "users-chad-projects-github-inluck-net-ilk-skills" / "plans"

import sys
sys.path.insert(0, str(_SCRIPTS))
import plan_lint  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────

def _has_suite(text: str) -> bool:
    """Thin wrapper around the private predicate under test."""
    return plan_lint._has_suite_gate(text)


def _findings_for(tmp_path: Path, filename: str, content: str) -> list[str]:
    """Write a sub-plan to tmp_path and return lint findings."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return plan_lint.lint_file(p)


# ── AC-1: resolver with --run-suite IS a suite gate ───────────────────────

_FIXTURE_RESOLVER_WITH_FLAG = """\
---
plan: test-resolver-gate
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-17
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks:
  - command: "python3 /path/to/skills/ilk-loop/scripts/verification_record.py --project . --batch batch-2026-09-17 --base-sha abc123 --run-suite --suite-timeout 1200"
    timeout: 1200
---

# Sub-plan: batch verification — resolver gate

Part of [MASTER-2026-09-17-execution-plan](./MASTER-2026-09-17-execution-plan.md).

## At-base rerun

_(no failures)_
"""


class TestAC1ResolverWithRunSuite:
    """AC-1: verification_record.py --run-suite counts as a suite gate."""

    def test_has_suite_gate_returns_true(self):
        """The resolver with --run-suite is recognised."""
        assert _has_suite(_FIXTURE_RESOLVER_WITH_FLAG), (
            "_has_suite_gate should return True for "
            "verification_record.py --run-suite"
        )

    def test_no_hard_finding_for_empty_at_base_table(self, tmp_path: Path):
        """With the resolver recognised, an empty at-base table is fine."""
        findings = _findings_for(
            tmp_path, "resolver-gate.md", _FIXTURE_RESOLVER_WITH_FLAG
        )
        hard = [f for f in findings if "HARD" in f]
        # The empty-table check should not fire because has_suite is True.
        empty_table_hard = [
            f for f in hard if "zero data rows" in f.lower()
            or "no test-suite gate" in f.lower()
        ]
        assert not empty_table_hard, (
            f"Resolver with --run-suite should suppress the empty-table "
            f"finding. Got: {empty_table_hard}"
        )


# ── AC-2: resolver WITHOUT --run-suite is NOT a suite gate ────────────────

_FIXTURE_RESOLVER_NO_FLAG = """\
---
plan: test-resolver-no-flag
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-17
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks:
  - command: "python3 /path/to/skills/ilk-loop/scripts/verification_record.py --project . --batch batch-2026-09-17 --base-sha abc123"
    timeout: 30
---

# Sub-plan: verification_record without --run-suite

Part of [MASTER-2026-09-17-execution-plan](./MASTER-2026-09-17-execution-plan.md).

## At-base rerun

Base: abc123 · worktree: detached · command: pytest

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" abc123

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| tests/test_example.py::test_something | passed | no | YES |
"""


class TestAC2ResolverWithoutRunSuite:
    """AC-2: verification_record.py without --run-suite is NOT a suite gate."""

    def test_has_suite_gate_returns_false(self):
        """The resolver without --run-suite is not recognised."""
        assert not _has_suite(_FIXTURE_RESOLVER_NO_FLAG), (
            "_has_suite_gate should return False for "
            "verification_record.py without --run-suite"
        )


# ── AC-3: empty table + no gate → HARD finding (original purpose survives) ─

_FIXTURE_EMPTY_TABLE_NO_GATE = """\
---
plan: test-empty-no-gate
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-17
verification_tier: loop-verified
must_add_tests: false
scope_paths: []
local_checks: []
---

# Sub-plan: batch verification — no gate at all

Part of [MASTER-2026-09-17-execution-plan](./MASTER-2026-09-17-execution-plan.md).

## At-base rerun

Base: abc123 · worktree: detached · command: pytest

WT="$(mktemp -d)/base-wt"
git worktree add --detach "$WT" abc123

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
"""


class TestAC3EmptyTableNoGate:
    """AC-3: empty at-base table + no suite gate → HARD finding."""

    def test_hard_finding_for_empty_table_no_gate(self, tmp_path: Path):
        """The original HARD finding still fires when there's no gate."""
        findings = _findings_for(
            tmp_path, "empty-no-gate.md", _FIXTURE_EMPTY_TABLE_NO_GATE
        )
        hard = [f for f in findings if "HARD" in f]
        assert hard, (
            f"Expected HARD finding for empty table with no suite gate. "
            f"Findings: {findings}"
        )
        empty_findings = [
            f for f in hard
            if "zero data rows" in f.lower()
            or "no test-suite gate" in f.lower()
        ]
        assert empty_findings, (
            f"HARD finding must describe empty table / no suite gate. "
            f"Findings: {hard}"
        )


# ── AC-4: the live verification sub-plan lints clean ──────────────────────

_VERIFY_SUBPLAN = _PLANS_DIR / "2026-09-17-state-truth-after-a-red-gate-verify.md"


@pytest.mark.skipif(
    not _VERIFY_SUBPLAN.exists(),
    reason="Live verification sub-plan not found",
)
class TestAC4LiveVerificationLintsClean:
    """AC-4: linting this batch's own verification sub-plan yields 0 HARD findings."""

    def test_no_hard_findings(self, tmp_path: Path):
        """The verification sub-plan must lint clean when copied to tmp_path."""
        text = _VERIFY_SUBPLAN.read_text(encoding="utf-8-sig")
        dest = tmp_path / _VERIFY_SUBPLAN.name
        dest.write_text(text, encoding="utf-8")
        findings = plan_lint.lint_file(dest)
        hard = [f for f in findings if "HARD" in f]
        assert not hard, (
            f"Verification sub-plan should produce 0 HARD findings, "
            f"got {len(hard)}: {hard}"
        )


# ── AC-5: existing literal runners still recognised ───────────────────────

def _subplan_with_gate(command: str) -> str:
    """Wrap a single local_checks command in a minimal sub-plan."""
    return textwrap.dedent(f"""\
        ---
        plan: test-runner-{hash(command) & 0xFFFF:04x}
        batch_verification: true
        status: pending
        current_step: 0
        priority: P0
        estimated_steps: 2
        last_updated: 2026-09-17
        verification_tier: loop-verified
        must_add_tests: false
        scope_paths: []
        local_checks:
          - command: "{command}"
            timeout: 300
        ---

        # Sub-plan: test runner recognition

        ## At-base rerun

        _(no failures)_
    """)


@pytest.mark.parametrize("runner", [
    "pytest",
    "python3 -m pytest --timeout=60 --timeout-method=signal",
    "unittest",
    "vitest run",
    "jest --coverage",
    "mocha --recursive",
    "cargo test",
    "go test ./...",
    "npm test",
])
class TestAC5ExistingRunnersStillWork:
    """AC-5: the existing literal runners are still recognised."""

    def test_runner_recognised(self, runner: str):
        """The runner string is recognised by _has_suite_gate."""
        assert _has_suite(_subplan_with_gate(runner)), (
            f"_has_suite_gate should recognise '{runner}'"
        )