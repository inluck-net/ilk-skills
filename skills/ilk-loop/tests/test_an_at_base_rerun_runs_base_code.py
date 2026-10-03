"""Pin that an at-base rerun runs BASE code, not the live skills.

The defect (R1 batch verify, 2026-10-03): ``run_at_base`` spawns its
subprocesses without an explicit ``env=``, so the child inherits the
driver's ``ILK_SKILL_HOME``.  A base test that launches the runner
executes the LIVE skills, not the base worktree's copy.

AC-1 and AC-2 are ``xfail(strict=True)`` — they red-first on this repo
because the fix is not yet applied.  AC-3 is red-first because the cache
has no env marker so old entries are (incorrectly) reused.  AC-4 is the
control: a cache WITH the marker is reused (existing behaviour).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from verification_record import run_at_adding_commit, run_at_base  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _make_two_commit_repo(tmp: Path, base_label: str, head_label: str) -> Path:
    """Create a repo with two commits whose probe script prints *X_label*.

    The probe lives at ``skills/probe.sh``.  A test at
    ``tests/test_probe.py`` imports ``ILK_SKILL_HOME`` and asserts its
    output equals the label.

    Returns the repo path.
    """
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    # ── base commit ────────────────────────────────────────────────────────
    skills = repo / "skills"
    skills.mkdir()
    (skills / "probe.sh").write_text(
        f"#!/bin/sh\necho {base_label}\n", encoding="utf-8"
    )
    (skills / "probe.sh").chmod(0o755)

    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_probe.py").write_text(textwrap.dedent(f"""\
        import os, subprocess, sys
        def test_probe():
            skill_home = os.environ.get("ILK_SKILL_HOME", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "skills"))
            r = subprocess.run([str(__import__("pathlib").Path(skill_home) / "probe.sh")],
                               capture_output=True, text=True, timeout=10)
            assert r.stdout.strip() == "{base_label}", (
                f"expected '{{r.stdout.strip()}}' == '{base_label}'"
            )
    """), encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", f"base: probe says {base_label}")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # ── head commit ────────────────────────────────────────────────────────
    (skills / "probe.sh").write_text(
        f"#!/bin/sh\necho {head_label}\n", encoding="utf-8"
    )
    (skills / "probe.sh").chmod(0o755)
    (tests / "test_probe.py").write_text(textwrap.dedent(f"""\
        import os, subprocess, sys
        def test_probe():
            skill_home = os.environ.get("ILK_SKILL_HOME", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "skills"))
            r = subprocess.run([str(__import__("pathlib").Path(skill_home) / "probe.sh")],
                               capture_output=True, text=True, timeout=10)
            assert r.stdout.strip() == "{head_label}", (
                f"expected '{{r.stdout.strip()}}' == '{head_label}'"
            )
    """), encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", f"head: probe says {head_label}")
    return repo


# ── AC-1: run_at_base uses base skills, not the parent's ILK_SKILL_HOME ────

@pytest.mark.xfail(strict=True, reason="red-first: run_at_base inherits ILK_SKILL_HOME before fix")
def test_run_at_base_uses_base_skills_not_parent_env(tmp_path: Path) -> None:
    repo = _make_two_commit_repo(tmp_path, base_label="base", head_label="head")
    base_sha = _git(repo, "rev-parse", "HEAD~1")

    # Point the PARENT at a dir whose probe says "head".
    head_skills = tmp_path / "head-skills"
    head_skills.mkdir()
    (head_skills / "probe.sh").write_text("#!/bin/sh\necho head\n", encoding="utf-8")
    (head_skills / "probe.sh").chmod(0o755)

    old = os.environ.get("ILK_SKILL_HOME")
    os.environ["ILK_SKILL_HOME"] = str(head_skills)
    try:
        verdicts = run_at_base(
            repo, base_sha,
            ["tests/test_probe.py::test_probe"],
            f"{sys.executable} -m pytest",
            timeout=60,
        )
    finally:
        if old is None:
            os.environ.pop("ILK_SKILL_HOME", None)
        else:
            os.environ["ILK_SKILL_HOME"] = old

    assert verdicts["tests/test_probe.py::test_probe"] == "passed", (
        "at-base rerun must run with ILK_SKILL_HOME pinned to the base worktree; "
        "the probe says 'base' at base but the parent env points at 'head'.  "
        f"got: {verdicts}"
    )


# ── AC-2: run_at_adding_commit uses the adding-commit's skills ─────────────

@pytest.mark.xfail(strict=True, reason="red-first: run_at_adding_commit inherits ILK_SKILL_HOME before fix")
def test_run_at_adding_commit_uses_adding_commit_skills(tmp_path: Path) -> None:
    """Same as AC-1 but for the adding-commit rerun.

    Build a 3-commit repo: base (probe="base"), adding (probe="adding"),
    head (probe="head").  The test at the adding commit asserts "adding".
    Call ``run_at_adding_commit`` with the parent's ``ILK_SKILL_HOME``
    pointing at "head".  It must report ``absent-at-base`` (meaning the
    test passed at the adding commit).
    """
    repo = tmp_path / "repo3"
    repo.mkdir()
    _git(repo, "init", "-q")
    skills = repo / "skills"
    skills.mkdir()
    tests = repo / "tests"
    tests.mkdir()

    # Commit 1: base — no test file, probe says "base"
    (skills / "probe.sh").write_text("#!/bin/sh\necho base\n", encoding="utf-8")
    (skills / "probe.sh").chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # Commit 2: adding — adds test, probe says "adding", trailer with other slug
    (skills / "probe.sh").write_text("#!/bin/sh\necho adding\n", encoding="utf-8")
    (skills / "probe.sh").chmod(0o755)
    (tests / "test_probe.py").write_text(textwrap.dedent("""\
        import os, subprocess
        def test_probe():
            skill_home = os.environ.get("ILK_SKILL_HOME", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "skills"))
            r = subprocess.run([str(__import__("pathlib").Path(skill_home) / "probe.sh")],
                               capture_output=True, text=True, timeout=10)
            assert r.stdout.strip() == "adding"
    """), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "add test [plan:other-slug#step-0]")
    adding_sha = _git(repo, "rev-parse", "HEAD")

    # Commit 3: head — probe says "head"
    (skills / "probe.sh").write_text("#!/bin/sh\necho head\n", encoding="utf-8")
    (skills / "probe.sh").chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "head")

    # Parent ILK_SKILL_HOME points at "head" skills.
    head_skills = tmp_path / "head-skills3"
    head_skills.mkdir()
    (head_skills / "probe.sh").write_text("#!/bin/sh\necho head\n", encoding="utf-8")
    (head_skills / "probe.sh").chmod(0o755)

    old = os.environ.get("ILK_SKILL_HOME")
    os.environ["ILK_SKILL_HOME"] = str(head_skills)
    try:
        verdicts, _ = run_at_adding_commit(
            repo, base_sha,
            ["tests/test_probe.py::test_probe"],
            f"{sys.executable} -m pytest",
            {"an-at-base-rerun-runs-base-code"},
            timeout=60,
        )
    finally:
        if old is None:
            os.environ.pop("ILK_SKILL_HOME", None)
        else:
            os.environ["ILK_SKILL_HOME"] = old

    assert verdicts["tests/test_probe.py::test_probe"] == "absent-at-base", (
        "adding-commit rerun must run with ILK_SKILL_HOME pinned to the "
        "adding-commit worktree; the probe says 'adding' at the adding commit "
        "but the parent env points at 'head'.  "
        f"got: {verdicts}"
    )


# ── AC-3: cache without env marker is not reused ────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: cache without env marker is reused before fix")
def test_cache_without_env_marker_is_not_reused(tmp_path: Path) -> None:
    repo = _make_two_commit_repo(tmp_path, base_label="base", head_label="head")
    base_sha = _git(repo, "rev-parse", "HEAD~1")

    # Write an at-base cache with a "failed" verdict and NO env marker.
    vdir = tmp_path / "verification"
    vdir.mkdir()
    cache = vdir / f"at-base-{base_sha}.json"
    cache.write_text(json.dumps({
        "invocation": f"{sys.executable} -m pytest",
        "verdicts": {"tests/test_probe.py::test_probe": "failed"},
    }), encoding="utf-8")

    # Monkey-patch _resolve_project_verification_dir to return our vdir.
    import verification_record as vr
    orig = vr._resolve_project_verification_dir
    vr._resolve_project_verification_dir = lambda _p: vdir
    try:
        verdicts = run_at_base(
            repo, base_sha,
            ["tests/test_probe.py::test_probe"],
            f"{sys.executable} -m pytest",
            timeout=60,
        )
    finally:
        vr._resolve_project_verification_dir = orig

    # Without the fix the cache is reused ("failed").  With the fix, the
    # cache is ignored and the test is re-measured (should pass at base).
    assert verdicts["tests/test_probe.py::test_probe"] == "passed", (
        "cache without env marker must be ignored and the id re-measured.  "
        f"got: {verdicts}"
    )


# ── AC-4 (control): cache WITH env marker is reused ─────────────────────────

def test_cache_with_env_marker_is_reused(tmp_path: Path) -> None:
    """Control: a cache with the env marker is reused, no subprocess."""
    repo = _make_two_commit_repo(tmp_path, base_label="base", head_label="head")
    base_sha = _git(repo, "rev-parse", "HEAD~1")

    vdir = tmp_path / "verification"
    vdir.mkdir()
    cache = vdir / f"at-base-{base_sha}.json"
    cache.write_text(json.dumps({
        "invocation": f"{sys.executable} -m pytest",
        "env": "base-pinned-v1",
        "verdicts": {"tests/test_probe.py::test_probe": "failed"},
    }), encoding="utf-8")

    import verification_record as vr
    orig = vr._resolve_project_verification_dir
    vr._resolve_project_verification_dir = lambda _p: vdir
    try:
        verdicts = run_at_base(
            repo, base_sha,
            ["tests/test_probe.py::test_probe"],
            f"{sys.executable} -m pytest",
            timeout=60,
        )
    finally:
        vr._resolve_project_verification_dir = orig

    # Cache hit: no rerun, verdict is "failed" from the cache.
    assert verdicts["tests/test_probe.py::test_probe"] == "failed", (
        "cache with env marker must be reused without a rerun.  "
        f"got: {verdicts}"
    )