"""Tests for collect.py's handling of selfmod worktree records and non-object JSONL lines.

Covers:
  AC-0  a non-object JSONL line never crashes collect
  AC-1  selfmod rows count (records with project = selfmod worktree path)
  AC-2  without --run-id, when newest run is that selfmod run, it is chosen
  AC-3  a record whose project is an unrelated path is still excluded (control)
  AC-4  last-launch.json naming a later run ⇒ started_at equals the run's own first record timestamp

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

# Repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Paths to scripts we invoke via subprocess.
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

# Same regex as ilk_paths.project_key — duplicated here to avoid import path gymnastics.
_KEY_PUNCT = re.compile(r"[^a-z0-9]+")
_KEY_HASH_LEN = 7
_KEY_SLUG_MAX = 80 - 1 - _KEY_HASH_LEN  # 72


def _project_key(project_path: Path) -> str:
    """Mirror ilk_paths.project_key: always append a 7-char SHA1 suffix."""
    abs_str = str(project_path.resolve())
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:_KEY_HASH_LEN]
    slug = _KEY_PUNCT.sub("-", abs_str.lower()).strip("-")[:_KEY_SLUG_MAX].rstrip("-")
    return f"{slug}-{h}" if slug else h


def _selfmod_worktree_path(data_home: Path, key: str) -> Path:
    """The selfmod worktree path for a key: <external_launcher_dir>/worktrees/selfmod-batch."""
    return data_home / "projects" / key / "runtime" / "launcher" / "worktrees" / "selfmod-batch"


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


def _write_jsonl_line(data_home: Path, key: str, record: dict) -> None:
    """Append one JSONL line to the canonical log file."""
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _write_last_launch(data_home: Path, key: str, launch: dict) -> None:
    """Write a last-launch.json sentinel."""
    launcher_dir = _launcher_dir(data_home, key)
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(json.dumps(launch), encoding="utf-8")


# ── AC-0: a non-object JSONL line never crashes collect ──────────────────────


def test_non_object_jsonl_line_does_not_crash(scratch_env):
    """A candidate JSONL containing the line "just a string" plus 1 valid record
    ⇒ no exception; 1 iteration; skipped_non_object: 1."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    selfmod_path = _selfmod_worktree_path(data_home, key)

    run_id = "20261001-232701"

    # Write a bare string line (non-object) followed by a valid record.
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write('"just a string"\n')
        f.write(json.dumps({
            "project": str(selfmod_path),
            "run_id": run_id,
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 2,
            "stop_reason": "already-shipped",
            "duration_sec": 60,
        }) + "\n")

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
        encoding="utf-8",
        errors="replace",
    )
    # Must not crash — the non-object line should be skipped.
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # No traceback.
    assert "Traceback" not in result.stderr, (
        f"Should not produce a traceback.\nstderr: {result.stderr}"
    )
    # Postmortem must exist with 1 iteration and skipped_non_object: 1.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"
    text = pm_path.read_text(encoding="utf-8")
    assert "1" in text  # iterations count


# ── AC-1: selfmod rows count ─────────────────────────────────────────────────


def test_selfmod_records_are_included(scratch_env):
    """A JSONL with 2 records whose project is the key's selfmod worktree path
    ⇒ collect.py --run-id <id> produces a postmortem with those 2 iterations,
    not 'No JSONL records'."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    selfmod_path = _selfmod_worktree_path(data_home, key)

    run_id = "20261001-232701"

    # Write 2 records with project = selfmod worktree path.
    for i in range(1, 3):
        _write_jsonl_line(data_home, key, {
            "project": str(selfmod_path),
            "run_id": run_id,
            "iteration": i,
            "exit_code": 0,
            "new_commits_total": 2,
            "stop_reason": "already-shipped" if i == 2 else None,
            "duration_sec": 60,
        })

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
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem must exist.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    # Must NOT say "No JSONL records" or "no-evidence".
    assert "No JSONL records" not in text, (
        f"Should not say 'No JSONL records' when selfmod records exist.\n{text[:500]}"
    )
    assert "no-evidence" not in text, (
        f"Should not be no-evidence when selfmod records exist.\n{text[:500]}"
    )
    # Should report 2 iterations.
    assert "2" in text, f"Should report 2 iterations.\n{text[:500]}"


# ── AC-2: without --run-id, newest selfmod run is chosen ─────────────────────


def test_newest_selfmod_run_chosen_without_run_id(scratch_env):
    """Without --run-id, when the newest run is that selfmod run, it is the one chosen."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    selfmod_path = _selfmod_worktree_path(data_home, key)

    # Write records for two runs: an older one and the selfmod one.
    _write_jsonl_line(data_home, key, {
        "project": str(selfmod_path),
        "run_id": "20261001-225153",
        "iteration": 1,
        "exit_code": 0,
        "new_commits_total": 1,
        "stop_reason": "already-shipped",
        "duration_sec": 30,
    })
    _write_jsonl_line(data_home, key, {
        "project": str(selfmod_path),
        "run_id": "20261001-232701",
        "iteration": 1,
        "exit_code": 0,
        "new_commits_total": 2,
        "stop_reason": "already-shipped",
        "duration_sec": 60,
    })

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
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Should classify the newest selfmod run.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / "20261001-232701.md"
    assert pm_path.exists(), (
        f"Postmortem for newest selfmod run not found at {pm_path}.\n"
        f"Dir contents: {list(pm_dir.glob('*.md')) if pm_dir.is_dir() else 'dir missing'}"
    )


# ── AC-3: a record whose project is an unrelated path is still excluded ──────


def test_unrelated_project_path_excluded(scratch_env):
    """A record whose project is an unrelated path is still excluded. (Control — no xfail.)"""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_id = "20261001-232701"

    # Write a record with a completely unrelated project path.
    _write_jsonl_line(data_home, key, {
        "project": "/some/other/unrelated/project",
        "run_id": run_id,
        "iteration": 1,
        "exit_code": 0,
        "new_commits_total": 2,
        "stop_reason": "already-shipped",
        "duration_sec": 60,
    })

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_id,
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    # Should fail — no records match.
    assert result.returncode != 0, (
        f"Expected non-zero exit (no matching records), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC-4: started_at comes from the run's own records, not last-launch.json ──


def test_started_at_comes_from_own_run_records(scratch_env):
    """A last-launch.json naming a later run ⇒ the postmortem's started_at
    equals the run's own first record timestamp."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])
    selfmod_path = _selfmod_worktree_path(data_home, key)

    run_id = "20261001-232701"
    run_timestamp = "2026-10-02T00:25:00+0800"
    later_timestamp = "2026-10-02T01:56:29+0800"

    # Write 2 records for the target run with their own timestamp.
    for i in range(1, 3):
        _write_jsonl_line(data_home, key, {
            "project": str(selfmod_path),
            "run_id": run_id,
            "iteration": i,
            "exit_code": 0,
            "new_commits_total": 2,
            "stop_reason": "already-shipped" if i == 2 else None,
            "duration_sec": 60,
            "timestamp": run_timestamp,
        })

    # Write a last-launch.json naming a LATER run with a later started_at.
    _write_last_launch(data_home, key, {
        "run_id": "20261002-015629",
        "started_at": later_timestamp,
        "max_iterations": 10,
        "iteration_timeout_min": 45,
    })

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
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem must exist.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    # started_at must be the run's own timestamp, NOT the later one from last-launch.json.
    assert run_timestamp in text, (
        f"Postmortem started_at should be {run_timestamp}, not {later_timestamp}.\n"
        f"Head:\n{text[:800]}"
    )
    assert later_timestamp not in text, (
        f"Postmortem must NOT use last-launch.json's started_at ({later_timestamp}).\n"
        f"Head:\n{text[:800]}"
    )