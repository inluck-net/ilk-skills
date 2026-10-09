"""Pin the host compatibility switch for linked-worktree keys.

ec827743 (v0.9.180) gave each linked worktree its own project key (ilk #44).
On rezmac that would turn kira-cloudflare-resolver from 1 run at a time into
3 before gh-resolve batch 42 makes parallel runs safe, so rezmac was held on
v0.9.179, idle, without every fix since. A host can now keep the v0.9.179
keying while running current ilk:

    <ilk data root>/host-compat.json   {"linked_worktree_key": "clone"}

AC-1: with the file, a linked worktree resolves to the main clone's root and
      key (the v0.9.179 behaviour).
AC-2 (control): without the file, the worktree is its own project (ec827743).
AC-3: any other value, or an unreadable file, is ignored (today's behaviour);
      the switch only turns on when it says exactly "clone".
AC-4: the CLI (`ilk_paths.py --start <worktree>`), which the shell scripts
      and gh-resolve call, reports the clone key with the file present.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    for var, sub in (("HOME", "home"), ("ILK_DATA_HOME", "data"), ("ILK_DATA_DIR", "data")):
        (tmp_path / sub).mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    main = tmp_path / "main"
    main.mkdir()
    for cmd in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=main, check=True)
    wt = tmp_path / "wt" / "resolver-1"
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "worktree", "add",
                    "-q", "--detach", str(wt)], cwd=main, check=True)
    return {"data": data, "main": main.resolve(), "wt": wt.resolve()}


def _compat(world, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (world["data"] / "host-compat.json").write_text(text)


def test_ac1_the_switch_keeps_clone_keys(world):
    import ilk_paths
    _compat(world, {"linked_worktree_key": "clone"})
    assert ilk_paths.git_root(world["wt"]) == world["main"]
    assert ilk_paths.project_key(ilk_paths.git_root(world["wt"])) == \
        ilk_paths.project_key(world["main"])


def test_ac2_without_the_switch_a_worktree_is_its_own_project(world):
    import ilk_paths
    assert ilk_paths.git_root(world["wt"]) == world["wt"]


@pytest.mark.parametrize("payload", [{"linked_worktree_key": "own"},
                                     {"other": "clone"}, "{not json"])
def test_ac3_anything_else_is_ignored(world, payload):
    import ilk_paths
    _compat(world, payload)
    assert ilk_paths.git_root(world["wt"]) == world["wt"]


def test_ac4_the_cli_reports_the_clone_key(world):
    _compat(world, {"linked_worktree_key": "clone"})
    out = subprocess.run([sys.executable, str(SCRIPTS / "ilk_paths.py"),
                          "--start", str(world["wt"])],
                         capture_output=True, text=True, env=os.environ.copy(),
                         timeout=30)
    assert out.returncode == 0, out.stderr
    import ilk_paths
    assert json.loads(out.stdout)["project_key"] == ilk_paths.project_key(world["main"])
