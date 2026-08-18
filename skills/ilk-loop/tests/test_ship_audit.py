"""Tests pinning the three ship-integrity defects (all xfail(strict=True)).

Each test asserts the CORRECT post-fix behavior.  They fail now because
the defects exist; after step 1 fixes them, they pass (XPASS for strict=True).

Defect 1 — ``|| true`` masks exit code (run_ilk_loop_claude.sh:1206-1207)
Defect 2 — ``grep -oP`` is GNU-only; BSD grep exits 2 (:1186)
Defect 3 — ``sed -i`` without backup suffix fails on BSD (:1211)
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
    """Create a minimal plans dir with a MASTER and a shipped sub-plan."""
    plans = tmp / "plans"
    plans.mkdir()
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

@pytest.mark.xfail(strict=True, reason="Defect 1: || true resets $? to 0 — violation branch unreachable")
def test_defect1_ship_integrity_detects_violation(tmp_path: Path) -> None:
    """``test_ship_integrity`` must return non-zero when a red gate is recorded.

    Defect 1 (``|| true`` at :1206-1207) makes ``si_exit`` always 0, so the
    violation branch at :1208 is unreachable and the function returns 0
    even with a red gate.

    After the fix, the function returns 1 (violations found).
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    # After fix: returns 1 (violation detected).  Now: returns 0.
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 2: grep -oP is GNU-only ──────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="Defect 2: BSD grep rejects -oP; slug extraction routed through Python")
def test_defect2_slug_extraction_portable(tmp_path: Path) -> None:
    """After the fix, ``test_ship_integrity`` detects a violation even when
    ``grep -oP`` would fail, because slug extraction is routed through Python.

    Now: ``grep -oP`` fails on BSD → slug empty → gate_json stays "null" →
    ``ship_integrity.py`` sees declared checks with no result → returns 1.
    BUT ``|| true`` (defect 1) masks the exit, so the function returns 0.

    After the fix: Python extracts the slug, gate_json is populated correctly,
    and the function returns 1.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    result = _source_runner_and_call(
        f"test_ship_integrity '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    assert result.returncode != 0, (
        f"Expected violation detection (exit 1), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Defect 3: sed -i without backup suffix ──────────────────────────────────

@pytest.mark.xfail(strict=True, reason="Defect 3: BSD sed -i fails; status revert routed through Python")
def test_defect3_status_revert_works(tmp_path: Path) -> None:
    """After the fix, ``test_ship_integrity`` reverts the sub-plan status from
    ``shipped`` to ``in-progress`` when a violation is detected.

    Now: ``sed -i`` fails on BSD (defect 3) AND ``|| true`` masks the exit
    (defect 1), so the file stays ``status: shipped``.

    After the fix: Python performs the revert and the file reads
    ``status: in-progress``.
    """
    plans, subplan = _make_plans_dir(tmp_path)
    lc_file = plans / "results.jsonl"
    lc_file.write_text('{"slug":"test-slug","outcome":"fail"}\n')

    _source_runner_and_call(
        f"test_ship_integrity '{lc_file}'",
        env_extra={"PROJECT_PATH": str(tmp_path)},
    )
    content = subplan.read_text()
    # After fix: status reverted to in-progress.  Now: still shipped.
    assert "status: in-progress" in content, (
        f"Expected status revert to in-progress, but file still contains:\n{content}"
    )
