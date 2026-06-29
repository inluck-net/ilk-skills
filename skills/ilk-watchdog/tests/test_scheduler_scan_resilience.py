"""Scheduler-scan resilience: a single bad project must not crash the scan,
and the JSON output must survive a zh-CN (GBK) stdout.

Regression guard for the 2026-06-30 three-project scheduler crash: a per-project
exception (e.g. a plan file read racing a concurrent loop's write) aborted the
whole scan, whose traceback — under the scheduler's `$ErrorActionPreference=Stop`
+ `2>&1` merge — killed the daemon. The fixes: isolate per-project failures in
``scan_projects`` and force UTF-8 stdout in ``main`` (same GBK family as
run_local_checks). Hermetic — no live scheduler.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
SCAN_SCRIPT = SCRIPTS_ILK_WATCHDOG / "scheduler_scan.py"


def _write_project(tmp_path: Path, key: str, *, repo_path: str) -> Path:
    """Scaffold a runnable project (active master + pending sub-plan)."""
    project_dir = tmp_path / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-test.md").write_text(
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-06-08T00:00:00+08:00\n"
        "status: active\n"
        "---\n\n"
        "# MASTER-test\n\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-06-08-work.md](./2026-06-08-work.md) | pending |\n",
        encoding="utf-8",
    )
    (plans_dir / "2026-06-08-work.md").write_text(
        "---\n"
        "plan: 2026-06-08-work\n"
        "status: pending\n"
        "last_updated: 2026-06-08\n"
        "---\n\n# 2026-06-08-work\n",
        encoding="utf-8",
    )
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": repo_path}), encoding="utf-8"
    )
    return project_dir


def _fresh_scheduler_scan():
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod in ("scheduler_scan", "ilk_paths", "plan_status"):
        sys.modules.pop(mod, None)
    import scheduler_scan
    return scheduler_scan


def test_bad_project_skipped_healthy_survives(tmp_path, monkeypatch):
    """A per-project exception skips just that project — the scan still
    returns the healthy one and never propagates the error."""
    _write_project(tmp_path, "good-proj", repo_path="/repo/good")
    _write_project(tmp_path, "bad-proj", repo_path="/repo/bad")

    scan = _fresh_scheduler_scan()
    scan.ilk_data_root = lambda: tmp_path

    orig = scan._scan_one_project

    def explode(project_dir):
        # Mimic a non-OSError surprise (e.g. a parser choking on a
        # half-written file) for exactly one project.
        if project_dir.name == "bad-proj":
            raise ValueError("simulated mid-write read race")
        return orig(project_dir)

    monkeypatch.setattr(scan, "_scan_one_project", explode)

    # Must NOT raise, and must still surface the healthy project.
    results = scan.scan_projects()
    keys = [r["key"] for r in results]
    assert keys == ["good-proj"], f"bad project should be skipped, got {keys}"


def test_main_survives_gbk_stdout_with_non_ascii(tmp_path):
    """main() emits valid UTF-8 JSON even when stdout would default to GBK and
    a field carries a non-GBK character (U+2713). Without the reconfigure this
    crashes with UnicodeEncodeError → empty stdout → daemon death."""
    # repo_path carries U+2713 '✓' — not encodable in cp936/GBK.
    _write_project(tmp_path, "uni-proj", repo_path="/repo/✓/path")

    env = dict(os.environ)
    env["ILK_DATA_HOME"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "gbk"  # simulate zh-CN console stdout

    proc = subprocess.run(
        [sys.executable, str(SCAN_SCRIPT)],
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0, (
        f"scan crashed under GBK stdout: {proc.stderr.decode('utf-8', 'replace')}"
    )
    parsed = json.loads(proc.stdout.decode("utf-8"))
    assert any("✓" in (r.get("repo_path") or "") for r in parsed), (
        f"non-ASCII repo_path should round-trip, got {parsed}"
    )
