"""Pin that an at-base rerun runs BASE code, not the live skills.

The defect (R1 batch verify, 2026-10-03): ``run_at_base`` spawns its
subprocesses without an explicit ``env=``, so the child inherits the
driver's ``ILK_SKILL_HOME``.  A base test that launches the runner
executes the LIVE skills, not the base worktree's copy.

AC-1 through AC-4 test the env-pinning and cache-marker behaviour.
AC-pin-1 through AC-pin-3 test the one-process at-base rerun contract:
batched execution, correct fallback verdicts, and equivalence with the
per-id path on a four-kind fixture.
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


# ── helpers for one-process at-base pins ─────────────────────────────────────

def _make_four_kind_repo(tmp: Path) -> tuple[Path, str, dict[str, str]]:
    """Create a repo whose base commit has four test kinds.

    Returns ``(repo, base_sha, expected_verdicts)`` where
    ``expected_verdicts`` maps each node id to its expected at-base verdict
    when run against the base commit.

    The four kinds:
    - ``test_pass`` — passes at base → ``"passed"``
    - ``test_fail`` — fails at base → ``"failed"``
    - ``test_absent`` — does not exist at base (added at head) →
      ``"absent-at-base"``
    - ``test_collection_error`` — file exists at base but has a syntax error →
      ``"absent-at-base"`` (pytest exits 4 with "not found:" for a
      syntax-broken file, same as an absent test — the per-id path also
      classifies this as absent-at-base)
    """
    repo = tmp / "four-kind"
    repo.mkdir()
    _git(repo, "init", "-q")
    tests = repo / "tests"
    tests.mkdir()

    # test_pass — a trivial passing test
    (tests / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    # test_fail — a trivially failing test
    (tests / "test_fail.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )

    # test_collection_error — file exists at base but has a syntax error
    (tests / "test_broken.py").write_text(
        "def test_broken(:\n    pass\n", encoding="utf-8"
    )

    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "base: four test kinds")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # head commit — adds test_absent
    (tests / "test_absent.py").write_text(
        "def test_new():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "head: add test_absent")

    expected = {
        "tests/test_pass.py::test_ok": "passed",
        "tests/test_fail.py::test_bad": "failed",
        "tests/test_broken.py::test_broken": "absent-at-base",
        # absent is not in the base commit at all
    }
    return repo, base_sha, expected


# ── AC-pin-1: one pytest process for N ≥ 3 ids ──────────────────────────────

def test_at_base_spawns_one_process_for_clean_ids(tmp_path: Path) -> None:
    """AC-1: run_at_base must run all uncached, non-declared ids in ONE
    pytest process when the batched output is unambiguous (no collection
    errors, no timeouts).  Collection errors trigger per-id fallback for
    all ids (tested separately)."""
    repo = tmp_path / "clean-ids"
    repo.mkdir()
    _git(repo, "init", "-q")
    tests = repo / "tests"
    tests.mkdir()

    # 3 passing tests + 1 failing test at base — all clean, no collection errors.
    for i in range(3):
        (tests / f"test_ok{i}.py").write_text(
            f"def test_ok{i}():\n    assert True\n", encoding="utf-8"
        )
    (tests / "test_fail.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "base: clean ids")
    base_sha = _git(repo, "rev-parse", "HEAD")

    node_ids = [
        "tests/test_ok0.py::test_ok0",
        "tests/test_ok1.py::test_ok1",
        "tests/test_ok2.py::test_ok2",
        "tests/test_fail.py::test_bad",
    ]

    import verification_record as vr
    call_count = 0
    orig_bounded = vr._bounded_run

    def _counting_bounded(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return orig_bounded(*args, **kwargs)

    vr._bounded_run = _counting_bounded
    try:
        run_at_base(
            repo, base_sha, node_ids,
            f"{sys.executable} -m pytest", timeout=60,
        )
    finally:
        vr._bounded_run = orig_bounded

    assert call_count == 1, (
        f"expected 1 pytest process for {len(node_ids)} clean ids, "
        f"got {call_count}"
    )


# ── AC-pin-2: absent and collection-error ids get correct verdict ────────────

def test_absent_and_collection_error_verdicts(tmp_path: Path) -> None:
    """AC-2: an absent-at-base id and a collection-error id must each get
    today's exact verdict via the per-id fallback."""
    repo, base_sha, _ = _make_four_kind_repo(tmp_path)

    node_ids = [
        "tests/test_pass.py::test_ok",
        "tests/test_fail.py::test_bad",
        "tests/test_broken.py::test_broken",
        "tests/test_absent.py::test_new",
    ]

    verdicts = run_at_base(
        repo, base_sha, node_ids,
        f"{sys.executable} -m pytest", timeout=60,
    )

    # test_broken has a syntax error — pytest exits 4 and prints
    # "ERROR: not found:" even though the file exists.  Today's per-id
    # logic also classifies this as "absent-at-base" (the distinction
    # between "file missing" and "file broken" is not visible in pytest's
    # exit code or output).  The batched path must match.
    assert verdicts["tests/test_broken.py::test_broken"] == "absent-at-base", (
        "syntax-broken file at base: pytest exits 4 with 'not found:', "
        "same as an absent test.  "
        f"got: {verdicts['tests/test_broken.py::test_broken']}"
    )

    # test_absent does not exist at the base commit → "absent-at-base"
    assert verdicts["tests/test_absent.py::test_new"] == "absent-at-base", (
        "a test that does not exist at base must be 'absent-at-base'.  "
        f"got: {verdicts['tests/test_absent.py::test_new']}"
    )


# ── AC-pin-3: equivalence — batched == per-id on four kinds ──────────────────

def test_batched_equals_per_id_on_four_kinds(tmp_path: Path) -> None:
    """AC-3: the batched implementation and the per-id implementation must
    return identical verdict dicts on the four-kind fixture.

    This test passes today because both paths use the same per-id loop.
    Once step 1 introduces the batched path, this becomes the regression
    guard proving the batched path produces the same verdicts.
    """
    repo, base_sha, _ = _make_four_kind_repo(tmp_path)

    node_ids = [
        "tests/test_pass.py::test_ok",
        "tests/test_fail.py::test_bad",
        "tests/test_broken.py::test_broken",
        "tests/test_absent.py::test_new",
    ]

    runner = f"{sys.executable} -m pytest"

    # Per-id: run each id separately (today's path).
    per_id_verdicts: dict[str, str] = {}
    for nid in node_ids:
        single = run_at_base(
            repo, base_sha, [nid], runner, timeout=60,
        )
        per_id_verdicts.update(single)

    # Batched: run all ids at once (the new path, once built).
    batched_verdicts = run_at_base(
        repo, base_sha, node_ids, runner, timeout=60,
    )

    assert batched_verdicts == per_id_verdicts, (
        "batched and per-id paths must return identical verdict dicts.\n"
        f"  batched: {batched_verdicts}\n"
        f"  per-id:  {per_id_verdicts}"
    )