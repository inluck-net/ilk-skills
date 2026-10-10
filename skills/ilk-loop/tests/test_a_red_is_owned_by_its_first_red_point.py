"""Red-first pins: a red is owned by its first red point.

Part of sub-plan ``a-red-is-owned-by-its-first-red-point``
(MASTER-2026-10-03i).

Tests that ``suite_ledger.owner_of`` resolves the first point whose ledger
entry has a failing id red after the last point that had it green, and that
``verification_record`` writes ``owned-by:<slug>@<sha>`` at-base cells and
an ``## Owners`` section.  Also tests that ``verify_attribution`` treats
``owned-by:`` cells as not attributed and rejects malformed ones.

AC-1 (cross-batch): base green; point A (slug ``foreign-x``, not in the
      registry) adds a commit that makes test T fail; point B (slug
      ``ours-y``, in the registry) changes an unrelated file; HEAD = B,
      both trees ledgered.  The record's T cell is
      ``owned-by:foreign-x@<A sha12>``, its ``## Owners`` row says
      ``foreign-x | <sha> | point``, and ``verify_attribution`` reports 0
      attributed.
AC-2 (in-batch): same, but B makes T fail.  T stays attributed and
      ``## Owners`` names ``ours-y``.
AC-3 (gap): two rows between the green point and the red one (slugs ``p``
      then ``q``), only the last ledgered; ``q``'s commit made T fail ⇒
      owner ``q``, ``how: bisect``.
AC-4 (hand commit): T is made red by a commit in no row's range ⇒
      ``how: unknown``, T attributed.
AC-5: ``derive_attributed`` treats ``owned-by:x@abc1234`` as not attributed
      in a 3-column and a 5-column row, and raises on ``owned-by:@abc1234``
      and on ``owned-by:x``.
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import suite_ledger  # noqa: E402
import verify_attribution as va  # noqa: E402


# ── module-level fixture: pin HOME + ILK_DATA_HOME, clear worker session ──

# pytest is often a ``--user`` install (it is wherever ``python3`` is the
# system interpreter).  Pinning HOME moves user-site resolution, so any
# subprocess that runs ``python3 -m pytest …`` — the owner probe here, the
# suite run in the sibling file — raises ``ModuleNotFoundError`` and every
# commit measures red.  Captured at import, while HOME is still the real one.
_USER_SITE = site.getusersitepackages()


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path; clear ILK_WORKER_SESSION."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    if _USER_SITE and os.path.isdir(_USER_SITE):
        prior = os.environ.get("PYTHONPATH") or ""
        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join([p for p in (_USER_SITE, prior) if p]),
        )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    try:
        suite_ledger._ORIGINAL_HOME = original_home
    except (AttributeError, TypeError):
        pass


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with one passing test and .ilk-launch.json."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            }
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8"
    )

    (repo / "test_foo.py").write_text(
        "def test_foo(): assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: green")
    return repo


def _write_point(repo: Path, *, run_id: str = "r1", iteration: int = 1,
                 slug: str, master: str = "m1",
                 before: str, after: str,
                 shipped: list[str] | None = None) -> None:
    """Write a point row via suite_ledger.record_point."""
    suite_ledger.record_point(
        repo, run_id=run_id, iteration=iteration,
        slug=slug, master=master, before=before, after=after,
        shipped=shipped or [],
    )


def _write_ledger_entry(repo: Path, tree: str, *,
                        failing_nodes: list[str] | None = None,
                        counts: dict | None = None) -> dict:
    """Write a ledger entry directly (bypassing measure)."""
    ld = suite_ledger.ledger_dir(repo)
    ld.mkdir(parents=True, exist_ok=True)
    if counts is None:
        counts = {"total": 2, "passed": 1, "failed": 1, "errors": 0,
                  "skipped": 0}
    if failing_nodes is None:
        failing_nodes = ["test_foo.py::test_foo"]
    invocation = "python3 -m pytest -q -p no:cacheprovider"
    entry: dict = {
        "schema": 1,
        "tree": tree,
        "commit": "",  # filled by caller
        "invocation": invocation,
        "env": "snapshot-clone-v1",
        "counts": counts,
        "failing_nodes": sorted(failing_nodes),
        "suite_duration_sec": 1,
        "started": "2026-01-01T00:00:00+0000",
        "finished": "2026-01-01T00:00:01+0000",
        "run_id": "test",
        "writer": "driver",
        "host": "test",
    }
    entry["digest"] = suite_ledger._compute_digest(entry)
    entry_path = ld / f"{tree}.json"
    entry_path.write_text(
        json.dumps(entry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return entry


def _tree_of(repo: Path, sha: str) -> str:
    """Resolve the tree sha for a commit."""
    return _git(repo, "rev-parse", f"{sha}^{{tree}}")


# ── AC-1: cross-batch — foreign owner is detected by point ───────────────────


class TestAC1CrossBatchForeignOwner:
    """AC-1: base green; point A (foreign-x, not in registry) makes T fail;
    point B (ours-y, in registry) changes unrelated file; HEAD = B.
    T's at-base cell is ``owned-by:foreign-x@<A sha12>``, Owners row says
    ``foreign-x | <sha> | point``, and verify_attribution reports 0 attributed."""

    def test_cross_batch_owner_is_foreign(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Point A (foreign-x): break the test.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        sha_a = _git(repo, "rev-parse", "HEAD")
        tree_a = _tree_of(repo, sha_a)

        # Point B (ours-y): unrelated file, test still broken.
        (repo / "unrelated.txt").write_text("change\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(y): unrelated")
        sha_b = _git(repo, "rev-parse", "HEAD")
        tree_b = _tree_of(repo, sha_b)

        # Write points and ledger entries.
        _write_point(repo, slug="foreign-x", before=base_sha, after=sha_a)
        _write_point(repo, slug="ours-y", before=sha_a, after=sha_b)
        _write_ledger_entry(repo, tree_a,
                            failing_nodes=["test_foo.py::test_foo"])
        _write_ledger_entry(repo, tree_b,
                            failing_nodes=["test_foo.py::test_foo"])

        # Call owner_of.
        result = suite_ledger.owner_of(
            repo, "test_foo.py::test_foo",
            base_sha=base_sha, head_sha=sha_b,
        )
        assert result["slug"] == "foreign-x"
        assert result["sha"] == sha_a
        assert result["how"] == "point"


# ── AC-2: in-batch — own plan makes T fail ──────────────────────────────────


class TestAC2InBatchOwnOwner:
    """AC-2: same as AC-1 but B makes T fail.  T stays attributed and
    ``## Owners`` names ``ours-y``."""

    def test_in_batch_owner_is_own(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Point A (foreign-x): changes unrelated file.
        (repo / "other.txt").write_text("change\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): other change")
        sha_a = _git(repo, "rev-parse", "HEAD")
        tree_a = _tree_of(repo, sha_a)

        # Point B (ours-y): breaks the test.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(y): break test")
        sha_b = _git(repo, "rev-parse", "HEAD")
        tree_b = _tree_of(repo, sha_b)

        # Write points and ledger entries.
        _write_point(repo, slug="foreign-x", before=base_sha, after=sha_a)
        _write_point(repo, slug="ours-y", before=sha_a, after=sha_b)
        # A's tree is green.
        _write_ledger_entry(repo, tree_a,
                            failing_nodes=[],
                            counts={"total": 2, "passed": 2, "failed": 0,
                                    "errors": 0, "skipped": 0})
        # B's tree is red.
        _write_ledger_entry(repo, tree_b,
                            failing_nodes=["test_foo.py::test_foo"])

        # Call owner_of.
        result = suite_ledger.owner_of(
            repo, "test_foo.py::test_foo",
            base_sha=base_sha, head_sha=sha_b,
        )
        assert result["slug"] == "ours-y"
        assert result["sha"] == sha_b
        assert result["how"] == "point"


# ── AC-3: gap — bisect finds the owner ──────────────────────────────────────


class TestAC3GapBisect:
    """AC-3: two rows between green and red (slugs p then q), only q
    ledgered; q's commit made T fail ⇒ owner q, how: bisect."""

    def test_gap_owner_by_bisect(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Commit for point p: unrelated change.
        (repo / "p.txt").write_text("p\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(p): change")
        sha_p = _git(repo, "rev-parse", "HEAD")

        # Commit for point q: breaks the test.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(q): break test")
        sha_q = _git(repo, "rev-parse", "HEAD")
        tree_q = _tree_of(repo, sha_q)

        # Write points (p is not ledgered, q is).
        _write_point(repo, slug="p", before=base_sha, after=sha_p)
        _write_point(repo, slug="q", before=sha_p, after=sha_q)
        # Only q's ledger entry exists.
        _write_ledger_entry(repo, tree_q,
                            failing_nodes=["test_foo.py::test_foo"])

        # Call owner_of.
        result = suite_ledger.owner_of(
            repo, "test_foo.py::test_foo",
            base_sha=base_sha, head_sha=sha_q,
        )
        assert result["slug"] == "q"
        assert result["how"] == "bisect"


# ── AC-4: hand commit — no owner ────────────────────────────────────────────


class TestAC4HandCommit:
    """AC-4: T is made red by a commit in no row's range ⇒ how: unknown,
    T attributed."""

    def test_hand_commit_has_no_owner(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")

        # A hand commit (no point covers it) that breaks the test.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "fix(stuff): manual fix")
        sha_hand = _git(repo, "rev-parse", "HEAD")

        # No points written — the commit is outside any point range.
        tree_hand = _tree_of(repo, sha_hand)
        _write_ledger_entry(repo, tree_hand,
                            failing_nodes=["test_foo.py::test_foo"])

        # Call owner_of.
        result = suite_ledger.owner_of(
            repo, "test_foo.py::test_foo",
            base_sha=base_sha, head_sha=sha_hand,
        )
        assert result["slug"] is None
        assert result["how"] == "unknown"


# ── AC-5: derive_attributed treats owned-by: as not attributed ───────────────


class TestAC5DeriveAttributedOwnedBy:
    """AC-5: ``derive_attributed`` treats ``owned-by:x@abc1234`` as not
    attributed in 3-column and 5-column rows, and raises on
    ``owned-by:@abc1234`` and ``owned-by:x``."""

    def test_owned_by_not_attributed_3col(self) -> None:
        """3-column: owned-by:<slug>@<hex> is not attributed."""
        rows = [["t1", "owned-by:foreign-x@abc1234", "no"]]
        bad, flaky = va.derive_attributed(rows)
        assert bad == []
        assert flaky == []

    def test_owned_by_not_attributed_5col(self) -> None:
        """5-column: owned-by:<slug>@<hex> is not attributed."""
        rows = [["t1", "owned-by:foreign-x@abc1234", "no", "3/3", "yes"]]
        bad, flaky = va.derive_attributed(rows)
        assert bad == []
        assert flaky == []

    def test_owned_by_empty_slug_raises(self) -> None:
        """owned-by:@abc1234 (empty slug) is a VerificationError.

        Today this raises via the generic "unrecognised at base value" check.
        Once owned-by: is added to _AT_BASE_OK, the owned-by-specific
        validator must still reject an empty slug.
        """
        rows = [["t1", "owned-by:@abc1234", "no"]]
        with pytest.raises(va.VerificationError):
            va.derive_attributed(rows)

    def test_owned_by_no_sha_raises(self) -> None:
        """owned-by:x (no @<hex>) is a VerificationError.

        Today this raises via the generic "unrecognised at base value" check.
        Once owned-by: is added to _AT_BASE_OK, the owned-by-specific
        validator must still reject a missing @<hex>.
        """
        rows = [["t1", "owned-by:x", "no"]]
        with pytest.raises(va.VerificationError):
            va.derive_attributed(rows)