"""A linked worktree is its own ilk project (ilk #44).

32795f80 (2026-09-21) walked every linked worktree back to its main clone, so
gh-resolve's resolver worktrees shared one key, one run.lock and one runner.
The contract in references/worktree-concurrency.md is one key per worktree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ilk_paths import (  # noqa: E402
    PIN_MARKER,
    find_project_root,
    project_key,
    resolve_project_key,
)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var, sub in (("HOME", "home"), ("ILK_DATA_HOME", "data"), ("ILK_DATA_DIR", "data")):
        (tmp_path / sub).mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    main = tmp_path / "main"
    main.mkdir()
    for cmd in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=main, check=True)
    return main


def _worktree(main: Path, path: Path) -> Path:
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "worktree", "add",
                    "-q", "--detach", str(path)], cwd=main, check=True)
    return path


def test_two_worktrees_get_two_keys_and_neither_is_the_clones(repo: Path, tmp_path: Path) -> None:
    a = _worktree(repo, tmp_path / "wt-a")
    b = _worktree(repo, tmp_path / "wt-b")
    keys = {resolve_project_key(a), resolve_project_key(b)}
    assert len(keys) == 2, keys
    assert project_key(repo.resolve()) not in keys


def test_a_worktree_subdir_resolves_to_the_worktree(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path / "wt")
    (wt / "pkg").mkdir()
    assert find_project_root(wt / "pkg") == (wt.resolve(), "single")


def test_the_clone_and_a_pinned_worktree_are_unchanged(repo: Path, tmp_path: Path) -> None:
    (repo / "sub").mkdir()
    assert find_project_root(repo / "sub")[0] == repo.resolve()
    wt = _worktree(repo, tmp_path / "wt-pinned")
    (wt / PIN_MARKER).touch()
    assert find_project_root(wt)[0] == wt.resolve()
