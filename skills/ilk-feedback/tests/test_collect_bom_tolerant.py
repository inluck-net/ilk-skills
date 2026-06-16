"""Red regression test: BOM-prefixed JSONL must classify, not return zero records.

Covers the bug where PowerShell 5.1's -Encoding utf8 writes a BOM
(EF BB BF) and collect.py's utf-8 reader silently drops the record.

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
_SCRATCH_ROOT = _REPO_ROOT / "scratch" / "collect-bom"

# Paths to scripts we invoke via subprocess.
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

# UTF-8 BOM bytes
_BOM = b"\xef\xbb\xbf"

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


def _runtime_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime"


# ── AC-1: BOM'd JSONL yields the record, not zero records ────────────────────


def test_bomd_jsonl_classifies(scratch_env):
    """A .ilk-loop.log whose first line is BOM-prefixed JSON must be parsed.

    Before the fix, read_jsonl_iters returns [] because json.loads raises
    'Unexpected UTF-8 BOM' and the except swallows it. After the fix,
    the record is returned and collect.py classifies the run.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    run_id = "20260616-175453"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "iteration": 1,
        "exit_code": 0,
        "new_commits_total": 2,
        "stop_reason": "already-shipped",
        "duration_sec": 60,
    }
    # Write BOM-prefixed JSONL (mimics PowerShell 5.1 Add-Content -Encoding utf8)
    with jsonl_path.open("wb") as f:
        f.write(_BOM)
        f.write((json.dumps(record) + "\n").encode("utf-8"))

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

    # Postmortem must exist and not be no-evidence.
    pm_dir = _runtime_dir(data_home, key) / "launcher" / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "no-evidence" not in text, (
        f"BOM'd JSONL should classify, not report no-evidence.\nHead:\n{text[:500]}"
    )


# ── AC-2: BOM'd last-exit.json is parsed by read_sentinel ────────────────────


def test_bomd_sentinel_parses(scratch_env):
    """A last-exit.json with a BOM prefix must be parsed by read_sentinel.

    Before the fix, read_sentinel returns None because json.loads raises
    'Unexpected UTF-8 BOM'. After the fix, the sentinel dict is returned.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    rt_dir = _runtime_dir(data_home, key)
    rt_dir.mkdir(parents=True, exist_ok=True)
    sentinel_path = rt_dir / "last-exit.json"

    run_id = "20260616-175453"
    sentinel = {"state": "running", "run_id": run_id, "iters": 1}
    # Write BOM-prefixed JSON (mimics PowerShell 5.1)
    with sentinel_path.open("wb") as f:
        f.write(_BOM)
        f.write(json.dumps(sentinel).encode("utf-8"))

    # Verify the BOM is present (precondition).
    raw = sentinel_path.read_bytes()
    assert raw[:3] == _BOM, "Test setup failed: no BOM prefix"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, r'"
                + str(_REPO_ROOT / "skills" / "ilk-feedback" / "scripts")
                + "'); "
                "from collect import read_sentinel; "
                "from pathlib import Path; "
                "sentinel = read_sentinel(Path(r'" + str(project_path) + "')); "
                "print('None' if sentinel is None else 'OK'); "
                "sys.exit(0 if sentinel is not None else 1)"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"read_sentinel should parse BOM'd sentinel, got exit {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout, "read_sentinel returned None for BOM'd sentinel"


# ── AC-3: clean (no-BOM) JSONL still works (regression guard) ────────────────


def test_clean_jsonl_still_classifies(scratch_env):
    """A .ilk-loop.log WITHOUT a BOM must still classify correctly.

    This is a regression guard: the utf-8-sig encoding strips a BOM if
    present and is a no-op when absent, so existing clean logs must not break.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    run_id = "20260616-175453"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "iteration": 1,
        "exit_code": 0,
        "new_commits_total": 2,
        "stop_reason": "already-shipped",
        "duration_sec": 60,
    }
    # Write clean JSONL (no BOM).
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

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

    pm_dir = _runtime_dir(data_home, key) / "launcher" / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "no-evidence" not in text, (
        f"Clean JSONL should classify.\nHead:\n{text[:500]}"
    )
