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

# MASTER — test

## Sub-plan registry

| # | Sub-plan | Steps |
|---|---|---|
| 1 | [2026-08-10-example.md](./2026-08-10-example.md) | 2 |
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


# ── AC-4: scan-failed is distinct from zero-found ────────────────────────────

def test_scan_failed_distinct_from_zero(tmp_path: Path) -> None:
    """When a repo in the list is not scannable (git fails), scan_failed must
    be True — not silently reported as zero commits."""
    # Create a directory that looks like it has .git but git log will fail
    # (empty .git directory, not a real repo)
    broken_repo = tmp_path / "broken-repo"
    broken_repo.mkdir()
    (broken_repo / ".git").mkdir()

    plans_dir = tmp_path / "external" / "plans"
    _seed_plans(plans_dir)

    # Patch find_repos to return our broken repo
    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        with patch.object(status_progress, "find_repos", return_value=[broken_repo]):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                with patch("sys.argv", [
                    "status_progress.py",
                    "--json",
                    "--project-path", str(tmp_path),
                ]):
                    status_progress.main()

            data = json.loads(buf.getvalue())

    assert data["summary"]["scan_failed"] is True, (
        "scan_failed must be True when git fails on a repo"
    )
    assert data["summary"]["pace_min_per_step"] is None


def test_genuine_zero_not_flagged_as_scan_failed(tmp_path: Path) -> None:
    """A real git repo with zero step commits must report scan_failed=False.
    The distinction is the whole point of AC-4."""
    repo = tmp_path / "myrepo"
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
        ["git", "commit", "-m", "initial commit"],
        cwd=str(repo), capture_output=True, check=True,
    )

    plans_dir = tmp_path / "external" / "plans"
    _seed_plans(plans_dir)

    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        from io import StringIO
        buf = StringIO()
        with patch("sys.stdout", buf):
            with patch("sys.argv", [
                "status_progress.py",
                "--json",
                "--project-path", str(repo),
            ]):
                status_progress.main()

        data = json.loads(buf.getvalue())

    assert data["summary"]["scan_failed"] is False, (
        "scan_failed must be False for a real git repo with no step commits"
    )
    assert data["summary"]["pace_min_per_step"] is None


def test_human_output_says_scan_failed(tmp_path: Path) -> None:
    """Human-readable output must say 'scan failed' (not 'insufficient data')
    when a repo cannot be scanned."""
    broken_repo = tmp_path / "broken-repo"
    broken_repo.mkdir()
    (broken_repo / ".git").mkdir()

    plans_dir = tmp_path / "external" / "plans"
    _seed_plans(plans_dir)

    with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
        with patch.object(status_progress, "find_repos", return_value=[broken_repo]):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                with patch("sys.argv", [
                    "status_progress.py",
                    "--project-path", str(tmp_path),
                ]):
                    status_progress.main()

    output = buf.getvalue()
    assert "scan failed" in output, (
        f"Human output must say 'scan failed' for a broken repo, got: {output}"
    )
    assert "insufficient data" not in output


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


# ── AC-8: stale sentinel returns state="unknown" ─────────────────────────────

def test_detect_sentinel_health_stale_returns_unknown(tmp_path: Path) -> None:
    """When sentinel says state=running but PID is dead, detect_sentinel_health
    must return state='unknown' and preserve the raw value in raw_state."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = runtime_dir / "last-exit.json"
    sentinel_file.write_text(json.dumps({"state": "running", "pid": 99999999}))

    result = status_progress.detect_sentinel_health(runtime_dir, 99999999)

    assert result["state"] == "unknown", (
        f"Expected state='unknown' for stale sentinel, got {result['state']!r}"
    )
    assert result["stale"] is True
    assert result["raw_state"] == "running", (
        f"Expected raw_state='running' for stale sentinel, got {result.get('raw_state')!r}"
    )
    assert result["pid"] == 99999999


def test_detect_sentinel_health_live_returns_running(tmp_path: Path) -> None:
    """When sentinel says state=running and PID is alive, detect_sentinel_health
    must return state='running' (not 'unknown')."""
    import os
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = runtime_dir / "last-exit.json"
    sentinel_file.write_text(json.dumps({"state": "running", "pid": os.getpid()}))

    result = status_progress.detect_sentinel_health(runtime_dir, os.getpid())

    assert result["state"] == "running", (
        f"Expected state='running' for live sentinel, got {result['state']!r}"
    )
    assert result["stale"] is False
    assert "raw_state" not in result, (
        "raw_state should not be present for non-stale sentinel"
    )


def test_detect_sentinel_health_terminal_state(tmp_path: Path) -> None:
    """A terminal sentinel (shipped, interrupted) must return its state as-is."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = runtime_dir / "last-exit.json"
    sentinel_file.write_text(json.dumps({"state": "shipped", "pid": 99999999}))

    result = status_progress.detect_sentinel_health(runtime_dir, 99999999)

    assert result["state"] == "shipped"
    assert result["stale"] is False
    assert "raw_state" not in result
