"""Prove that the gate isolates the working tree to HEAD before running checks.

Part of [plan:the-gate-runs-on-the-committed-state].  These tests pin the
defect: a gate today passes against uncommitted content because
`run_local_checks.py` runs in the live working tree.  After step 2 wires
`isolate_to_head`, the same test flips green — the gate now measures HEAD.

Hermeticity (§23): every test uses `tmp_path` for the git repo and the
sub-plan file.  The repo-root `conftest.py` host guard is active and will
fail the test if it mutates host state.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Add the scripts dir so we can import isolate_to_head directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_local_checks import isolate_to_head, IsolationState  # noqa: E402


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file."""
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
    marker = repo / "marker.txt"
    marker.write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _make_subplan(tmp_path: Path, repo: Path, slug: str, command: str) -> Path:
    """Write a minimal sub-plan file with a frontmatter local_checks gate.

    Also creates a MASTER file so ``run_local_checks.py``'s plans-dir
    resolver discovers the directory.
    """
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    # MASTER marker — the resolver needs at least one MASTER-*.md to find the dir
    master = plans_dir / "MASTER-test.md"
    if not master.exists():
        master.write_text(
            "---\nmaster_plan: test\nbatch_date: 2026-08-30\nstatus: active\n---\n\n# Test\n",
            encoding="utf-8",
        )
    subplan = plans_dir / f"{slug}.md"
    subplan.write_text(
        f"""\
---
plan: {slug}
status: in-progress
current_step: 0
estimated_steps: 1
last_updated: 2026-08-30
---

# Test sub-plan

## Steps

### Step 0 — test

```yaml
local_checks:
  - command: "{command}"
    timeout: 30
```
""",
        encoding="utf-8",
    )
    return subplan


def _run_gate(repo: Path, slug: str) -> dict:
    """Run run_local_checks.py and return the parsed JSON output."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "run_local_checks.py"
    result = subprocess.run(
        ["python3", str(script), "--project", str(repo), "--slug", slug, "--step", "0"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The script prints JSON to stdout regardless of exit code
    return json.loads(result.stdout)


class TestGateReadsWorkingTree:
    """Pin the defect: today the gate passes against uncommitted content."""

    def test_gate_passes_against_uncommitted_content(self, tmp_path: Path) -> None:
        """The gate reads the uncommitted file and passes.

        This is the defect this batch exists to fix.  After step 2 wires
        `isolate_to_head`, the gate will measure HEAD (where marker.txt
        contains "committed") and FAIL — so the assertion flips:
        `all_passed` becomes False, and `head_sha` / `isolated` appear.
        """
        repo = _make_git_repo(tmp_path)
        marker = repo / "marker.txt"
        # Overwrite uncommitted: the gate will read this, not the committed version
        marker.write_text("dirty", encoding="utf-8")

        slug = "test-gate-reads-working-tree"
        _make_subplan(tmp_path, repo, slug, "grep -q dirty marker.txt")

        output = _run_gate(repo, slug)

        # --- Desired state (after step 2): these will be True ---
        # Today they are False → the test is RED.
        assert output["all_passed"] is False, (
            "Gate should fail against HEAD (committed content), but it passed "
            "against the uncommitted dirty file — the defect."
        )
        # These fields don't exist yet → KeyError → test RED.
        assert "head_sha" in output, "output should carry head_sha after isolation"
        assert "isolated" in output, "output should carry isolated after isolation"

    def test_head_sha_matches_repo(self, tmp_path: Path) -> None:
        """head_sha in the output matches `git rev-parse HEAD`."""
        repo = _make_git_repo(tmp_path)
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

        slug = "test-head-sha"
        _make_subplan(tmp_path, repo, slug, "true")

        output = _run_gate(repo, slug)

        assert output.get("head_sha") == expected_sha

    def test_isolated_when_clean(self, tmp_path: Path) -> None:
        """On a clean tree, isolated=True and dirty_paths=0."""
        repo = _make_git_repo(tmp_path)

        slug = "test-clean-tree"
        _make_subplan(tmp_path, repo, slug, "true")
        # Commit so the tree is actually clean
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True)

        output = _run_gate(repo, slug)

        assert output.get("isolated") is True
        assert output.get("dirty_paths") == 0


class TestIsolateToHead:
    """Direct tests of the isolate_to_head context manager."""

    def test_clean_tree_no_op(self, tmp_path: Path) -> None:
        """AC-3: on a clean tree, isolated=True, dirty_paths=0, no stash."""
        repo = _make_git_repo(tmp_path)
        with isolate_to_head(repo) as iso:
            assert iso.isolated is True
            assert iso.dirty_paths == 0
            assert iso.head_sha is not None
            assert iso.restore_error is None

    def test_non_git_dir(self, tmp_path: Path) -> None:
        """AC-6: in a non-git directory, isolated=False, head_sha=None, no crash."""
        not_git = tmp_path / "not-a-repo"
        not_git.mkdir()
        with isolate_to_head(not_git) as iso:
            assert iso.isolated is False
            assert iso.head_sha is None
            assert iso.restore_error is None

    def test_dirty_tree_restores(self, tmp_path: Path) -> None:
        """AC-2: after a gate run on a dirty tree, git status is restored."""
        repo = _make_git_repo(tmp_path)
        marker = repo / "marker.txt"
        marker.write_text("dirty", encoding="utf-8")
        # Also add an untracked file
        untracked = repo / "untracked.txt"
        untracked.write_text("untracked", encoding="utf-8")

        # Record pre-isolation status
        pre_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout

        with isolate_to_head(repo) as iso:
            assert iso.isolated is True
            assert iso.dirty_paths > 0

        # Post-restore: status should be byte-identical
        post_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert post_status == pre_status, "dirty tree not restored after isolation"

    def test_restore_on_exception(self, tmp_path: Path) -> None:
        """AC-4: when a check raises, the tree is still restored."""
        repo = _make_git_repo(tmp_path)
        marker = repo / "marker.txt"
        marker.write_text("dirty", encoding="utf-8")

        pre_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout

        with pytest.raises(RuntimeError, match="simulated failure"):
            with isolate_to_head(repo) as iso:
                assert iso.isolated is True
                raise RuntimeError("simulated failure")

        # Post-restore: status should be byte-identical
        post_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert post_status == pre_status, "dirty tree not restored after exception"

    def test_stash_message_has_prefix(self, tmp_path: Path) -> None:
        """The stash message carries the ilk-gate-isolation prefix."""
        repo = _make_git_repo(tmp_path)
        marker = repo / "marker.txt"
        marker.write_text("dirty", encoding="utf-8")

        # Force a pop conflict by committing a change during isolation
        with isolate_to_head(repo) as iso:
            assert iso.isolated is True
            # Create a conflicting committed change
            marker.write_text("conflict", encoding="utf-8")
            subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "conflict"],
                cwd=repo, check=True, capture_output=True,
            )

        # The stash entry should still exist (never dropped)
        stash_list = subprocess.run(
            ["git", "stash", "list"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert "ilk-gate-isolation" in stash_list, "stash entry should still exist after pop conflict"

    def test_stash_pop_conflict_sets_restore_error(self, tmp_path: Path) -> None:
        """AC-5: pop conflict populates restore_error, stash entry preserved."""
        repo = _make_git_repo(tmp_path)
        marker = repo / "marker.txt"
        marker.write_text("dirty", encoding="utf-8")

        iso_state = None
        with isolate_to_head(repo) as iso:
            # Create a conflicting committed change
            marker.write_text("conflict", encoding="utf-8")
            subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "conflict"],
                cwd=repo, check=True, capture_output=True,
            )
            iso_state = iso

        assert iso_state.restore_error is not None, "restore_error should be set on pop conflict"
        assert "stash pop failed" in iso_state.restore_error

        # Stash entry must still be on the stack (never dropped)
        stash_list = subprocess.run(
            ["git", "stash", "list"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert "ilk-gate-isolation" in stash_list

    def test_untracked_files_survive(self, tmp_path: Path) -> None:
        """Untracked files survive a full isolate/restore cycle."""
        repo = _make_git_repo(tmp_path)
        # Create some untracked files
        untracked = repo / "untracked.txt"
        untracked.write_text("untracked content", encoding="utf-8")
        subdir = repo / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested", encoding="utf-8")

        pre_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout

        with isolate_to_head(repo) as iso:
            assert iso.isolated is True
            assert iso.dirty_paths >= 2

        # Untracked files must still exist
        assert untracked.exists(), "untracked file disappeared after isolation"
        assert (subdir / "nested.txt").exists(), "nested untracked file disappeared"
        assert untracked.read_text(encoding="utf-8") == "untracked content"

        # Status must be identical
        post_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert post_status == pre_status

    def test_no_destructive_git_commands(self) -> None:
        """Structural guard: source contains no stash-drop / checkout -- / reset --hard / clean -fd."""
        import pathlib
        import re
        src = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_local_checks.py"
        text = src.read_text(encoding="utf-8")

        # Check for actual subprocess.run calls with destructive git commands.
        # Match patterns like: "stash", "drop" in a list literal passed to subprocess.run
        # or direct string mentions in command-building code.
        # Exclude docstrings/comments by checking for subprocess context.
        destructive = [
            r'\[\s*"git"\s*,\s*"stash"\s*,\s*"drop"',
            r'\[\s*"git"\s*,\s*"checkout"\s*,\s*"--"',
            r'\[\s*"git"\s*,\s*"reset"\s*,\s*"--hard"',
            r'\[\s*"git"\s*,\s*"clean"\s*,\s*"-fd"',
            r'\[\s*"git"\s*,\s*"clean"\s*,\s*"-f"\s*,\s*"-d"',
        ]
        for pattern in destructive:
            match = re.search(pattern, text)
            assert match is None, (
                f"source contains destructive git command matching '{pattern}' — "
                "isolate_to_head must never destroy uncommitted work"
            )
