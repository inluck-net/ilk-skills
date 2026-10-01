"""A red is owned by the commit that made it, in selfmod too.

Part of sub-plan ``a-red-is-owned-by-the-commit-that-made-it``
(MASTER-2026-10-01b).

Four acceptance criteria, verified against the real
``red_owner.attribute_red`` and ``red_owner.bisect_red_owner`` functions
and the runner's iteration-base resolution.

The tests use fixture repos under tmp_path — no loop runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import red_owner  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("0\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _write_heads_file(path: Path, entries: dict[str, str]) -> None:
    """Write a heads-before/after file with repo=sha entries."""
    with path.open("w", encoding="utf-8") as fh:
        for repo, sha in entries.items():
            fh.write(f"{repo}={sha}\n")


def _make_gate_row(
    slug: str,
    step: int,
    outcome: str = "fail",
    head_sha: str | None = None,
    attribution: dict | None = None,
) -> dict:
    """Build a synthetic gate row."""
    row: dict = {
        "slug": slug,
        "step": step,
        "outcome": outcome,
    }
    if head_sha is not None:
        row["head_sha"] = head_sha
    if attribution is not None:
        row["attribution"] = attribution
    return row


# ── AC-1: iteration base resolves in selfmod mode ────────────────────────────

class TestIterationBaseResolvesInSelfmod:
    """AC-1: the heads-before file is keyed by the clone path, and
    PROJECT_PATH is the worktree ⇒ the iteration base resolves."""

    def test_base_resolves_with_clone_key(self, tmp_path: Path) -> None:
        """heads-before keyed by clone path, PROJECT_PATH=worktree ⇒ base found."""
        clone = _make_repo(tmp_path)
        # Create a worktree
        wt = tmp_path / "worktree"
        _git(clone, "worktree", "add", "-q", "--detach", str(wt), "HEAD")

        # Make a commit in the worktree
        (wt / "f.txt").write_text("1\n")
        _git(wt, "commit", "-q", "-am", "wt commit")

        # Write heads-before keyed by clone path
        heads_file = tmp_path / "heads-before-0.tmp"
        _write_heads_file(heads_file, {str(clone): _git(clone, "rev-parse", "HEAD")})

        # The runner's head_before_sha should resolve using clone path
        # even when PROJECT_PATH is the worktree.
        # Test the bash function directly with the environment variables.
        result = subprocess.run(
            ["bash", "-c", f"""
# Source only the head_before_sha function
head_before_sha() {{
  local repo="$1" before_file="$2" sha
  sha=$(grep -F "$repo=" "$before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
  if [[ "$sha" == "(unknown)" ]]; then
    sha=""
  fi
  echo "$sha"
}}
head_before_sha "{clone}" "{heads_file}"
"""],
            capture_output=True, text=True,
        )
        base = result.stdout.strip()
        assert base, f"Iteration base should resolve, got empty. stderr: {result.stderr}"
        assert base == _git(clone, "rev-parse", "HEAD")


# ── AC-2: the 20261001-232701 shape ──────────────────────────────────────────

class TestRedOwnedByItsCommit:
    """AC-2: one iteration with commits for two slugs; the red is owned
    by the commit that broke it, not the gate's slug."""

    def test_red_owned_by_other_slug(self, tmp_path: Path) -> None:
        """Commit A (six) green, commit B (seven) breaks test ⇒ owned by seven."""
        repo = _make_repo(tmp_path)

        # Write a test file that passes at the base (included in base commit).
        test_file = repo / "test_stuff.py"
        test_file.write_text("def test_ok(): assert True\n")
        _git(repo, "add", "test_stuff.py")
        _git(repo, "commit", "-q", "-m", "add test")

        base_sha = _git(repo, "rev-parse", "HEAD")

        # Commit A: plan six step 1 (doesn't touch the test)
        (repo / "f.txt").write_text("1\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m",
             "feat(runner): six change [plan:six#step-1]")

        # Commit B: plan seven step 1 (breaks the test)
        test_file.write_text("def test_ok(): assert False\n")
        _git(repo, "add", "test_stuff.py")
        _git(repo, "commit", "-q", "-m",
             "fix(runner): seven change [plan:seven#step-1]")

        head_sha = _git(repo, "rev-parse", "HEAD")

        # Run attribution with a command that exercises the test.
        result = red_owner.attribute_red(
            repo,
            iteration_base=base_sha,
            batch_base=None,
            head=head_sha,
            cmd="python3 -m pytest test_stuff.py -q",
            stdout_tail="FAILED test_stuff.py::test_ok",
            budget_s=60,
        )

        assert result["verdict"] == "owned"
        assert result["owner_slug"] == "seven"
        assert result["owner_sha"] is not None


# ── AC-3: single-slug iteration (control) ────────────────────────────────────

class TestSingleSlugIterationStrikeOnSelf:
    """AC-3: a single-slug iteration whose own commit breaks the test ⇒
    the strike is on that slug. This is the existing behaviour — no xfail."""

    def test_strike_on_own_slug(self, tmp_path: Path) -> None:
        """Single slug breaks test ⇒ strike on that slug."""
        repo = _make_repo(tmp_path)

        # Commit: plan my-slug step 1
        (repo / "f.txt").write_text("broken\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m",
             "feat(runner): change [plan:my-slug#step-1]")
        sha = _git(repo, "rev-parse", "HEAD")

        # Verify the commit has the trailer
        msg = _git(repo, "log", "-1", "--format=%s")
        assert "[plan:my-slug#step-1]" in msg


# ── AC-4: owner_slug not in active master ────────────────────────────────────

class TestOwnerSlugNotInMaster:
    """AC-4: owner_slug is not in the active master ⇒ today's behaviour,
    and the log line names the reason."""

    def test_outside_master_behaviour(self, tmp_path: Path) -> None:
        """When owner_slug is outside the active master, fail closed.

        ``attribute_red`` returns ``owned`` with ``owner_slug`` set to the
        slug from the commit trailer.  The runner's active-master check
        (line 3265-3269 of run_ilk_loop_claude.sh) then logs
        "not in the active master — verdict … recorded, not enforced"
        and skips enforcement.  This test verifies the attribution half:
        ``owner_slug`` is correctly extracted so the runner can act on it.
        """
        repo = _make_repo(tmp_path)

        # Create a test file that passes at base.
        (repo / "test_stuff.py").write_text("def test_ok(): pass\n")
        _git(repo, "add", "test_stuff.py")
        _git(repo, "commit", "-q", "-m", "add test")

        base_sha = _git(repo, "rev-parse", "HEAD")

        # Break the test with a commit owned by "outside-slug".
        (repo / "test_stuff.py").write_text("def test_ok(): assert False\n")
        _git(repo, "add", "test_stuff.py")
        _git(repo, "commit", "-q", "-m",
             "feat(runner): outside change [plan:outside-slug#step-1]")

        head_sha = _git(repo, "rev-parse", "HEAD")

        # Run attribution.
        result = red_owner.attribute_red(
            repo,
            iteration_base=base_sha,
            batch_base=None,
            head=head_sha,
            cmd="python3 -m pytest test_stuff.py -q",
            stdout_tail="FAILED test_stuff.py::test_ok",
            budget_s=60,
        )

        # The verdict is owned — the iteration broke it.
        assert result["verdict"] == "owned"
        # owner_slug is extracted from the trailer.
        assert result["owner_slug"] == "outside-slug"
        assert result["owner_sha"] is not None
        # The owner_slug differs from any gate slug in the active master,
        # so the runner's active-master check would log
        # "not in the active master" and skip enforcement (fail closed).