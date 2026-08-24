"""RED tests for config-level pytest timeout bound.

AC-1: pytest.ini addopts carries --timeout=60 --timeout-method=signal.
AC-2: A bare pytest on a hanging fixture terminates by timeout, not hang.

Both tests are RED today — pytest.ini addopts has only --import-mode=importlib.
"""
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "hanging_test"
PYTEST_INI = Path(__file__).resolve().parents[2] / "pytest.ini"


# ── AC-1: pytest.ini addopts carries the bound ───────────────────────────────

def _get_addopts() -> str:
    """Extract the addopts value from pytest.ini."""
    for line in PYTEST_INI.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("addopts"):
            _, _, val = stripped.partition("=")
            return val.strip()
    return ""


def test_pytest_ini_addopts_has_timeout_and_signal_method():
    """addopts must carry --timeout=60 --timeout-method=signal.

    The bound goes in config so the worker cannot route around it.
    The method must be signal (thread is known to hang — SKILL.md:453).
    """
    addopts = _get_addopts()
    assert "--timeout=" in addopts, (
        f"pytest.ini addopts missing --timeout=; got: {addopts!r}"
    )
    assert "--timeout-method=signal" in addopts, (
        f"pytest.ini addopts missing --timeout-method=signal; got: {addopts!r}"
    )


# ── AC-2: a hanging test is killed by config, not by CLI flags ───────────────

def test_hanging_fixture_killed_by_config_timeout():
    """A bare pytest (no --timeout on CLI) on a sleeping test must timeout.

    The fixture sleeps 90s.  If pytest.ini addopts carries --timeout=60,
    pytest kills it at 60s.  We give the subprocess 120s hard wall-clock
    so a regression fails loudly rather than hanging the suite.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(FIXTURE_DIR), "-q"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(PYTEST_INI.parent),
    )
    combined = result.stdout + result.stderr
    # A timeout failure shows "FAILED" or "Timeout >" or exit code 2
    timed_out = (
        "Timeout" in combined
        or "FAILED" in combined
        or result.returncode != 0
    )
    assert timed_out, (
        "Hanging test was NOT killed — pytest.ini addopts does not bound it.\n"
        f"stdout: {result.stdout[-500:]}\n"
        f"stderr: {result.stderr[-500:]}"
    )
