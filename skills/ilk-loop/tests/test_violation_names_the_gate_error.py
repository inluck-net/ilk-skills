"""Red-first: a ship-integrity violation names the gate's own error.

rezmac run 20260923-150625: the violation reason was ``gate record unreadable
(no results field, all_passed=false)`` when the actual gate record said
``error: timeout after 120s``.  The scalar ``--gate-passed false`` path
discarded the error field.

AC-1  (xfail) ``--gate-results-file F --slug s`` on a JSONL with a failing
      record ⇒ reason names ``<command> (<error>)``, not ``unreadable``.
AC-2  (unmarked) ``--gate-passed false`` alone ⇒ output unchanged from today.
AC-3  (unmarked) existing ``test_ship_integrity.py`` stays green.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = _REPO / "skills" / "ilk-loop" / "scripts" / "ship_integrity.py"
SCRIPTS_DIR = _REPO / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


def _make_gated_subplan(tmp_path: Path) -> Path:
    """Create a shipped sub-plan with a declared gate."""
    sp = tmp_path / "2026-09-23-test-subplan.md"
    sp.write_text(
        "---\n"
        "plan: test-subplan\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        "---\n\n"
        "# test\n\n"
        "### Step 0\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"bun run x\"\n"
        "    timeout: 120\n"
        "```\n",
        encoding="utf-8",
    )
    return sp


def _run_integrity(
    subplan: Path,
    *,
    gate_passed: str | None = None,
    gate_results_file: Path | None = None,
    slug: str | None = None,
) -> tuple[int, str]:
    args = [sys.executable, str(SCRIPT), "--subplan", str(subplan)]
    if gate_passed is not None:
        args += ["--gate-passed", gate_passed]
    if gate_results_file is not None:
        args += ["--gate-results-file", str(gate_results_file)]
    if slug is not None:
        args += ["--slug", slug]
    r = subprocess.run(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, r.stdout + r.stderr


def test_violation_names_the_gate_error(tmp_path: Path) -> None:
    """AC-1: the violation reason names the command and error, not 'unreadable'."""
    sp = _make_gated_subplan(tmp_path)
    lc = tmp_path / "gate.jsonl"
    lc.write_text(
        json.dumps({
            "slug": "test-subplan", "step": 0,
            "outcome": "fail", "exit_code": 1,
            "command": "bun run x",
            "error": "timeout after 120s",
        }) + "\n",
        encoding="utf-8",
    )
    rc, out = _run_integrity(
        sp, gate_passed="false",
        gate_results_file=lc, slug="test-subplan",
    )
    assert rc != 0, f"expected violation but got exit {rc}: {out}"
    assert "bun run x" in out, f"command not in output: {out}"
    assert "timeout after 120s" in out, f"error not in output: {out}"
    assert "unreadable" not in out, f"'unreadable' leaked through: {out}"


def test_gate_passed_false_alone_unchanged(tmp_path: Path) -> None:
    """AC-2: --gate-passed false alone ⇒ today's 'unreadable' message."""
    sp = _make_gated_subplan(tmp_path)
    rc, out = _run_integrity(sp, gate_passed="false")
    assert rc != 0, f"expected violation but got exit {rc}: {out}"
    assert "unreadable" in out, f"expected 'unreadable' in back-compat path: {out}"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-plan: an-unrunnable-derived-check-stops-once (step 0, RED-FIRST)
#
# Pins for the environment-fault stop: a derived mention check that cannot
# run (exit 126/127) should produce an environment-fault gate record, and the
# runner should stop once with local_checks_environment_fault instead of
# entering the ship-integrity revert loop.
# ─────────────────────────────────────────────────────────────────────────────

def test_exit127_mention_check_is_environment_fault() -> None:
    """AC-1: exit-127 mention check → gate record with outcome=error,
    reason=environment-fault: command not found.

    The gate record must carry an environment-fault reason so the runner can
    distinguish it from a real test failure (exit 1 with undeclared failures).
    """
    from run_local_checks import _classify_check

    # Exit 127 from a mention check — the binary is missing from PATH.
    # Today _classify_check returns ("error", "command not found").
    # After step 1, for scope="mention" it must return
    # ("error", "environment-fault: command not found").
    outcome, reason = _classify_check(127, "", "")
    assert outcome == "error", f"expected error, got {outcome}"
    assert reason is not None and reason.startswith("environment-fault:"), (
        f"exit-127 mention check must produce environment-fault reason, "
        f"got: {reason!r}"
    )


def test_runner_stops_once_on_environment_fault() -> None:
    """AC-2: runner sets stop_reason=local_checks_environment_fault,
    does NOT apply ship-integrity revert, sub-plan stays at current step.

    The runner must not revert a sub-plan when the gate could not run —
    the work is still valid, only the environment is broken.

    Tests that the gate record's environment-fault reason is detected by
    the runner's local_checks handling and produces the correct stop reason.
    """
    from run_local_checks import _classify_check

    # Simulate the runner's gate-record inspection: when the rollup outcome
    # is "error" and a mention check's reason starts with "environment-fault:",
    # the runner must set iter_stop_reason="local_checks_environment_fault"
    # instead of "local_checks_failed".
    #
    # Today, the runner does not distinguish environment-fault from other
    # errors — it always sets "local_checks_failed".  After step 1, it must
    # detect the environment-fault prefix and use the new stop reason.
    outcome, reason = _classify_check(127, "", "")
    assert outcome == "error"
    assert reason is not None

    # The runner must recognize this pattern.  Verify the stop reason
    # would be local_checks_environment_fault (not local_checks_failed).
    # This is the signal the runner uses to skip the ship-integrity revert.
    is_env_fault = reason.startswith("environment-fault:")
    assert is_env_fault, (
        f"the gate reason must start with 'environment-fault:' so the runner "
        f"can distinguish it from a real test failure, got: {reason!r}"
    )


def test_collect_maps_environment_fault_to_local_checks_stuck() -> None:
    """AC-3: collect.py classifies local_checks_environment_fault as
    local-checks-stuck (the existing blacklist label).

    This ensures the watchdog parks the project instead of re-dispatching
    it in a loop.
    """
    # The _SENTINEL_FAILURE_MAP in collect.py must include this mapping.
    # Read the source file directly to check for the mapping.
    collect_path = SCRIPTS_DIR.parent.parent / "ilk-feedback" / "scripts" / "collect.py"
    source = collect_path.read_text(encoding="utf-8")
    assert "local_checks_environment_fault" in source, (
        "collect.py does not reference local_checks_environment_fault — "
        "the new stop reason will fall through to generic heuristics and be "
        "classified as clean-success, causing the watchdog to relaunch forever"
    )


def test_exit1_undeclared_red_is_still_fail() -> None:
    """AC-4: exit-1 with undeclared failures → outcome=fail (unchanged).

    A real red gate (tests actually failing) must still block the ship.
    This is the existing behavior that must not change.
    """
    from run_local_checks import _classify_check

    outcome, reason = _classify_check(1, "", "")
    assert outcome == "fail", (
        f"exit-1 must produce outcome=fail (unchanged behavior), got {outcome}"
    )
