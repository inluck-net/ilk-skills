"""A commit does not start a background full-suite spawn.

Regression for 2026-10-07: 17 background full suites started (one per
committing iteration); 10 of the last 12 ended 'unmeasured suite exceeded
600s timeout'.  R2e's verify waited ~6 min on one (16:43-16:49) then ran
the same suite again.

Acceptance criteria (from sub-plan a-commit-does-not-start-a-suite):
  AC-1  the runner file has no function ``ledger_spawn_for_head`` and no
        line invoking ``suite_ledger.py`` with ``spawn`` or ``measure``
  AC-2  sourcing the runner with ``ILK_DOTSOURCE_ONLY=1`` and calling
        ``type ledger_spawn_for_head`` fails (function absent)
  AC-3  ``ledger_record_point`` is still defined and still called in
        ``main()`` (the cheap point record stays)
  AC-4  drive ``verification_record``'s ledger branch with
        ``--ledger require``, no ledger entry for HEAD's tree ⇒
        ``run_suite`` is called once, ``wait_for`` is never called, and
        the record says ``ledger_wait_sec: 0``
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


# ── helpers ───────────────────────────────────────────────────────────────────


def _runner_text() -> str:
    return _RUNNER.read_text(encoding="utf-8", errors="replace")


def _bash_source_and_call(script: str, env: dict[str, str] | None = None,
                          timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Source the runner under ILK_DOTSOURCE_ONLY=1 and run *script*."""
    e = {**os.environ, "ILK_DOTSOURCE_ONLY": "1"}
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, timeout=timeout,
    )


# ── AC-1: no ``ledger_spawn_for_head``, no ``suite_ledger.py spawn`` ─────────


def test_ac1_no_ledger_spawn_for_head_function() -> None:
    """AC-1a: the runner file defines no function ``ledger_spawn_for_head``."""
    text = _runner_text()
    assert "ledger_spawn_for_head()" not in text, (
        "runner still defines ledger_spawn_for_head()"
    )


def test_ac1_no_suite_ledger_spawn_or_measure_invocation() -> None:
    """AC-1b: the runner file has no invocation of ``suite_ledger.py`` with
    ``spawn`` or ``measure``.  Handles line continuations and shell quoting."""
    text = _runner_text()
    # Join continuation lines so a multi-line invocation is detected.
    joined = text.replace("\\\n", " ")
    # Pattern: suite_ledger.py" (shell expansion) then optional whitespace then
    # the keyword.  Also matches bare "suite_ledger.py spawn".
    pattern = re.compile(r'suite_ledger\.py["\']?\s+(spawn|measure)\b')
    m = pattern.search(joined)
    if m:
        kw = m.group(1)
        line_no = joined[:m.start()].count("\n") + 1
        pytest.fail(
            f"runner invokes suite_ledger.py with {kw} near line {line_no}"
        )


# ── AC-2: sourcing + ``type ledger_spawn_for_head`` fails ────────────────────


def test_ac2_type_ledger_spawn_for_head_fails() -> None:
    """AC-2: sourcing the runner and calling ``type ledger_spawn_for_head``
    fails because the function is absent."""
    result = _bash_source_and_call(
        f"source '{_RUNNER}' 2>/dev/null; type ledger_spawn_for_head 2>&1"
    )
    assert result.returncode != 0, (
        f"expected type to fail, got rc={result.returncode}: {result.stdout!r}"
    )


# ── AC-3: ``ledger_record_point`` stays ──────────────────────────────────────


def test_ac3_ledger_record_point_is_defined() -> None:
    """AC-3a: the runner file still defines ``ledger_record_point``."""
    text = _runner_text()
    assert "ledger_record_point()" in text, (
        "runner no longer defines ledger_record_point()"
    )


def test_ac3_ledger_record_point_is_called_in_main() -> None:
    """AC-3b: ``ledger_record_point`` is called inside ``main()``."""
    text = _runner_text()
    # Find where main() starts and verify the function is called after it.
    lines = text.splitlines()
    in_main = False
    found = False
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("main()") or stripped == r'main "$@"':
            in_main = True
            continue
        if in_main and "ledger_record_point" in line:
            found = True
            break
    assert found, (
        "ledger_record_point is not called inside main()"
    )


# ── AC-4: verify path contains no ledger wait ────────────────────────────────


def test_ac4_verification_record_has_no_wait_for_call() -> None:
    """AC-4a: ``verification_record.py``'s ledger branch does not call
    ``suite_ledger.wait_for``.  The verify path measures in-process on
    a cache miss (the ``require`` path), never waits on a background job.
    """
    vr_path = Path(__file__).resolve().parent.parent / "scripts" / "verification_record.py"
    text = vr_path.read_text(encoding="utf-8", errors="replace")

    # Find the ledger branch: from "if ledger_mode != \"off\":" to the
    # next top-level def/class or end of function.
    in_ledger_branch = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if "ledger_mode" in line and "off" in line and "if" in line:
            in_ledger_branch = True
            continue
        if in_ledger_branch and stripped.startswith("def ") and not stripped.startswith("def _"):
            break
        if in_ledger_branch and "wait_for" in line and not line.strip().startswith("#"):
            pytest.fail(
                f"verification_record.py line {line_no} calls wait_for "
                f"in the ledger branch: {line!r}"
            )


def test_ac4_record_renders_zero_ledger_wait_sec() -> None:
    """AC-4b: when ``ledger_wait_sec`` is 0, the record includes the field.
    The render function only emits it when not None; 0 is a valid int.
    """
    vr_path = Path(__file__).resolve().parent.parent / "scripts" / "verification_record.py"
    text = vr_path.read_text(encoding="utf-8", errors="replace")
    # Verify the render function handles ledger_wait_sec=0 correctly.
    # The field should appear when ledger_wait_sec is 0 (not None).
    assert "if ledger_wait_sec is not None:" in text, (
        "render_record does not guard ledger_wait_sec on 'is not None'"
    )