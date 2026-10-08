"""A train-only scan entry is offered the train and never dispatched.

Measured 2026-10-06/07 (ilk-skills on chad-mbp): after a batch shipped, the
scan returned the project as ``train_only`` (scheduler_scan.py, the drained
queue branch).  The scheduler offered the train (``skip-permits``) and then
dispatched the project anyway, because scheduler.sh never read
``train_only``.  The dispatched runner had nothing to run, exited after 0
iterations and overwrote the shipped sentinel (19:59 run, and again 00:16
run 20261007-001633), so the project left the scan and permits written
later never started a train.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


def _shipped_project(data_home: Path, repo: Path, key: str = "shipped-proj") -> Path:
    pd = data_home / "projects" / key
    plans = pd / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-test.md").write_text(
        "---\ntitle: MASTER-test\ncreated: 2026-10-06T00:00:00+08:00\n"
        "status: active\npriority: 0\npause_after_ship: false\n---\n\n# MASTER-test\n\n"
        "## Sub-plan registry\n\n| # | Sub-plan | Status |\n|---|---|---|\n"
        "| 1 | [2026-10-06-work.md](./2026-10-06-work.md) | shipped |\n",
        encoding="utf-8")
    (plans / "2026-10-06-work.md").write_text(
        "---\nplan: work\nstatus: shipped\ncurrent_step: 2\nestimated_steps: 2\n"
        "last_updated: 2026-10-06\n---\n\n# work\n", encoding="utf-8")
    launcher = pd / "runtime" / "launcher"
    launcher.mkdir(parents=True)
    (launcher / "last-exit.json").write_text(json.dumps({
        "state": "blocked-no-runnable", "run_id": "R1", "iterations": 3,
        "project_path": str(repo)}), encoding="utf-8")
    (launcher / "last-launch.json").write_text(
        json.dumps({"project_path": str(repo)}), encoding="utf-8")
    # A host with no permit: the train is offered and refused (skip-permits).
    (pd / "runtime" / "ship-config.json").write_text(
        json.dumps({"ship": {"hosts": ["h1"]}}), encoding="utf-8")
    return pd


def test_train_only_entry_is_offered_but_not_dispatched(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                       cwd=repo, check=True)
    data_home = tmp_path / ".ilk-data"
    pd = _shipped_project(data_home, repo)
    env = {**os.environ, "HOME": str(tmp_path), "ILK_DATA_HOME": str(data_home),
           "ILK_SKILL_HOME": str(SKILLS_DIR), "ILK_AUTOPLAN": "0",
           "ILK_BATCH_AUDIT": "0"}  # test-only: skip audit, test train logic
    env.pop("ILK_DATA_DIR", None)

    r = subprocess.run(["bash", str(SCHEDULER), "--once", "--dry-run"],
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr[-800:]
    decisions = [json.loads(l) for l in r.stdout.splitlines()
                 if l.startswith("{") and '"decision"' in l]
    assert not any(d.get("decision") == "dispatch" and d.get("key") == pd.name
                   for d in decisions), r.stdout
    log = (data_home / "logs" / "scheduler.log").read_text(encoding="utf-8")
    assert f"skip-permits: {pd.name}" in log, log
    assert f"skip-train-only: {pd.name}" in log, log
    # The shipped sentinel survives, so a later pass with permits can start it.
    sentinel = json.loads((pd / "runtime" / "launcher" / "last-exit.json").read_text())
    assert sentinel["run_id"] == "R1" and sentinel["iterations"] == 3


def test_offering_a_train_is_not_counted_as_a_launch(tmp_path: Path) -> None:
    """Passes that only offer a train never advance the no-progress counter.

    Backlog 05f11c22, measured 2026-10-07/08: the bound sat above the train
    offer and the skip-train-only exit, so each post-ship pass counted as a
    non-clean, no-progress launch.  gh-resolve reached 92 with 0 launches,
    and ilk-skills blocked its own release at 11:47 (3 permit-wait passes).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                       cwd=repo, check=True)
    data_home = tmp_path / ".ilk-data"
    pd = _shipped_project(data_home, repo)
    env = {**os.environ, "HOME": str(tmp_path), "ILK_DATA_HOME": str(data_home),
           "ILK_SKILL_HOME": str(SKILLS_DIR), "ILK_AUTOPLAN": "0",
           "ILK_BATCH_AUDIT": "0"}  # test-only: skip audit, test train logic
    env.pop("ILK_DATA_DIR", None)

    for _ in range(4):  # one more than the threshold of 3
        r = subprocess.run(["bash", str(SCHEDULER), "--once", "--dry-run"],
                           capture_output=True, text=True, timeout=60, env=env,
                           encoding="utf-8")
        assert r.returncode == 0, r.stderr[-800:]

    log = (data_home / "logs" / "scheduler.log").read_text(encoding="utf-8")
    assert log.count(f"skip-train-only: {pd.name}") == 4, log
    assert f"no-progress-bound: {pd.name}" not in log, log
    np_file = pd / "runtime" / "launcher" / "no-progress.json"
    count = json.loads(np_file.read_text())["count"] if np_file.exists() else 0
    assert count == 0, f"4 train offers counted as {count} launches"


def _run_once(tmp_path: Path, extra_env: dict[str, str]) -> tuple[subprocess.CompletedProcess, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                       cwd=repo, check=True)
    data_home = tmp_path / ".ilk-data"
    pd = _shipped_project(data_home, repo)
    env = {**os.environ, "HOME": str(tmp_path), "ILK_DATA_HOME": str(data_home),
           "ILK_SKILL_HOME": str(SKILLS_DIR), "ILK_AUTOPLAN": "0", **extra_env}
    env.pop("ILK_DATA_DIR", None)
    r = subprocess.run(["bash", str(SCHEDULER), "--once", "--dry-run"],
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8")
    log_path = data_home / "logs" / "scheduler.log"
    return r, pd, log_path.read_text(encoding="utf-8") if log_path.exists() else ""


def test_a_non_pass_audit_is_logged_with_its_real_verdict(tmp_path: Path) -> None:
    """Backlog a47ea3a1: batch_audit.py exits 1/2 with its JSON on stdout,
    and `|| audit_result="{}"` logged every non-pass as verdict=error."""
    r, pd, log = _run_once(tmp_path, {"ILK_BATCH_AUDIT": "1"})
    assert r.returncode == 0, r.stderr[-800:]
    line = next((l for l in log.splitlines() if f"skip-audit-failed: {pd.name}" in l), "")
    assert line, log
    assert "verdict=error" not in line and "failed=" in line, line


def test_machine_permits_with_the_audit_off_do_not_abort(tmp_path: Path) -> None:
    """Backlog 39d0d4e0: audit_verdict was declared only inside the audit
    branch, so machine mode + ILK_BATCH_AUDIT=0 hit an unbound variable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"permit_mode": "machine"}}), encoding="utf-8")
    r, pd, log = _run_once(tmp_path, {"ILK_BATCH_AUDIT": "0"})
    assert r.returncode == 0, r.stderr[-800:]
    assert "unbound variable" not in r.stderr, r.stderr[-800:]
    assert f"skip-permits: {pd.name}" in log, log
