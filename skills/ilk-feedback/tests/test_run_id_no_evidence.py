"""Tests for collect.py --run-id with no-evidence classification.

Covers the fix for the stale-classify bug: when --run-id R is passed but
R has no JSONL records, collect.py should classify as "no-evidence" (if a
sentinel exists for R) instead of silently falling back to newest_run_id.

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
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "collect-run-id"

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


def _write_sentinel(data_home: Path, key: str, run_id: str, state: str = "local_checks_failed") -> None:
    """Write a last-exit.json sentinel."""
    rt_dir = _runtime_dir(data_home, key)
    rt_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {"state": state, "run_id": run_id, "iters": 1}
    (rt_dir / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")


def _write_jsonl_record(data_home: Path, key: str, project_path: Path, run_id: str, iteration: int = 1) -> None:
    """Write a single JSONL iteration record."""
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


# ── Test: --run-id R with no JSONL but sentinel present → no-evidence ──────


def test_run_id_no_jsonl_with_sentinel_returns_no_evidence(scratch_env):
    """When --run-id R is passed, R has no JSONL records, but a sentinel
    exists for R, classify as no-evidence (exit 0, postmortem written)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    target_run = "20260610-071415"
    _write_sentinel(data_home, key, target_run)

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
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem must exist with no-evidence classification.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{target_run}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert 'classification: "no-evidence"' in text or "classification: no-evidence" in text, (
        f"Frontmatter missing 'classification: no-evidence'.\nHead:\n{text[:500]}"
    )
    # Verify the reason mentions "no usable JSONL records"
    assert "no usable JSONL" in text, (
        f"Postmortem body should mention 'no usable JSONL records'.\nBody:\n{text[:800]}"
    )


# ── Test: --run-id R with no JSONL and no sentinel → error ─────────────────


def test_run_id_no_jsonl_no_sentinel_exits_error(scratch_env):
    """When --run-id R is passed, R has no JSONL records, and no sentinel
    exists, collect.py should error out (the run truly doesn't exist)."""
    project_path, env, key = scratch_env

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            "20260610-999999",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    # Should exit non-zero (the run doesn't exist at all).
    assert result.returncode != 0, (
        f"Expected non-zero exit, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Test: --run-id R with JSONL records → classifies normally ──────────────


def test_run_id_with_jsonl_classifies_that_run(scratch_env):
    """When --run-id R is passed and R has JSONL records, classify that
    run specifically (not newest_run_id)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    # Write records for two runs: an older one and the target.
    _write_jsonl_record(data_home, key, project_path, "20260608-100000", iteration=1)
    _write_jsonl_record(data_home, key, project_path, "20260610-071415", iteration=1)

    # Also write sentinel for the target run.
    _write_sentinel(data_home, key, "20260610-071415")

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            "20260610-071415",
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

    # Postmortem must exist for the target run (not the older one).
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / "20260610-071415.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "20260610-071415" in text, "Postmortem should reference the target run_id."
    # Should NOT be no-evidence (it has JSONL records).
    assert "no-evidence" not in text, (
        f"Should not be no-evidence when JSONL records exist.\nHead:\n{text[:500]}"
    )


# ── Test: no --run-id falls back to newest_run_id (existing behavior) ──────


def test_no_run_id_flag_uses_newest(scratch_env):
    """When no --run-id is passed, collect.py uses newest_run_id (existing behavior)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    # Write records for two runs.
    _write_jsonl_record(data_home, key, project_path, "20260608-100000", iteration=1)
    _write_jsonl_record(data_home, key, project_path, "20260610-071415", iteration=1)

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
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Should classify the newest run.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / "20260610-071415.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"
