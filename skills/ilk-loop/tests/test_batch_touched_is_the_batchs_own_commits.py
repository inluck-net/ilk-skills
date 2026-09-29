"""``batch touched file`` counts only the batch's own commits.

``batch_touched_files`` diffed ``<base_sha>..HEAD``.  On a shared main that
range holds every batch that landed after the base, so a test file changed
only by ANOTHER batch read ``batch touched: yes`` and its intermittent red was
attributed to this one (verify_attribution.py:206).  gh-resolve 29b, base
7466ab0: tests/test_picker_sizes_the_comment_route.py was changed in range by
exactly one commit, 21114725 (29c's ``a-comment-contract-is-sized-on-its-own``)
and both its nodes were attributed to 29b.

  AC-1  a file changed only by another batch's commit -> not touched
  AC-2  a file changed by a commit carrying this batch's trailer -> touched
  AC-2b a file changed by a trailer-less commit -> touched (never under-
        attribute: only a commit provably another batch's is excluded)
  AC-3  no batch slugs known -> today's range diff (conservative: touched)
  AC-4  slugs known but no commit in range carries one -> range diff too
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verification_record as vr  # noqa: E402

OURS = "tests/test_ours.py::test_a"
THEIRS = "tests/test_theirs.py::test_b"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _commit(repo: Path, path: str, msg: str) -> None:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(msg + "\n", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", msg)


def _repo(tmp_path: Path, *, ours_trailer: bool = True) -> tuple[Path, str]:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "README", "base")
    base = _git(repo, "rev-parse", "HEAD")
    # Interleaved on one main: our batch, another batch, our batch again.
    ours_msg = "fix: ours [plan:our-slug#step-0]" if ours_trailer else "fix: ours"
    _commit(repo, "tests/test_ours.py", ours_msg)
    _commit(repo, "tests/test_theirs.py", "fix: theirs [plan:their-slug#step-0]")
    _commit(repo, "src/x.py", "chore(plans): ours shipped [plan:our-slug#ship]"
            if ours_trailer else "chore: x")
    return repo, base


def test_ac1_another_batchs_file_is_not_touched(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    got = vr.batch_touched_files(repo, base, [THEIRS], batch_slugs={"our-slug"})
    assert got == {THEIRS: False}


def test_ac2_our_trailer_commit_touches_its_file(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    got = vr.batch_touched_files(repo, base, [OURS], batch_slugs={"our-slug"})
    assert got == {OURS: True}


def test_ac2b_a_trailerless_commit_still_counts(tmp_path: Path) -> None:
    # gh-resolve 7a8b8547: a hand fix for the batch with no plan trailer.
    # Dropping it would under-attribute, the direction that lets a ship by.
    repo, base = _repo(tmp_path)
    _commit(repo, "tests/test_hand.py", "test: operator hand fix")
    hand = "tests/test_hand.py::test_c"
    got = vr.batch_touched_files(repo, base, [hand, THEIRS],
                                 batch_slugs={"our-slug"})
    assert got == {hand: True, THEIRS: False}


def test_ac3_no_slugs_keeps_the_range_diff(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    got = vr.batch_touched_files(repo, base, [OURS, THEIRS])
    assert got == {OURS: True, THEIRS: True}


def test_ac4_no_trailer_in_range_keeps_the_range_diff(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, ours_trailer=False)
    got = vr.batch_touched_files(repo, base, [OURS, THEIRS],
                                 batch_slugs={"our-slug"})
    assert got == {OURS: True, THEIRS: True}
