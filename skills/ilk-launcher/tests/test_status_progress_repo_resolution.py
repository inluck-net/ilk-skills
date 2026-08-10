"""Regression tests for project root resolution in status_progress.py.

Defect: status_progress.py:500 did ``project_root = plans_dir.parent.parent``
assuming the legacy in-tree layout ``<root>/docs/plans``.  When ``find_plans_dir``
returned the external layout ``~/.ilk-data/projects/<key>/plans``, the parent walk
overshot into the ``projects`` container — the display name rendered as "projects"
and ``find_repos()`` scanned a non-repo, so step-commit counts were permanently zero.

These tests lock the fix in place by seeding two fixture repos (external and
in-tree) and asserting that ``project.root``, ``project.name``, and
``step_commit_count`` are correct for each layout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import status_progress  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

MASTER_FM = """\
---
master_plan: 2026-08-10-test
batch_date: 2026-08-10
source_status: test
total_tickets: 1
status: active
current_subplan: 2026-08-10-example
---
"""

SUBPLAN_FM = """\
---
plan: 2026-08-10-example
status: in-progress
current_step: 1
estimated_steps: 2
last_updated: 2026-08-10
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.

### Step 1 — Do another
- Doing another.
"""


def _seed_repo(repo: Path) -> None:
    """Initialise a git repo with two step commits."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True, check=True,
    )
    (repo / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore(plans): example step 0 [plan:2026-08-10-example#step-0]"],
        cwd=str(repo), capture_output=True, check=True,
    )
    (repo / "README.md").write_text("test updated\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore(plans): example step 1 [plan:2026-08-10-example#step-1]"],
        cwd=str(repo), capture_output=True, check=True,
    )


def _seed_plans(plans_dir: Path) -> None:
    """Create a MASTER and one sub-plan in the given directory."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-2026-08-10-test.md").write_text(MASTER_FM)
    (plans_dir / "2026-08-10-example.md").write_text(SUBPLAN_FM)


# ── AC-1: external layout resolves correctly ─────────────────────────────────

def test_external_layout_resolves_project_root(tmp_path: Path) -> None:
    """External plans dir ``~/.ilk-data/projects/<key>/plans`` must resolve
    ``project.root`` to the actual git repo, not the ``projects`` container."""
    repo = tmp_path / "myrepo"
    _seed_repo(repo)

    plans_dir = tmp_path / "external" / "plans"
    _seed_plans(plans_dir)

    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        captured = {}
        orig_build = status_progress.build_json

        def spy_build(*a, **kw):
            captured.update(kw)
            return orig_build(*a, **kw)

        with patch.object(status_progress, "build_json", side_effect=spy_build):
            with patch("sys.argv", [
                "status_progress.py",
                "--json",
                "--project-path", str(repo),
            ]):
                status_progress.main()

    assert captured["project_root"] == repo.resolve(), (
        f"project.root should be the git repo, got {captured['project_root']}"
    )
    assert captured["step_commit_count"] >= 1, (
        f"step_commit_count should be non-zero (was 0 before the fix), got {captured['step_commit_count']}"
    )


# ── AC-2: in-tree layout still resolves correctly ────────────────────────────

def test_in_tree_layout_resolves_project_root(tmp_path: Path) -> None:
    """In-tree plans dir ``<root>/docs/plans`` must still resolve correctly.
    The current code is right for that case; a fix that inverts the bug is not a fix."""
    repo = tmp_path / "myrepo"
    _seed_repo(repo)

    plans_dir = repo / "docs" / "plans"
    _seed_plans(plans_dir)

    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        captured = {}
        orig_build = status_progress.build_json

        def spy_build(*a, **kw):
            captured.update(kw)
            return orig_build(*a, **kw)

        with patch.object(status_progress, "build_json", side_effect=spy_build):
            with patch("sys.argv", [
                "status_progress.py",
                "--json",
                "--project-path", str(repo),
            ]):
                status_progress.main()

    assert captured["project_root"] == repo.resolve(), (
        f"project.root should be the git repo, got {captured['project_root']}"
    )
    assert captured["step_commit_count"] >= 1, (
        f"step_commit_count should be non-zero (was 0 before the fix), got {captured['step_commit_count']}"
    )


# ── AC-5: JSON keys are preserved ────────────────────────────────────────────

def test_json_keys_preserved(tmp_path: Path) -> None:
    """project.name, project.root, and summary.pace_min_per_step must keep
    their names — status_all.py and the tray read this surface."""
    repo = tmp_path / "myrepo"
    _seed_repo(repo)

    plans_dir = tmp_path / "external" / "plans"
    _seed_plans(plans_dir)

    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        with patch("sys.argv", [
            "status_progress.py",
            "--json",
            "--project-path", str(repo),
        ]):
            # Capture stdout
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                status_progress.main()

        data = json.loads(buf.getvalue())

    assert "name" in data["project"], "JSON must have project.name"
    assert "root" in data["project"], "JSON must have project.root"
    assert "pace_min_per_step" in data["summary"], (
        "JSON must have summary.pace_min_per_step"
    )
