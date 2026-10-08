"""A diff on the release path widens the verify to the full suite.

Backlog 93d82cbae7b970e3.  Since v0.9.174 the release train refuses a verify
not recorded as ``full`` when the release range touches the release path
(the safety-kernel list plus scheduler.sh).  The verify's scope selector uses
the same definition, so a batch that changes those files runs its one suite
in full rather than producing a scoped verdict the train can never release.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo_changing(tmp_path: Path, changed: str) -> tuple[Path, str]:
    """A repo with a module and its test at base; HEAD changes *changed*."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    for rel, body in (("skills/x/scripts/m.py", "def m(): return 1\n"),
                      ("skills/x/tests/test_m.py", "from m import m\n"),
                      (changed, "# base\n")):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / changed).write_text("# changed\n")
    (repo / "skills/x/scripts/m.py").write_text("def m(): return 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo, base


@pytest.mark.parametrize("changed", [
    "skills/ilk-watchdog/scripts/scheduler.sh",
    "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
    "skills/ilk-ship/scripts/release_train.py",
])
def test_release_path_diff_is_full(tmp_path, changed):
    repo, base = _repo_changing(tmp_path, changed)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "full", scope
    assert scope["reason"].startswith("diff touches the release path:")
    assert changed in scope["reason"]


def test_control_off_release_path_stays_scoped(tmp_path):
    repo, base = _repo_changing(tmp_path, "skills/ilk-watchdog/scripts/other.sh")
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "scoped", scope
    assert scope["selection"] == ["skills/x/tests/test_m.py"]


def test_the_train_uses_the_same_definition():
    ship = Path(__file__).resolve().parents[2] / "ilk-ship" / "scripts"
    if str(ship) not in sys.path:
        sys.path.insert(0, str(ship))
    release_train = importlib.import_module("release_train")
    assert not hasattr(release_train, "_RELEASE_PATH_EXTRA")
    assert vr.release_path_hits([
        "skills/ilk-watchdog/scripts/scheduler.sh",
        "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
        "docs/notes.md",
    ]) == [
        "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
        "skills/ilk-watchdog/scripts/scheduler.sh",
    ]
