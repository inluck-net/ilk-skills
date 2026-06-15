"""Tests for collect.py per-iter JSONL fallback (never cross-run misclassification).

Covers the fix for CANDIDATE-8: when the target run_id has no summary JSONL
records but DOES have per-iter JSONL (logs/runs/<run-id>/iter-*.jsonl),
collect.py should classify from those per-iter logs — never silently fall
back to a different run's summary records.

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

# Repo root — scratch dirs live here, never in tmp_path (§9 sandbox rule).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "collect-per-iter"

# Paths to scripts we invoke via subprocess.
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

# Same regex as ilk_paths.project_key — duplicated here to avoid import path gymnastics.
_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(project_path: Path) -> str:
    """Replicate ilk_paths.project_key logic (pure, no subprocess)."""
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


@pytest.fixture()
def scratch_env(tmp_path: Path):
    """Build an isolated ILK_DATA_HOME + temp project dir.

    Returns (project_path, env_dict, key) where *key* is the project_key
    ilk_paths.py derives for *project_path* under the isolated data root.
    """
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
    data_home: Path, key: str, project_path: Path, run_id: str, iteration: int = 1
) -> None:
    """Write a single JSONL summary record to .ilk-loop.log."""
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "iteration": iteration,
        "exit_code": 0,
        "new_commits_total": 2,
        "stop_reason": "already-shipped",
        "duration_sec": 60,
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _write_per_iter_jsonl(
    data_home: Path, key: str, run_id: str, iteration: int = 1
) -> None:
    """Write a per-iter JSONL record to logs/runs/<run-id>/iter-NN.log.jsonl."""
    runs_dir = _logs_dir(data_home, key) / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = runs_dir / f"iter-{iteration:02d}.log.jsonl"
    record = {
        "run_id": run_id,
        "iteration": iteration,
        "exit_code": 0,
        "new_commits_total": 3,
        "stop_reason": "already-shipped",
        "duration_sec": 90,
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _write_sentinel(data_home: Path, key: str, run_id: str, state: str = "running") -> None:
    """Write a last-exit.json sentinel."""
    rt_dir = data_home / "projects" / key / "runtime"
    rt_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {"state": state, "run_id": run_id, "iters": 1}
    (rt_dir / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")


# ── AC-1: per-iter fallback classifies target run, never cross-run ─────────


def test_per_iter_fallback_classifies_target_run(scratch_env):
    """Run X has ONLY per-iter JSONL (no summary).  Run Y IS in summary.
    collect.py --run-id X must classify X from per-iter logs — never Y."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_y = "20260608-100000"  # older, in summary
    run_x = "20260615-120000"  # target, per-iter only

    _write_summary_record(data_home, key, project_path, run_y, iteration=1)
    _write_per_iter_jsonl(data_home, key, run_x, iteration=1)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_x,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem must exist for run X (not Y).
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_x}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    # Must classify X, not Y.
    assert run_x in text, f"Postmortem should reference {run_x}, not {run_y}."
    assert run_y not in text, (
        f"Postmortem should NOT reference {run_y} (cross-run misclassification).\n"
        f"Head:\n{text[:500]}"
    )
    # Should NOT be no-evidence (per-iter records exist).
    assert "no-evidence" not in text, (
        f"Should not be no-evidence when per-iter JSONL exists.\nHead:\n{text[:500]}"
    )


# ── AC-2: no records anywhere → no-evidence for that run_id ────────────────


def test_no_records_anywhere_returns_no_evidence_for_target(scratch_env):
    """Run X has NO records (no summary, no per-iter, no sentinel).
    Run Y IS in summary.  collect.py --run-id X must report no-evidence
    for X — the report's run_id must be X, never a classification of Y."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_y = "20260608-100000"
    run_x = "20260615-120000"

    _write_summary_record(data_home, key, project_path, run_y, iteration=1)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_x,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    # Should exit 0 with a no-evidence postmortem (not error, not classify Y).
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_x}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    # Must be no-evidence for run X (not a classification of Y).
    assert "no-evidence" in text, (
        f"Should be no-evidence when no records exist.\nHead:\n{text[:500]}"
    )
    assert f'run_id: "{run_x}"' in text or f"run_id: {run_x}" in text, (
        f"Frontmatter run_id must be {run_x}.\nHead:\n{text[:500]}"
    )
    # Classification must be no-evidence (not clean-success, not any label from Y).
    assert 'classification: "no-evidence"' in text or "classification: no-evidence" in text, (
        f"Classification must be no-evidence.\nHead:\n{text[:500]}"
    )


# ── AC-3: summary path unchanged (regression guard) ────────────────────────


def test_summary_path_still_works(scratch_env):
    """When summary records for the target run exist, classification is
    unchanged — existing behavior preserved."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_id = "20260615-120000"
    _write_summary_record(data_home, key, project_path, run_id, iteration=1)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_id,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert run_id in text, "Postmortem should reference the target run_id."
    assert "no-evidence" not in text, (
        f"Should not be no-evidence when summary records exist.\nHead:\n{text[:500]}"
    )


# ── Auto-detect path: per-iter fallback for newest run ─────────────────────


def test_auto_detect_with_no_summary_no_per_iter_returns_error(scratch_env):
    """When no --run-id is passed and there are no summary records,
    collect.py cannot discover a run_id to check per-iter logs against.
    This is expected — per-iter fallback requires a known run_id (via --run-id
    or summary records).  Verify exit 1 with helpful message."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    # Write per-iter JSONL for a run, but no summary and no --run-id.
    # collect.py can't discover this run_id without summary records.
    run_x = "20260615-120000"
    _write_per_iter_jsonl(data_home, key, run_x, iteration=1)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    # Expected: exit 1 (no summary records → can't discover run_id).
    assert result.returncode == 1, (
        f"Expected exit 1 (no summary records), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "No JSONL records" in result.stdout, (
        f"Should report 'No JSONL records'.\nstdout: {result.stdout}"
    )


# ── Sentinel + per-iter: per-iter takes priority ───────────────────────────


def test_per_iter_takes_priority_over_sentinel(scratch_env):
    """When both per-iter JSONL and sentinel exist for a run with no summary,
    per-iter records should be used (classification from actual data, not
    no-evidence)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_x = "20260615-120000"
    _write_per_iter_jsonl(data_home, key, run_x, iteration=1)
    _write_sentinel(data_home, key, run_x, state="running")

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_x,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_x}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "no-evidence" not in text, (
        f"Per-iter records should take priority over sentinel.\nHead:\n{text[:500]}"
    )
