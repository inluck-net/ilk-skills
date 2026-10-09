"""Pin that a release a live runner uses is never pruned.

gh-resolve run 20261010-003018 ran pinned to ~/.ilk/releases/v0.9.184.
Deploying v0.9.189 kept only the 5 newest release dirs (KEEP_RELEASES), so
v0.9.184 was deleted under the live run.  Its next helper call failed with
"can't open file .../ship_integrity.py", which the runner read as a
ship-integrity violation and reverted a legitimately shipped sub-plan.

AC-1: a release named by a live runner's refs (argv or open files) is kept;
      the other old one is pruned.
AC-2: refs that cannot be measured (None) prune nothing.
AC-3 (control): no live runner -> the oldest beyond KEEP_RELEASES go.
AC-4: through the real _live_runner_refs: a runner launched via a `current`
      SYMLINK path (argv never names the release; pinning uses `cd -P`) still
      protects its physical release dir.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ilk_release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("ilk_release_under_test", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _releases(tmp_path: Path, n: int) -> tuple[Path, list[Path]]:
    root = tmp_path / ".ilk" / "releases"
    root.mkdir(parents=True)
    dirs = []
    for i in range(n):
        d = root / f"v0.9.{180 + i}"
        (d / "skills" / "ilk-loop" / "scripts").mkdir(parents=True)
        (d / ".ilk-release.json").write_text(
            json.dumps({"extracted_at": f"2026-10-10T00:{i:02d}:00Z", "sha": str(i)}))
        dirs.append(d)
    os.symlink(str(dirs[-1]), root.parent / "current")
    os.symlink(str(dirs[-2]), root.parent / "previous")
    return root, dirs


def test_a_release_a_live_runner_names_is_kept(tmp_path: Path) -> None:
    m = _mod()
    root, dirs = _releases(tmp_path, m.KEEP_RELEASES + 2)
    live = str(dirs[0]) + "/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
    m._prune(root, live_refs=lambda: f"n{live}\n")
    assert dirs[0].is_dir(), "the live runner's release was pruned"
    assert not dirs[1].exists(), "the unused old release should still be pruned"


def test_unmeasured_refs_prune_nothing(tmp_path: Path) -> None:
    m = _mod()
    root, dirs = _releases(tmp_path, m.KEEP_RELEASES + 2)
    m._prune(root, live_refs=lambda: None)
    assert all(d.is_dir() for d in dirs)


def test_control_no_live_runner_prunes_the_oldest(tmp_path: Path) -> None:
    m = _mod()
    root, dirs = _releases(tmp_path, m.KEEP_RELEASES + 2)
    m._prune(root, live_refs=lambda: "")
    assert not dirs[0].exists() and not dirs[1].exists()
    assert all(d.is_dir() for d in dirs[2:])


def test_a_runner_launched_via_a_symlink_is_seen(tmp_path: Path) -> None:
    m = _mod()
    root, dirs = _releases(tmp_path, m.KEEP_RELEASES + 2)
    script = dirs[0] / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    script.write_text("#!/usr/bin/env bash\nsleep 30\n")
    script.chmod(0o755)
    link = tmp_path / "current-link"
    os.symlink(str(dirs[0]), link)
    via = link / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    proc = subprocess.Popen(["bash", str(via)])
    try:
        time.sleep(0.5)
        refs = m._live_runner_refs()
        assert refs is not None, "lsof/pgrep could not measure a live runner"
        m._prune(root)
    finally:
        proc.kill()
        proc.wait()
    assert dirs[0].is_dir(), (
        "a runner launched through a symlink lost its release: argv names the "
        "link, so the physical dir must come from its open files")
    assert not dirs[1].exists()
