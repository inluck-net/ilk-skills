"""Pin that loop_status renders [OK] shipped over unproven sub-plans.

Part of sub-plan ``the-status-line-admits-the-gap`` step 0.  Builds a fixture
plans dir with three shipped sub-plans (proven, red-gate, missing-step) and
asserts that today's rendering is **indistinguishable** for all three — the
bug this sub-plan exists to fix.

Also records today's exit codes for the ``next is None`` and ``next`` paths
as the AC-3 baseline (these must pass now and keep passing after the fix).
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "loop_status.py"


# ── git helpers (reuse shape from test_ship_audit) ─────────────────────────

def _init_repo(path: Path) -> None:
    """Create a git repo with an initial commit so ``git log`` works."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path,
        capture_output=True, check=True,
    )
    (path / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "add", ".gitkeep"], cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True,
    )


def _commit_with_message(path: Path, subject: str, body: str = "") -> None:
    """Create a commit with a specific subject and optional body."""
    (path / "marker.txt").write_text(subject)
    subprocess.run(
        ["git", "add", "marker.txt"], cwd=path, capture_output=True, check=True,
    )
    msg = subject if not body else f"{subject}\n\n{body}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True, check=True,
    )


# ── fixture builders ───────────────────────────────────────────────────────

def _make_gap_fixture(tmp: Path) -> Path:
    """Create a plans dir with 3 shipped sub-plans (proven, red, missing-step)
    plus 1 pending so the master is not auto-filtered as all-shipped.

    Also initialises a git repo at *tmp* with commits for the proven sub-plan's
    steps so ``ship_audit`` can verify step-commit presence.
    """
    _init_repo(tmp)

    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)

    # MASTER — references all 4 sub-plans.
    (plans / "MASTER-2026-08-14-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-14-test
        status: active
        ---
        # test master

        | # | Slug |
        |---|---|
        | 1 | 2026-08-14-proven-ok.md |
        | 2 | 2026-08-14-red-gate.md |
        | 3 | 2026-08-14-missing-step.md |
        | 4 | 2026-08-14-placeholder.md |
    """))

    # Sub-plan A: proven (all steps committed, green gate).
    (plans / "2026-08-14-proven-ok.md").write_text(textwrap.dedent("""\
        ---
        plan: proven-ok
        status: shipped
        current_step: 3
        estimated_steps: 3
        verification_tier: loop-verified
        local_checks:
          - command: echo ok
            timeout: 10
        ---
        ### Step 0
        ### Step 1
        ### Step 2
    """))

    # Sub-plan B: shipped but red final gate.
    (plans / "2026-08-14-red-gate.md").write_text(textwrap.dedent("""\
        ---
        plan: red-gate
        status: shipped
        current_step: 3
        estimated_steps: 3
        verification_tier: loop-verified
        local_checks:
          - command: echo fail
            timeout: 10
        ---
        ### Step 0
        ### Step 1
        ### Step 2
    """))

    # Sub-plan C: shipped but missing a step commit.
    (plans / "2026-08-14-missing-step.md").write_text(textwrap.dedent("""\
        ---
        plan: missing-step
        status: shipped
        current_step: 3
        estimated_steps: 3
        verification_tier: loop-verified
        local_checks:
          - command: echo ok
            timeout: 10
        ---
        ### Step 0
        ### Step 1
        ### Step 2
    """))

    # Pending placeholder so master_has_nonshipped returns True.
    (plans / "2026-08-14-placeholder.md").write_text(textwrap.dedent("""\
        ---
        plan: placeholder
        status: pending
        current_step: 0
        estimated_steps: 1
        ---
        ### Step 0
    """))

    # Create git commits for the proven sub-plan's steps.
    for step_n in range(3):
        _commit_with_message(
            tmp,
            f"feat(proven-ok): step {step_n} [plan:proven-ok#step-{step_n}]",
        )

    # red-gate: all steps committed (gate failure is not visible to
    # resolve_status which passes gate_passed="unknown").
    for step_n in range(3):
        _commit_with_message(
            tmp,
            f"feat(red-gate): step {step_n} [plan:red-gate#step-{step_n}]",
        )

    # missing-step: only steps 0 and 1 — step 2 is missing.
    for step_n in range(2):
        _commit_with_message(
            tmp,
            f"feat(missing-step): step {step_n} [plan:missing-step#step-{step_n}]",
        )

    return plans


def _run_status(tmp_path: Path) -> subprocess.CompletedProcess:
    """Run loop_status.py with cwd in the fixture dir."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
        env={**__import__("os").environ, "ILK_DATA_HOME": str(tmp_path / "ilk-data")},
    )


# ── xfail tests: pin the bug ───────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="AC-1: shipped-with-red-gate renders [OK] indistinguishable from proven")
def test_red_gate_row_looks_proven(tmp_path: Path) -> None:
    _make_gap_fixture(tmp_path)
    result = _run_status(tmp_path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for line in lines:
        if "red-gate" in line:
            assert "[OK]" in line, f"Row missing [OK]: {line}"
            assert "(!)" not in line, f"Row has unexpected discrepancy marker: {line}"
            return
    pytest.fail("red-gate row not found in output")


@pytest.mark.xfail(strict=True, reason="AC-1: shipped-with-missing-step renders [OK] without discrepancy marker")
def test_missing_step_row_looks_proven(tmp_path: Path) -> None:
    _make_gap_fixture(tmp_path)
    result = _run_status(tmp_path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for line in lines:
        if "missing-step" in line:
            assert "[OK]" in line, f"Row missing [OK]: {line}"
            assert "(!)" not in line, f"Row has unexpected discrepancy marker: {line}"
            return
    pytest.fail("missing-step row not found in output")


@pytest.mark.xfail(strict=True, reason="AC-5: summary prints 'All 3 shipped' without qualifying unproven")
def test_summary_qualifies_unproven(tmp_path: Path) -> None:
    _make_gap_fixture(tmp_path)
    result = _run_status(tmp_path)
    assert result.returncode == 0, result.stderr
    # After the fix, "nothing to do" must NOT appear when unproven sub-plans exist.
    assert "nothing to do" in result.stdout, (
        "Expected 'nothing to do' in output (the bug — unproven not qualified).\n"
        f"stdout: {result.stdout}"
    )


# ── baseline exit-code tests (NOT xfail — must pass now and keep passing) ──

def test_exit_code_zero_when_all_shipped(tmp_path: Path) -> None:
    """AC-3 baseline: exit 0 when next is None (all shipped, master filtered)."""
    _init_repo(tmp_path)
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-14-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-14-test
        status: active
        ---
        # test
        | # | Slug |
        |---|---|
        | 1 | 2026-08-14-done.md |
    """))
    (plans / "2026-08-14-done.md").write_text(textwrap.dedent("""\
        ---
        plan: done
        status: shipped
        current_step: 1
        estimated_steps: 1
        ---
        ### Step 0
    """))
    _commit_with_message(tmp_path, "feat(done): step 0 [plan:done#step-0]")
    result = _run_status(tmp_path)
    assert result.returncode == 0, (
        f"Expected exit 0 when all sub-plans shipped, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_exit_code_one_when_pending_subplan(tmp_path: Path) -> None:
    """AC-3 baseline: exit 1 when a pending sub-plan is the next action."""
    _init_repo(tmp_path)
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-14-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-14-test
        status: active
        ---
        # test
        | # | Slug |
        |---|---|
        | 1 | 2026-08-14-pending.md |
    """))
    (plans / "2026-08-14-pending.md").write_text(textwrap.dedent("""\
        ---
        plan: pending
        status: pending
        current_step: 0
        estimated_steps: 1
        ---
        ### Step 0
    """))
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
        env={**__import__("os").environ, "ILK_DATA_HOME": str(tmp_path / "ilk-data")},
    )
    assert result.returncode == 1, (
        f"Expected exit 1 when pending sub-plan exists, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC-6: --json parity ───────────────────────────────────────────────────

def test_json_has_proven_field(tmp_path: Path) -> None:
    """AC-6: --json output carries per-subplan 'proven' boolean."""
    _make_gap_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
        env={**__import__("os").environ, "ILK_DATA_HOME": str(tmp_path / "ilk-data")},
    )
    assert result.returncode in (0, 1), result.stderr
    data = json.loads(result.stdout)
    for sp in data["subplans"]:
        assert "proven" in sp, (
            f"subplan {sp['fname']} missing 'proven' key in --json output"
        )
        assert isinstance(sp["proven"], bool), (
            f"subplan {sp['fname']} 'proven' should be bool, got {type(sp['proven'])}"
        )


def test_json_existing_keys_preserved(tmp_path: Path) -> None:
    """AC-6: additive-only — existing keys must not be removed."""
    _make_gap_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
        env={**__import__("os").environ, "ILK_DATA_HOME": str(tmp_path / "ilk-data")},
    )
    assert result.returncode in (0, 1), result.stderr
    data = json.loads(result.stdout)

    # Top-level keys that must always be present.
    expected_top = {
        "master", "master_status", "plans_dir", "subplans",
        "active", "queued", "shipped", "queue_exit", "stalled",
        "compile_only_summary", "notices",
    }
    missing_top = expected_top - set(data.keys())
    assert not missing_top, f"Missing top-level keys: {missing_top}"

    # Per-subplan keys that must always be present.
    expected_sp = {"fname", "slug", "status", "current_step", "estimated_steps", "repo", "verification_tier"}
    for sp in data["subplans"]:
        missing_sp = expected_sp - set(sp.keys())
        assert not missing_sp, f"subplan {sp['fname']} missing keys: {missing_sp}"
