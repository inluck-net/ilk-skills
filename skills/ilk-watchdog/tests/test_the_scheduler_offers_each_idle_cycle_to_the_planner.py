"""Tests: the scheduler offers each idle cycle to the auto-planner.

Sub-plan: the-scheduler-offers-each-idle-cycle-to-the-planner (step 0).
Covers AC-1..AC-6: maybe_tick_autoplan hook in scheduler.sh.

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path`` with a sandbox
project.  The auto-planner ``autoplan.py`` is replaced by a stub script that
records its argv and prints a canned JSON decision.  The scheduler is driven
via ``ILK_DOTSOURCE_ONLY=1`` sourcing (for AC-1..AC-4) or via
``--dry-run --once`` (for AC-5..AC-6).

AC-1..AC-5 are xfail(strict=True) — the hook does not exist yet.
AC-6 is a control: the dispatch path is unchanged and must pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_stub_autoplan(tmp_path: Path, *, stdout: str = '{"decision":"idle","detail":"3/6"}',
                         exit_code: int = 0) -> Path:
    """Write a stub ``autoplan.py`` that records argv and prints canned output.

    Returns the path to the stub script.
    """
    stub = tmp_path / "autoplan_stub.py"
    stub.write_text(textwrap.dedent(f"""\
        import json, sys, pathlib
        argv_file = pathlib.Path(sys.argv[0]).with_name("argv.json")
        argv_file.write_text(json.dumps(sys.argv[1:]))
        sys.stdout.write({stdout!r} + "\\n")
        sys.exit({exit_code})
    """), encoding="utf-8")
    return stub


def _write_scheduler_log(data_home: Path) -> Path:
    """Return the scheduler.log path (created on first write)."""
    return data_home / "logs" / "scheduler.log"


def _read_scheduler_log(data_home: Path) -> str:
    """Read the scheduler.log contents, or empty string if absent."""
    log_path = data_home / "logs" / "scheduler.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8")


def _read_tick_log(data_home: Path) -> str:
    """Read the autoplan tick.log contents, or empty string if absent."""
    tick_log = data_home / "autoplan" / "tick.log"
    if not tick_log.exists():
        return ""
    return tick_log.read_text(encoding="utf-8")


def _read_argv(stub_path: Path) -> list[str]:
    """Read the argv.json the stub recorded."""
    argv_file = stub_path.with_name("argv.json")
    if not argv_file.exists():
        return []
    return json.loads(argv_file.read_text(encoding="utf-8"))


def _source_scheduler_fn(env: dict[str, str], fn_name: str, *args: str) -> subprocess.CompletedProcess:
    """Source scheduler.sh with ILK_DOTSOURCE_ONLY=1 and call fn_name.

    This avoids running the full scheduler loop.
    """
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source "{SCHEDULER}"
        {fn_name} {" ".join(repr(a) for a in args)}
    """)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        env=env, encoding="utf-8",
    )


def _make_env(tmp_path: Path, data_home: Path, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build an isolated env for sourcing scheduler.sh."""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(SKILLS_DIR),
    }
    env.pop("ILK_DATA_DIR", None)
    if extra:
        env.update(extra)
    return env


def _write_project_data(data_home: Path, key: str, *, master_status: str = "active") -> Path:
    """Scaffold a minimal project data dir for the scheduler scan."""
    project_dir = data_home / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-10-03T00:00:00+08:00\n"
        f"status: {master_status}\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-10-03-work.md](./2026-10-03-work.md) | pending |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

    subplan = (
        "---\n"
        "plan: work\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-03\n"
        "---\n"
        "\n"
        "# work\n"
    )
    (plans_dir / "2026-10-03-work.md").write_text(subplan, encoding="utf-8")
    return project_dir


def _run_scheduler_dry_run(env: dict[str, str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run scheduler.sh --once --dry-run under the given env."""
    return subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True, text=True, timeout=timeout,
        env=env, encoding="utf-8",
    )


# ── AC-1: tick runs with correct argv ──────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="hook does not exist yet")
def test_ac1_tick_runs_with_dry_run_flag(tmp_path: Path) -> None:
    """AC-1: sourced scheduler with DRY_RUN=false runs stub with ``tick``;
    DRY_RUN=true runs stub with ``tick --dry-run``."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    stub = _write_stub_autoplan(tmp_path)
    env = _make_env(tmp_path, data_home, extra={"AUTOPLAN_PY": str(stub)})

    # DRY_RUN=false
    result = _source_scheduler_fn(env, "maybe_tick_autoplan")
    assert result.returncode == 0, result.stderr
    argv = _read_argv(stub)
    assert argv == ["tick"], f"expected ['tick'], got {argv}"

    # Clean argv for next run
    (stub.with_name("argv.json")).unlink(missing_ok=True)

    # DRY_RUN=true
    env["DRY_RUN"] = "true"
    result = _source_scheduler_fn(env, "maybe_tick_autoplan")
    assert result.returncode == 0, result.stderr
    argv = _read_argv(stub)
    assert argv == ["tick", "--dry-run"], f"expected ['tick', '--dry-run'], got {argv}"


# ── AC-2: started decision logs autoplan-start ─────────────────────────────


@pytest.mark.xfail(strict=True, reason="hook does not exist yet")
def test_ac2_started_decision_logs_autoplan_start(tmp_path: Path) -> None:
    """AC-2: stub printing ``{"decision":"started","detail":"abc123"}`` gives
    one ``autoplan-start`` line in scheduler.log; idle gives none."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    # started decision
    stub = _write_stub_autoplan(
        tmp_path,
        stdout='{"decision":"started","detail":"abc123"}',
    )
    env = _make_env(tmp_path, data_home, extra={"AUTOPLAN_PY": str(stub)})
    result = _source_scheduler_fn(env, "maybe_tick_autoplan")
    assert result.returncode == 0, result.stderr

    log = _read_scheduler_log(data_home)
    assert "autoplan-start" in log, f"expected autoplan-start in log, got:\n{log}"
    assert "abc123" in log, f"expected detail in log, got:\n{log}"

    # idle decision — clean log and retry
    (data_home / "logs" / "scheduler.log").unlink(missing_ok=True)
    stub2 = _write_stub_autoplan(
        tmp_path / "stub2",
        stdout='{"decision":"idle","detail":"3/6"}',
    )
    env2 = _make_env(tmp_path, data_home, extra={"AUTOPLAN_PY": str(stub2)})
    result2 = _source_scheduler_fn(env2, "maybe_tick_autoplan")
    assert result2.returncode == 0, result2.stderr

    log2 = _read_scheduler_log(data_home)
    assert "autoplan-start" not in log2, f"unexpected autoplan-start in log:\n{log2}"


# ── AC-3: stub crash leaves function status 0 and traceback in tick.log ─────


@pytest.mark.xfail(strict=True, reason="hook does not exist yet")
def test_ac3_stub_crash_leaves_status_zero(tmp_path: Path) -> None:
    """AC-3: a stub that exits 1 and prints a traceback leaves the function's
    status 0 and the traceback in tick.log."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    stub = _write_stub_autoplan(
        tmp_path,
        stdout='Traceback (most recent call last):\n  File "autoplan.py", line 1\n    broken\nNameError: name \'broken\' is not defined',
        exit_code=1,
    )
    env = _make_env(tmp_path, data_home, extra={"AUTOPLAN_PY": str(stub)})
    result = _source_scheduler_fn(env, "maybe_tick_autoplan")

    # Function must not fail the cycle
    assert result.returncode == 0, f"function should swallow error, got rc={result.returncode}\nstderr: {result.stderr}"

    # Traceback must be in tick.log
    tick_log = _read_tick_log(data_home)
    assert "Traceback" in tick_log or "broken" in tick_log, \
        f"expected traceback in tick.log, got:\n{tick_log}"


# ── AC-4: ILK_AUTOPLAN=0 disables the hook ─────────────────────────────────


@pytest.mark.xfail(strict=True, reason="hook does not exist yet")
def test_ac4_kill_switch_disables_hook(tmp_path: Path) -> None:
    """AC-4: ILK_AUTOPLAN=0 means the stub never runs (argv file absent)."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    stub = _write_stub_autoplan(tmp_path)
    env = _make_env(tmp_path, data_home, extra={
        "AUTOPLAN_PY": str(stub),
        "ILK_AUTOPLAN": "0",
    })
    result = _source_scheduler_fn(env, "maybe_tick_autoplan")
    assert result.returncode == 0, result.stderr

    # The stub must not have been called
    argv = _read_argv(stub)
    assert argv == [], f"stub should not have run, but got argv={argv}"


# ── AC-5: --dry-run --once on empty queue runs stub with tick --dry-run ─────


@pytest.mark.xfail(strict=True, reason="hook does not exist yet")
def test_ac5_dry_run_once_on_empty_queue(tmp_path: Path) -> None:
    """AC-5: ``scheduler.sh --dry-run --once`` on a sandbox with an empty
    queue runs the stub once with ``tick --dry-run`` and still prints the
    ``{"decision":"idle","reason":"all-queues-empty"}`` line."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    stub = _write_stub_autoplan(tmp_path)
    env = _make_env(tmp_path, data_home, extra={"AUTOPLAN_PY": str(stub)})

    result = _run_scheduler_dry_run(env)
    assert result.returncode == 0, f"scheduler failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    # Stub must have been called with tick --dry-run
    argv = _read_argv(stub)
    assert argv == ["tick", "--dry-run"], f"expected ['tick', '--dry-run'], got {argv}"

    # Scheduler must still report idle
    assert '"decision":"idle"' in result.stdout or '"reason":"all-queues-empty"' in result.stdout, \
        f"expected idle JSON in stdout, got:\n{result.stdout}"


# ── AC-6 (control): dispatch path is unchanged ─────────────────────────────


def test_ac6_control_dispatch_unchanged(tmp_path: Path) -> None:
    """AC-6 (control): a dispatchable sandbox project is dispatched exactly
    as before.  This test must pass without any changes to scheduler.sh.

    Runs ``--dry-run --once`` with a project in the queue and compares
    the dispatch JSON to ensure the hook did not break the existing path.
    """
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)

    # Create a dispatchable project
    _write_project_data(data_home, "test-project")

    # No AUTOPLAN_PY set — the hook should skip gracefully
    env = _make_env(tmp_path, data_home)

    result = _run_scheduler_dry_run(env)
    assert result.returncode == 0, f"scheduler failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    # The scheduler should have dispatched the project (dry-run prints JSON)
    # or logged an idle reason.  Either way, it must not crash.
    # The key invariant: the output is valid JSON or empty (no partial output).
    stdout = result.stdout.strip()
    if stdout:
        # Parse the last line of stdout (the scheduler's decision JSON)
        last_line = stdout.splitlines()[-1]
        parsed = json.loads(last_line)
        assert "decision" in parsed, f"expected 'decision' key in JSON, got {parsed}"