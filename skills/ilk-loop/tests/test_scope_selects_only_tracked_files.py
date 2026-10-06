"""Scoped selection picks only git-tracked test files.

MEASURED 2026-10-06 on ilk-skills, batch-2026-10-06-project-registry-data-home:
a Claude Code worktree at ``.claude/worktrees/registry-reconcile`` (excluded
via ``.git/info/exclude``) held older copies of the test files.
``compute_suite_scope`` found test files with ``project.rglob``, which walks
untracked and excluded directories, so the selection grew from 28 files to 53.
Under ``-n 8 --dist loadfile`` the nested copy sometimes loaded first and its
older ``status_all`` module broke the real tests: 33 failures were attributed
to a batch whose clean measurement is 292 of 292 passed, and the verify
sub-plan quarantined itself.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402

_ENV = {"PATH": "/usr/bin:/bin",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, env=_ENV,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout.strip()


def _repo_with_nested_copy(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "alpha.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "tests" / "test_alpha.py").write_text(
        "def test_a(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_uses_alpha.py").write_text(
        "from alpha import A\n\ndef test_b(): pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "src" / "alpha.py").write_text("A = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change alpha")

    # The measured shape: a nested tree hidden from git by info/exclude,
    # carrying copies of the same test files.
    nested = repo / ".claude" / "worktrees" / "wt" / "tests"
    nested.mkdir(parents=True)
    (nested / "test_alpha.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (nested / "test_alpha_extra.py").write_text(
        "def test_c(): pass\n", encoding="utf-8")
    (nested / "test_other_uses_alpha.py").write_text(
        "import alpha\n\ndef test_d(): pass\n", encoding="utf-8")
    with open(repo / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
        fh.write("**/.claude/worktrees/\n")
    assert _git(repo, "status", "--porcelain") == ""
    return repo, base


def test_an_excluded_nested_copy_is_never_selected(tmp_path: Path) -> None:
    repo, base = _repo_with_nested_copy(tmp_path)
    s = vr.compute_suite_scope(repo, base)
    assert s["mode"] == "scoped", s
    assert s["selection"] == ["tests/test_alpha.py"], s


def test_an_untracked_importer_is_never_selected(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    # beta has no conventionally named test; only an importer covers it.
    (repo / "src" / "beta.py").write_text("B = 1\n", encoding="utf-8")
    (repo / "tests" / "test_uses_beta.py").write_text(
        "from beta import B\n\ndef test_b(): pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "beta.py").write_text("B = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change beta")

    nested = repo / ".claude" / "worktrees" / "wt" / "tests"
    nested.mkdir(parents=True)
    (nested / "test_old_beta_importer.py").write_text(
        "import beta\n\ndef test_d(): pass\n", encoding="utf-8")
    with open(repo / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
        fh.write("**/.claude/worktrees/\n")
    assert _git(repo, "status", "--porcelain") == ""

    s = vr.compute_suite_scope(repo, base)
    assert s["mode"] == "scoped", s
    assert s["selection"] == ["tests/test_uses_beta.py"], s
