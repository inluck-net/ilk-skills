"""Tests for the measure_baseline module.

Sub-plan: a-tags-baseline-can-be-measured, step 0 (red-first pins).

Each test builds a throwaway git repo under ``tmp_path`` with two test files
(test_ok.py with 2 passing, test_bad.py with 1 failing), committed and tagged
``v0.0.1``.  A data dir is created under ``tmp_path``.  Tag and invocation are
passed explicitly so the tests do not depend on the repo's ``.ilk-launch.json``.

Acceptance criteria:
  AC-1  check is missing before; measure stores; load_baseline returns ids
  AC-2  measurement runs at the tag (not HEAD)
  AC-3  no-summary run stores nothing; attempts tracked; unmeasurable after 2
  AC-4  worktree is gone after measure (both success and failure)
  AC-5  check with live pid marker is measuring
  AC-6  CLI: check prints state word; measure prints result JSON
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"


def _import_measure_baseline():
    """Import ``measure_baseline`` with the correct sys.path."""
    for p in (str(SHIP_SCRIPTS), str(LOOP_SCRIPTS)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("measure_baseline")


def _make_git_repo(project: Path) -> None:
    """Create a minimal git repo with two test files and tag ``v0.0.1``."""
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True,
                   capture_output=True)
    # test_ok.py: 2 passing tests
    (project / "test_ok.py").write_text(textwrap.dedent("""\
        def test_ok_1():
            assert True
        def test_ok_2():
            assert True
    """))
    # test_bad.py: 1 failing test
    (project / "test_bad.py").write_text(textwrap.dedent("""\
        def test_bad():
            assert False
    """))
    subprocess.run(["git", "add", "."], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "tag", "v0.0.1"], cwd=project, check=True,
                   capture_output=True)
    # Now fix test_bad on HEAD so we can verify AC-2 (measurement runs at tag)
    (project / "test_bad.py").write_text(textwrap.dedent("""\
        def test_bad():
            assert True
    """))
    subprocess.run(["git", "add", "."], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "fix test_bad"], cwd=project,
                   check=True, capture_output=True)


def _count_worktrees(project: Path) -> int:
    """Count git worktrees (excluding the main one)."""
    r = subprocess.run(["git", "worktree", "list"], cwd=project,
                       capture_output=True, text=True)
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    return len(lines)


# ── AC-1: check is missing before; measure stores; load returns ids ────────

def test_ac1_check_missing_then_measure_stores(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()
    invocation = "python3 -m pytest -q -p no:cacheprovider"

    # check is missing before measure
    result = mb.check(project, data_dir)
    assert result["state"] == "missing", f"expected missing, got {result['state']}"

    # measure stores the baseline
    measure_result = mb.measure(project, data_dir, tag="v0.0.1",
                                invocation=invocation)
    assert measure_result["stored"] is True

    # load_baseline returns the expected ids
    from baseline_diff import load_baseline
    loaded = load_baseline(data_dir, "v0.0.1", invocation)
    assert loaded is not None
    node_ids, search_space = loaded
    assert node_ids == {"test_bad.py::test_bad"}
    assert search_space == 3

    # check is now present
    result = mb.check(project, data_dir)
    assert result["state"] == "present"


# ── AC-2: measurement runs at the tag, not HEAD ────────────────────────────

def test_ac2_measurement_runs_at_tag_not_head(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()
    invocation = "python3 -m pytest -q -p no:cacheprovider"

    # At HEAD, test_bad passes (we fixed it).  At tag v0.0.1, it fails.
    result = mb.measure(project, data_dir, tag="v0.0.1", invocation=invocation)
    assert result["stored"] is True

    from baseline_diff import load_baseline
    loaded = load_baseline(data_dir, "v0.0.1", invocation)
    assert loaded is not None
    node_ids, _ = loaded
    # The failing test at the tag is test_bad, even though HEAD has it passing
    assert "test_bad.py::test_bad" in node_ids


# ── AC-3: no-summary run stores nothing; unmeasurable after 2 attempts ─────

def test_ac3_no_summary_stores_nothing_unmeasurable_after_two(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()

    # First attempt with a crashing invocation
    result = mb.measure(project, data_dir, tag="v0.0.1",
                        invocation="python3 -c 'raise SystemExit(3)'")
    assert result["stored"] is False

    # Marker records attempts: 1
    marker = data_dir / "runtime" / "release" / "baseline-measure-v0.0.1.json"
    assert marker.exists()
    marker_data = json.loads(marker.read_text())
    assert marker_data["attempts"] == 1

    # check is still missing (not unmeasurable yet)
    check_result = mb.check(project, data_dir)
    assert check_result["state"] == "missing"

    # Second attempt
    result = mb.measure(project, data_dir, tag="v0.0.1",
                        invocation="python3 -c 'raise SystemExit(3)'")
    assert result["stored"] is False

    # Now check is unmeasurable
    check_result = mb.check(project, data_dir)
    assert check_result["state"] == "unmeasurable"


# ── AC-4: worktree is gone after measure ───────────────────────────────────

def test_ac4_worktree_cleaned_up_on_success(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()
    invocation = "python3 -m pytest -q -p no:cacheprovider"

    before = _count_worktrees(project)
    mb.measure(project, data_dir, tag="v0.0.1", invocation=invocation)
    after = _count_worktrees(project)
    assert after == before, f"worktree leaked: {after} vs {before} before"


def test_ac4_worktree_cleaned_up_on_failure(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()

    before = _count_worktrees(project)
    mb.measure(project, data_dir, tag="v0.0.1",
               invocation="python3 -c 'raise SystemExit(3)'")
    after = _count_worktrees(project)
    assert after == before, f"worktree leaked on failure: {after} vs {before} before"


# ── AC-5: check with live pid marker is measuring ──────────────────────────

def test_ac5_check_with_live_pid_is_measuring(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mb = _import_measure_baseline()

    # Write a marker with a live pid (our own)
    marker_dir = data_dir / "runtime" / "release"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / "baseline-measure-v0.0.1.json"
    marker.write_text(json.dumps({
        "pid": os.getpid(),
        "tag": "v0.0.1",
        "attempts": 0,
        "started_at": "2026-10-10T00:00:00",
    }))

    result = mb.check(project, data_dir)
    assert result["state"] == "measuring"


# ── AC-6: CLI prints state word and result JSON ────────────────────────────

def test_ac6_cli_check_prints_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # check --project P --data-dir D prints "missing"
    r = subprocess.run(
        [sys.executable, str(SHIP_SCRIPTS / "measure_baseline.py"),
         "check", "--project", str(project), "--data-dir", str(data_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "missing"

    # After measure, check prints "present"
    invocation = "python3 -m pytest -q -p no:cacheprovider"
    subprocess.run(
        [sys.executable, str(SHIP_SCRIPTS / "measure_baseline.py"),
         "measure", "--project", str(project), "--data-dir", str(data_dir),
         "--tag", "v0.0.1"],
        capture_output=True, text=True, check=True,
    )
    r = subprocess.run(
        [sys.executable, str(SHIP_SCRIPTS / "measure_baseline.py"),
         "check", "--project", str(project), "--data-dir", str(data_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "present"


def test_ac6_cli_measure_prints_json(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    r = subprocess.run(
        [sys.executable, str(SHIP_SCRIPTS / "measure_baseline.py"),
         "measure", "--project", str(project), "--data-dir", str(data_dir),
         "--tag", "v0.0.1"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    result = json.loads(r.stdout)
    assert result["stored"] is True