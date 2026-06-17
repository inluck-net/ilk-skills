"""Tests for collect.py's read-only boundary w.r.t. the skills/ tree.

Verifies that collect.py writes ONLY under ~/.ilk-data (postmortems +
backlog candidates), never under skills/**.

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
    """Build an isolated ILK_DATA_HOME + temp project dir with JSONL data."""
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
    return project_path, env, key, data_home


def test_collect_writes_only_under_ilk_data_home(scratch_env):
    """collect.py postmortem + backlog candidates must stay under ILK_DATA_HOME."""
    project_path, env, key, data_home = scratch_env

    # Set up JSONL log with a local-checks-stuck classification so
    # maybe_emit_upstream_candidate fires (toolkit signal).
    log_dir = data_home / "projects" / key / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = log_dir / ".ilk-loop.log"
    # Write 5 iterations, last 3 with failing local_checks → local-checks-stuck
    records = []
    for i in range(1, 6):
        lc = {"outcome": "fail", "command": "pytest"} if i >= 3 else {"outcome": "pass", "command": "pytest"}
        records.append({
            "run_id": "20260609-120000",
            "iteration": i,
            "project": str(project_path),
            "exit_code": 1 if i >= 3 else 0,
            "duration_sec": 120,
            "new_commits_total": 1,
            "stop_reason": "no-progress" if i == 5 else None,
            "local_checks": lc,
        })
    jsonl_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    # Write sentinel
    rt_dir = data_home / "projects" / key / "runtime"
    rt_dir.mkdir(parents=True, exist_ok=True)
    (rt_dir / "last-exit.json").write_text(
        json.dumps({"state": "running", "run_id": "20260609-120000"}),
        encoding="utf-8",
    )

    # Snapshot the skills/ tree's mtime before running collect.py
    skills_dir = _REPO_ROOT / "skills"
    pre_mtimes: dict[Path, float] = {}
    for p in skills_dir.rglob("*"):
        if p.is_file():
            try:
                pre_mtimes[p] = p.stat().st_mtime
            except OSError:
                pass

    # Run collect.py
    result = subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"collect.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Verify no files under skills/ were modified
    for p in skills_dir.rglob("*"):
        if p.is_file():
            try:
                post_mtime = p.stat().st_mtime
            except OSError:
                continue
            pre = pre_mtimes.get(p)
            if pre is not None and post_mtime > pre:
                pytest.fail(
                    f"collect.py modified a file under skills/: {p}"
                )

    # Verify postmortem was written under ILK_DATA_HOME
    pm_dir = data_home / "projects" / key / "runtime" / "launcher" / "postmortems"
    assert pm_dir.exists(), "postmortems dir not created under ILK_DATA_HOME"
    pm_files = list(pm_dir.glob("*.md"))
    assert len(pm_files) >= 1, "no postmortem file written"

    # Verify backlog candidates were written under ILK_DATA_HOME
    backlog_dir = data_home / "ilk-skills-improvements"
    if backlog_dir.exists():
        candidates_path = backlog_dir / "candidates.json"
        if candidates_path.exists():
            data = json.loads(candidates_path.read_text(encoding="utf-8"))
            assert isinstance(data, list), "candidates.json should be a JSON array"


def test_backlog_add_candidate_writes_only_under_data_home(tmp_path):
    """improvement_backlog.add_candidate writes only under the backlog dir."""
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-feedback" / "scripts"))
    import improvement_backlog

    backlog_dir = tmp_path / "backlog"

    entry = improvement_backlog.add_candidate(
        title="test gap",
        gap="missing feature X",
        evidence={"project": "test"},
        backlog_dir=backlog_dir,
    )
    assert entry.title == "test gap"
    assert entry.seen_count == 1

    # Verify file exists under backlog_dir
    candidates_path = backlog_dir / "candidates.json"
    assert candidates_path.exists()

    # Verify nothing was written outside backlog_dir
    # (tmp_path has only backlog_dir under it)
    other_dirs = [p for p in tmp_path.iterdir() if p != backlog_dir]
    assert len(other_dirs) == 0, f"unexpected dirs created: {other_dirs}"
