"""Tests verifying batch-gate exit-status reporting (GATE-EXIT-DEAD-BRANCH).

The runner's ``invoke_batch_gate`` currently uses ``|| true`` which swallows
the gate's exit code — ``$?`` is always 0, so the failure branch is dead code.
These tests drive the function with a stub gate script and assert:

  AC-1: non-zero gate exit → failure line naming exit code + output
  AC-2: zero gate exit → success line, no failure line
  AC-3: both cases return 0 (runner still terminates)
  AC-4: missing gate script → existing warning, return 0

Reuses the ``_source_runner_and_call`` dot-source harness from test_ship_audit.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_runner_and_call(
    func_call: str, env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    """Dot-source the driver and execute *func_call* in the same shell.

    If *env_extra* contains ``_SKILL_ROOT``, it is re-set AFTER sourcing
    the runner (which sets it at line 17) so the override takes effect.
    """
    env = {"ILK_DOTSOURCE_ONLY": "1"}
    if env_extra:
        env.update(env_extra)
    # Build post-source overrides for _SKILL_ROOT (runner line 17 clobbers
    # the env value on source).
    skill_root_override = ""
    if env_extra and "_SKILL_ROOT" in env_extra:
        skill_root_override = f"_SKILL_ROOT='{env_extra['_SKILL_ROOT']}'; "
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER}' 2>/dev/null; "
        f"{skill_root_override}"
        f"set +e; "
        f"{func_call}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _make_stub_gate(tmp: Path, exit_code: int = 0, msg: str = "gate ran") -> Path:
    """Create a stub ``batch_gate.py`` that exits with *exit_code* and prints *msg*."""
    skill_root = tmp / "fake-skill-root" / "ilk-loop" / "scripts"
    skill_root.mkdir(parents=True)
    stub = skill_root / "batch_gate.py"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        print("{msg}")
        sys.exit({exit_code})
    """))
    stub.chmod(0o755)
    return tmp / "fake-skill-root"


# ── AC-1: non-zero gate → failure line ───────────────────────────────────────

def test_failing_gate_reports_failure_line(tmp_path: Path) -> None:
    """When the batch gate exits non-zero, the runner prints a failure line.

    AC-1: the line names the exit code and includes the gate's output.
    Today this is RED — ``|| true`` makes ``$?`` always 0.
    """
    fake_skill_root = _make_stub_gate(tmp_path, exit_code=1, msg="FAIL: tests broke")
    result = _source_runner_and_call(
        f"invoke_batch_gate '{tmp_path}' '{tmp_path / 'runtime'}'",
        env_extra={"_SKILL_ROOT": str(fake_skill_root)},
    )
    assert "Gate exited with code" in result.stdout, (
        f"Expected failure line in output, got none.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FAIL: tests broke" in result.stdout, (
        "Expected gate output in the failure report.\n"
        f"stdout: {result.stdout}"
    )


# ── AC-2: zero gate → success, no failure line ──────────────────────────────

def test_passing_gate_reports_success(tmp_path: Path) -> None:
    """When the batch gate exits 0, no failure line is printed.

    AC-2: success path — gate completed, no failure line.
    """
    fake_skill_root = _make_stub_gate(tmp_path, exit_code=0, msg="all passed")
    result = _source_runner_and_call(
        f"invoke_batch_gate '{tmp_path}' '{tmp_path / 'runtime'}'",
        env_extra={"_SKILL_ROOT": str(fake_skill_root)},
    )
    assert "Gate exited with code" not in result.stdout, (
        f"Passing gate must not print failure line.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Gate completed" in result.stdout, (
        f"Expected success message.\nstdout: {result.stdout}"
    )


# ── AC-3: always returns 0 ──────────────────────────────────────────────────

def test_failing_gate_returns_zero(tmp_path: Path) -> None:
    """A failing gate must not prevent the runner from terminating.

    AC-3 / parent AC-6: ``invoke_batch_gate`` returns 0 even when the gate fails.
    """
    fake_skill_root = _make_stub_gate(tmp_path, exit_code=42, msg="bad")
    result = _source_runner_and_call(
        f"invoke_batch_gate '{tmp_path}' '{tmp_path / 'runtime'}'; echo EXIT=$?",
        env_extra={"_SKILL_ROOT": str(fake_skill_root)},
    )
    assert "EXIT=0" in result.stdout, (
        f"invoke_batch_gate must return 0 even on gate failure.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_passing_gate_returns_zero(tmp_path: Path) -> None:
    """A passing gate also returns 0 (regression guard for AC-3)."""
    fake_skill_root = _make_stub_gate(tmp_path, exit_code=0, msg="ok")
    result = _source_runner_and_call(
        f"invoke_batch_gate '{tmp_path}' '{tmp_path / 'runtime'}'; echo EXIT=$?",
        env_extra={"_SKILL_ROOT": str(fake_skill_root)},
    )
    assert "EXIT=0" in result.stdout, (
        f"invoke_batch_gate must return 0 on success.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC-4: missing gate script → existing warning, return 0 ──────────────────

def test_missing_gate_script_warns_and_returns_zero(tmp_path: Path) -> None:
    """When batch_gate.py is absent, the existing warning fires and returns 0.

    AC-4: this path already works and must not regress.
    """
    # Point _SKILL_ROOT at a dir that has no batch_gate.py
    empty_skill_root = tmp_path / "empty-skill-root"
    (empty_skill_root / "ilk-loop" / "scripts").mkdir(parents=True)
    result = _source_runner_and_call(
        f"invoke_batch_gate '{tmp_path}' '{tmp_path / 'runtime'}'; echo EXIT=$?",
        env_extra={"_SKILL_ROOT": str(empty_skill_root)},
    )
    assert "WARNING" in result.stdout, (
        f"Missing script should print a warning.\nstdout: {result.stdout}"
    )
    assert "EXIT=0" in result.stdout, (
        f"Missing script must still return 0.\nstdout: {result.stdout}"
    )
