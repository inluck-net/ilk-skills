"""Red-first pins for prune_tmp_keys.py — AC-1 through AC-4.

Each test constructs a temporary ILK_DATA_HOME with a mix of tmp-derived
and real project keys, then runs prune_tmp_keys.py as a subprocess.

All tests are xfail(strict=True) until the script is written (step 1).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO / "skills" / "ilk-loop" / "scripts" / "prune_tmp_keys.py"


def _tmp_slug_prefix() -> str:
    """Derive the tmp-derived key prefix slug (without hash suffix).

    Mirrors conftest.py:724-730 — a child tmp path's key is
    <parent-slug>-pytest-of-<user>-p-<hash>, so the parent's full key
    (which ends in its own hash) never matches.  We strip the 8-char
    hash suffix to get the prefix that all tmp-derived keys share.
    """
    # Import project_key from ilk_paths so the construction is authoritative.
    sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))
    from ilk_paths import project_key
    full_key = project_key(Path(os.path.realpath(tempfile.gettempdir())))
    return full_key[: len(full_key) - 8]


def _make_key_dir(data_home: Path, key: str, *, n_files: int = 0) -> None:
    """Create a project key directory under data_home/projects/."""
    d = data_home / "projects" / key
    if n_files > 0:
        runtime = d / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "batch-gate.json").write_text("{}", encoding="utf-8")
    else:
        d.mkdir(parents=True)


_XFAIL = pytest.mark.xfail(
    strict=True, reason="red-first: script not written"
)


@_XFAIL
def test_dry_run_lists_only_tmp_derived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-1: dry-run lists exactly tmp-derived keys, changes nothing."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    prefix = _tmp_slug_prefix()

    # Two tmp-derived keys (one empty, one with a file).
    _make_key_dir(data_home, f"{prefix}abcdef1", n_files=0)
    _make_key_dir(data_home, f"{prefix}abcdef2", n_files=1)

    # One real-looking key (must never be a candidate).
    _make_key_dir(
        data_home,
        "users-chad-projects-keyreply-kira-cloudflare-scratch-worktrees-resolver-abc-1234567",
        n_files=1,
    )
    # One real selfmod key (must never be a candidate).
    _make_key_dir(
        data_home,
        "users-chad-ilk-data-projects-users-chad-projects-github-inluck-net-ilk-s-901208d",
        n_files=1,
    )

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env={**os.environ, "ILK_DATA_HOME": str(data_home), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    candidate_keys = [c["key"] for c in out["candidates"]]
    assert len(candidate_keys) == 2
    assert all(k.startswith(prefix) for k in candidate_keys)
    assert not any("keyreply" in k for k in candidate_keys)
    assert not any("ilk-s-" in k for k in candidate_keys)

    # Nothing moved.
    assert data_home / "projects" / f"{prefix}abcdef1"
    assert data_home / "projects" / f"{prefix}abcdef2"


@_XFAIL
def test_apply_moves_only_tmp_derived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-2: --apply moves exactly tmp-derived keys into trash/, nothing deleted."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    prefix = _tmp_slug_prefix()

    _make_key_dir(data_home, f"{prefix}abcdef1", n_files=0)
    _make_key_dir(data_home, f"{prefix}abcdef2", n_files=1)
    _make_key_dir(
        data_home,
        "users-chad-projects-keyreply-kira-cloudflare-scratch-worktrees-resolver-abc-1234567",
        n_files=1,
    )
    _make_key_dir(
        data_home,
        "users-chad-ilk-data-projects-users-chad-projects-github-inluck-net-ilk-s-901208d",
        n_files=1,
    )

    # Count files before.
    all_before = sum(1 for _ in data_home.rglob("*") if _.is_file())

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--apply", "--json"],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env={**os.environ, "ILK_DATA_HOME": str(data_home), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert len(out["moved"]) == 2

    # Original tmp-derived keys are gone from projects/.
    assert not (data_home / "projects" / f"{prefix}abcdef1").exists()
    assert not (data_home / "projects" / f"{prefix}abcdef2").exists()

    # Real keys remain.
    assert (data_home / "projects" / "users-chad-projects-keyreply-kira-cloudflare-scratch-worktrees-resolver-abc-1234567").exists()
    assert (data_home / "projects" / "users-chad-ilk-data-projects-users-chad-projects-github-inluck-net-ilk-s-901208d").exists()

    # Nothing deleted: file count is conserved across projects/ + trash/.
    all_after = sum(1 for _ in data_home.rglob("*") if _.is_file())
    assert all_before == all_after


@_XFAIL
def test_live_pid_refuses_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3: a tmp-derived key with a live pid is refused."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    prefix = _tmp_slug_prefix()
    key = f"{prefix}abcdef1"
    _make_key_dir(data_home, key, n_files=1)

    # Write our own pid as the running.pid.
    pid_file = data_home / "projects" / key / "runtime" / "launcher" / "running.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env={**os.environ, "ILK_DATA_HOME": str(data_home), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert len(out["candidates"]) == 0
    refused_keys = [r["key"] for r in out["refused"]]
    assert key in refused_keys


@_XFAIL
def test_registered_key_refuses_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4: a tmp-derived key registered in projects.json is refused."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    prefix = _tmp_slug_prefix()
    key = f"{prefix}abcdef1"
    _make_key_dir(data_home, key, n_files=1)

    # Write a projects.json registering this key.
    projects_json = data_home / "launcher" / "projects.json"
    projects_json.parent.mkdir(parents=True)
    projects_json.write_text(
        json.dumps([{"name": "test-proj", "path": str(tmp_path / "repo")}]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env={**os.environ, "ILK_DATA_HOME": str(data_home), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert len(out["candidates"]) == 0
    refused_keys = [r["key"] for r in out["refused"]]
    assert key in refused_keys