"""Tests: a shipped batch's release train starts even when its queue is empty.

Sub-plan: an-empty-queue-still-starts-the-train (step 0).
Covers AC-1..AC-6: ``offer_release_trains`` in scheduler.sh and
``train_candidates.py`` module.

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path`` with a sandbox
project whose sentinel is a success state.  The release-train script is
replaced by a stub that records its argv.  The scheduler is driven via
``ILK_DOTSOURCE_ONLY=1`` sourcing.
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


def _source_scheduler_fn(env: dict[str, str], fn_name: str, *args: str,
                         post_source: str = "") -> subprocess.CompletedProcess:
    """Source scheduler.sh with ILK_DOTSOURCE_ONLY=1 and call fn_name.

    This avoids running the full scheduler loop.  *post_source* is injected
    between the ``source`` and the function call so tests can override
    variables the script's top-level defaults would otherwise shadow.
    """
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source "{SCHEDULER}"
        {post_source}
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
        "ILK_BATCH_AUDIT": "0",  # test-only: skip audit, test train logic
    }
    env.pop("ILK_DATA_DIR", None)
    if extra:
        env.update(extra)
    return env


def _read_scheduler_log(data_home: Path) -> str:
    """Read the scheduler.log contents, or empty string if absent."""
    log_path = data_home / "logs" / "scheduler.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8")


def _write_sandbox_data(data_home: Path, key: str, tmp_path: Path) -> Path:
    """Scaffold a sandbox data dir with a shipped sentinel and git repo.

    Returns the project data directory.
    """
    project_dir = data_home / "projects" / key
    project_dir.mkdir(parents=True, exist_ok=True)

    # --- git repo (for resolve_repo_path and permit checks) ---
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, timeout=10)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo_dir, capture_output=True, timeout=10,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    subprocess.run(["git", "tag", "v0.0.0"], cwd=repo_dir, capture_output=True, timeout=10)

    # --- plans dir (all shipped) ---
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-10-06T00:00:00+08:00\n"
        "status: shipped\n"
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
        "| 1 | [2026-10-06-work.md](./2026-10-06-work.md) | shipped |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")
    subplan = (
        "---\n"
        "plan: work\n"
        "status: shipped\n"
        "current_step: 3\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-06\n"
        "---\n"
        "\n"
        "# work\n"
    )
    (plans_dir / "2026-10-06-work.md").write_text(subplan, encoding="utf-8")

    # --- runtime: last-launch.json (so resolve_repo_path finds the repo) ---
    runtime_launcher = project_dir / "runtime" / "launcher"
    runtime_launcher.mkdir(parents=True, exist_ok=True)
    last_launch = {"project_path": str(repo_dir)}
    (runtime_launcher / "last-launch.json").write_text(
        json.dumps(last_launch), encoding="utf-8"
    )

    # --- sentinel: shipped (success state) ---
    sentinel = {
        "state": "shipped",
        "run_id": "R1",
        "iterations": 3,
        "project_path": str(repo_dir),
    }
    (runtime_launcher / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    return project_dir


def _write_release_train_stub(tmp_path: Path) -> Path:
    """Write a stub release_train.py that records its argv and exits 0.

    Returns the path to the stub.
    """
    stub = tmp_path / "release_train_stub.py"
    stub.write_text(textwrap.dedent("""\
        import json, sys, pathlib
        argv_file = pathlib.Path(sys.argv[0]).with_name("release_train_argv.json")
        argv_file.write_text(json.dumps(sys.argv[1:]))
        sys.exit(0)
    """), encoding="utf-8")
    return stub


def _read_release_train_argv(stub_path: Path) -> list[str]:
    """Read the argv.json the release train stub recorded.

    The stub is launched via ``nohup ... &`` (backgrounded), so the file
    may not exist yet when the caller returns.  Wait briefly for it.
    """
    import time
    argv_file = stub_path.with_name("release_train_argv.json")
    for _ in range(20):
        if argv_file.exists():
            return json.loads(argv_file.read_text(encoding="utf-8"))
        time.sleep(0.1)
    return []


# ── AC-1: No permits → train starts ────────────────────────────────────────


def test_ac1_no_permits_starts_train(tmp_path: Path) -> None:
    """AC-1: no permits configured (no hosts) → one ``offer_release_trains '[]'``
    call writes ``runtime/release/R1.started``, the stub records
    ``run --project <repo>``, and scheduler.log has ``release-train-started``."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    _write_sandbox_data(data_home, "proj1", tmp_path)

    stub = _write_release_train_stub(tmp_path)
    env = _make_env(tmp_path, data_home)
    post_source = f"_RELEASE_TRAIN_SCRIPT='{stub}'"
    result = _source_scheduler_fn(env, "offer_release_trains", "[]",
                                  post_source=post_source)
    assert result.returncode == 0, (
        f"offer_release_trains failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Marker file created
    marker = data_home / "projects" / "proj1" / "runtime" / "release" / "R1.started"
    assert marker.is_file(), f"expected marker at {marker}"

    # Stub recorded the run command
    argv = _read_release_train_argv(stub)
    assert "run" in argv and "--project" in argv, (
        f"expected ['run', '--project', ...], got {argv}"
    )

    # scheduler.log has release-train-started
    log = _read_scheduler_log(data_home)
    assert "release-train-started" in log, (
        f"expected 'release-train-started' in log, got:\n{log}"
    )


# ── AC-2: Permits configured but missing → skip-permits, then start ────────


def test_ac2_permits_missing_then_present(tmp_path: Path) -> None:
    """AC-2: ``runtime/ship-config.json`` with hosts but no permit →
    ``skip-permits`` logged, no marker, stub not run.  After a valid permit
    is written, the next call starts it."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    project_dir = _write_sandbox_data(data_home, "proj1", tmp_path)
    repo_dir = tmp_path / "repo"

    # ship-config with one host
    ship_cfg = {"ship": {"hosts": ["h1"]}}
    (project_dir / "runtime" / "ship-config.json").write_text(
        json.dumps(ship_cfg), encoding="utf-8"
    )

    stub = _write_release_train_stub(tmp_path)
    env = _make_env(tmp_path, data_home)
    post_source = f"_RELEASE_TRAIN_SCRIPT='{stub}'"

    # --- First call: no permit → skip-permits ---
    result = _source_scheduler_fn(env, "offer_release_trains", "[]",
                                  post_source=post_source)
    assert result.returncode == 0, (
        f"offer_release_trains failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # No marker
    marker = data_home / "projects" / "proj1" / "runtime" / "release" / "R1.started"
    assert not marker.is_file(), f"marker should not exist yet: {marker}"

    # Stub not called
    assert _read_release_train_argv(stub) == [], "stub should not have been called"

    # skip-permits logged
    log = _read_scheduler_log(data_home)
    assert "skip-permits" in log, f"expected 'skip-permits' in log, got:\n{log}"

    # --- Write a valid permit ---
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=repo_dir, capture_output=True, text=True, timeout=10,
    ).stdout.strip()

    # Resolve the project key via the same canonical function the code uses
    from ilk_paths import project_key as _project_key
    proj_key = _project_key(repo_dir)

    permit = {
        "project": proj_key,
        "base_tag": tag,
        "candidate_head": head,
        "host": "h1",
        "issued_at": "2026-10-06T00:00:00+00:00",
        "expires_at": "2026-12-31T23:59:59+00:00",
        "consumed": False,
        "revoked": False,
    }
    permit_dir = project_dir / "runtime" / "permits"
    permit_dir.mkdir(parents=True, exist_ok=True)
    (permit_dir / "h1.json").write_text(json.dumps(permit), encoding="utf-8")

    # --- Second call: permit present → train starts ---
    result2 = _source_scheduler_fn(env, "offer_release_trains", "[]",
                                   post_source=post_source)
    assert result2.returncode == 0, (
        f"offer_release_trains failed:\nstdout: {result2.stdout}\nstderr: {result2.stderr}"
    )

    assert marker.is_file(), f"expected marker after permit: {marker}"
    argv = _read_release_train_argv(stub)
    assert "run" in argv and "--project" in argv, (
        f"expected ['run', '--project', ...], got {argv}"
    )


# ── AC-3: Second call after AC-1 starts nothing ────────────────────────────


def test_ac3_second_call_starts_nothing(tmp_path: Path) -> None:
    """AC-3: a second call after AC-1 starts nothing (marker present)."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    _write_sandbox_data(data_home, "proj1", tmp_path)

    stub = _write_release_train_stub(tmp_path)
    env = _make_env(tmp_path, data_home)
    post_source = f"_RELEASE_TRAIN_SCRIPT='{stub}'"

    # First call — starts the train
    r1 = _source_scheduler_fn(env, "offer_release_trains", "[]",
                              post_source=post_source)
    assert r1.returncode == 0, r1.stderr
    assert _read_release_train_argv(stub) != [], "stub should have been called"

    # Clear the stub's argv record
    (stub.with_name("release_train_argv.json")).unlink(missing_ok=True)

    # Second call — marker present → nothing started
    r2 = _source_scheduler_fn(env, "offer_release_trains", "[]",
                              post_source=post_source)
    assert r2.returncode == 0, r2.stderr

    argv2 = _read_release_train_argv(stub)
    assert argv2 == [], f"stub should not have been called again, got {argv2}"


# ── AC-4: Key in scan JSON is NOT offered ──────────────────────────────────


def test_ac4_key_in_scan_excluded(tmp_path: Path) -> None:
    """AC-4: a key passed in the scan JSON is NOT offered, even with a
    success sentinel."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    _write_sandbox_data(data_home, "proj1", tmp_path)

    stub = _write_release_train_stub(tmp_path)
    env = _make_env(tmp_path, data_home)
    post_source = f"_RELEASE_TRAIN_SCRIPT='{stub}'"

    # Pass proj1 in the scan JSON → should be excluded
    scan_json = json.dumps([{"key": "proj1", "path": "x"}])
    result = _source_scheduler_fn(env, "offer_release_trains", scan_json,
                                  post_source=post_source)
    assert result.returncode == 0, result.stderr

    # Marker must NOT exist
    marker = data_home / "projects" / "proj1" / "runtime" / "release" / "R1.started"
    assert not marker.is_file(), f"marker should not exist for excluded key: {marker}"

    # Stub not called
    assert _read_release_train_argv(stub) == [], (
        "stub should not have been called for excluded key"
    )


# ── AC-5 (control): Sentinel local_checks_failed → nothing started ─────────


def test_ac5_failed_sentinel_starts_nothing(tmp_path: Path) -> None:
    """AC-5 (control): sentinel ``local_checks_failed`` → nothing started."""
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    project_dir = _write_sandbox_data(data_home, "proj1", tmp_path)

    # Overwrite sentinel with a failure state
    sentinel = {
        "state": "local_checks_failed",
        "run_id": "R1",
        "iterations": 3,
    }
    (project_dir / "runtime" / "launcher" / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    stub = _write_release_train_stub(tmp_path)
    env = _make_env(tmp_path, data_home)
    post_source = f"_RELEASE_TRAIN_SCRIPT='{stub}'"

    result = _source_scheduler_fn(env, "offer_release_trains", "[]",
                                  post_source=post_source)
    assert result.returncode == 0, result.stderr

    marker = data_home / "projects" / "proj1" / "runtime" / "release" / "R1.started"
    assert not marker.is_file(), f"marker should not exist for failed sentinel: {marker}"
    assert _read_release_train_argv(stub) == [], (
        "stub should not have been called for failed sentinel"
    )


# ── AC-6: Call site ordering in scheduler.sh ────────────────────────────────


def test_ac6_call_site_follows_autoplan(tmp_path: Path) -> None:
    """AC-6: the call site follows ``maybe_tick_autoplan || true`` and
    precedes the ``"$count" == "0"`` idle test (assert by line numbers)."""
    lines = SCHEDULER.read_text(encoding="utf-8").splitlines()

    # Find the line numbers
    tick_line = None
    offer_line = None
    count_line = None
    for i, line in enumerate(lines, 1):
        if "maybe_tick_autoplan || true" in line:
            tick_line = i
        if "offer_release_trains" in line and not line.strip().startswith("#"):
            offer_line = i
        if '"$count" == "0"' in line:
            count_line = i

    assert tick_line is not None, "could not find 'maybe_tick_autoplan || true'"
    assert offer_line is not None, "could not find 'offer_release_trains' call"
    assert count_line is not None, "could not find '\"$count\" == \"0\"'"

    assert tick_line < offer_line < count_line, (
        f"ordering violation: tick={tick_line}, offer={offer_line}, count={count_line}"
    )