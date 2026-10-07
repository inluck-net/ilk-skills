"""Pin the golden batch fixture, its stub, and the harness refusals.

AC-1..AC-3, ``xfail(strict=True)`` until step 1 adds the fixture and
harness.  Each test imports inside the test body so the module loads
even when the fixture does not yet exist.

AC-1: the fixture parses — ``plan_status.extract_subplan_files`` returns
      the four sub-plans in order; ``expected.json`` names all four.
AC-2: the stub, run by hand on a tmp copy, makes one commit carrying
      ``[plan:golden-real#step-0]`` and prints a stream-json ``init`` line.
AC-3: ``golden_batch.run`` refuses (exit 2) when ``PATH`` has no
      ``gtimeout``, and when the resolved ``ILK_SKILL_HOME`` would be
      inside this repo.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

pytestmark = pytest.mark.timeout(120)


# ── AC-1: fixture parses ─────────────────────────────────────────────────────

def test_fixture_parses_four_subplans_in_order() -> None:
    """AC-1: extract_subplan_files returns golden-real, golden-inert,
    golden-red, golden-verify in order; expected.json names all four."""
    sys.path.insert(0, str(_SCRIPTS))
    from plan_status import extract_subplan_files

    master = (_FIXTURES / "plans" / "MASTER-golden-execution-plan.md"
              ).read_text(encoding="utf-8")
    slugs = extract_subplan_files(master)
    assert slugs == [
        "2026-10-04-golden-real.md",
        "2026-10-04-golden-inert.md",
        "2026-10-04-golden-red.md",
        "2026-10-04-golden-verify.md",
    ], f"extract_subplan_files returned {slugs!r}"

    expected = json.loads(
        (_FIXTURES / "expected.json").read_text(encoding="utf-8")
    )
    for slug in ["golden-real", "golden-inert", "golden-red", "golden-verify"]:
        assert slug in expected, f"{slug!r} missing from expected.json"


# ── AC-2: stub makes a trailered commit ──────────────────────────────────────

def test_stub_makes_trailered_commit_and_prints_init(tmp_path: Path) -> None:
    """AC-2: run the stub by hand with ILK_ITERATION_SUBPLAN=golden-real
    and current_step: 0; it must commit with [plan:golden-real#step-0]
    and print a stream-json init line."""
    # Copy the fixture project to tmp.
    project = tmp_path / "project"
    shutil.copytree(_FIXTURES / "project", project)

    # Initialize as a git repo.
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "init", "-q", str(project)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "add", "-A"],
        cwd=str(project), check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=str(project), check=True, capture_output=True, text=True,
    )

    # Copy the stub.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    shutil.copy2(_FIXTURES / "stub-claude", stub)
    stub.chmod(0o755)

    # Set up plans dir with golden-real at current_step: 0.
    data_home = tmp_path / ".ilk-data"
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)
    shutil.copytree(_FIXTURES / "plans", plans, dirs_exist_ok=True)

    # Run the stub.
    env = {
        **os.environ,
        "ILK_ITERATION_SUBPLAN": "golden-real",
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    r = subprocess.run(
        [str(stub)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=str(project),
    )
    assert r.returncode == 0, f"stub exited {r.returncode}.\n{r.stderr[-500:]}"

    # Check the commit.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        capture_output=True, text=True, cwd=str(project),
    )
    assert "[plan:golden-real#step-0]" in log.stdout, (
        f"commit trailer missing.\nstdout={log.stdout}\nstderr={log.stderr}"
    )

    # Check the stream-json init line.
    assert '"type":"system"' in r.stdout and '"subtype":"init"' in r.stdout, (
        f"stream-json init line missing.\nstdout={r.stdout[-500:]}"
    )


# ── AC-3: harness refusals ───────────────────────────────────────────────────

def test_golden_batch_refuses_without_gtimeout(tmp_path: Path) -> None:
    """AC-3a: golden_batch.run exits 2 when PATH has no gtimeout."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import golden_batch

    # Remove gtimeout from PATH.
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
        with pytest.raises(SystemExit) as exc_info:
            golden_batch.run(out=tmp_path / "out.json")
    assert exc_info.value.code == 2, (
        f"expected exit 2 for missing gtimeout, got {exc_info.value.code}"
    )


def test_golden_batch_refuses_when_skill_home_in_repo(tmp_path: Path) -> None:
    """AC-3b: _check_refusals exits 2 when ILK_SKILL_HOME resolves
    inside a live git repo (one with .git).

    The test owns its own git-shaped fixture so it is hermetic: it
    passes identically from a ``git archive`` copy (teeth) and from
    the live clone.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import golden_batch

    # Build a self-contained "repo" with a .git marker and a skills dir.
    fake_repo = tmp_path / "fake-repo"
    (fake_repo / ".git").mkdir(parents=True)
    (fake_repo / "skills").mkdir(parents=True)

    from unittest.mock import patch as _patch
    with _patch.object(golden_batch, "_REPO", fake_repo):
        with _patch.dict(os.environ, {"ILK_SKILL_HOME": str(fake_repo / "skills")}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                golden_batch._check_refusals()
    assert exc_info.value.code == 2, (
        f"expected exit 2 for skill-home-in-repo, got {exc_info.value.code}"
    )

# ── 630af2e3: the harness never leaves a runner behind ───────────────────────
#
# 2026-10-07 04:44: golden_batch.py (pid 41478) was reparented to pid 1 and
# kept two fixture runners alive for 3+ min; the release-permit writer refused
# "fleet not quiet" until they were reaped by hand.

_FAKE_RUNNER = """#!/bin/bash
sleep 300 &
echo "$$ $!" > "$1"
wait
"""

_DRIVER = """
import sys
sys.path.insert(0, {inv!r})
import golden_batch
golden_batch._run_runner(["bash", {runner!r}, {pids!r}], env=None, cwd=None, timeout=600)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _start(tmp_path: Path, launcher: list[str]) -> tuple[subprocess.Popen, list[int]]:
    runner = tmp_path / "fake_runner.sh"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    pids_file = tmp_path / "pids.txt"
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER.format(inv=str(Path(__file__).resolve().parent),
                                     runner=str(runner), pids=str(pids_file)),
                      encoding="utf-8")
    proc = subprocess.Popen(launcher + [sys.executable, str(driver)],
                            start_new_session=True)
    for _ in range(100):
        if pids_file.exists() and len(pids_file.read_text().split()) == 2:
            break
        import time as _t
        _t.sleep(0.1)
    pids = [int(x) for x in pids_file.read_text().split()]
    assert all(_alive(p) for p in pids)
    return proc, pids


def _wait_dead(pids: list[int], seconds: float = 10.0) -> list[int]:
    import time as _t
    deadline = _t.monotonic() + seconds
    while _t.monotonic() < deadline:
        live = [p for p in pids if _alive(p)]
        if not live:
            return []
        _t.sleep(0.2)
    return [p for p in pids if _alive(p)]


def test_sigterm_to_the_harness_kills_its_runner(tmp_path: Path) -> None:
    """SIGTERM to the harness kills the runner and the runner's children."""
    import signal
    proc, pids = _start(tmp_path, [])
    try:
        os.kill(proc.pid, signal.SIGTERM)
        assert _wait_dead(pids) == []
    finally:
        for p in pids:
            if _alive(p):
                os.kill(p, signal.SIGKILL)
        proc.kill()


def test_an_orphaned_harness_kills_its_runner(tmp_path: Path) -> None:
    """When the harness's parent dies, it kills its runner and exits."""
    import signal
    # bash starts the driver in the background and exits after 2 s, so the
    # driver is reparented mid-run, as happened on 2026-10-07.
    proc, pids = _start(tmp_path, ["bash", "-c", '"$@" & sleep 2', "_"])
    try:
        assert _wait_dead(pids, seconds=15.0) == []
    finally:
        for p in pids:
            if _alive(p):
                os.kill(p, signal.SIGKILL)
        proc.kill()
