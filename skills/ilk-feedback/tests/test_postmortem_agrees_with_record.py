"""Tests for postmortem stats agreeing with the JSONL record.

Covers the defect where run 20260904-103214's postmortem showed
new_commits_total=0, total_elapsed_sec=0, per-iter Commits=0, Exit=?,
Stop reason=-  while the .ilk-loop.log record had new_commits_total=1,
duration_sec=820, exit_code=0, stop_reason=local_checks_failed.

Diagnosis: the aggregation at collect.py:1825-1828 is correct; the
defect is upstream in record resolution — `iters` was empty, so every
sum fell to 0 and every per-iter cell fell to its placeholder.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "postmortem-agrees"
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(project_path: Path) -> str:
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


@pytest.fixture()
def scratch_env(tmp_path: Path):
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "my-proj"
    project_path.mkdir()
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    key = _project_key(project_path)
    return project_path, env, key


def _logs_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "logs"


def _launcher_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime" / "launcher"


def _write_summary_record(
    data_home: Path,
    key: str,
    project_path: Path,
    run_id: str,
    *,
    iteration: int = 1,
    exit_code: int = 0,
    new_commits_total: int = 1,
    duration_sec: int = 820,
    stop_reason: str = "local_checks_failed",
) -> None:
    """Write a single JSONL summary record to .ilk-loop.log."""
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "iteration": iteration,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
        "stop_reason": stop_reason,
        "duration_sec": duration_sec,
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── AC-1: front-matter stats equal the record's stats ─────────────────────


def test_postmortem_stats_equal_record_stats(scratch_env):
    """Given a single-iteration record with new_commits_total=1 and
    duration_sec=820, the generated front-matter must carry those values.

    The aggregation at collect.py:1825-1828 is correct — this test PASSES.
    The original defect (all-zero stats) was caused by empty `iters`, which
    is a record-resolution bug upstream.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    run_id = "20260904-103214"

    _write_summary_record(
        data_home, key, project_path, run_id,
        iteration=1, exit_code=0, new_commits_total=1,
        duration_sec=820, stop_reason="local_checks_failed",
    )

    result = subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_path = _launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")

    # AC-1: front-matter stats
    assert "new_commits_total: 1" in text, (
        f"Expected new_commits_total: 1 in front-matter.\nHead:\n{text[:600]}"
    )
    assert "total_elapsed_sec: 820" in text, (
        f"Expected total_elapsed_sec: 820 in front-matter.\nHead:\n{text[:600]}"
    )


# ── AC-2: per-iteration table shows real values, not placeholders ─────────


def test_per_iter_table_shows_real_values(scratch_env):
    """The per-iteration table for a single-iteration record with
    exit_code=0, new_commits_total=1, stop_reason=local_checks_failed
    must show Commits 1, Exit 0, Stop reason local_checks_failed —
    no '?' and no '-'.

    The per-iter rendering is correct — this test PASSES.  The original
    defect (placeholder values) was caused by empty `iters`, which is a
    record-resolution bug upstream.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    run_id = "20260904-103214"

    _write_summary_record(
        data_home, key, project_path, run_id,
        iteration=1, exit_code=0, new_commits_total=1,
        duration_sec=820, stop_reason="local_checks_failed",
    )

    result = subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    pm_path = _launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md"
    text = pm_path.read_text(encoding="utf-8")

    # Find the per-iteration table section
    # The table has columns: Iter | Exit | Commits | Duration | Stop reason
    # For iter 1: Exit 0, Commits 1, Stop reason local_checks_failed
    assert "| 1 |" in text, (
        f"Per-iteration table should have row for iter 1.\nBody:\n{text[600:]}"
    )

    # Commits must be 1, not 0
    # Look for the iter-1 row pattern
    lines = text.split("\n")
    iter_row = None
    for line in lines:
        if line.strip().startswith("| 1 |"):
            iter_row = line
            break

    assert iter_row is not None, (
        f"Could not find per-iteration row for iter 1.\nBody:\n{text[600:]}"
    )

    # Parse the row: | # | Duration (min) | Commits | Exit | Stop reason |
    cells = [c.strip() for c in iter_row.split("|")]
    # cells[0] is empty (before first |), cells[1]=#, cells[2]=Duration,
    # cells[3]=Commits, cells[4]=Exit, cells[5]=Stop reason, cells[6]=empty
    assert len(cells) >= 6, f"Expected ≥6 cells, got {len(cells)}: {cells}"

    commits_cell = cells[3]
    exit_cell = cells[4]
    stop_cell = cells[5]

    # AC-2: no placeholders — all values must be real, not fallbacks
    assert commits_cell != "?", f"Commits should not be '?'. Row: {iter_row}"
    assert exit_cell != "?", f"Exit should not be '?'. Row: {iter_row}"
    assert stop_cell != "-", f"Stop reason should not be '-'. Row: {iter_row}"

    # Correct values
    assert commits_cell == "1", f"Commits should be '1'. Row: {iter_row}"
    assert exit_cell == "0", f"Exit should be '0'. Row: {iter_row}"
    assert "local_checks_failed" in stop_cell, (
        f"Stop reason should contain 'local_checks_failed'. Row: {iter_row}"
    )
