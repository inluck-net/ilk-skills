"""Sub-plan ``a-red-names-its-owner`` step 0 — xfail pins.

AC-1 through AC-5 for the red-owner bisect feature.  Each test is
``xfail(strict=True, reason="red-first")`` until step 1 implements
``red_owner.py`` and wires it into the runner.

AC-1: fixture repo with base (green), commit A (sub-plan ``one``, breaks the
      test), commit B (sub-plan ``two``); gate of ``two`` red ⇒
      ``red_owner.py`` names A.  The runner logs the owner ``one``, and
      ``two``'s ``auto_block_fails`` is unchanged.
AC-2: the failing test is red at base ⇒ no bisect, and strikes behave as
      today.
AC-3: the owner is the running sub-plan ⇒ strikes behave as today.
AC-4: the budget cap returns ``first_red: null`` with ``reason: budget``, and
      the runner then behaves as today.
AC-5: bisect never touches the working tree: ``git status --porcelain`` and
      ``git stash list`` are unchanged afterwards.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_RED_OWNER = _SCRIPTS / "red_owner.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Create a fixture repo with three commits:
    - base: test passes
    - A (sub-plan ``one``): test fails
    - B (sub-plan ``two``): test still fails
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # base — green
    (repo / "test_foo.py").write_text("def test_foo(): assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: green")

    # A — sub-plan one, breaks the test
    (repo / "test_foo.py").write_text("def test_foo(): assert False\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): break test [plan:one#step-0]")

    # B — sub-plan two, still broken
    (repo / "bar.py").write_text("# unrelated\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(y): unrelated [plan:two#step-0]")

    return repo


def _run_red_owner(repo: Path, base: str, head: str,
                   gate_cmd: str = "python -m pytest test_foo.py -q",
                   nodes: list[str] | None = None,
                   budget: int = 300) -> dict:
    args = [
        "python3", str(_RED_OWNER),
        "--repo", str(repo),
        "--base", base, "--head", head,
        "--cmd", gate_cmd,
        "--budget-s", str(budget),
    ]
    for n in (nodes or ["test_foo.py::test_foo"]):
        args += ["--node", n]
    r = subprocess.run(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == 0, f"red_owner.py exited {r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


# ── AC-1: bisect names the commit that broke the test ────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_bisect_names_commit_a(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~2")
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_red_owner(repo, base, head)
    first_red = result["first_red"]
    assert first_red is not None, result
    assert result.get("base_green") is True, result
    # first_red should be commit A (the one with [plan:one#step-0])
    subject = _git(repo, "log", "--format=%s", "-1", first_red)
    assert "[plan:one#step-0]" in subject, (
        f"expected first_red to be commit A, got: {subject}"
    )


# ── AC-2: red at base ⇒ no bisect ────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_red_at_base_skips_bisect(tmp_path: Path) -> None:
    """If the test is already red at base, bisect should not run."""
    repo = _make_repo(tmp_path)
    # Overwrite base to be red too
    base_sha = _git(repo, "rev-parse", "HEAD~2")
    (repo / "test_foo.py").write_text("def test_foo(): assert False\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "--amend", "-m", "base: red")
    base_sha = _git(repo, "rev-parse", "HEAD~2")  # re-resolve after amend
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_red_owner(repo, base_sha, head)
    assert result.get("base_green") is False, result
    assert result["first_red"] is None, result


# ── AC-3: owner is the running sub-plan ⇒ strikes behave as today ────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_owner_is_running_subplan(tmp_path: Path) -> None:
    """When the red commit belongs to the running sub-plan (``two``),
    the normal strike logic applies — the owner IS the running sub-plan.
    """
    repo = _make_repo(tmp_path)
    # Make commit B the one that breaks the test
    (repo / "test_foo.py").write_text("def test_foo(): assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix: restore green")
    (repo / "test_foo.py").write_text("def test_foo(): assert False\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(y): re-break [plan:two#step-0]")

    base = _git(repo, "rev-parse", "HEAD~3")
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_red_owner(repo, base, head)
    first_red = result["first_red"]
    assert first_red is not None, result
    subject = _git(repo, "log", "--format=%s", "-1", first_red)
    assert "[plan:two#step-0]" in subject, (
        f"expected owner to be 'two' (the running sub-plan), got: {subject}"
    )


# ── AC-4: budget cap returns first_red: null ─────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_budget_cap_returns_null(tmp_path: Path) -> None:
    """With a budget of 0s, the bisect should give up immediately."""
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~2")
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_red_owner(repo, base, head, budget=0)
    assert result["first_red"] is None, result
    assert result.get("reason") == "budget", result


# ── AC-5: bisect never touches the working tree ──────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac5_bisect_preserves_working_tree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~2")
    head = _git(repo, "rev-parse", "HEAD")

    before_status = _git(repo, "status", "--porcelain")
    before_stash = _git(repo, "stash", "list")

    _run_red_owner(repo, base, head)

    after_status = _git(repo, "status", "--porcelain")
    after_stash = _git(repo, "stash", "list")
    assert before_status == after_status, "working tree was modified"
    assert before_stash == after_stash, "stash was modified"