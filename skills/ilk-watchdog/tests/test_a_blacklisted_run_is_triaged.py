"""Red-first pins: a blacklisted run starts triage once, never while its runner lives.

Sub-plan: a-blacklisted-run-is-triaged (step 0).
Drives scheduler.sh --once (single cycle, real dispatch path) in a sandbox:
isolated HOME + ILK_DATA_HOME + ILK_SKILL_HOME, with a stub ilk_triage.py
that records its argv to a JSON file.

AC-1..AC-4 are xfail(strict=True) — the scheduler does not yet call triage.
AC-5 (control) is unmarked — dry-run dispatch JSON must not regress.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


# ── helpers ─────────────────────────────────────────────────────────


def _setup_triage_stub(skill_home: Path) -> Path:
    """Write a stub ``ilk_triage.py`` that records invocations to a JSON file.

    Returns the path to the invocations file.
    """
    scripts_dir = skill_home / "ilk-watchdog" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    invocations_file = scripts_dir / "triage_invocations.json"

    stub = scripts_dir / "ilk_triage.py"
    stub.write_text(
        f"""\
#!/usr/bin/env python3
\"\"\"Stub triage agent — records argv to a JSON file.\"\"\"
import json, sys, pathlib
invocations_file = pathlib.Path(r\"{invocations_file}\")
try:
    records = json.loads(invocations_file.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    records = []
records.append(sys.argv[1:])
invocations_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return invocations_file


def _setup_project(
    data_home: Path,
    key: str,
    *,
    run_id: str = "test-run-001",
    pid: int = 0,
    blacklist: bool = True,
    backoff: bool = False,
) -> Path:
    """Scaffold a project with postmortem blacklist and last-exit sentinel.

    Returns the project data dir.
    """
    project_dir = data_home / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Minimal MASTER
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-10-03T00:00:00+08:00\n"
        "status: active\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n\n"
        "# MASTER-test\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-10-03-work.md](./2026-10-03-work.md) | pending |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

    # Minimal sub-plan
    subplan = (
        "---\n"
        "plan: 2026-10-03-work\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-03\n"
        "---\n\n"
        "# 2026-10-03-work\n"
    )
    (plans_dir / "2026-10-03-work.md").write_text(subplan, encoding="utf-8")

    # last-launch.json so scan_projects finds a repo_path
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": str(project_dir)}),
        encoding="utf-8",
    )

    # last-exit.json sentinel with run_id and pid
    sentinel = {
        "state": "local_checks_failed",
        "run_id": run_id,
        "pid": pid,
        "started_at": "2026-10-03T10:00:00+0800",
        "ended_at": "2026-10-03T10:05:00+0800",
        "iterations": 1,
    }
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    # Postmortem for blacklist (or backoff)
    if blacklist or backoff:
        pm_dir = launcher_dir / "postmortems"
        pm_dir.mkdir(parents=True, exist_ok=True)
        classification = "stuck-no-progress" if blacklist else "rapid-terminal"
        pm = (
            f"---\nproject: x\nclassification: \"{classification}\"\n"
            f"generated_at: \"2026-10-03T09:00:00\"\n---\n\n"
            f"# Postmortem {run_id}\n"
        )
        (pm_dir / f"{run_id}.md").write_text(pm, encoding="utf-8")

    return project_dir


def _run_scheduler(
    sandbox,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run scheduler.sh --once (NOT --dry-run) so triage can fire."""
    env = {**sandbox.env, **(extra_env or {})}
    return subprocess.run(
        ["bash", str(SCHEDULER), "--once"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        encoding="utf-8",
        preexec_fn=sandbox.preexec,
    )


def _wait_for_triage(invocations_file: Path, timeout: float = 10.0) -> list:
    """Poll until the stub writes its record."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if invocations_file.exists():
            try:
                records = json.loads(invocations_file.read_text(encoding="utf-8"))
                if records:
                    return records
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.2)
    return []


# ── AC-1: blacklisted + dead pid → triage starts once ──────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="scheduler does not yet call maybe_start_triage on skip-blacklist",
)
def test_blacklisted_starts_triage_once(scheduler_sandbox):
    """AC-1: a blacklisted project with a dead pid starts triage exactly once
    with ``run --project-key <key> --run-id R``.

    A second cycle must NOT start triage again (idempotent marker).
    """
    sandbox = scheduler_sandbox
    invocations_file = _setup_triage_stub(
        Path(sandbox.env["ILK_SKILL_HOME"])
    )
    _setup_project(
        sandbox.root / ".ilk-data",
        "test-blacklist",
        run_id="run-abc",
        pid=0,  # dead pid
        blacklist=True,
    )

    # First cycle — triage should start
    _run_scheduler(sandbox)
    records = _wait_for_triage(invocations_file)
    assert len(records) == 1, (
        f"expected 1 triage invocation, got {len(records)}: {records}"
    )
    argv = records[0]
    assert "run" in argv, f"argv should contain 'run', got {argv}"
    assert "--project-key" in argv
    key_idx = argv.index("--project-key")
    assert argv[key_idx + 1] == "test-blacklist"
    assert "--run-id" in argv
    rid_idx = argv.index("--run-id")
    assert argv[rid_idx + 1] == "run-abc"

    # Second cycle — must NOT start again (idempotent marker)
    _run_scheduler(sandbox)
    records_after = json.loads(invocations_file.read_text(encoding="utf-8"))
    assert len(records_after) == 1, (
        f"triage started again on second cycle: {len(records_after)} invocations"
    )


# ── AC-2: live pid → no start ──────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="scheduler does not yet call maybe_start_triage (live-pid guard)",
)
def test_live_pid_no_triage(scheduler_sandbox):
    """AC-2: a blacklisted project whose sentinel pid is alive must NOT
    start triage."""
    sandbox = scheduler_sandbox
    invocations_file = _setup_triage_stub(
        Path(sandbox.env["ILK_SKILL_HOME"])
    )

    # Start a background process to own the pid
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        _setup_project(
            sandbox.root / ".ilk-data",
            "test-live-pid",
            run_id="run-live",
            pid=sleeper.pid,
            blacklist=True,
        )

        _run_scheduler(sandbox)
        # Give a short window in case triage erroneously starts
        time.sleep(2)

        if invocations_file.exists():
            records = json.loads(invocations_file.read_text(encoding="utf-8"))
        else:
            records = []
        assert len(records) == 0, (
            f"triage should NOT start while runner is alive, but got {records}"
        )
    finally:
        sleeper.kill()
        sleeper.wait(timeout=5)


# ── AC-3: skip-backoff → no start ──────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="scheduler does not yet call maybe_start_triage (backoff exclusion)",
)
def test_backoff_no_triage(scheduler_sandbox):
    """AC-3: a project skipped for skip-backoff only must NOT start triage."""
    sandbox = scheduler_sandbox
    invocations_file = _setup_triage_stub(
        Path(sandbox.env["ILK_SKILL_HOME"])
    )
    _setup_project(
        sandbox.root / ".ilk-data",
        "test-backoff",
        run_id="run-bo",
        pid=0,
        blacklist=False,
        backoff=True,  # only backoff, not blacklist
    )

    _run_scheduler(sandbox)
    time.sleep(2)

    if invocations_file.exists():
        records = json.loads(invocations_file.read_text(encoding="utf-8"))
    else:
        records = []
    assert len(records) == 0, (
        f"backoff-skip must NOT start triage, but got {records}"
    )


# ── AC-4: ILK_TRIAGE=0 → no start ─────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="scheduler does not yet call maybe_start_triage (ILK_TRIAGE=0 guard)",
)
def test_triage_disabled_env(scheduler_sandbox):
    """AC-4: ILK_TRIAGE=0 disables the triage hook entirely."""
    sandbox = scheduler_sandbox
    invocations_file = _setup_triage_stub(
        Path(sandbox.env["ILK_SKILL_HOME"])
    )
    _setup_project(
        sandbox.root / ".ilk-data",
        "test-disabled",
        run_id="run-dis",
        pid=0,
        blacklist=True,
    )

    _run_scheduler(sandbox, extra_env={"ILK_TRIAGE": "0"})
    time.sleep(2)

    if invocations_file.exists():
        records = json.loads(invocations_file.read_text(encoding="utf-8"))
    else:
        records = []
    assert len(records) == 0, (
        f"ILK_TRIAGE=0 must prevent triage, but got {records}"
    )


# ── AC-5 (control): dry-run dispatch JSON is unchanged ─────────────────────


def test_dry_run_dispatch_unaffected(scheduler_sandbox):
    """AC-5: a dispatchable project in --dry-run --once produces valid
    dispatch JSON (regression guard — no triage in dry-run)."""
    sandbox = scheduler_sandbox
    _setup_project(
        sandbox.root / ".ilk-data",
        "test-dispatch",
        run_id="run-dsp",
        pid=0,
        blacklist=False,
        backoff=False,
    )

    result = subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        env=sandbox.env,
        encoding="utf-8",
        preexec_fn=sandbox.preexec,
    )
    # The scheduler should produce valid JSON lines on stdout
    lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
    assert len(lines) > 0, (
        f"expected at least one JSON line from dry-run, got: {result.stdout!r}"
    )
    # Each line must be valid JSON
    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict), f"expected dict, got {type(parsed)}"
    # Triage must NOT appear in dry-run output
    combined = result.stdout + result.stderr
    assert "triage-start" not in combined, (
        "triage must not start in dry-run mode"
    )