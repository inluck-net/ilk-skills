"""Red test: loop_status.py --json + status_all.py all-projects aggregator.

AC-1: loop_status.py --json (cwd = a project) prints valid JSON with keys
      master, subplans [{slug,status,current_step,estimated_steps}],
      active/queued/shipped counts, queue_exit (0/1/2).
      Text mode + exit codes unchanged when --json absent.

AC-2: status_all.py --json prints a JSON array; each entry has
      project_key, path, active_master, next_subplan, step (cur/est),
      sentinel ({pid,state,alive}), last_class (null when no postmortem).

AC-3: sentinel.alive reflects real pid liveness cross-platform.
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
LOOP_STATUS = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "loop_status.py"
STATUS_ALL = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "status_all.py"

# Compute project_key inline (avoid import path gymnastics).
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


# We use a fixed scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "status-json"
ILK_DATA = SCRATCH / "ilk-data"


# ── helpers ─────────────────────────────────────────────────────────

def _make_git_project(name: str) -> Path:
    """Create a minimal git repo at SCRATCH/projects/<name>/. Returns root."""
    root = SCRATCH / "projects" / name
    root.mkdir(parents=True, exist_ok=True)
    # init + empty commit so .git exists
    subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
    )
    return root


def _setup_project(name: str, *, master_status: str = "active",
                   sub_status: str = "pending", pid: int = 99999999,
                   state: str = "running", run_id: str = "test-run-001") -> Path:
    """Create a git project + external plans/runtime under ILK_DATA.

    Returns the git project root (cwd for loop_status.py).
    """
    root = _make_git_project(name)
    key = _project_key(root)
    plans = ILK_DATA / "projects" / key / "plans"
    runtime = ILK_DATA / "projects" / key / "runtime"
    plans.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)

    # Master plan — sub-plan filename must match YYYY-MM-DD-*.md for
    # extract_master_order() regex to find it.
    sub_fname = f"2026-06-07-{name}-sub.md"
    master = (
        "---\n"
        f"title: Test {name}\n"
        f"slug: {name}\n"
        f"created: 2026-06-07T00:00:00+08:00\n"
        f"status: {master_status}\n"
        f"priority: 5\n"
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
        f"| 1 | 1 | [{name}-sub](./{sub_fname}) | test | 3 | {sub_status} |\n"
    )
    (plans / f"MASTER-2026-06-07-{name}.md").write_text(master, encoding="utf-8")

    # Sub-plan
    sub = (
        "---\n"
        f"plan: {name}-sub\n"
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
    (runtime / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    return root


def _cleanup():
    if SCRATCH.exists():
        import shutil
        def _rm_onerror(func, path, exc):
            """Ignore permission errors (Windows git objects)."""
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


# ── AC-1: loop_status.py --json ─────────────────────────────────────

class TestAC1_LoopStatusJson:
    """loop_status.py --json emits structured per-project status."""

    def test_json_output_has_required_keys(self):
        """--json prints valid JSON with master, subplans, counts, queue_exit."""
        proj = _setup_project("alpha", pid=os.getpid())
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(LOOP_STATUS), "--json"],
            cwd=str(proj),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1, f"exit {result.returncode}: {result.stderr}"
        data = json.loads(result.stdout)

        # Top-level keys
        assert "master" in data, "missing 'master' key"
        assert "subplans" in data, "missing 'subplans' key"
        assert "active" in data, "missing 'active' count"
        assert "queued" in data, "missing 'queued' count"
        assert "shipped" in data, "missing 'shipped' count"
        assert "queue_exit" in data, "missing 'queue_exit' key"

        # Subplan structure
        assert isinstance(data["subplans"], list)
        assert len(data["subplans"]) >= 1
        sp = data["subplans"][0]
        for key in ("slug", "status", "current_step", "estimated_steps"):
            assert key in sp, f"subplan missing '{key}'"

    def test_text_mode_unchanged_when_no_json_flag(self):
        """Without --json, output is human text (not JSON) and exit code is 1."""
        proj = _setup_project("beta", pid=os.getpid())
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(LOOP_STATUS)],
            cwd=str(proj),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1
        # Should NOT be valid JSON
        try:
            json.loads(result.stdout)
            assert False, "text mode should not emit JSON"
        except json.JSONDecodeError:
            pass  # expected

    def test_exit_code_0_when_all_shipped(self):
        """--json with all shipped sub-plans returns exit 0."""
        proj = _setup_project("gamma", pid=os.getpid(), sub_status="shipped")
        key = _project_key(proj)
        master_path = ILK_DATA / "projects" / key / "plans" / "MASTER-2026-06-07-gamma.md"
        text = master_path.read_text(encoding="utf-8")
        text = text.replace("status: active", "status: shipped")
        master_path.write_text(text, encoding="utf-8")

        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(LOOP_STATUS), "--json"],
            cwd=str(proj),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["queue_exit"] == 0


# ── AC-2: status_all.py --json ──────────────────────────────────────

class TestAC2_StatusAllJson:
    """status_all.py --json emits an array of all projects."""

    def test_json_array_with_two_projects(self):
        """Two projects produce a JSON array of length 2 with required keys."""
        _setup_project("proj-a", pid=os.getpid())
        _setup_project("proj-b", pid=99999999)
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 2

        for entry in data:
            for key in ("project_key", "path", "active_master",
                        "next_subplan", "step", "sentinel", "last_class"):
                assert key in entry, f"entry missing '{key}'"
            # step is "cur/est"
            assert isinstance(entry["step"], str)
            assert "/" in entry["step"]
            # sentinel structure
            sent = entry["sentinel"]
            assert isinstance(sent, dict)
            for skey in ("pid", "state", "alive"):
                assert skey in sent, f"sentinel missing '{skey}'"

    def test_last_class_null_when_no_postmortem(self):
        """Without postmortem files, last_class is null."""
        _setup_project("proj-c", pid=os.getpid())
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["last_class"] is None


# ── AC-3: pid liveness cross-platform ───────────────────────────────

class TestAC3_PidLiveness:
    """sentinel.alive reflects real pid liveness."""

    def test_alive_for_current_pid(self):
        """os.getpid() should show alive=True."""
        _setup_project("live-pid", pid=os.getpid())
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        data = json.loads(result.stdout)
        entry = next(e for e in data if e["project_key"].endswith("live-pid"))
        assert entry["sentinel"]["alive"] is True

    def test_dead_for_nonexistent_pid(self):
        """A bogus pid should show alive=False."""
        _setup_project("dead-pid", pid=99999999)
        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        data = json.loads(result.stdout)
        entry = next(e for e in data if e["project_key"].endswith("dead-pid"))
        assert entry["sentinel"]["alive"] is False

    def test_alive_for_bom_encoded_sentinel(self):
        """The PowerShell runner writes last-exit.json with a UTF-8 BOM. The
        reader MUST use utf-8-sig, else json.loads chokes on the BOM -> sentinel
        None -> alive=False, and the tray renders every running loop as "(idle)".
        Regression for the 2026-06-13 tray-always-idle bug."""
        proj = _setup_project("bom-pid", pid=os.getpid())
        key = _project_key(proj)
        sentinel_path = ILK_DATA / "projects" / key / "runtime" / "last-exit.json"
        sentinel = {"state": "running", "pid": os.getpid(),
                    "iterations": 3, "run_id": "bom-run"}
        # Write WITH a UTF-8 BOM, exactly like the PowerShell runner does.
        sentinel_path.write_text(json.dumps(sentinel), encoding="utf-8-sig")
        assert sentinel_path.read_bytes()[:3] == b"\xef\xbb\xbf", "fixture must have a BOM"

        env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True, text=True, env=env,
        )
        data = json.loads(result.stdout)
        entry = next(e for e in data if e["project_key"].endswith("bom-pid"))
        assert entry["sentinel"]["state"] == "running", entry["sentinel"]
        assert entry["sentinel"]["alive"] is True, entry["sentinel"]
