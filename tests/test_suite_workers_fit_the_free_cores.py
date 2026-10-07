"""Pin that the suite's workers fit the free cores.

Part of plan:suite-workers-fit-the-free-cores.  These tests are xfail at
base because the conftest hook and -n auto flag don't exist yet.  They
will go green once step 1 implements the contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── AC-1: suite_worker_count pure helper ──────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_quiet_high_core():
    """(10, 0.0) → 8 — quiet host, plenty of cores."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 0.0) == 8


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_moderate_load():
    """(10, 1.6) → 8 — moderate load still fits."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 1.6) == 8


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_heavy_load():
    """(10, 4.2) → 6 — heavy load reduces workers."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 4.2) == 6


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_extreme_load():
    """(10, 13.4) → 4 — extreme load clamps to floor."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 13.4) == 4


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_many_cores():
    """(16, 0.0) → 8 — cap at 8 even with many cores."""
    from conftest import suite_worker_count
    assert suite_worker_count(16, 0.0) == 8


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_suite_worker_count_few_cores():
    """(4, 0.0) → 4 — floor at 4 even when quiet."""
    from conftest import suite_worker_count
    assert suite_worker_count(4, 0.0) == 4


# ── AC-2: pytest_xdist_auto_num_workers hook ─────────────────────────────────

@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_xdist_auto_num_workers_extreme_load():
    """Hook returns 4 when ncpu=10, load=13.4."""
    import conftest as root_conftest
    with (
        patch.object(os, "cpu_count", return_value=10),
        patch.object(os, "getloadavg", return_value=(13.4, 0.0, 0.0)),
    ):
        config = type("FakeConfig", (), {})()
        result = root_conftest.pytest_xdist_auto_num_workers(config)
    assert result == 4


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_xdist_auto_num_workers_getloadavg_oserror():
    """Hook returns 8 when getloadavg raises OSError (fallback to load 0)."""
    import conftest as root_conftest
    with (
        patch.object(os, "cpu_count", return_value=10),
        patch.object(os, "getloadavg", side_effect=OSError("not available")),
    ):
        config = type("FakeConfig", (), {})()
        result = root_conftest.pytest_xdist_auto_num_workers(config)
    assert result == 8


# ── AC-3: .ilk-launch.json has -n auto ────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_ilk_launch_json_has_n_auto():
    """ship.suite.flags contains the adjacent pair '-n', 'auto'."""
    import json
    launch = json.loads((_REPO_ROOT / ".ilk-launch.json").read_text())
    flags = launch["ship"]["suite"]["flags"]
    # Find '-n' and check the next element is 'auto'
    for i, flag in enumerate(flags):
        if flag == "-n" and i + 1 < len(flags):
            assert flags[i + 1] == "auto", (
                f"-n followed by {flags[i + 1]!r}, expected 'auto'"
            )
            return
    pytest.fail("no '-n' found in ship.suite.flags")


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_ilk_launch_json_no_digit_after_n():
    """No '-n' followed by a digit anywhere in flags."""
    import json
    launch = json.loads((_REPO_ROOT / ".ilk-launch.json").read_text())
    flags = launch["ship"]["suite"]["flags"]
    for i, flag in enumerate(flags):
        if flag == "-n" and i + 1 < len(flags):
            assert not flags[i + 1].isdigit(), (
                f"-n followed by digit {flags[i + 1]!r}"
            )


@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_resolve_expected_invocation_contains_n_auto():
    """ship_audit._resolve_expected_invocation shows -n auto."""
    sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
    from ship_audit import _resolve_expected_invocation
    invocation = _resolve_expected_invocation(_REPO_ROOT)
    assert "-n auto" in invocation, (
        f"expected '-n auto' in invocation, got: {invocation!r}"
    )


# ── AC-4: pytester/subprocess header line ─────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="workers fixed at -n 8")
def test_report_header_prints_worker_count(pytester):
    """A run with -n auto -p xdist prints the xdist workers header."""
    # Write a trivial test
    pytester.makepyfile("""
        def test_trivial():
            pass
    """)
    # Write a conftest that imports the root hook
    conftest_src = (
        f"import sys; sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
        "from conftest import pytest_report_header, pytest_xdist_auto_num_workers\n"
    )
    pytester.makeconftest(conftest_src)
    result = pytester.runpytest("-n", "auto", "-p", "xdist", "--timeout=30",
                                "--timeout-method=signal")
    result.stdout.fnmatch_lines([
        "xdist workers: * (ncpu *, load1 *)",
    ])