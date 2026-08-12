"""Tests for collect.py's handling of runs with malformed/no JSONL records.

Covers the KeyError on iters[-1]["iteration"] when a run has JSONL records
but they lack the "iteration" key (e.g. a crash before the summary was written).

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
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "collect-no-records"

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


def _runtime_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime"


def _launcher_dir(data_home: Path, key: str) -> Path:
    return _runtime_dir(data_home, key) / "launcher"


def _logs_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "logs"


def _write_jsonl_record(data_home: Path, key: str, project_path: Path, run_id: str, iteration: int = 1) -> None:
    """Write a single valid JSONL iteration record."""
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


def _write_malformed_jsonl_record(data_home: Path, key: str, project_path: Path, run_id: str) -> None:
    """Write a JSONL record missing the 'iteration' key (simulates crash before summary)."""
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "exit_code": 0,
        # Missing "iteration" key — this is the crash trigger
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _write_per_iter_log(data_home: Path, key: str, run_id: str, iter_num: int = 1) -> None:
    """Write a per-iteration .log file (not .jsonl) to simulate partial evidence."""
    runs_dir = _logs_dir(data_home, key) / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    log_path = runs_dir / f"iter-{iter_num:02d}.log"
    log_path.write_text(f"[iter-{iter_num:02d}] some log content\n", encoding="utf-8")


# ── Test: run with malformed JSONL (missing iteration key) → KeyError ─────


def test_malformed_jsonl_exits_with_message(scratch_env):
    """When --run-id R is passed and R has JSONL records but they lack the
    'iteration' key, collect.py should exit non-zero with a clear message
    pointing at the run log directory (AC-1, AC-2, AC-3)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    target_run = "20260812-095202"
    _write_malformed_jsonl_record(data_home, key, project_path, target_run)
    _write_per_iter_log(data_home, key, target_run, iter_num=1)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            target_run,
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8", errors="replace",
    )
    # Should NOT raise KeyError — should exit non-zero with clear message
    assert result.returncode != 0, (
        f"Expected non-zero exit (no valid records), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Should NOT have a traceback
    assert "Traceback" not in result.stderr, (
        f"Should not produce a traceback.\nstderr: {result.stderr}"
    )
    assert "KeyError" not in result.stderr, (
        f"Should not produce a KeyError.\nstderr: {result.stderr}"
    )
    # AC-2: message names the run id and points at the run log directory
    assert target_run in result.stderr, (
        f"stderr should mention the run id {target_run!r}.\nstderr: {result.stderr}"
    )
    assert "per-iteration logs are at" in result.stderr, (
        f"stderr should point at the run log directory.\nstderr: {result.stderr}"
    )
    # AC-3: the run log dir it names actually exists on disk
    # Extract the path from the stderr message
    import re
    match = re.search(r"per-iteration logs are at (.+)$", result.stderr, re.MULTILINE)
    assert match, f"Could not extract run log dir from stderr: {result.stderr}"
    run_log_dir = Path(match.group(1).strip())
    assert run_log_dir.is_dir(), (
        f"Run log dir should exist on disk: {run_log_dir}"
    )


# ── Test: run with valid JSONL records → still produces report (AC-4) ─────


def test_valid_records_still_produce_report(scratch_env):
    """When --run-id R is passed and R has valid JSONL records with 'iteration'
    key, collect.py should produce a postmortem report (AC-4 guard)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    target_run = "20260810-172912"
    _write_jsonl_record(data_home, key, project_path, target_run, iteration=1)
    _write_jsonl_record(data_home, key, project_path, target_run, iteration=2)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            target_run,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem file must exist.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{target_run}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    # Should contain classification (not no-evidence).
    text = pm_path.read_text(encoding="utf-8")
    assert "no-evidence" not in text, (
        f"Should not be no-evidence when valid JSONL records exist.\nHead:\n{text[:500]}"
    )
