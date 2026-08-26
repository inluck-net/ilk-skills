"""RED tests for config-level pytest timeout bound.

AC-1: pytest.ini addopts carries --timeout=60 --timeout-method=signal.
AC-2: A bare pytest on a hanging fixture terminates by timeout, not hang.

Both tests are RED today — pytest.ini addopts has only --import-mode=importlib.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# The old fixtures/hanging_test (a 90s sleep) is no longer used: AC-2 now
# builds its own 30s sleeper under tmp_path with a 5s bound.  Left on disk
# because `norecursedirs = fixtures` keeps it uncollected either way.
PYTEST_INI = Path(__file__).resolve().parents[3] / "pytest.ini"


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

@pytest.mark.timeout(60)
def test_hanging_fixture_killed_by_config_timeout(tmp_path):
    """A bare pytest (no --timeout on CLI) on a sleeping test must timeout.

    The property under test is that the **config** bounds a hanging test —
    not a CLI flag.  So the bound must come from a pytest.ini, and the run
    must pass no --timeout of its own.

    It used to assert that against the REPO's pytest.ini (--timeout=60) with
    a fixture sleeping 90s, which cost a real 60 seconds every suite run —
    60.21s of a 299.55s suite, one of the three tests that were 61% of it
    (batch-gate --durations=25, 2026-08-26).

    A throwaway pytest.ini with --timeout=5 proves exactly the same property
    for 5s.  Overriding with `--timeout=5` on the command line would NOT:
    that is the CLI path, which is precisely what this test exists to rule
    out.  The repo's own value is covered by AC-1 above, which is free.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "pytest.ini").write_text(
        "[pytest]\naddopts = --timeout=5 --timeout-method=signal\n",
        encoding="utf-8",
    )
    (sandbox / "test_sleeps.py").write_text(
        "import time\n\n\ndef test_hangs():\n    time.sleep(30)\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(sandbox), "-q"],
        capture_output=True,
        text=True,
        timeout=45,
        cwd=str(sandbox),
        encoding="utf-8",
    )
    combined = result.stdout + result.stderr
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
