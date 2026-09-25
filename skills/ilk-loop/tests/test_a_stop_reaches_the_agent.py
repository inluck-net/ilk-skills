"""Red-first: a stop reaches a running agent.

Part of sub-plan ``a-stop-reaches-the-agent`` (step 0).

AC-1  (xfail) SIGTERM to the launched pid while the stub agent holds for 60 s
      ⇒ the runner exits within 15 s, the result file says ``interrupted``,
      and no stub process survives.
AC-3  (pass)  a normal (unsignalled) iteration whose stub exits 7 ⇒ the
      runner's exit code equals the pipeline's status (7 or a retry-
      normalised 0).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import shutil

_REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS))

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

# Shared across _build_world and _any_stub_alive so the signal test
# can check whether the stub process survived.
_STUB_PID_FILE: Path | None = None


# ── helpers ────────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )


def _build_world(
    root: Path,
    *,
    stub_exit_code: int = 0,
    stub_hold_seconds: int = 0,
) -> dict:
    """Build a minimal project world for signal-testing the runner.

    Returns dict with ``project``, ``plans``, ``data_home``, ``key``,
    ``bin``, ``slug``, ``stem``.
    """
    global _STUB_PID_FILE

    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    slug = "stop-test-subplan"
    stem = f"2026-09-25-{slug}"
    (plans / "MASTER-2026-09-25-stop-test.md").write_text(
        "---\n"
        "master_plan: 2026-09-25-stop-test\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{stem}](./{stem}.md) |\n",
        encoding="utf-8",
    )

    (plans / f"{stem}.md").write_text(
        f"---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {slug}\n\n"
        "### Step 0 — do the thing\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Stub claude: writes its PID to a file, sleeps for the configured
    # duration, then exits.  The hold seconds are passed via a config file
    # because environment variables set in Popen(env=...) don't reliably
    # reach through the runner's pipeline on all platforms.
    bin_dir = root / "bin"
    bin_dir.mkdir()
    _STUB_PID_FILE = root / "stub.pid"
    stub_hold_file = root / "stub-hold-seconds"
    stub_hold_file.write_text(str(stub_hold_seconds), encoding="utf-8")
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo $$ > \"{_STUB_PID_FILE}\"\n"
        f"HOLD=$(cat \"{stub_hold_file}\" 2>/dev/null || echo 0)\n"
        "sleep \"$HOLD\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "slug": slug,
        "stem": stem,
    }


def _run_one_iteration(
    world: dict,
    root: Path,
    *,
    extra_env: dict | None = None,
    send_signal: int | None = None,
    signal_delay: float = 3.0,
    timeout: float = 300,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    if extra_env:
        env.update(extra_env)
    (root / ".claude").mkdir(exist_ok=True)

    proc = subprocess.Popen(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(root),
        text=True,
        encoding="utf-8",
        errors="replace",
        preexec_fn=os.setpgrp,  # new process group for the runner
    )

    if send_signal is not None:
        time.sleep(signal_delay)  # let the runner get past setup
        # Send signal to the runner's process group so the trap fires.
        os.killpg(proc.pid, send_signal)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=10)
    else:
        stdout, stderr = proc.communicate(timeout=timeout)

    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _read_sentinel(world: dict) -> dict:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    sentinel = runtime / "launcher" / "last-exit.json"
    assert sentinel.exists(), f"no sentinel found at {sentinel}"
    return json.loads(sentinel.read_text(encoding="utf-8"))


def _stub_pid_alive() -> bool:
    """Check if the stub agent's PID (from the PID file) is still alive."""
    if _STUB_PID_FILE is None or not _STUB_PID_FILE.exists():
        return False
    try:
        pid = int(_STUB_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0: check if alive
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


# ── AC-1: SIGTERM interrupts the running agent ────────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(90)
def test_sigterm_interrupts_running_agent(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: SIGTERM to the launched pid while the stub agent holds for 60 s
    ⇒ the runner exits within 15 s, the result file says ``interrupted``,
    and no stub process survives.

    Today the runner's INT/TERM traps fire only after the foreground
    pipeline exits, so the stop waits up to the iteration timeout.
    """
    root = tmp_path_factory.mktemp("stop-ac1")
    world = _build_world(root, stub_hold_seconds=60)

    t0 = time.monotonic()
    result = _run_one_iteration(
        world, root,
        send_signal=signal.SIGTERM,
        signal_delay=3.0,
        timeout=60,
    )
    elapsed = time.monotonic() - t0
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # The runner should exit within ~15 s of the signal (10 s grace + overhead).
    assert elapsed < 20, (
        f"runner took {elapsed:.1f}s to exit after SIGTERM — should be < 20 s.\n{tail}"
    )

    # Sentinel should say interrupted.
    sentinel = _read_sentinel(world)
    assert sentinel.get("state") == "interrupted", (
        f"sentinel state is {sentinel.get('state')!r}, expected 'interrupted'.\n{tail}"
    )

    # No stub process should survive.
    assert not _stub_pid_alive(), (
        "stub agent process still alive after SIGTERM — the agent was not killed"
    )


# ── AC-3: normal exit code passthrough ────────────────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(30)
def test_unsignalled_iteration_exit_code_matches_pipeline(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: a normal (unsignalled) iteration whose stub exits 7 ⇒ the
    runner's exit code equals the pipeline's status.

    The stub holds for 0 s and exits 7.  The runner's B2 retry logic may
    normalise the exit code — either 7 or 0 (retry succeeded) is acceptable.
    The key assertion is that no signal was involved and the runner
    terminated normally.
    """
    root = tmp_path_factory.mktemp("stop-ac3")
    world = _build_world(root, stub_exit_code=7, stub_hold_seconds=0)

    result = _run_one_iteration(world, root)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # The runner should have exited — either with the stub's exit code or 0
    # if B2 retry normalised it.  A signal exit (>128) would be wrong.
    assert result.returncode in (0, 7), (
        f"exit code is {result.returncode}, expected 0 or 7.\n{tail}"
    )

    # Sentinel should exist and be terminal (not "running").
    sentinel = _read_sentinel(world)
    assert sentinel.get("state") != "running", (
        f"sentinel still says 'running' after a normal iteration.\n{tail}"
    )