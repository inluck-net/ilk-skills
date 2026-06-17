"""Tests for collect.py's early-death / no-JSONL classification.

Covers the two branches of the empty-records guard in main():
  1. Sentinel present  → classify "interrupted", write postmortem, exit 0
  2. No sentinel       → keep today's exit 1 (ilk never ran here)

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
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "collect-early-death"

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


# ── Test: sentinel present → interrupted postmortem, exit 0 ────────────────

def test_early_death_emits_interrupted(scratch_env):
    """When JSONL is empty but a sentinel exists, classify interrupted."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    # Write sentinel (what the runner writes at start + finally).
    rt_dir = _runtime_dir(data_home, key)
    rt_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": "interrupted",
        "iters": 1,
        "run_id": "20260607-124231",
    }
    (rt_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    # Run collect.py — expect exit 0 and a postmortem file.
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
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Postmortem file must exist with correct classification.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / "20260607-124231.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    head = pm_path.read_text(encoding="utf-8")[:500]
    assert 'classification: "interrupted"' in head or "classification: interrupted" in head, (
        f"Frontmatter missing 'classification: interrupted'.\nHead:\n{head}"
    )


# ── Test: no sentinel → exit 1, no postmortem ─────────────────────────────

def test_never_ran_exits_1(scratch_env):
    """When neither JSONL nor sentinel exist, collect.py exits 1."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 1, (
        f"Expected exit 1, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # No postmortem should be written.
    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    assert not pm_dir.exists(), f"Postmortem dir unexpectedly exists: {pm_dir}"
