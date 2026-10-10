"""Tests: a missing release baseline is measured before the train is offered.

Sub-plan: the-scheduler-measures-before-the-train, step 0 (red-first pins).

Exercises ``maybe_start_release_train`` from ``scheduler.sh`` through a bash
wrapper that evals the extracted functions (the house pattern from
``test_progress_bound_without_postmortem.py``).  All external commands
(``measure_baseline.py``, ``release_train_dispatch``, ``spawn_detached``) are
stubs so no real train, no real ``~/.ilk-data``, and no real release process
are touched.

Five acceptance criteria:
  AC-1  stubbed ``check`` printing ``missing`` → return 1, ``baseline-measuring``
        in scheduler.log, ``measure`` spawned with correct argv, no permit file.
  AC-2  ``measuring`` → return 1, ``skip-baseline-measuring``, nothing spawned.
  AC-3  ``unmeasurable`` → return 1, ``skip-baseline-unmeasurable``.
  AC-4  (control) ``present`` → falls through to permit pre-flight.
  AC-5  (structural) the new check sits between ``skip-audit-failed`` and
        ``Pre-flight: check permits`` in the source.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

_WATCHDOG_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_SCHEDULER_SH = _WATCHDOG_SCRIPTS / "scheduler.sh"
_LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Isolation fixture ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME so no test touches the real data root."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("ILK_DATA_HOME", str(fake_home / ".ilk-data"))
    # Prevent sourcing _ilk_pid.sh from acquiring a real lock.
    monkeypatch.setenv("ILK_DOTSOURCE_ONLY", "1")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _python_path() -> str:
    """Return the real interpreter path."""
    return sys.executable


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    (repo / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _sentinel_path(tmp_path: Path) -> Path:
    """Return the sentinel path under the test's pinned ILK_DATA_HOME."""
    return (
        tmp_path / "home" / ".ilk-data" / "projects" / "data"
        / "runtime" / "launcher" / "last-exit.json"
    )


def _scheduler_log_path(tmp_path: Path) -> Path:
    """Return the scheduler log path for the test."""
    return tmp_path / "logs" / "scheduler.log"


def _write_sentinel(path: Path, *, state: str = "all-shipped") -> None:
    """Write a minimal exit sentinel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "state": state,
        "pid": 99999999,
        "run_id": "test-run-001",
        "started_at": "2026-10-10T10:00:00+0800",
        "ended_at": "2026-10-10T10:30:00+0800",
        "iterations": 3,
        "project_path": "/tmp/fake-repo",
        "cli": "claude",
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _run_maybe_start_release_train(
    *,
    tmp_path: Path,
    repo: Path,
    data_dir: Path,
    scheduler_log: Path,
    stub_state: str,
    stub_script: Path,
    python_path: str = "",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Build a bash wrapper and call ``maybe_start_release_train``.

    Uses the house pattern from test_progress_bound_without_postmortem.py:
    eval the extracted function bodies from scheduler.sh so only the
    functions under test run, not the whole daemon.
    """
    py = python_path or _python_path()
    ilkd = str(data_dir)
    skill_root = str(_SKILL_ROOT)
    scheduler_sh = str(_SCHEDULER_SH)
    stub = str(stub_script)
    log = str(scheduler_log)
    repo_s = str(repo)

    # Resolve the sentinel path via ilk_paths so the test agrees with the
    # real ``sentinel_path_for_data_dir``.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ilk_paths", str(_LOOP_SCRIPTS / "ilk_paths.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_ilk_paths"] = mod
    spec.loader.exec_module(mod)
    sentinel_p = str(mod.sentinel_path(os.path.basename(ilkd.rstrip("/"))))
    dispatch_dir = str(_WATCHDOG_SCRIPTS)

    log_dir = str(tmp_path / "logs")
    log_file = str(tmp_path / "logs" / "scheduler.log")
    ilkd_home = str(tmp_path / "home" / ".ilk-data")

    # Resolve sentinel path under the pinned ILK_DATA_HOME.
    sentinel_p = str(
        tmp_path / "home" / ".ilk-data" / "projects" / "data"
        / "runtime" / "launcher" / "last-exit.json"
    )

    # Create a stub spawn_detached.py that runs the command directly
    spawn_stub = tmp_path / "spawn_detached_stub.py"
    spawn_stub.write_text(textwrap.dedent("""\
import sys, os
args = sys.argv[1:]
# Parse --log PATH
log_path = None
if len(args) >= 2 and args[0] == "--log":
    log_path = args[1]
    args = args[2:]
if not args:
    sys.exit(2)
# Run the command directly
os.execvp(args[0], args)
"""), encoding="utf-8")

    script = textwrap.dedent(f"""\
set -euo pipefail
PYTHON="{py}"
_ILK_SCRIPT_DIR="{str(_WATCHDOG_SCRIPTS)}"
_SKILL_ROOT="{skill_root}"
_RELEASE_TRAIN_SCRIPT="{str(_WATCHDOG_SCRIPTS / '..' / '..' / 'ilk-ship' / 'scripts' / 'release_train.py')}"
_RELEASE_TRAIN_DISPATCH="{dispatch_dir}/release_train_dispatch.py"
_SPAWN_DETACHED="{spawn_stub}"
_MEASURE_BASELINE_SCRIPT="{stub}"
SCHEDULER_LOG_DIR="{log_dir}"
SCHEDULER_LOG_FILE="{log_file}"
export ILK_DATA_HOME="{ilkd_home}"
export HOME="{str(tmp_path / 'home')}"

eval "$(sed -n '/^write_scheduler_log()/,/^}}/p' "{scheduler_sh}")"
eval "$(sed -n '/^maybe_start_release_train()/,/^}}/p' "{scheduler_sh}")"

# Stub sentinel_path_for_data_dir: the real one shells out to Python +
# ilk_paths; we resolve it inline.
sentinel_path_for_data_dir() {{
  echo "{sentinel_p}"
}}

mkdir -p "{log_dir}"
maybe_start_release_train "test-key" "{repo_s}" "{ilkd}" "{repo_s}" "test-run-001"
""")

    env = {**os.environ, "ILK_DATA_HOME": str(tmp_path / "home" / ".ilk-data"),
           "HOME": str(tmp_path / "home")}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        env=env,
    )


# ── AC-1: missing → baseline-measuring, measure spawned ─────────────────────


def test_missing_baseline_spawns_measure(tmp_path: Path) -> None:
    """AC-1: ``check`` printing ``missing`` → return 1, ``baseline-measuring``
    logged, measure spawned with correct argv, no permit file."""
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scheduler_log = _scheduler_log_path(tmp_path)

    _write_sentinel(_sentinel_path(tmp_path), state="all-shipped")

    # Create stub measure_baseline.py
    stub = tmp_path / "measure_baseline.py"
    stub.write_text(textwrap.dedent(f"""\
import sys, json, os
args = sys.argv[1:]
state = os.environ.get("STUB_BASELINE_STATE", "missing")
record = os.environ.get("STUB_RECORD_FILE", "/dev/null")
with open(record, "a") as f:
    f.write(json.dumps({{"args": args, "verb": args[0] if args else ""}}) + "\\n")
if args and args[0] == "check":
    print(state)
elif args and args[0] == "measure":
    print(json.dumps({{"ok": True}}))
sys.exit(0)
"""), encoding="utf-8")

    record_file = tmp_path / "stub_record.jsonl"

    proc = _run_maybe_start_release_train(
        tmp_path=tmp_path, repo=repo, data_dir=data_dir,
        scheduler_log=scheduler_log, stub_state="missing",
        stub_script=stub,
        extra_env={"STUB_BASELINE_STATE": "missing",
                   "STUB_RECORD_FILE": str(record_file),
                   "ILK_BATCH_AUDIT": "0"},
    )
    assert proc.returncode == 1, (
        f"expected rc=1, got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    # Scheduler log has baseline-measuring
    log_content = scheduler_log.read_text(encoding="utf-8") if scheduler_log.exists() else ""
    assert "baseline-measuring" in log_content, (
        f"expected 'baseline-measuring' in log:\n{log_content}"
    )

    # Stub recorded the check and measure argv
    # Wait for the background measure process to complete
    import time
    records = []
    for _ in range(20):
        if record_file.exists():
            records = [json.loads(line) for line in record_file.read_text().splitlines() if line.strip()]
            verbs = [r["verb"] for r in records]
            if "measure" in verbs:
                break
        time.sleep(0.1)
    assert record_file.exists(), "stub did not record any calls"
    records = [json.loads(line) for line in record_file.read_text().splitlines() if line.strip()]
    verbs = [r["verb"] for r in records]
    assert "check" in verbs, f"expected 'check' call, got: {records}"
    assert "measure" in verbs, f"expected 'measure' call, got: {records}"

    # check argv has --project and --data-dir with fixture values
    check_rec = next(r for r in records if r["verb"] == "check")
    check_args = check_rec["args"]
    assert "--project" in check_args, f"check missing --project: {check_args}"
    assert "--data-dir" in check_args, f"check missing --data-dir: {check_args}"
    proj_idx = check_args.index("--project")
    assert check_args[proj_idx + 1] == str(repo), (
        f"check --project mismatch: {check_args[proj_idx + 1]} != {repo}"
    )
    dd_idx = check_args.index("--data-dir")
    assert check_args[dd_idx + 1] == str(data_dir), (
        f"check --data-dir mismatch: {check_args[dd_idx + 1]} != {data_dir}"
    )

    # No permit file written
    permit_dir = data_dir / "runtime" / "release"
    if permit_dir.exists():
        permit_files = list(permit_dir.glob("*.permit*"))
        assert permit_files == [], f"unexpected permit files: {permit_files}"


# ── AC-2: measuring → skip-baseline-measuring ───────────────────────────────


def test_measuring_state_skips(tmp_path: Path) -> None:
    """AC-2: ``check`` printing ``measuring`` → return 1,
    ``skip-baseline-measuring`` logged, nothing spawned."""
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scheduler_log = _scheduler_log_path(tmp_path)

    _write_sentinel(_sentinel_path(tmp_path), state="all-shipped")

    stub = tmp_path / "measure_baseline.py"
    stub.write_text(textwrap.dedent("""\
import sys, os
args = sys.argv[1:]
state = os.environ.get("STUB_BASELINE_STATE", "measuring")
if args and args[0] == "check":
    print(state)
sys.exit(0)
"""), encoding="utf-8")

    proc = _run_maybe_start_release_train(
        tmp_path=tmp_path, repo=repo, data_dir=data_dir,
        scheduler_log=scheduler_log, stub_state="measuring",
        stub_script=stub,
        extra_env={"STUB_BASELINE_STATE": "measuring", "ILK_BATCH_AUDIT": "0"},
    )
    assert proc.returncode == 1

    log_content = scheduler_log.read_text(encoding="utf-8") if scheduler_log.exists() else ""
    assert "skip-baseline-measuring" in log_content, (
        f"expected 'skip-baseline-measuring' in log:\n{log_content}"
    )

    # No measure process spawned: only "check" in the stub record
    record_file = tmp_path / "stub_record.jsonl"
    if record_file.exists():
        records = [json.loads(l) for l in record_file.read_text().splitlines() if l.strip()]
        verbs = [r["verb"] for r in records]
        assert "measure" not in verbs, f"measure should not be spawned: {records}"


# ── AC-3: unmeasurable → skip-baseline-unmeasurable ─────────────────────────


def test_unmeasurable_state_skips(tmp_path: Path) -> None:
    """AC-3: ``check`` printing ``unmeasurable`` → return 1,
    ``skip-baseline-unmeasurable`` logged."""
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scheduler_log = _scheduler_log_path(tmp_path)

    _write_sentinel(_sentinel_path(tmp_path), state="all-shipped")

    stub = tmp_path / "measure_baseline.py"
    stub.write_text(textwrap.dedent("""\
import sys, os
args = sys.argv[1:]
state = os.environ.get("STUB_BASELINE_STATE", "unmeasurable")
if args and args[0] == "check":
    print(state)
sys.exit(0)
"""), encoding="utf-8")

    proc = _run_maybe_start_release_train(
        tmp_path=tmp_path, repo=repo, data_dir=data_dir,
        scheduler_log=scheduler_log, stub_state="unmeasurable",
        stub_script=stub,
        extra_env={"STUB_BASELINE_STATE": "unmeasurable", "ILK_BATCH_AUDIT": "0"},
    )
    assert proc.returncode == 1

    log_content = scheduler_log.read_text(encoding="utf-8") if scheduler_log.exists() else ""
    assert "skip-baseline-unmeasurable" in log_content, (
        f"expected 'skip-baseline-unmeasurable' in log:\n{log_content}"
    )


# ── AC-4 (control): present → falls through to permit pre-flight ────────────


def test_present_baseline_reaches_permit_check(tmp_path: Path) -> None:
    """AC-4 (control): ``check`` printing ``present`` → the function falls
    through to the permit pre-flight.  With hosts configured but no permits
    the function returns 1 and logs ``skip-permits``."""
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scheduler_log = _scheduler_log_path(tmp_path)

    _write_sentinel(_sentinel_path(tmp_path), state="all-shipped")

    # Configure hosts so the permit check finds missing permits.
    launch_cfg = repo / ".ilk-launch.json"
    launch_cfg.write_text(json.dumps({
        "ship": {"hosts": ["test-host"], "permit_mode": "owner"},
    }), encoding="utf-8")

    stub = tmp_path / "measure_baseline.py"
    stub.write_text(textwrap.dedent("""\
import sys, os
args = sys.argv[1:]
state = os.environ.get("STUB_BASELINE_STATE", "present")
if args and args[0] == "check":
    print(state)
sys.exit(0)
"""), encoding="utf-8")

    proc = _run_maybe_start_release_train(
        tmp_path=tmp_path, repo=repo, data_dir=data_dir,
        scheduler_log=scheduler_log, stub_state="present",
        stub_script=stub,
        extra_env={"STUB_BASELINE_STATE": "present", "ILK_BATCH_AUDIT": "0"},
    )
    assert proc.returncode == 1, (
        f"expected rc=1, got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    log_content = scheduler_log.read_text(encoding="utf-8") if scheduler_log.exists() else ""
    assert "skip-permits" in log_content, (
        f"expected 'skip-permits' (fell through baseline check) in log:\n{log_content}"
    )


# ── AC-5 (structural): check sits between audit and permit pre-flight ───────


def test_baseline_check_sits_between_audit_and_permit() -> None:
    """AC-5 (structural): the new check block in scheduler.sh sits after the
    line containing ``skip-audit-failed`` and before the line containing
    ``Pre-flight: check permits``."""
    content = _SCHEDULER_SH.read_text(encoding="utf-8")

    # Find the maybe_start_release_train function.
    in_func = False
    func_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("maybe_start_release_train()"):
            in_func = True
        if in_func:
            func_lines.append(line)
            if in_func and line == "}" and len(func_lines) > 2:
                break

    func_text = "\n".join(func_lines)

    # Find the three landmarks.
    audit_line = None
    baseline_line = None
    permit_line = None
    for i, line in enumerate(func_lines):
        if "skip-audit-failed" in line:
            audit_line = i
        if "baseline-measuring" in line or "_MEASURE_BASELINE_SCRIPT" in line:
            baseline_line = i
        if "Pre-flight: check permits" in line:
            permit_line = i

    assert audit_line is not None, (
        "could not find 'skip-audit-failed' in maybe_start_release_train"
    )
    assert baseline_line is not None, (
        "could not find baseline check in maybe_start_release_train"
    )
    assert permit_line is not None, (
        "could not find 'Pre-flight: check permits' in maybe_start_release_train"
    )
    assert audit_line < baseline_line < permit_line, (
        f"ordering wrong: audit={audit_line}, baseline={baseline_line}, "
        f"permit={permit_line}"
    )