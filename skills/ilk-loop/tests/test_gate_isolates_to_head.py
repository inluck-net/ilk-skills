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
from pathlib import Path

import pytest


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

    def test_clean_tree_reports_isolated(self, tmp_path: Path) -> None:
        """On a clean tree, isolated=True and dirty_paths=0."""
        repo = _make_git_repo(tmp_path)

        slug = "test-clean-tree"
        _make_subplan(tmp_path, repo, slug, "true")

        output = _run_gate(repo, slug)

        assert output.get("isolated") is True
        assert output.get("dirty_paths") == 0
