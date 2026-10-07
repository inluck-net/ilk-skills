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

def test_suite_worker_count_quiet_high_core():
    """(10, 0.0) → 8 — quiet host, plenty of cores."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 0.0) == 8


def test_suite_worker_count_moderate_load():
    """(10, 1.6) → 8 — moderate load still fits."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 1.6) == 8


def test_suite_worker_count_heavy_load():
    """(10, 4.2) → 6 — heavy load reduces workers."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 4.2) == 6


def test_suite_worker_count_extreme_load():
    """(10, 13.4) → 4 — extreme load clamps to floor."""
    from conftest import suite_worker_count
    assert suite_worker_count(10, 13.4) == 4


def test_suite_worker_count_many_cores():
    """(16, 0.0) → 8 — cap at 8 even with many cores."""
    from conftest import suite_worker_count
    assert suite_worker_count(16, 0.0) == 8


def test_suite_worker_count_few_cores():
    """(4, 0.0) → 4 — floor at 4 even when quiet."""
    from conftest import suite_worker_count
    assert suite_worker_count(4, 0.0) == 4


# ── AC-2: pytest_xdist_auto_num_workers hook ─────────────────────────────────

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


def test_resolve_expected_invocation_contains_n_auto():
    """ship_audit._resolve_expected_invocation shows -n auto."""
    sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
    from ship_audit import _resolve_expected_invocation
    invocation = _resolve_expected_invocation(_REPO_ROOT)
    assert "-n auto" in invocation, (
        f"expected '-n auto' in invocation, got: {invocation!r}"
    )


# ── AC-4: pytester/subprocess header line ─────────────────────────────────────

def test_report_header_prints_worker_count(tmp_path):
    """A run with -n auto -p xdist prints the xdist workers header."""
    import subprocess as _sp
    # Write a trivial test and a conftest that loads the root hook
    (tmp_path / "test_trivial.py").write_text("def test_trivial(): pass\n")
    _conftest_path = str(_REPO_ROOT / "conftest.py")
    conftest_src = (
        "import importlib.util\n"
        f"_spec = importlib.util.spec_from_file_location('_root_conftest', {_conftest_path!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "pytest_report_header = _mod.pytest_report_header\n"
        "pytest_xdist_auto_num_workers = _mod.pytest_xdist_auto_num_workers\n"
    )
    (tmp_path / "conftest.py").write_text(conftest_src)
    result = _sp.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_trivial.py"),
         "-n", "auto", "-p", "xdist", "--timeout=30", "--timeout-method=signal",
         "-v"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    import re
    assert re.search(r"xdist workers: \d+ \(ncpu \d+, load1 \d+\.\d\)", result.stdout), (
        f"header line not found in stdout:\n{result.stdout[-500:]}"
    )