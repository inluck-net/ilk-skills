"""Red test: status_all.py alive must be False when sentinel state is terminal.

Bug: resolve_project_status() sets alive = pid_alive(pid) without checking
whether the sentinel state is "running". When a terminal-state sentinel
(local_checks_failed, shipped, interrupted, etc.) has a recycled PID,
pid_alive returns True → alive=True → tray shows "running" indefinitely.

Fix (step 1): gate alive on state ∈ LIVE_SENTINEL_STATES before consulting PID.

Fix (step 2): that gate only covers runs that *reached* Finalize-Sentinel.
A run killed before it could rewrite the state keeps state="running"
forever, leaving the PID as the sole evidence — and a recycled PID then
resurrects it.  Observed 2026-08-13: a gh-triage sentinel from 2026-07-08
named PID 18920, by then a `/bin/zsh -c … pytest` shell; the menu bar
counted two running loops while one was running.  alive now additionally
requires the PID's command line to be an ilk process (ilk_pid_alive).

AC-1: terminal state + live PID       → alive == False
AC-2: running + live *ilk* PID        → alive == True  (no regression)
AC-3: running + dead PID              → alive == False (stale-detection preserved)
AC-4: running + live *non-ilk* PID    → alive == False (recycled-PID phantom)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
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
    # The sentinel lives in runtime/launcher/ — the path every reader uses.
    # Moved there by `the-sentinel-lands-where-readers-look` (736d6d5); these
    # tests still wrote the old runtime/ path and so found no sentinel at all.
    launcher = runtime / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "last-exit.json").write_text(
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


@contextmanager
def _ilk_stub_process():
    """Spawn a live process whose command line reads as an ilk runner.

    os.getpid() cannot serve as the "live PID" any more: the pytest
    process is exactly the kind of unrelated command a recycled PID
    lands on, which is what AC-4 asserts is *not* alive.  A real ilk
    runner is identified by `run_ilk_loop` in its argv, so the stub is
    a script named for it — the same trick test_project_runner_liveness.sh
    uses against the bash helper.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stub = SCRATCH / "run_ilk_loop_stub.py"
    stub.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(stub)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield proc.pid
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


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
        """Terminal state + live ilk PID → alive must be False."""
        with _ilk_stub_process() as live_pid:
            _setup_project("term-live", state=state, pid=live_pid)
            sentinel = _get_sentinel("term-live")
        assert sentinel["alive"] is False, (
            f"state={state!r}, pid={live_pid} (alive ilk runner) — "
            f"sentinel.alive should be False but was {sentinel['alive']}"
        )


# ── AC-2: running + live ilk PID → alive == True ───────────────────

class TestAC2_RunningLivePid:
    """A running sentinel with a live ilk PID must stay alive (no regression)."""

    def test_running_live_pid_is_alive(self):
        """state=running + live PID owned by an ilk runner → alive must be True."""
        with _ilk_stub_process() as live_pid:
            _setup_project("run-live", state="running", pid=live_pid)
            sentinel = _get_sentinel("run-live")
        assert sentinel["alive"] is True, (
            f"state='running', pid={live_pid} (alive ilk runner) — "
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


# ── AC-4: running + live non-ilk PID → alive == False ──────────────

class TestAC4_RunningRecycledPid:
    """A never-finalised sentinel whose PID was recycled must read dead.

    The regression this guards: gh-triage's 2026-07-08 sentinel still said
    state="running", and its PID 18920 had been handed to an unrelated
    shell.  State-gating (step 1) cannot catch it — the state really is
    "running" — so only the command check can.
    """

    def test_running_recycled_pid_is_not_alive(self):
        """state=running + live PID owned by a non-ilk process → alive False."""
        # os.getpid() is the pytest process: alive, and emphatically not
        # an ilk runner — the same shape as the observed recycled PID.
        _setup_project("run-recycled", state="running", pid=os.getpid())
        sentinel = _get_sentinel("run-recycled")
        assert sentinel["alive"] is False, (
            f"state='running', pid={os.getpid()} (alive, but a pytest "
            f"process, not an ilk runner) — sentinel.alive should be False "
            f"but was {sentinel['alive']}"
        )
