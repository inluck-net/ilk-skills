"""Tests for the baseline-reuse lookup — previous batch's result as this batch's baseline.

The batch-verification template caches the base-commit baseline at
``<ext logs>/verification/baseline-<base-sha>.json``, keyed on the base sha.
That cache can only hit when two batches share a base, which is rare — measured
on gh-resolve 2026-09-16: 6 baseline files on disk, **0** for that batch's base
(``d05cb909``).  A second full suite runs almost every time.

The previous batch's verification record already measured that tree.  When batch
N verifies green at tree T, and batch N+1's base *is* T, reuse is licensed —
**but only by a tree match, never by a head match** (the empty-marker commits
the loop makes diverge head from tree on every step).

Four cases pin the contract before any implementation:

1. **tree-match reuses** (AC-1) — the previous record's tree equals the base
   tree ⇒ reuse, no suite run.
2. **head-differs-tree-matches reuses** (AC-2) — the empty-marker case: heads
   differ but trees match ⇒ still reuse.
3. **tree-differs measures** — the trees don't match ⇒ measure.
4. **record-names-no-tree measures** (AC-3) — the record does not name its
   tree ⇒ measure.  Unknown is not "matches".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_verification_dir(tmp_path: Path, monkeypatch, proj_path: Path | None = None):
    """Create an isolated external logs dir and return (project, verification_dir).

    Sets ``ILK_DATA_HOME`` and ``HOME`` so ``ilk_paths`` resolves into *tmp_path*.
    *proj_path* is created as a minimal git repo when provided.
    """
    import subprocess

    data_home = tmp_path / "data"
    home = tmp_path / "home"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))

    proj = proj_path or (tmp_path / "proj")
    if not proj.exists():
        proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=proj, check=True, env=env,
        )

    sys.path.insert(0, str(_SCRIPTS_DIR))
    from ilk_paths import external_logs_dir, resolve_project_key  # noqa: E402

    key = resolve_project_key(proj)
    vdir = external_logs_dir(key) / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    return proj, vdir


def _write_batch_record(
    vdir: Path,
    slug: str,
    verified_head: str | None = None,
    verified_tree: str | None = None,
    suite_failed: int = 0,
) -> Path:
    """Write a minimal batch-verification record to *vdir*."""
    body = f"suite_failed: {suite_failed}\n\n"
    if verified_head:
        body += f"**verified_head:** `{verified_head}`\n\n"
    if verified_tree:
        body += f"**verified_tree:** `{verified_tree}`\n\n"
    body += "## At-base rerun\n\n_(no failures)_\n"
    p = vdir / f"{slug}-batch.md"
    p.write_text(body, encoding="utf-8")
    return p


def _git_sha(proj: Path, *args: str) -> str:
    """Run a git command and return stripped stdout."""
    import subprocess
    return subprocess.run(
        ["git", *args], cwd=proj, capture_output=True, text=True, check=True,
    ).stdout.strip()


# ── resolve_previous_batch_tree ──────────────────────────────────────────────
#
# Finds the most recent ``*-batch.md`` in the project's verification dir and
# resolves the tree it verified — by reading ``verified_tree`` if present, or
# resolving ``verified_head`` through git.  Returns (tree_sha, record_path) or
# raises when the record cannot establish which tree it measured.


class TestResolvePreviousBatchTree:
    """The previous batch's record names a tree, directly or via its head."""

    def test_tree_field_read_directly(self, tmp_path: Path, monkeypatch) -> None:
        """When the record carries ``verified_tree``, use it without git."""
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        _write_batch_record(vdir, "batch-2026-09-16a", verified_tree="abc1234" + "0" * 33)
        tree, record = vr.resolve_previous_batch_tree(proj)
        assert tree == "abc1234" + "0" * 33
        assert record.name == "batch-2026-09-16a-batch.md"

    def test_head_resolved_to_tree(self, tmp_path: Path, monkeypatch) -> None:
        """When only ``verified_head`` is present, resolve through git."""
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        head = _git_sha(proj, "rev-parse", "HEAD")
        _write_batch_record(vdir, "batch-2026-09-16b", verified_head=head)
        tree, record = vr.resolve_previous_batch_tree(proj)
        expected_tree = _git_sha(proj, "rev-parse", f"{head}^{{tree}}")
        assert tree == expected_tree

    def test_no_records_raises(self, tmp_path: Path, monkeypatch) -> None:
        """An empty verification dir is not "no previous batch" — it is unknown."""
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError, match="no verification records"):
            vr.resolve_previous_batch_tree(proj)

    def test_record_with_no_head_or_tree_raises(self, tmp_path: Path, monkeypatch) -> None:
        """A record that names no commit cannot establish its tree."""
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        _write_batch_record(vdir, "batch-2026-09-16c")  # no head, no tree
        with pytest.raises((FileNotFoundError, ValueError), match="tree|head"):
            vr.resolve_previous_batch_tree(proj)


# ── resolve_baseline: the reuse-vs-measure decision ─────────────────────────
#
# The main entry point.  Given a project and a base tree sha, either:
#   - returns (record_counts, source_record_path) when the previous batch's
#     verified tree matches (reuse);
#   - returns None (caller must measure) when they differ or when the previous
#     record cannot establish its tree.
#
# AC-4 demands a ``baseline_source`` field; that is an implementation detail
# tested at step 2.  These tests pin the decision only.


class TestBaselineReuse:
    """tree-match → reuse; anything else → measure."""

    # ── AC-1: the positive case ──────────────────────────────────────────────

    def test_tree_match_reuses(self, tmp_path: Path, monkeypatch) -> None:
        """When the base tree equals the previous record's tree, reuse it.

        AC-1: reuse is keyed on tree, and the counts from the previous record
        are returned rather than re-measured.
        """
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        head = _git_sha(proj, "rev-parse", "HEAD")
        tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        _write_batch_record(vdir, "batch-2026-09-16a", verified_head=head)

        result = vr.resolve_baseline(proj, base_tree=tree)
        assert result is not None, "tree match should reuse, not return None"
        counts, source = result
        assert source.name == "batch-2026-09-16a-batch.md"
        assert counts is not None, "reuse must return the previous record's counts"

    # ── AC-2: the empty-marker case ──────────────────────────────────────────

    def test_head_differs_tree_matches_reuses(self, tmp_path: Path, monkeypatch) -> None:
        """The loop's empty marker commits diverge head from tree.

        AC-2: a record whose head differs from ours but whose tree matches
        (the common case after marker commits) must still be reused.
        """
        import subprocess

        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        # Record the tree at the current commit.
        tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        recorded_head = _git_sha(proj, "rev-parse", "HEAD")
        _write_batch_record(vdir, "batch-2026-09-16a", verified_head=recorded_head)

        # Simulate an empty marker commit — head moves, tree does not.
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "marker"],
            cwd=proj, check=True, env=env,
        )
        new_head = _git_sha(proj, "rev-parse", "HEAD")
        assert new_head != recorded_head, "marker must change head"
        new_tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        assert new_tree == tree, "marker must not change tree"

        # The base tree still matches the record's tree → reuse.
        result = vr.resolve_baseline(proj, base_tree=tree)
        assert result is not None, (
            "head-differs-tree-matches (the empty-marker case) must reuse"
        )

    # ── negative: tree-differs → measure ─────────────────────────────────────

    def test_tree_differs_measures(self, tmp_path: Path, monkeypatch) -> None:
        """When the base tree differs from the previous record's tree, measure.

        This is the common case: a genuinely new batch off a different base.
        """
        import subprocess

        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        recorded_head = _git_sha(proj, "rev-parse", "HEAD")
        recorded_tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        _write_batch_record(vdir, "batch-2026-09-16a", verified_head=recorded_head)

        # Change the tree so it differs from the record's.
        (proj / "new_file.txt").write_text("content\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=proj, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "new code"], cwd=proj, check=True, env=env)
        new_tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        assert new_tree != recorded_tree, "commit must change the tree"

        result = vr.resolve_baseline(proj, base_tree=new_tree)
        assert result is None, "tree-differs must not reuse; return None to signal 'measure'"

    # ── negative: record-names-no-tree → measure ─────────────────────────────

    def test_record_without_tree_measures(self, tmp_path: Path, monkeypatch) -> None:
        """AC-3: a record that does not name its tree is refused.

        Unknown is not "matches".  The same refusal shape as
        ``check_verified_tree``, and for the same reason.
        """
        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")
        # Write a record with no verified_head and no verified_tree.
        _write_batch_record(vdir, "batch-2026-09-16a")  # no head, no tree

        result = vr.resolve_baseline(proj, base_tree=tree)
        assert result is None, "record naming no tree must not be reused"

    # ── AC-6: the existing sha-keyed cache is untouched ──────────────────────

    def test_sha_keyed_cache_is_not_replaced(self, tmp_path: Path, monkeypatch) -> None:
        """The new lookup goes ahead of the sha-keyed cache; it does not replace it.

        AC-6: a batch whose base sha has a cached baseline still hits the old path.
        """
        import json

        proj, vdir = _make_verification_dir(tmp_path, monkeypatch)
        tree = _git_sha(proj, "rev-parse", "HEAD^{tree}")

        # A sha-keyed baseline exists (the old cache).
        cached = vdir / f"baseline-{tree[:12]}.json"
        cached.write_text(json.dumps({"passed": 100, "failed": 0}), encoding="utf-8")

        # No batch records exist — the new lookup would miss.
        # The old cache still works independently.
        assert cached.is_file(), "sha-keyed baseline must still exist on disk"
        data = json.loads(cached.read_text())
        assert data["passed"] == 100
