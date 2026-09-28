"""Pins for the D3 master snapshot (design §4 I3, §7 D3).

A master's ``status`` and park fields belong to the runner and park_master,
never to a worker.  ``PRE_ITER_ALL_STEPS`` covered sub-plans only, the one-ship
check skipped ``MASTER*``, and a worker that marked a parked master ``shipped``
was un-parked by the next reconcile.

AC-1: a worker that un-parks a master (status, parked_reason, hold) is restored
      to the pre-dispatch snapshot, and restore exits 3.
AC-2: the reconcile transition (queued/active -> shipped with every sub-plan
      shipped) is accepted: the scheduler makes it while the agent runs.
      The same edit with a sub-plan still pending is restored.
AC-3: no change -> exit 0, nothing written.
AC-4: through the runner's real main(): a stub worker that edits the active
      master's status is reverted, and the run ends ship_integrity_violation.
      Control: a stub that edits nothing ends some other way, master intact.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_SNAP = _SCRIPTS / "master_snapshot.py"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402


def _snap(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_SNAP), *args],
                          capture_output=True, text=True, timeout=60)


def _world(plans: Path, master_status: str = "blocked",
           sub_status: str = "pending") -> Path:
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-aaa.md").write_text(
        f"---\nplan: aaa\nstatus: {sub_status}\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n### Step 0 — x\n")
    m = plans / "MASTER-2026-09-29a.md"
    park = ('parked_at: 2026-09-28T22:50:00\n'
            'parked_reason: "operator stop"\nhold: human\n'
            if master_status == "blocked" else "")
    m.write_text(
        "---\nmaster_plan: 2026-09-29a\n"
        f"status: {master_status}\npriority: 1\n{park}---\n\n"
        "| # | Slug |\n|---|---|\n| 1 | 2026-09-29-aaa.md |\n")
    return m


def _take(plans: Path, out: Path) -> None:
    r = _snap("take", "--plans-dir", str(plans), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr


# ── AC-1 ────────────────────────────────────────────────────────────────────

def test_a_worker_unpark_is_restored(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    m = _world(plans)
    before = m.read_text()
    snap = tmp_path / "snap.json"
    _take(plans, snap)

    # The worker "unparks" by hand.
    m.write_text(before.replace("status: blocked", "status: queued")
                 .replace('parked_reason: "operator stop"\n', "")
                 .replace("hold: human\n", ""))
    r = _snap("restore", "--plans-dir", str(plans), "--snapshot", str(snap))
    assert r.returncode == 3, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert sorted(out["restored"][0]["changed"]) == ["hold", "parked_reason", "status"]
    assert m.read_text() == before, "restore must put back exactly what was there"


# ── AC-2 ────────────────────────────────────────────────────────────────────

def test_the_reconcile_transition_is_accepted(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    m = _world(plans, master_status="active", sub_status="shipped")
    snap = tmp_path / "snap.json"
    _take(plans, snap)
    m.write_text(m.read_text().replace("status: active", "status: shipped"))
    r = _snap("restore", "--plans-dir", str(plans), "--snapshot", str(snap))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["accepted"], r.stdout
    assert parse_frontmatter(m.read_text())["status"] == "shipped"


def test_a_ship_claim_the_subplans_do_not_back_is_restored(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    m = _world(plans, master_status="active", sub_status="pending")
    snap = tmp_path / "snap.json"
    _take(plans, snap)
    m.write_text(m.read_text().replace("status: active", "status: shipped"))
    r = _snap("restore", "--plans-dir", str(plans), "--snapshot", str(snap))
    assert r.returncode == 3, r.stdout + r.stderr
    assert parse_frontmatter(m.read_text())["status"] == "active"


# ── AC-3 ────────────────────────────────────────────────────────────────────

def test_no_change_is_clean(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    m = _world(plans)
    snap = tmp_path / "snap.json"
    _take(plans, snap)
    mtime = m.stat().st_mtime_ns
    r = _snap("restore", "--plans-dir", str(plans), "--snapshot", str(snap))
    assert r.returncode == 0, r.stdout + r.stderr
    assert m.stat().st_mtime_ns == mtime


# ── AC-4: through the runner ───────────────────────────────────────────────

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def _run_main(root: Path, worker_edit: str) -> tuple[subprocess.CompletedProcess, Path, dict]:
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")

    data_home = root / ".ilk-data"
    prev = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
        sentinel = ilk_paths.sentinel_path(key)
    finally:
        if prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = prev
    plans = data_home / "projects" / key / "plans"
    master = _world(plans, master_status="active", sub_status="in-progress")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\n" + worker_edit.format(
        master=shlex.quote(str(master))) + "\nexit 0\n")
    stub.chmod(0o755)
    (root / ".claude").mkdir()

    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(project))} \\
     --max-iterations 1 --iteration-timeout-min 1 --model test-model
echo "MAIN_RC=$?"
"""
    env = {**os.environ,
           "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "HOME": str(root), "ILK_DATA_HOME": str(data_home)}
    env.pop("ILK_DATA_DIR", None)
    env.pop("ILK_DOTSOURCE_ONLY", None)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180,
                          env=env, cwd=str(root))
    state = json.loads(sentinel.read_text()) if sentinel.exists() else {}
    return proc, master, state


@_NEEDS_GTIMEOUT
def test_the_runner_reverts_a_worker_master_edit(tmp_path: Path) -> None:
    proc, master, state = _run_main(
        tmp_path, "sed -i '' 's/^status: active$/status: blocked/' {master}")
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    assert parse_frontmatter(master.read_text())["status"] == "active", tail
    assert state.get("state") == "ship_integrity_violation", (state, tail)
    assert "the worker changed master state" in proc.stderr, tail


@_NEEDS_GTIMEOUT
def test_control_a_worker_that_leaves_masters_alone(tmp_path: Path) -> None:
    proc, master, state = _run_main(tmp_path, ": nothing")
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    assert parse_frontmatter(master.read_text())["status"] == "active", tail
    assert state.get("state") not in ("", None, "ship_integrity_violation"), (state, tail)
    assert "the worker changed master state" not in proc.stderr, tail
