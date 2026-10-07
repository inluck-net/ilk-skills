"""Red test: pending_batches counts every owed master, not just up to the first queued one with work.

Regression for: status_all.py --json reported pending_batches 1 for ilk-skills
with active=1 queued=5; Chad saw only the running batch on the SwiftBar panel.
Root cause: the loop at status_all.py:891-918 counts pending_batches and searches
for a queued master with work in the same loop; the `break` at :917 (for the
search) also ends the count.

AC-1: 1 active + 5 queued masters, each queued one with a pending sub-plan
      → pending_batches == 6.
AC-2: plus 1 draft, 1 paused, 1 shipped master → still 6.
AC-3: manually_runnable is unchanged versus base for the AC-1 fixture.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATUS_ALL = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "status_all.py"

sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
from ilk_paths import project_key as _project_key  # noqa: E402

SCRATCH = REPO_ROOT / "scratch" / f"panel-count-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
ILK_DATA = SCRATCH / "ilk-data"


# ── helpers ─────────────────────────────────────────────────────────

def _make_git_project(name: str) -> Path:
    """Create a minimal git repo at SCRATCH/projects/<name>/."""
    root = SCRATCH / "projects" / name
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", str(root)], capture_output=True, check=True,
        encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True, encoding="utf-8", errors="replace",
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)},
    )
    return root


def _write_master(plans_dir: Path, slug: str, master_status: str,
                  sub_status: str = "pending") -> None:
    """Write a MASTER + sub-plan into plans_dir."""
    sub_fname = f"2026-10-07-{slug}-sub.md"
    master = (
        "---\n"
        f"title: Test {slug}\n"
        f"slug: {slug}\n"
        f"created: 2026-10-07T00:00:00+08:00\n"
        f"status: {master_status}\n"
        f"priority: 5\n"
        "pause_after_ship: false\n"
        "branch: null\n"
        "goal: test fixture\n"
        "out_of_scope: []\n"
        "cross_cutting_invariants: []\n"
        "---\n"
        f"\n# Test {slug}\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|---|\n"
        f"| 1 | 1 | [{slug}-sub](./{sub_fname}) | test | 3 | {sub_status} |\n"
    )
    (plans_dir / f"MASTER-2026-10-07-{slug}.md").write_text(master, encoding="utf-8")

    sub = (
        "---\n"
        f"plan: {slug}-sub\n"
        f"status: {sub_status}\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P2\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-07\n"
        "---\n"
        f"\n# Sub-plan for {slug}\n"
    )
    (plans_dir / sub_fname).write_text(sub, encoding="utf-8")


def _setup_multi_master_project(name: str, masters: list[tuple[str, str]],
                                *, sentinel: dict | None = None) -> Path:
    """Create a project with multiple masters.

    masters: list of (slug, master_status) tuples.
    Each master gets one pending sub-plan.
    sentinel: if provided, write a last-exit.json sentinel; if None, no sentinel
    (project appears idle, not stale-running).
    Returns the git project root.
    """
    root = _make_git_project(name)
    key = _project_key(root)
    plans = ILK_DATA / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    for slug, status in masters:
        _write_master(plans, slug, status)

    if sentinel is not None:
        runtime = ILK_DATA / "projects" / key / "runtime" / "launcher"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "last-exit.json").write_text(
            json.dumps(sentinel), encoding="utf-8"
        )
    return root


def _status_all_json(project_root: Path) -> dict:
    """Run status_all.py --json and return the entry for this project."""
    env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
    result = subprocess.run(
        [sys.executable, str(STATUS_ALL), "--json"],
        cwd=str(project_root),
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    data = json.loads(result.stdout)
    key = _project_key(project_root)
    matches = [e for e in data if e["project_key"] == key]
    assert len(matches) == 1, f"expected 1 entry for {key}, got {len(data)}"
    return matches[0]


# ── fixtures ────────────────────────────────────────────────────────

def _rm_onerror(func, path, exc):
    """Ignore permission errors (Windows git objects)."""
    try:
        os.chmod(path, os.stat(path).st_mode | 0o700)
        func(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _clean():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, onerror=_rm_onerror)
    yield
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, onerror=_rm_onerror)


# ── AC-1 ────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="count stops at the first queued master with work")
def test_pending_batches_counts_all_owed_masters():
    """1 active + 5 queued masters → pending_batches == 6."""
    masters = [("active-batch", "active")]
    for i in range(5):
        masters.append((f"queued-batch-{i}", "queued"))

    proj = _setup_multi_master_project("six-batches", masters)
    entry = _status_all_json(proj)
    assert entry["pending_batches"] == 6


# ── AC-2 ────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="count stops at the first queued master with work")
def test_pending_batches_ignores_draft_paused_shipped():
    """+1 draft, +1 paused, +1 shipped → still 6."""
    masters = [("active-batch", "active")]
    for i in range(5):
        masters.append((f"queued-batch-{i}", "queued"))
    # These three must not count
    masters.append(("draft-batch", "draft"))
    masters.append(("paused-batch", "paused"))
    masters.append(("shipped-batch", "shipped"))

    proj = _setup_multi_master_project("nine-batches", masters)
    entry = _status_all_json(proj)
    assert entry["pending_batches"] == 6


# ── AC-3 ────────────────────────────────────────────────────────────

def test_queued_has_work_unchanged():
    """manually_runnable is True when there's a queued master with work and the project is idle."""
    masters = [("active-batch", "active")]
    for i in range(5):
        masters.append((f"queued-batch-{i}", "queued"))

    # No sentinel → not stale-running → not blocked → manually_runnable can fire.
    proj = _setup_multi_master_project("runnable-check", masters)
    entry = _status_all_json(proj)
    # The queued masters have pending sub-plans, so manually_runnable must be True.
    assert entry["manually_runnable"] is True