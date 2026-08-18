"""Tests verifying the ship-integrity correction path executes correctly.

Each test drives ``test_ship_integrity`` (dot-sourced from the driver) against
a shipped sub-plan with a red gate and asserts the three formerly-dead defects
are fixed:

Defect 1 — ``|| true`` masks exit code → fixed: uses ``|| si_exit=$?`` capture
Defect 2 — ``grep -oP`` is GNU-only   → fixed: Python extracts slug
Defect 3 — ``sed -i`` fails on BSD    → fixed: Python reverts status
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_runner_and_call(func_call: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Dot-source the driver and execute *func_call* in the same shell."""
    env = {"ILK_DOTSOURCE_ONLY": "1"}
    if env_extra:
        env.update(env_extra)
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER}' 2>/dev/null; "
        f"set +e; "  # driver sets -e; the function handles errors internally
        f"{func_call}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _make_plans_dir(tmp: Path) -> tuple[Path, Path]:
    """Create a minimal plans dir with a MASTER and a shipped sub-plan.

    Uses ``docs/plans/`` layout so ``get_plans_dir`` (legacy walk-up) finds it.
    """
    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-14-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-14-test
        status: active
        ---
        # test
    """))
    subplan = plans / "2026-08-14-test-slug.md"
    subplan.write_text(textwrap.dedent("""\
        ---
        plan: test-slug
        status: shipped
        current_step: 3
        local_checks:
          - command: echo ok
            timeout: 10
        ---
        ### Step 0
        ### Step 1
        ### Step 2
    """))
    return plans, subplan


# ── Defect 1: || true masks exit code ────────────────────────────────────────

def test_defect1_ship_integrity_detects_violation(tmp_path: Path) -> None:
    """``test_ship_integrity`` must return non-zero when a red gate is recorded.

    The ``|| true`` antipattern (now fixed) made ``si_exit`` always 0, so the
    violation branch was unreachable.  After the fix the function returns 1.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 2: grep -oP is GNU-only ──────────────────────────────────────────

def test_defect2_slug_extraction_portable(tmp_path: Path) -> None:
    """Slug extraction uses Python, so BSD ``grep`` cannot defeat it.

    The old ``grep -oP`` failed on BSD (exit 2) → slug empty → gate stays
    ``"null"`` → ``ship_integrity.py`` saw declared checks with no result but
    the exit was masked by ``|| true``.  After the fix, Python extracts the
    slug and the gate lookup works.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 3: sed -i without backup suffix ──────────────────────────────────

def test_defect3_status_revert_works(tmp_path: Path) -> None:
    """Status revert uses Python, so BSD ``sed -i`` cannot defeat it.

    The old ``sed -i 's/.../…/'`` (no backup suffix) fails on BSD with
    ``invalid command code f``.  After the fix, Python performs the revert
    and the file reads ``status: in-progress``.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    _source_runner_and_call(
        f"test_ship_integrity '{plans}' '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    content = subplan.read_text()
    assert "status: in-progress" in content, (
        f"Expected status revert to in-progress, but file still contains:\n{content}"
    )
