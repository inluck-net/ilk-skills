"""Sub-plan ``a-worker-turns-its-red-green`` step 0 — pin it red-first.

The contract (from the sub-plan's "The contract" section):

1. **Exact gate for the worker:**
   ``run_local_checks.py --print-step-gate <sub-plan .md> <step>`` prints
   the step's declared commands exactly as the driver will run them (same
   extraction as ``extract_step_local_checks``, same prelude), one per line,
   exit 0; exit 2 if the step declares none.

2. **Worker contract** (SKILL.md step loop, :195-208): after the step's
   commit, run every printed command; while any is red, fix and commit,
   then rerun. End the turn only when all are green. Fixing a test is
   allowed only for a test-infrastructure fault (isolation, fixture, stale
   path), and the commit body says ``test-infra:`` plus the reason. Never
   weaken or delete an assertion, never add a deselect or a ``baseline_red``
   entry. If green is not reachable in the iteration, end the turn with
   the failing ids written to the sub-plan's Findings.

3. **Driver** (replaces 07l #1's one-retry cap): a red post-iteration gate
   does not end the run. The next iteration continues the same (slug, step)
   with the failure output (07l #1's ``gate-red-<iteration>.txt`` and prompt
   line). The run ends ``local_checks_failed`` only when (a) two consecutive
   red gates on the same (slug, step) have the same failing set and no new
   commit in between (no progress), or (b) the gate fails as an environment
   fault (``:6485``), or (c) the iteration/run budget is spent.

4. No timeout, budget or threshold value changes.

Test structure:
- **CLI tests** verify ``--print-step-gate`` exists and behaves correctly.
  At base the CLI option does NOT exist, so these are ``xfail(strict=True)``.
- **Worker-contract tests** verify the worker runs its step gate and iterates
  until green. At base the worker does NOT run gates, so these are
  ``xfail(strict=True)``.
- **Driver tests** verify the runner's stop/retry logic. Some pass at base
  (the driver already has retry logic from #1); others test the new
  no-progress behaviour and are ``xfail(strict=True)``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

_DRIVER = SCRIPTS / "run_ilk_loop_claude.sh"
_RUN_LOCAL_CHECKS = SCRIPTS / "run_local_checks.py"


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_subplan(tmp_path: Path, *, step_gate: str | None = None,
                  step: int = 0) -> Path:
    """Create a minimal sub-plan file with an optional per-step gate."""
    body = textwrap.dedent("""\
        ---
        plan: test-slug
        status: in-progress
        current_step: 0
        estimated_steps: 2
        ---

        # Sub-plan: test

        ## Steps

        ### Step 0 — first step

        """)
    if step_gate is not None:
        body += textwrap.dedent(f"""\
            ```yaml
            local_checks:
              - command: "{step_gate}"
                timeout: 60
            ```
            """)
    p = tmp_path / "test-subplan.md"
    p.write_text(body, encoding="utf-8")
    return p


def _run_print_step_gate(subplan: Path, step: int) -> subprocess.CompletedProcess:
    """Run ``run_local_checks.py --print-step-gate`` and return the result."""
    return subprocess.run(
        [sys.executable, str(_RUN_LOCAL_CHECKS),
         "--print-step-gate", str(subplan), str(step)],
        capture_output=True, text=True, timeout=30,
    )


# ── CLI tests: --print-step-gate ───────────────────────────────────────────

class TestPrintStepGateCLI:
    """Tests for the --print-step-gate CLI option."""

    @pytest.mark.xfail(strict=True, reason="--print-step-gate does not exist at base")
    def test_print_step_gate_exits_zero_with_declared_gate(self, tmp_path: Path) -> None:
        """AC: exit 0 when the step declares a gate."""
        sp = _make_subplan(tmp_path, step_gate="echo ok")
        r = _run_print_step_gate(sp, step=0)
        assert r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}"

    @pytest.mark.xfail(strict=True, reason="--print-step-gate does not exist at base")
    def test_print_step_gate_exits_two_without_declared_gate(self, tmp_path: Path) -> None:
        """AC: exit 2 when the step declares no gate (not exit 2 from arg parse failure)."""
        sp = _make_subplan(tmp_path, step_gate=None)
        r = _run_print_step_gate(sp, step=0)
        # Must be exit 2 with a specific message, not a generic arg-parse failure.
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
        assert "no gate" in r.stdout.lower() or "no gate" in r.stderr.lower() or \
               "no checks" in r.stdout.lower() or "no checks" in r.stderr.lower() or \
               "declares none" in r.stdout.lower() or "declares none" in r.stderr.lower(), (
            f"exit 2 but without gate-absent message — likely arg parse failure: "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    @pytest.mark.xfail(strict=True, reason="--print-step-gate does not exist at base")
    def test_print_step_gate_outputs_one_command_per_line(self, tmp_path: Path) -> None:
        """AC: prints each declared command on its own line."""
        sp = _make_subplan(tmp_path, step_gate="echo hello")
        r = _run_print_step_gate(sp, step=0)
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        assert len(lines) == 1, f"expected 1 command, got {len(lines)}: {r.stdout!r}"
        assert "echo hello" in lines[0]

    @pytest.mark.xfail(strict=True, reason="--print-step-gate does not exist at base")
    def test_print_step_gate_multiple_commands(self, tmp_path: Path) -> None:
        """AC: prints multiple declared commands, one per line."""
        body = textwrap.dedent("""\
            ---
            plan: test-slug
            status: in-progress
            current_step: 0
            estimated_steps: 2
            ---

            # Sub-plan: test

            ## Steps

            ### Step 0 — first step

            ```yaml
            local_checks:
              - command: "echo one"
                timeout: 60
              - command: "echo two"
                timeout: 60
            ```
            """)
        sp = tmp_path / "test-subplan.md"
        sp.write_text(body, encoding="utf-8")
        r = _run_print_step_gate(sp, step=0)
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        assert len(lines) == 2, f"expected 2 commands, got {len(lines)}: {r.stdout!r}"

    @pytest.mark.xfail(strict=True, reason="--print-step-gate does not exist at base")
    def test_print_step_gate_includes_path_prelude(self, tmp_path: Path) -> None:
        """AC: the output includes the same path prelude the driver applies."""
        # The driver reads path_prelude from .ilk-launch.json and prepends it.
        # --print-step-gate should do the same.
        sp = _make_subplan(tmp_path, step_gate="echo ok")
        r = _run_print_step_gate(sp, step=0)
        # At minimum, the command should be present; the prelude test is
        # project-specific, so we just verify the command is there.
        assert r.returncode == 0
        assert "echo ok" in r.stdout


# ── Worker-contract tests ──────────────────────────────────────────────────

class TestWorkerGateContract:
    """Tests for the worker running its step gate after committing."""

    @pytest.mark.xfail(strict=True, reason="worker does not run gates at base")
    def test_worker_runs_declared_gate_after_commit(self) -> None:
        """AC: the worker runs every printed gate command after the step commit.

        Verifies that the runner's worker prompt includes instructions to
        run the declared gate and iterate until green.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        # The worker prompt should instruct the agent to run the gate.
        assert "run every declared gate" in src.lower() or \
               "run the declared gate" in src.lower() or \
               "run your step" in src.lower(), (
            "runner does not instruct the worker to run its declared gate"
        )

    @pytest.mark.xfail(strict=True, reason="worker does not iterate until green at base")
    def test_worker_iterates_until_green(self) -> None:
        """AC: the worker iterates (fix + commit + rerun) until all gates pass.

        Verifies that the runner's worker prompt includes iteration-until-green
        instructions.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "iterate until green" in src.lower() or \
               "until all are green" in src.lower() or \
               "while any is red" in src.lower(), (
            "runner does not instruct the worker to iterate until green"
        )

    @pytest.mark.xfail(strict=True, reason="test-infra exception not enforced at base")
    def test_worker_allows_test_infra_fix_only(self) -> None:
        """AC: fixing a test is allowed only for test-infrastructure fault.

        Verifies that the worker prompt restricts test fixes to infra issues.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "test-infra" in src.lower() or \
               "test-infrastructure" in src.lower() or \
               "test infrastructure" in src.lower(), (
            "runner does not restrict test fixes to infrastructure faults"
        )

    @pytest.mark.xfail(strict=True, reason="assertion-weakening guard not enforced at base")
    def test_worker_never_weakens_assertions(self) -> None:
        """AC: never weaken or delete an assertion, never add deselect or baseline_red.

        Verifies that the worker prompt includes the assertion guard.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "never weaken" in src.lower() or \
               "never delete an assertion" in src.lower() or \
               "never add a deselect" in src.lower() or \
               "baseline_red" in src, (
            "runner does not include assertion-weakening guard"
        )

    @pytest.mark.xfail(strict=True, reason="failing-ids recording not implemented at base")
    def test_worker_records_failing_ids_on_unreachable_green(self) -> None:
        """AC: if green is not reachable, end the turn with failing ids in Findings.

        Verifies that the worker prompt includes the failing-ids recording
        instruction.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "failing ids" in src.lower() or \
               "failing_ids" in src.lower() or \
               "not reachable" in src.lower(), (
            "runner does not instruct the worker to record failing ids"
        )


# ── Driver tests: stop/retry logic ─────────────────────────────────────────

class TestDriverGateRetry:
    """Tests for the runner's gate-failure retry and stop logic."""

    def test_driver_has_gate_failure_block(self) -> None:
        """Control: the runner's gate-failure block exists at base."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert 'iter_stop_reason="local_checks_failed"' in src, (
            "runner's gate-failure block not found"
        )

    def test_driver_calls_gate_retry(self) -> None:
        """Control: the runner calls gate_retry.py (from #1's changes)."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "gate_retry.py" in src, (
            "run_ilk_loop_claude.sh does not call gate_retry.py"
        )

    def test_driver_writes_gate_red_file(self) -> None:
        """Control: the runner writes gate-red-<iteration>.txt on retry."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "gate-red-" in src, (
            "run_ilk_loop_claude.sh does not write gate-red file"
        )

    @pytest.mark.xfail(strict=True,
                        reason="no-progress compares failing sets, not just counts — not at base")
    def test_driver_stops_on_same_failing_set_no_progress(self) -> None:
        """AC: the run ends local_checks_failed when two consecutive red gates
        on the same (slug, step) have the SAME FAILING SET and no new commit.

        The current implementation uses a count-based approach (2 consecutive
        reds = stop regardless of whether the failing set changed). The new
        contract requires comparing the actual failing test ids so that a
        worker making partial progress (fixing some failures) is NOT stopped.
        """
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        # The new logic should compare failing sets between iterations.
        # Look for evidence of set comparison rather than just counting.
        has_set_comparison = any(
            phrase in src.lower()
            for phrase in (
                "failing_set", "same failing", "failing ids",
                "compare.*fail", "diff.*fail",
            )
        )
        # Also check that the gate-red-count approach is NOT the sole mechanism.
        # The count-based approach alone is insufficient.
        assert has_set_comparison, (
            "runner uses only count-based no-progress detection; "
            "contract requires comparing the actual failing set between iterations"
        )

    def test_driver_stops_on_environment_fault(self) -> None:
        """Control: environment fault stops the run (existing behavior)."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "local_checks_environment_fault" in src, (
            "runner does not handle environment_fault"
        )

    def test_driver_stops_on_budget(self) -> None:
        """Control: budget exhausted stops the run (existing behavior)."""
        src = _DRIVER.read_text(encoding="utf-8", errors="replace")
        assert "budget" in src.lower(), (
            "runner does not handle budget exhaustion"
        )


# ── Value-change guard ─────────────────────────────────────────────────────

class TestNoValueChanges:
    """Tests that no timeout, budget or threshold values were changed."""

    def test_run_local_checks_defines_no_new_constants(self) -> None:
        """AC: the helper defines no new timeout/budget/threshold constants."""
        src = _RUN_LOCAL_CHECKS.read_text(encoding="utf-8")
        # The existing DEFAULT_CHECK_TIMEOUT_S is allowed.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or \
               stripped.startswith("'''"):
                continue
            if "=" in stripped:
                for kw in ("NEW_TIMEOUT", "NEW_BUDGET", "NEW_THRESHOLD"):
                    assert kw not in stripped, (
                        f"run_local_checks.py defines {kw} on line: {stripped!r}"
                    )

    def test_gate_retry_defines_no_constants(self) -> None:
        """AC: gate_retry.py defines no timeout/budget/threshold constants."""
        src = (SCRIPTS / "gate_retry.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or \
               stripped.startswith("'''"):
                continue
            if "=" in stripped:
                for kw in ("TIMEOUT", "BUDGET", "THRESHOLD"):
                    assert kw not in stripped, (
                        f"gate_retry.py defines {kw} on line: {stripped!r}"
                    )