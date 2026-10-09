"""Pin that a worker-written sub-plan status outside the state machine is undone.

Backlog aba1019c (driver half, part 2) and d61afe74 part 3.  gh-resolve run
20261009-133350 set ``status: complete`` after its last step; another worker
set a sub-plan to ``queued`` (a MASTER status).  Readers branch on a fixed set
(loop_status.py:418-431,656), so the sub-plan was neither runnable nor
terminal, its dependent never became runnable, and the master went
blocked-no-runnable.

subplan_status_snapshot.py takes the ``status`` of every sub-plan the run's
master registers before dispatch, and restores after the agent returns.

AC-1: complete / queued / a removed status line are restored to the
      pre-dispatch value, and restore exits 3.
AC-2: an in-vocabulary change (shipped, blocked) is left alone, exit 0.
AC-3: a pre-dispatch value that is itself unknown is not written back; it
      is reported, exit 0.
AC-4: no change -> exit 0, file bytes unchanged.
AC-5: take refuses a missing master (an empty snapshot would read clean).
AC-6: through the runner's real main(): a stub worker that writes
      ``status: complete`` is restored and the log names it.  Control: a
      stub that edits nothing prints no [status-guard] line.
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
_SNAP = _SCRIPTS / "subplan_status_snapshot.py"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402

_MASTER = "MASTER-2026-10-10a.md"
_SUB = "2026-10-10a-aaa.md"


def _snap(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_SNAP), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def _world(plans: Path, sub_status: str = "in-progress",
           master_status: str = "active") -> Path:
    plans.mkdir(parents=True, exist_ok=True)
    sub = plans / _SUB
    sub.write_text(
        f"---\nplan: aaa\nstatus: {sub_status}\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n### Step 0 — x\n")
    (plans / _MASTER).write_text(
        f"---\nmaster_plan: 2026-10-10a\nstatus: {master_status}\npriority: 1\n---\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | {_SUB} |\n")
    return sub


def _set_status(sub: Path, line: str | None) -> None:
    lines = [ln for ln in sub.read_text().splitlines(keepends=True)
             if not ln.startswith("status:")]
    if line is not None:
        lines.insert(2, f"status: {line}\n")
    sub.write_text("".join(lines))


def _take(plans: Path, out: Path) -> None:
    r = _snap("take", "--plans-dir", str(plans), "--master", _MASTER, "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr


def _restore(plans: Path, out: Path) -> subprocess.CompletedProcess:
    return _snap("restore", "--plans-dir", str(plans), "--snapshot", str(out))


# ── AC-1 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("written", ["complete", "queued", None])
def test_an_unknown_status_is_restored(tmp_path: Path, written: str | None) -> None:
    plans, out = tmp_path / "plans", tmp_path / "snap.json"
    sub = _world(plans)
    _take(plans, out)
    _set_status(sub, written)
    r = _restore(plans, out)
    assert r.returncode == 3, r.stdout + r.stderr
    assert parse_frontmatter(sub.read_text())["status"] == "in-progress"
    assert f"[status-guard] {_SUB}: status {written or '<absent>'} -> in-progress" in r.stdout


# ── AC-2 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("written", ["shipped", "blocked"])
def test_a_known_status_change_is_left_alone(tmp_path: Path, written: str) -> None:
    plans, out = tmp_path / "plans", tmp_path / "snap.json"
    sub = _world(plans)
    _take(plans, out)
    _set_status(sub, written)
    r = _restore(plans, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert parse_frontmatter(sub.read_text())["status"] == written
    assert r.stdout == ""


# ── AC-3 ────────────────────────────────────────────────────────────────────

def test_an_unknown_snapshot_value_is_reported_not_written(tmp_path: Path) -> None:
    plans, out = tmp_path / "plans", tmp_path / "snap.json"
    sub = _world(plans, sub_status="done")
    _take(plans, out)
    _set_status(sub, "complete")
    r = _restore(plans, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert parse_frontmatter(sub.read_text())["status"] == "complete"
    assert "left for the operator" in r.stdout


# ── AC-4 ────────────────────────────────────────────────────────────────────

def test_no_change_is_clean(tmp_path: Path) -> None:
    plans, out = tmp_path / "plans", tmp_path / "snap.json"
    sub = _world(plans)
    before = sub.read_bytes()
    _take(plans, out)
    r = _restore(plans, out)
    assert r.returncode == 0 and r.stdout == "", r.stdout + r.stderr
    assert sub.read_bytes() == before


# ── AC-5 ────────────────────────────────────────────────────────────────────

def test_take_refuses_a_missing_master(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    r = _snap("take", "--plans-dir", str(plans), "--master", _MASTER,
              "--out", str(tmp_path / "snap.json"))
    assert r.returncode == 2
    assert "master not found" in r.stderr


# ── AC-6: through the runner ───────────────────────────────────────────────

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")


def _run_main(root: Path, worker_edit: str) -> tuple[subprocess.CompletedProcess, Path]:
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
    finally:
        if prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = prev
    plans = data_home / "projects" / key / "plans"
    sub = _world(plans)

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\n"
                    + worker_edit.format(sub=shlex.quote(str(sub))) + "\nexit 0\n")
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
    return proc, sub


@_NEEDS_GTIMEOUT
def test_the_runner_restores_a_worker_written_complete(tmp_path: Path) -> None:
    proc, sub = _run_main(
        tmp_path, "sed -i '' 's/^status: in-progress$/status: complete/' {sub}")
    out = proc.stdout + proc.stderr
    tail = "\n".join(out.splitlines()[-40:])
    assert f"[status-guard] {_SUB}: status complete -> in-progress" in out, tail
    assert parse_frontmatter(sub.read_text())["status"] != "complete", tail


@_NEEDS_GTIMEOUT
def test_control_a_worker_that_leaves_statuses_alone(tmp_path: Path) -> None:
    proc, sub = _run_main(tmp_path, ": nothing")
    out = proc.stdout + proc.stderr
    tail = "\n".join(out.splitlines()[-40:])
    assert "[status-guard]" not in out, tail
    assert parse_frontmatter(sub.read_text())["status"] == "in-progress", tail
