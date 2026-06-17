"""Red test: status_all.py alive must be False when sentinel state is terminal.

Bug: resolve_project_status() sets alive = pid_alive(pid) without checking
whether the sentinel state is "running". When a terminal-state sentinel
(local_checks_failed, shipped, interrupted, etc.) has a recycled PID,
pid_alive returns True → alive=True → tray shows "running" indefinitely.

Fix (step 1): gate alive on state ∈ LIVE_SENTINEL_STATES before consulting PID.

AC-1: terminal state + live PID → alive == False
AC-2: running + live PID → alive == True  (no regression)
AC-3: running + dead PID → alive == False (existing stale-detection preserved)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ → ilk-loop/ → skills/ → root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATUS_ALL = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "status_all.py"

# Fixed scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "terminal-sentinel"
ILK_DATA = SCRATCH / "ilk-data"


# ── helpers ─────────────────────────────────────────────────────────

def _make_git_project(name: str) -> Path:
    """Create a minimal git repo at SCRATCH/projects/<name>/. Returns root."""
    root = SCRATCH / "projects" / name
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], capture_output=True, check=True, encoding="utf-8", errors="replace")
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
        encoding="utf-8", errors="replace",
    )
    return root


def _setup_project(name: str, *, state: str = "running",
                   pid: int = 99999999) -> Path:
    """Create a git project + external runtime with a sentinel.

    The ILK_DATA directory uses `name` directly as the project key
    (matching how scan_projects() discovers projects by directory name).
    """
    _make_git_project(name)
    key = name
    plans = ILK_DATA / "projects" / key / "plans"
    runtime = ILK_DATA / "projects" / key / "runtime"
    plans.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)

    # Minimal master (needs at least one sub-plan for resolve_project_status).
    sub_fname = f"2026-06-16-{name}-sub.md"
    master = (
        "---\n"
        f"title: Test {name}\n"
        f"slug: {name}\n"
        f"created: 2026-06-16T00:00:00+08:00\n"
        "status: active\n"
        "priority: 5\n"
        "pause_after_ship: false\n"
        "branch: null\n"
        "goal: test fixture\n"
        "out_of_scope: []\n"
        "cross_cutting_invariants: []\n"
        "---\n"
        f"\n# Test {name}\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|---|\n"
        f"| 1 | 1 | [{name}-sub](./{sub_fname}) | test | 3 | pending |\n"
    )
    (plans / f"MASTER-2026-06-16-{name}.md").write_text(master, encoding="utf-8")

    sub = (
        "---\n"
        f"plan: {name}-sub\n"
        "status: pending\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P2\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-16\n"
        "---\n"
        f"\n# Sub-plan for {name}\n"
    )
    (plans / sub_fname).write_text(sub, encoding="utf-8")

    # Sentinel — state and pid are the variables under test.
    sentinel = {"state": state, "pid": pid, "iterations": 3, "run_id": f"{name}-run"}
    (runtime / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )


def _cleanup():
    if SCRATCH.exists():
        import shutil
        def _rm_onerror(func, path, exc):
            try:
                os.chmod(path, 0o666)
                func(path)
            except OSError:
                pass
        shutil.rmtree(SCRATCH, onerror=_rm_onerror)


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


def _get_sentinel(project_name: str) -> dict:
    """Run status_all.py --json and return the sentinel for *project_name*."""
    env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
    result = subprocess.run(
        [sys.executable, str(STATUS_ALL), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    data = json.loads(result.stdout)
    try:
        entry = next(e for e in data if e["project_key"] == project_name)
    except StopIteration:
        raise AssertionError(
            f"No entry with key {project_name!r} in {json.dumps(data, indent=2)}"
        )
    return entry["sentinel"]


# ── AC-1: terminal state + live PID → alive == False ───────────────

class TestAC1_TerminalStateLivePid:
    """A terminal sentinel state must yield alive=False even when PID is alive."""

    @pytest.mark.parametrize("state", [
        "local_checks_failed",
        "shipped",
        "interrupted",
        "error",
        "max-iterations",
        "budget_exhausted",
    ])
    def test_terminal_state_forces_dead(self, state: str):
        """Terminal state + live PID (os.getpid()) → alive must be False."""
        _setup_project("term-live", state=state, pid=os.getpid())
        sentinel = _get_sentinel("term-live")
        assert sentinel["alive"] is False, (
            f"state={state!r}, pid={os.getpid()} (alive) — "
            f"sentinel.alive should be False but was {sentinel['alive']}"
        )


# ── AC-2: running + live PID → alive == True ───────────────────────

class TestAC2_RunningLivePid:
    """A running sentinel with a live PID must stay alive (no regression)."""

    def test_running_live_pid_is_alive(self):
        """state=running + live PID → alive must be True."""
        _setup_project("run-live", state="running", pid=os.getpid())
        sentinel = _get_sentinel("run-live")
        assert sentinel["alive"] is True, (
            f"state='running', pid={os.getpid()} (alive) — "
            f"sentinel.alive should be True but was {sentinel['alive']}"
        )


# ── AC-3: running + dead PID → alive == False ──────────────────────

class TestAC3_RunningDeadPid:
    """A running sentinel with a dead PID must show stale (alive=False)."""

    def test_running_dead_pid_is_not_alive(self):
        """state=running + dead PID → alive must be False."""
        _setup_project("run-dead", state="running", pid=99999999)
        sentinel = _get_sentinel("run-dead")
        assert sentinel["alive"] is False, (
            f"state='running', pid=99999999 (dead) — "
            f"sentinel.alive should be False but was {sentinel['alive']}"
        )
