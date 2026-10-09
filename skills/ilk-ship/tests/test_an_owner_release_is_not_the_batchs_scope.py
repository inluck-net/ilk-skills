"""Tests: a tagged owner release mid-batch is excluded from scope audit.

Sub-plan: an-owner-release-is-not-the-batchs-scope (step 0).
Covers AC-1..AC-5: tagged owner exclusion, untagged still fails, out-of-scope
batch commit still fails, shared file still fails, control (commits after last
trailered commit ignored).

Each test builds a throwaway git repo under ``tmp_path`` and calls
``batch_audit._check_scope`` directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _commit(repo: Path, rel: str, msg: str) -> None:
    """Create or overwrite ``repo/<rel>`` and commit."""
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {msg}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", msg],
        cwd=repo, check=True, capture_output=True,
    )


def _head(repo: Path) -> str:
    """Return the current HEAD short SHA."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _check_scope(repo: Path, base_sha: str, sub_plans: list[dict]) -> dict:
    """Call ``_check_scope`` directly and return the result dict."""
    from batch_audit import _check_scope

    return _check_scope(repo, base_sha, sub_plans)


# ── AC-1: tagged owner commit inside the range → pass ──────────────────────


class TestAC1TaggedOwnerExcluded:
    """AC-1: a tagged owner release mid-batch is excluded from scope."""

    def test_tagged_owner_release_excluded(self, tmp_path: Path) -> None:
        """Tagged owner commit to CHANGELOG.md excluded; ok is True with
        '1 tagged commit(s) excluded' in detail."""
        repo = _make_git_repo(tmp_path)
        base = _head(repo)

        # Batch commit in scope
        _commit(repo, "src/a.py", "feat: work [plan:s#step-0]")
        # Owner commit — will be tagged
        _commit(repo, "CHANGELOG.md", "docs(changelog): v9.9.1")
        subprocess.run(
            ["git", "tag", "v9.9.1"],
            cwd=repo, check=True, capture_output=True,
        )
        # Final batch commit (ship marker)
        _commit(repo, "src/a.py", "chore: ship [plan:s#ship]")

        result = _check_scope(
            repo, base,
            [{"slug": "s", "scope_paths": ["src/a.py"]}],
        )
        assert result["ok"] is True, result
        assert "1 tagged commit(s) excluded" in result["detail"], result


# ── AC-2: untagged owner commit inside the range → fail ────────────────────


class TestAC2UntaggedOwnerStillFails:
    """AC-2: an untagged owner commit is still judged and fails scope."""

    def test_untagged_owner_commit_fails(self, tmp_path: Path) -> None:
        """Untagged owner commit to CHANGELOG.md → ok is False, names CHANGELOG.md."""
        repo = _make_git_repo(tmp_path)
        base = _head(repo)

        _commit(repo, "src/a.py", "feat: work [plan:s#step-0]")
        _commit(repo, "CHANGELOG.md", "docs(changelog): v9.9.2")  # no tag
        _commit(repo, "src/a.py", "chore: ship [plan:s#ship]")

        result = _check_scope(
            repo, base,
            [{"slug": "s", "scope_paths": ["src/a.py"]}],
        )
        assert result["ok"] is False, result
        assert "CHANGELOG.md" in result["detail"], result


# ── AC-3: batch commit out of scope → fail ─────────────────────────────────


class TestAC3BatchCommitOutOfScope:
    """AC-3: a trailered batch commit touching a file outside scope still fails."""

    def test_out_of_scope_batch_commit_fails(self, tmp_path: Path) -> None:
        """Trailered batch commit to src/b.py → ok is False, names src/b.py."""
        repo = _make_git_repo(tmp_path)
        base = _head(repo)

        _commit(repo, "src/b.py", "feat: wrong file [plan:s#step-0]")

        result = _check_scope(
            repo, base,
            [{"slug": "s", "scope_paths": ["src/a.py"]}],
        )
        assert result["ok"] is False, result
        assert "src/b.py" in result["detail"], result


# ── AC-4: shared file (tagged owner + batch) → fail ────────────────────────


class TestAC4SharedFileStillFails:
    """AC-4: a file touched by both a tagged owner commit and a batch commit
    is still judged because it appears in the kept commit's diff-tree."""

    def test_shared_file_fails(self, tmp_path: Path) -> None:
        """Tagged owner and batch commit both touch src/b.py → ok is False."""
        repo = _make_git_repo(tmp_path)
        base = _head(repo)

        _commit(repo, "src/b.py", "feat: batch [plan:s#step-0]")
        _commit(repo, "src/b.py", "docs: owner release")
        subprocess.run(
            ["git", "tag", "v9.9.1"],
            cwd=repo, check=True, capture_output=True,
        )
        _commit(repo, "src/a.py", "chore: ship [plan:s#ship]")

        result = _check_scope(
            repo, base,
            [{"slug": "s", "scope_paths": ["src/a.py"]}],
        )
        assert result["ok"] is False, result
        assert "src/b.py" in result["detail"], result


# ── AC-5: control — commits after last trailered commit are ignored ─────────


class TestAC5ControlCommitsAfterBatchIgnored:
    """AC-5: commits after the last trailered commit (the batch boundary)
    are not judged, matching existing behaviour."""

    def test_post_batch_commits_ignored(self, tmp_path: Path) -> None:
        """Commits after the last trailered commit → ok is True."""
        repo = _make_git_repo(tmp_path)
        base = _head(repo)

        _commit(repo, "src/a.py", "feat: work [plan:s#step-0]")
        _commit(repo, "src/a.py", "chore: ship [plan:s#ship]")
        # Post-batch commits (tagged or not) — not in batch range
        _commit(repo, "CHANGELOG.md", "docs(changelog): v9.9.9")
        subprocess.run(
            ["git", "tag", "v9.9.9"],
            cwd=repo, check=True, capture_output=True,
        )
        _commit(repo, "README.md", "fix: post-batch fix")

        result = _check_scope(
            repo, base,
            [{"slug": "s", "scope_paths": ["src/a.py"]}],
        )
        assert result["ok"] is True, result