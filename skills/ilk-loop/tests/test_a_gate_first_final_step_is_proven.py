"""A gate-first final step is proven by the gate that ran before its marker.

A ``gate_first`` step's gate runs **before** the step's own commit, so its
``head_sha`` is always an ancestor of the ``[plan:<slug>#step-N]`` marker
and of the ``[plan:<slug>#ship]`` commit after it.  ``check_final_step_gate``
demanded the reverse ancestry, so every batch-verify sub-plan (both steps
gate-first) was reverted as "shipped without a passing final-step gate"
however green its gates were: ilk-skills run 20260929-224906, iteration 4,
gate at 8913109, marker 73a2c04 and ship 7f1b8dd both empty, all three
commits on tree 00d933d.

A gate proves a tree.  When the final-step commit has the same tree as the
gate's head, that gate is a gate on the shipped content.

  AC-1  pass row at A; empty ``#step-1`` marker and empty ``#ship`` after A
        -> proven
  AC-2  pass row at A; a ``#step-1`` commit after A that changes the tree
        -> NOT proven (the tree-equality acceptance is not a bypass)
  AC-3  control: pass row at the ship commit itself -> proven (passes today)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ship_integrity as si  # noqa: E402

SLUG = "the-verify"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", f"chore(loop): gate-first marker [plan:{SLUG}#step-0]")
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _empty(repo: Path, msg: str) -> str:
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _head(repo)


def _proven(repo: Path, gate_head: str) -> bool:
    rows = [{"slug": SLUG, "step": 1, "outcome": "pass", "head_sha": gate_head}]
    last = si.find_last_step_commit(SLUG, 1, repo)
    assert last is not None, "fixture must carry a final-step or #ship trailer"
    return si.check_final_step_gate(rows, SLUG, 1, last, cwd=repo)


def test_ac1_empty_marker_and_ship_after_the_gate_are_proven(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gate_head = _head(repo)
    _empty(repo, f"chore(loop): gate-first marker [plan:{SLUG}#step-1]")
    _empty(repo, f"chore(plans): {SLUG} shipped [plan:{SLUG}#ship]")
    assert _proven(repo, gate_head)


def test_ac2_a_tree_change_after_the_gate_is_not_proven(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gate_head = _head(repo)
    (repo / "work.txt").write_text("changed after the gate\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", f"feat: unproven [plan:{SLUG}#step-1]")
    assert not _proven(repo, gate_head)


def test_ac3_control_gate_at_the_ship_commit_is_proven(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _empty(repo, f"chore(loop): gate-first marker [plan:{SLUG}#step-1]")
    ship = _empty(repo, f"chore(plans): {SLUG} shipped [plan:{SLUG}#ship]")
    assert _proven(repo, ship)
