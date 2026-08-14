"""Tests for status_all action fields: path, runnable, parked.

Covers AC-1 from tray-actions-render sub-plan:
  - path: project root directory
  - runnable: has dispatchable master with pending/in-progress work AND not alive
  - parked: blacklisted with no valid resolve-ack

Four synthetic states tested: running, runnable-idle, parked, all-shipped.
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

# Compute project_key inline.
import hashlib
import re as _re

_KEY_PUNCT = _re.compile(r"[^a-z0-9]+")


def _project_key(root: Path) -> str:
    abs_str = str(root.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


# Fixed scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "status-actions"
ILK_DATA = SCRATCH / "ilk-data"


# ── helpers ─────────────────────────────────────────────────────────

def _make_git_project(name: str) -> Path:
    """Create a minimal git repo at SCRATCH/projects/<name>. Returns root."""
    root = SCRATCH / "projects" / name
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], capture_output=True, check=True,
                   encoding="utf-8", errors="replace")
    subprocess.run(["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
                   capture_output=True, check=True, encoding="utf-8", errors="replace")
    return root


def _setup_project(
    name: str,
    *,
    master_status: str = "active",
    sub_status: str = "pending",
    pid: int = 99999999,
    state: str = "running",
    run_id: str = "test-run-001",
    blacklist_class: str | None = None,
    supervised_only: bool = False,
) -> Path:
    """Create a git project + external plans/runtime under ILK_DATA.

    Returns the git project root (cwd for status_all.py).
    """
    root = _make_git_project(name)
    key = _project_key(root)
    plans = ILK_DATA / "projects" / key / "plans"
    runtime = ILK_DATA / "projects" / key / "runtime"
    launcher = runtime / "launcher"
    plans.mkdir(parents=True, exist_ok=True)
    launcher.mkdir(parents=True, exist_ok=True)

    # Master plan — use short slug for filename to avoid truncation issues.
    slug = f"t{name}"
    sub_fname = f"2026-06-07-{slug}-sub.md"
    supervised_line = "supervised_only: true\n" if supervised_only else ""
    master = (
        "---\n"
        f"title: Test {name}\n"
        f"slug: {slug}\n"
        f"created: 2026-06-07T00:00:00+08:00\n"
        f"status: {master_status}\n"
        f"priority: 5\n"
        f"{supervised_line}"
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
        f"| 1 | 1 | [{slug}-sub](./{sub_fname}) | test | 3 | {sub_status} |\n"
    )
    (plans / f"MASTER-2026-06-07-{slug}.md").write_text(master, encoding="utf-8")

    # Sub-plan
    sub = (
        "---\n"
        f"plan: {slug}-sub\n"
        f"status: {sub_status}\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P2\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-07\n"
        "---\n"
        f"\n# Sub-plan for {name}\n"
    )
    (plans / sub_fname).write_text(sub, encoding="utf-8")

    # Sentinel (last-exit.json)
    sentinel = {"state": state, "pid": pid, "iterations": 3, "run_id": run_id}
    # The sentinel lives in runtime/launcher/ — the path every reader uses.
    # Moved there by `the-sentinel-lands-where-readers-look` (736d6d5); these
    # tests still wrote the old runtime/ path and so found no sentinel at all.
    launcher = runtime / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")

    # Optional: blacklist postmortem (use recent naive datetime to stay within backoff)
    if blacklist_class:
        import datetime as _dt
        recent = _dt.datetime.now() - _dt.timedelta(minutes=30)
        recent_str = recent.strftime("%Y-%m-%dT%H:%M:%S")
        pm_text = (
            "---\n"
            f"classification: {blacklist_class}\n"
            f"generated_at: {recent_str}\n"
            "---\n"
            "# Test postmortem\n"
        )
        (launcher / "postmortems").mkdir(parents=True, exist_ok=True)
        (launcher / "postmortems" / f"{run_id}.md").write_text(pm_text, encoding="utf-8")

    return root


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


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


def _get_status(name: str) -> dict:
    """Run status_all.py --json and return the entry for project `name`."""
    env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
    result = subprocess.run(
        [sys.executable, str(STATUS_ALL), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    data = json.loads(result.stdout)
    # Match on the exact key computed the same way the fixture stored it —
    # NOT project_key.endswith(name): a long checkout path pushes the slug past
    # 80 chars, at which point project_key() truncates + appends a hash, so the
    # key no longer ends with name (and the entry's `path` is the ilk-data dir,
    # whose basename is that same hashed key).
    expected_key = _project_key(SCRATCH / "projects" / name)
    entry = next(e for e in data if e["project_key"] == expected_key)
    return entry


# ── AC-1: action fields present ─────────────────────────────────────

class TestActionFieldsPresent:
    """Every entry has path, runnable, parked."""

    def test_path_field(self):
        """path is the project's ilk-data dir (basename == project_key)."""
        _setup_project("af")
        entry = _get_status("af")
        assert "path" in entry
        # `path` is str(project_dir) under ILK_DATA, so its basename is the
        # project_key — assert that, not endswith("af"), which only held while
        # the (untruncated) key happened to end with the project name.
        assert Path(entry["path"]).name == _project_key(SCRATCH / "projects" / "af")

    def test_runnable_field(self):
        """runnable is a boolean."""
        _setup_project("rf")
        entry = _get_status("rf")
        assert "runnable" in entry
        assert isinstance(entry["runnable"], bool)

    def test_parked_field(self):
        """parked is a boolean."""
        _setup_project("pf")
        entry = _get_status("pf")
        assert "parked" in entry
        assert isinstance(entry["parked"], bool)


# ── State: running (loop alive) ─────────────────────────────────────

class TestStateRunning:
    """When loop is alive: runnable=False, parked=False."""

    def test_runnable_false_when_alive(self, live_ilk_pid):
        _setup_project("r", pid=live_ilk_pid, state="running")
        entry = _get_status("r")
        assert entry["sentinel"]["alive"] is True
        assert entry["runnable"] is False

    def test_parked_false_when_alive(self, live_ilk_pid):
        _setup_project("rp", pid=live_ilk_pid, state="running")
        entry = _get_status("rp")
        assert entry["sentinel"]["alive"] is True
        assert entry["parked"] is False


# ── State: runnable-idle (active master, pending work, not alive) ───

class TestStateRunnableIdle:
    """Active master with pending sub-plan, loop not alive: runnable=True, parked=False."""

    def test_runnable_true_when_idle(self):
        _setup_project("ri", pid=99999999, state="shipped")
        entry = _get_status("ri")
        assert entry["sentinel"]["alive"] is False
        assert entry["runnable"] is True

    def test_parked_false_when_idle(self):
        _setup_project("rnp", pid=99999999, state="shipped")
        entry = _get_status("rnp")
        assert entry["parked"] is False


# ── State: parked (blacklisted) ─────────────────────────────────────

class TestStateParked:
    """Blacklisted project: runnable=False, parked=True."""

    def test_runnable_false_when_parked(self):
        _setup_project("pk", pid=99999999, state="shipped",
                       blacklist_class="local-checks-stuck")
        entry = _get_status("pk")
        assert entry["blocked"] is True
        assert entry["blocked_reason"] == "within-backoff"
        assert entry["runnable"] is False

    def test_parked_true_when_blacklisted(self):
        _setup_project("pkt", pid=99999999, state="shipped",
                       blacklist_class="local-checks-stuck")
        entry = _get_status("pkt")
        assert entry["parked"] is True


# ── AC-1: supervised_only queued master yields manually_runnable ──────

class TestManuallyRunnableSupervised:
    """AC-1: supervised_only + queued with pending sub-plan → manually_runnable=True."""

    def test_supervised_queued_manually_runnable(self):
        """A project whose only master is supervised_only + queued with a
        pending sub-plan yields manually_runnable == True."""
        _setup_project("mr1", master_status="queued", supervised_only=True,
                       pid=99999999, state="shipped")
        entry = _get_status("mr1")
        assert entry["manually_runnable"] is True
        # Should NOT be scheduler-runnable (supervised_only is skipped by scan_projects).
        assert entry["runnable"] is False


# ── AC-3: running project yields manually_runnable=False ─────────────

class TestManuallyRunnableRunning:
    """AC-3: running project (sentinel alive) → manually_runnable=False."""

    def test_running_not_manually_runnable(self, live_ilk_pid):
        _setup_project("mrr", pid=live_ilk_pid, state="running")
        entry = _get_status("mrr")
        assert entry["sentinel"]["alive"] is True
        assert entry["manually_runnable"] is False


# ── AC-4: all-shipped/idle project appears with manually_runnable=False ─

class TestManuallyRunnableAllShipped:
    """AC-4: all-shipped/idle project appears in output with manually_runnable=False."""

    def test_all_shipped_in_output(self):
        _setup_project("mrs", sub_status="shipped", pid=99999999, state="shipped")
        entry = _get_status("mrs")
        # Project should still appear in the list.
        assert entry["project_key"] == _project_key(SCRATCH / "projects" / "mrs")
        assert entry["manually_runnable"] is False


# ── AC-6: runnable regression guard ────────────────────────────────────

class TestRunnableRegression:
    """AC-6: scheduler-facing `runnable` is unchanged across all states.

    The `runnable` flag is consumed by scheduler_scan.py for autonomous
    dispatch.  Adding `manually_runnable` must NOT alter its semantics.
    """

    def test_runnable_false_when_alive(self, live_ilk_pid):
        """Running loop → runnable=False (scheduler must not re-dispatch)."""
        _setup_project("rr1", pid=live_ilk_pid, state="running")
        entry = _get_status("rr1")
        # Assert the premise: without this the test would still pass via the
        # stale-running blocked path, i.e. for the wrong reason.
        assert entry["sentinel"]["alive"] is True
        assert entry["runnable"] is False
        # Also verify manually_runnable is consistent.
        assert entry["manually_runnable"] is False

    def test_runnable_true_when_active_idle(self):
        """Active master + pending work + not alive → runnable=True."""
        _setup_project("rr2", pid=99999999, state="shipped")
        entry = _get_status("rr2")
        assert entry["runnable"] is True
        assert entry["manually_runnable"] is True

    def test_runnable_false_when_supervised_queued(self):
        """supervised_only + queued → runnable=False (scheduler skips)."""
        _setup_project("rr3", master_status="queued", supervised_only=True,
                       pid=99999999, state="shipped")
        entry = _get_status("rr3")
        assert entry["runnable"] is False
        # But manually_runnable is True (human can /ilk it).
        assert entry["manually_runnable"] is True

    def test_runnable_false_when_blocked(self):
        """Blacklisted project → runnable=False."""
        _setup_project("rr4", pid=99999999, state="shipped",
                       blacklist_class="local-checks-stuck")
        entry = _get_status("rr4")
        assert entry["runnable"] is False

    def test_runnable_false_when_all_shipped(self):
        """All sub-plans shipped → runnable=False."""
        _setup_project("rr5", sub_status="shipped", pid=99999999, state="shipped")
        entry = _get_status("rr5")
        assert entry["runnable"] is False
        assert entry["manually_runnable"] is False


# ── AC-5: model from JSONL ────────────────────────────────────────────

class TestModelFromJsonl:
    """AC-5: model field from latest JSONL record."""

    def _write_jsonl(self, key: str, records: list[dict]) -> None:
        """Write JSONL records to the project's logs dir."""
        import hashlib, re as _re
        _KEY_PUNCT = _re.compile(r"[^a-z0-9]+")
        abs_str = str((SCRATCH / "projects" / key).resolve()).lower()
        slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
        if len(slug) <= 80:
            proj_key = slug
        else:
            h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
            proj_key = slug[: 80 - 8].rstrip("-") + "-" + h
        logs_dir = ILK_DATA / "projects" / proj_key / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = logs_dir / ".ilk-loop.log"
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_model_present_in_entry(self, live_ilk_pid):
        """When JSONL has model, entry reflects it."""
        _setup_project("mp1", pid=live_ilk_pid, state="running")
        self._write_jsonl("mp1", [
            {"run_id": "r1", "iteration": 1, "model": "claude-sonnet-4-20250514"},
        ])
        entry = _get_status("mp1")
        assert entry["model"] == "claude-sonnet-4-20250514"

    def test_model_empty_when_no_jsonl(self):
        """When JSONL file is absent, model is empty string."""
        _setup_project("mp2", pid=99999999, state="shipped")
        entry = _get_status("mp2")
        assert entry["model"] == ""

    def test_model_empty_when_field_missing(self):
        """When JSONL records have no model key, model is empty string."""
        _setup_project("mp3", pid=99999999, state="shipped")
        self._write_jsonl("mp3", [
            {"run_id": "r1", "iteration": 1},
        ])
        entry = _get_status("mp3")
        assert entry["model"] == ""

    def test_model_from_latest_record(self, live_ilk_pid):
        """model comes from the LAST JSONL record."""
        _setup_project("mp4", pid=live_ilk_pid, state="running")
        self._write_jsonl("mp4", [
            {"run_id": "r1", "iteration": 1, "model": "claude-haiku-4-5-20251001"},
            {"run_id": "r1", "iteration": 2, "model": "claude-sonnet-4-20250514"},
        ])
        entry = _get_status("mp4")
        assert entry["model"] == "claude-sonnet-4-20250514"


# ── Backward compatibility ──────────────────────────────────────────

class TestBackwardCompatibility:
    """Existing test_status_all_json.py tests should still pass."""

    def test_existing_keys_preserved(self):
        """All original AC-2 keys still present."""
        _setup_project("bc", pid=os.getpid())
        entry = _get_status("bc")
        for key in ("project_key", "path", "active_master", "next_subplan",
                     "step", "sentinel", "last_class", "blocked",
                     "classification", "blocked_reason", "blocked_expiry",
                     "report_path"):
            assert key in entry, f"missing original key: {key}"

    def test_blocked_info_unchanged(self):
        """blocked field still computed correctly."""
        _setup_project("bci", pid=99999999, state="running")
        entry = _get_status("bci")
        # stale-running: sentinel says running but PID dead
        assert entry["blocked"] is True
        assert entry["blocked_reason"] == "stale-running"
