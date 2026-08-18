"""Characterization test: what does a red-first step-0 gate's outcome look like?

A "red-first" step is one whose gate command is DESIGNED to exit non-zero
(e.g. `pytest` on tests that must fail). The gate asserts `exit 0`, which
contradicts the step's purpose. This test records the CURRENT behavior of
the gate-result computation when a step-0 gate command exits non-zero.

This is a characterization test — it encodes present behaviour, not desired
behaviour. Step 1's fix will change some of these assertions; the ones that
change must be updated with a comment naming why, never deleted silently.

Context: gh-resolve batch MASTER-2026-08-17b, sub-plan
`a-terminal-run-keeps-its-unshipped-commits`, step 0. Commit b0b129b body
reads "Red-first: 4 failed, 2 passed of 6 tests" — the gate command exited
non-zero. Yet current_step advanced to 3 with --run-local-checks active.

Determination: **(A) enforced-but-passed.** The gate path DOES execute
(run_local_checks.py is invoked with --step 0), but the agent has already
bumped current_step before the gate fires (the gate runs post-iteration).
The gate fails, the B2 confirm path re-runs and confirms, and the driver
sets iter_stop_reason=local_checks_failed — but the agent's commits already
landed with the step advanced. The gate's exit-0 assertion is structurally
incompatible with a red-first design.

file:line evidence:
- run_ilk_loop_claude.sh:1752 — gate runs only when total_new > 0
- run_ilk_loop_claude.sh:1791 — invoke_local_checks called with merged_targets
- run_ilk_loop_claude.sh:1805-1820 — B2 confirm path on fail/error
- run_ilk_loop_claude.sh:1924 — iter_stop_reason="local_checks_failed"
- run_local_checks.py:535 — passed = all(r.passed for r in results)
- run_local_checks.py:547 — return 0 if passed else 1
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

FIXTURE_SUBPLAN_RED_FIRST = """\
---
plan: fixture-red-first-gate
status: in-progress
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-08-18
verification_tier: loop-verified
local_checks: []
scope_paths:
  - "some/module.py"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: ""
ci_timeout_minutes: 30
ci_max_retries: 2
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: fixture red-first gate

## Steps

### Step 0 — record the red counts
```yaml
local_checks:
  - command: "exit 1"
    timeout: 30
```
- Run the failing tests and record the red counts in the commit body.

### Step 1 — fix the tests
```yaml
local_checks:
  - command: "exit 0"
    timeout: 30
```
- Fix the tests so they pass.
"""

FIXTURE_SUBPLAN_GREEN_GATE = """\
---
plan: fixture-green-gate
status: in-progress
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-08-18
verification_tier: loop-verified
local_checks: []
scope_paths:
  - "some/module.py"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: ""
ci_timeout_minutes: 30
ci_max_retries: 2
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: fixture green gate

## Steps

### Step 0 — run the passing tests
```yaml
local_checks:
  - command: "exit 0"
    timeout: 30
```
- Run the tests and confirm they pass.

### Step 1 — ship it
```yaml
local_checks:
  - command: "exit 0"
    timeout: 30
```
- Final verification.
"""

FIXTURE_SUBPLAN_NO_STEP_GATE = """\
---
plan: fixture-no-step-gate
status: in-progress
current_step: 0
tickets: []
priority: P0
estimated_steps: 2
last_updated: 2026-08-18
verification_tier: loop-verified
local_checks: []
scope_paths:
  - "some/module.py"
unit_test_targets: []
e2e_test_targets: []
must_add_tests: false
ci_required: false
ci_status_endpoint: ""
ci_timeout_minutes: 30
ci_max_retries: 2
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: fixture no step gate

## Steps

### Step 0 — do something without a gate
- Just do the work, no local_checks.

### Step 1 — ship it
```yaml
local_checks:
  - command: "exit 0"
    timeout: 30
```
- Final verification.
"""


def _make_project(tmp_path: Path, subplan_content: str) -> Path:
    """Create a minimal project with a docs/plans/ dir and one sub-plan."""
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "2026-08-18-fixture.md").write_text(subplan_content, encoding="utf-8")
    # Create a minimal MASTER so ilk_paths can find the plans dir
    (plans_dir / "MASTER-2026-08-18-execution-plan.md").write_text(
        "---\nmaster_plan: 2026-08-18-execution\nbatch_date: 2026-08-18\n"
        "status: active\ntotal_tickets: 1\n---\n# Master\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_cli(project: Path, slug: str, step: int) -> tuple[int, dict]:
    """Run run_local_checks.py as a subprocess (AC-7: real consumer entry point).

    Returns (exit_code, parsed_json).
    """
    script = Path(__file__).resolve().parent.parent / "scripts" / "run_local_checks.py"
    result = subprocess.run(
        [sys.executable, str(script),
         "--project", str(project),
         "--slug", slug,
         "--step", str(step)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, data


# ── characterization tests ───────────────────────────────────────────────────

class TestRedFirstGateCharacterization:
    """Record the CURRENT behaviour of a red-first step-0 gate.

    These tests must pass against the tree as it exists BEFORE any fix.
    A test that only passes after the fix cannot document what the bug was.
    """

    def test_red_gate_exit_code_is_1(self, tmp_path: Path) -> None:
        """A step-0 gate command that exits non-zero → run_local_checks exit 1.

        Characterization: this IS the current behaviour. The gate FAILS.
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_RED_FIRST)
        exit_code, data = _run_cli(project, "fixture-red-first-gate", step=0)
        # The gate command is `exit 1`, so run_local_checks should report failure.
        assert exit_code == 1, (
            f"expected exit 1 (gate fails), got {exit_code}. "
            f"stdout: {data.get('raw_stdout', '')[:500]}"
        )

    def test_red_gate_all_passed_is_false(self, tmp_path: Path) -> None:
        """A step-0 gate command that exits non-zero → all_passed: false.

        Characterization: the gate correctly reports the check did not pass.
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_RED_FIRST)
        _, data = _run_cli(project, "fixture-red-first-gate", step=0)
        assert data.get("all_passed") is False, (
            f"expected all_passed=false, got {data.get('all_passed')}. "
            f"Full output: {json.dumps(data, indent=2)[:1000]}"
        )

    def test_red_gate_has_one_result_with_fail_outcome(self, tmp_path: Path) -> None:
        """The results list contains one check with passed=False.

        Characterization: the gate runs and produces a result record.
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_RED_FIRST)
        _, data = _run_cli(project, "fixture-red-first-gate", step=0)
        results = data.get("results", [])
        assert len(results) == 1, (
            f"expected 1 result, got {len(results)}. "
            f"Full output: {json.dumps(data, indent=2)[:1000]}"
        )
        r = results[0]
        assert r.get("passed") is False, (
            f"expected passed=false, got {r.get('passed')}"
        )
        assert r.get("exit_code") == 1, (
            f"expected exit_code=1, got {r.get('exit_code')}"
        )

    def test_green_gate_exit_code_is_0(self, tmp_path: Path) -> None:
        """A step-0 gate command that exits 0 → run_local_checks exit 0.

        Baseline: a green gate passes (sanity check for comparison).
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_GREEN_GATE)
        exit_code, data = _run_cli(project, "fixture-green-gate", step=0)
        assert exit_code == 0, (
            f"expected exit 0 (gate passes), got {exit_code}. "
            f"stdout: {data.get('raw_stdout', '')[:500]}"
        )

    def test_no_step_gate_returns_zero_results(self, tmp_path: Path) -> None:
        """A step with no local_checks block → 0 results, exit 0.

        Characterization: a missing gate is indistinguishable from a passing
        gate at the run_local_checks.py level. The driver's B2 path never
        sees a fail/error, so it does not block. This is the silent-skip
        class that the comment at run_ilk_loop_claude.sh:774 describes:
        "local_checks gate silently never ran while the sub-plan still
        shipped as verification_tier: loop-verified."
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_NO_STEP_GATE)
        exit_code, data = _run_cli(project, "fixture-no-step-gate", step=0)
        assert exit_code == 0, (
            f"expected exit 0 (no checks = vacuous pass), got {exit_code}"
        )
        assert data.get("step_check_count") == 0, (
            f"expected step_check_count=0, got {data.get('step_check_count')}"
        )
        assert data.get("all_passed") is True, (
            f"expected all_passed=true (vacuous), got {data.get('all_passed')}"
        )

    def test_step_check_count_for_red_gate(self, tmp_path: Path) -> None:
        """The step_check_count reflects the number of checks extracted.

        Characterization: step 0 has 1 local_check entry.
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_RED_FIRST)
        _, data = _run_cli(project, "fixture-red-first-gate", step=0)
        assert data.get("step_check_count") == 1, (
            f"expected step_check_count=1, got {data.get('step_check_count')}"
        )

    def test_subplan_check_count_is_zero(self, tmp_path: Path) -> None:
        """Frontmatter local_checks are [] — only per-step checks apply.

        Characterization: subplan-level checks are separate from step-level.
        """
        project = _make_project(tmp_path, FIXTURE_SUBPLAN_RED_FIRST)
        _, data = _run_cli(project, "fixture-red-first-gate", step=0)
        assert data.get("subplan_check_count") == 0, (
            f"expected subplan_check_count=0, got {data.get('subplan_check_count')}"
        )


class TestRedFirstGateDriverBehaviour:
    """Characterization of the driver's gate path for red-first steps.

    These tests exercise the logic in run_ilk_loop_claude.sh indirectly,
    by verifying the building blocks: target extraction, merge, and the
    B2 confirm-before-block decision.
    """

    def test_b2_confirm_blocks_on_persistent_failure(self) -> None:
        """A gate that fails on both first pass and re-run → blocked.

        This is the B2 confirm path: the driver re-runs blocking checks
        once before committing to local_checks_failed.
        """
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_local_checks import confirm_b2_block

        first = [
            {"command": "exit 1", "slug": "fixture", "step": 0, "outcome": "fail"},
        ]
        rerun = [
            {"command": "exit 1", "slug": "fixture", "step": 0, "outcome": "fail"},
        ]
        result = confirm_b2_block(first, rerun)
        assert result["blocked"] is True
        assert len(result["blocking_checks"]) == 1

    def test_b2_confirm_clears_transient_failure(self) -> None:
        """A gate that fails on first pass but passes on re-run → not blocked.

        This is the transient-clearance path: a flaky gate is not held against
        the sub-plan. The command string must match between first and rerun
        (the B2 path re-runs the SAME command).
        """
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_local_checks import confirm_b2_block

        # Simulates a flaky gate: same command, fails first, passes on rerun.
        first = [
            {"command": "pytest tests/test_flaky.py -q", "slug": "fixture", "step": 0, "outcome": "fail"},
        ]
        rerun = [
            {"command": "pytest tests/test_flaky.py -q", "slug": "fixture", "step": 0, "outcome": "pass"},
        ]
        result = confirm_b2_block(first, rerun)
        assert result["blocked"] is False
        assert len(result["transient_cleared"]) == 1
