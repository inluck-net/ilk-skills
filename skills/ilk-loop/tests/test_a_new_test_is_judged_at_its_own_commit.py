"""Tests for "a new test is judged at its own commit".

AC-1 through AC-5 verify the adding-commit rerun behaviour.
AC-6 is a control (passes today).

Fixture: a tmp_path git repo with four commits:
  1. base
  2. [plan:other#step-0] adds tests/test_x.py with one failing + one passing
  3. [plan:alpha#step-0] (this batch) adds tests/test_y.py with a failing test
  4. untrailered commit breaks the passing test in test_x.py
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402
import verify_attribution as va    # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30)
    return (r.stdout or "").strip()


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "test")
    return repo


_TEST_X_PASSING = textwrap.dedent("""\
    def test_x_passes():
        assert True

    def test_x_fails():
        assert False
""")

_TEST_Y_FAILING = textwrap.dedent("""\
    def test_y_fails():
        assert False
""")

# A version of test_x.py where the passing test is broken.
_TEST_X_BROKEN = textwrap.dedent("""\
    def test_x_passes():
        assert False

    def test_x_fails():
        assert False
""")


def _make_fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Build the fixture repo.  Returns (repo, base_sha, other_commit_sha)."""
    repo = _init_repo(tmp_path)

    # Commit 1: base (empty)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").strip()

    # Commit 2: [plan:other#step-0] adds tests/test_x.py
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_x.py").write_text(_TEST_X_PASSING)
    _git(repo, "add", "tests/test_x.py")
    _git(repo, "commit", "-q", "-m",
         "feat(x): add test_x [plan:other#step-0]")
    other_sha = _git(repo, "rev-parse", "HEAD").strip()

    # Commit 3: [plan:alpha#step-0] adds tests/test_y.py (this batch)
    (tests_dir / "test_y.py").write_text(_TEST_Y_FAILING)
    _git(repo, "add", "tests/test_y.py")
    _git(repo, "commit", "-q", "-m",
         "feat(y): add test_y [plan:alpha#step-0]")

    # Commit 4: untrailered — breaks the passing test in test_x.py
    (tests_dir / "test_x.py").write_text(_TEST_X_BROKEN)
    _git(repo, "add", "tests/test_x.py")
    _git(repo, "commit", "-q", "-m", "fix(x): break test_x_passes")

    return repo, base_sha, other_sha


# ── AC-1: this-batch slug ⇒ absent-at-base, attributed, no rerun ─────────────

def test_this_batch_slug_no_rerun(tmp_path: Path) -> None:
    """An id whose test file was added by a commit with THIS batch's slug
    is ``absent-at-base`` and attributed — no adding-commit rerun."""
    repo, base_sha, _ = _make_fixture_repo(tmp_path)
    registry_slugs = {"alpha"}
    # test_y.py was added by [plan:alpha#step-0] — this batch's slug.
    # The adding-commit rerun should NOT fire; verdict stays absent-at-base.
    result = vr.run_at_base(
        repo, base_sha,
        node_ids=["tests/test_y.py::test_y_fails"],
        invocation="python3 -m pytest",
        registry_slugs=registry_slugs,
    )
    # No rerun: verdict is still absent-at-base.
    assert result["tests/test_y.py::test_y_fails"] == "absent-at-base"


# ── AC-2: other-plan slug, failed at adding commit ⇒ born-red-at ─────────────

def test_other_plan_failed_at_adding_commit(tmp_path: Path) -> None:
    """An id whose test file was added by another plan's commit, and that
    test already failed at the adding commit, gets ``born-red-at:<sha>``."""
    repo, base_sha, other_sha = _make_fixture_repo(tmp_path)
    registry_slugs = {"alpha"}
    # test_x.py was added by [plan:other#step-0].
    # test_x_fails already fails at that commit.
    result = vr.run_at_base(
        repo, base_sha,
        node_ids=["tests/test_x.py::test_x_fails"],
        invocation="python3 -m pytest",
        registry_slugs=registry_slugs,
    )
    verdict = result["tests/test_x.py::test_x_fails"]
    assert verdict.startswith("born-red-at:"), (
        f"expected born-red-at:<sha>, got {verdict}"
    )
    # The sha should be the other-plan commit.
    assert other_sha.startswith(verdict.split(":")[1]), (
        f"expected other-plan sha {other_sha[:12]}, got {verdict}"
    )


# ── AC-3: other-plan slug, passed at adding commit ⇒ absent-at-base ──────────

def test_other_plan_passed_at_adding_commit(tmp_path: Path) -> None:
    """An id whose test file was added by another plan's commit, and that
    test passed at the adding commit, stays ``absent-at-base`` (attributed)."""
    repo, base_sha, _ = _make_fixture_repo(tmp_path)
    registry_slugs = {"alpha"}
    # test_x_passes passes at the adding commit (other#step-0).
    # At HEAD it is broken by the untrailered commit, so it is in failing_nodes.
    result = vr.run_at_base(
        repo, base_sha,
        node_ids=["tests/test_x.py::test_x_passes"],
        invocation="python3 -m pytest",
        registry_slugs=registry_slugs,
    )
    # Passed at adding commit → something after broke it → attributed.
    assert result["tests/test_x.py::test_x_passes"] == "absent-at-base"


# ── AC-4: batching — one worktree per adding commit, not per id ──────────────

def test_batching_one_worktree_per_adding_commit(tmp_path: Path) -> None:
    """Multiple ids from the same adding commit share one worktree.

    We verify this by checking that the result contains both ids — if a
    per-id worktree were created, the second would fail to create because
    the first already exists at the same path.  (The actual batching
    implementation will confirm via call-count or path reuse; this pin
    asserts the contract that both ids get verdicts.)
    """
    repo, base_sha, _ = _make_fixture_repo(tmp_path)
    registry_slugs = {"alpha"}
    # Both test_x ids were added by the same commit (other#step-0).
    result = vr.run_at_base(
        repo, base_sha,
        node_ids=[
            "tests/test_x.py::test_x_passes",
            "tests/test_x.py::test_x_fails",
        ],
        invocation="python3 -m pytest",
        registry_slugs=registry_slugs,
    )
    assert len(result) == 2, (
        f"expected 2 verdicts (batched), got {len(result)}: {result}"
    )


# ── AC-5: derive_attributed treats born-red-at:* as not attributed ────────────

def test_derive_attributed_born_red_not_attributed() -> None:
    """``verify_attribution.derive_attributed`` treats ``born-red-at:*`` as
    not attributed (the test was already red when it was born)."""
    rows = [
        ["tests/test_x.py::test_x_fails", "born-red-at:abc1234", "no",
         "3/3", "no"],
    ]
    bad, flaky = va.derive_attributed(rows)
    assert bad == [], (
        f"born-red-at must not be attributed; got bad={bad}"
    )
    assert flaky == [], (
        f"born-red-at must not be flaky; got flaky={flaky}"
    )


# ── AC-6: control — other unknown at-base values are still attributed ─────────

def test_other_unknown_at_base_values_attributed() -> None:
    """Absent-at-base and passed are still attributed (control — passes today)."""
    rows = [
        ["tests/test_a.py::test_a", "absent-at-base", "no", "3/3", "yes"],
        ["tests/test_b.py::test_b", "passed", "no", "3/3", "yes"],
    ]
    bad, flaky = va.derive_attributed(rows)
    assert len(bad) == 2, (
        f"absent-at-base and passed must be attributed; got {len(bad)} bad"
    )

# ── An UNTRAILERED adding commit inside the batch is the batch's own ─────────

def test_untrailered_adding_commit_in_the_batch_stays_attributed(tmp_path: Path) -> None:
    """A test file first created by an untrailered commit inside base..HEAD
    (the runner's `WIP: preserve timed-out iteration changes`) is this
    batch's work: ``absent-at-base`` (attributed), never ``born-red-at``.

    gh-resolve G4 (2026-10-03): a red-first pin written in an iteration that
    timed out was WIP-committed untrailered (b15797e7); slug None never
    matched the registry, the rerun found it red at that commit, and the
    verify EXCUSED the batch's own red as born-red.
    """
    repo = _init_repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_w.py").write_text(_TEST_Y_FAILING)
    _git(repo, "add", "tests/test_w.py")
    _git(repo, "commit", "-q", "-m", "WIP: preserve timed-out iteration changes")
    result = vr.run_at_base(
        repo, base_sha,
        node_ids=["tests/test_w.py::test_y_fails"],
        invocation="python3 -m pytest",
        registry_slugs={"alpha"},
    )
    assert result["tests/test_w.py::test_y_fails"] == "absent-at-base"
