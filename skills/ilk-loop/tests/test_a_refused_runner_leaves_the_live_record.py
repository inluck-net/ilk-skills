"""Red-first: a refused runner leaves the live record alone.

Part of sub-plan ``a-refused-runner-leaves-the-live-record`` (step 0).

AC-1  (xfail) runner A holds the lock, runner B is refused.
      last-exit.json still has A's running state, last-refusal.json has
      B's lock_held, and B's exit code is 3.
AC-2  (xfail) a runner whose own child exits 3 (not a lock refusal) ⇒
      the wrapper exits 3, and NO lock_held record or result file is
      written.
AC-3  (xfail) a runner exiting 1 ⇒ the wrapper exits 1 without
      printing "lock helper failed".
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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


# ── helpers ────────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )


def _build_world(root: Path, *, result_file: str | None = None) -> dict:
    """Build a minimal project world for lock-wrapper tests."""
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    with __import__("unittest.mock").patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Master frontmatter.
    fm_lines = [
        "---",
        "master_plan: 2026-09-25-refused-test",
        "batch_date: 2026-09-25",
        "status: active",
        "supervised_only: false",
    ]
    if result_file is not None:
        fm_lines.append(f"result_file: {result_file}")
    fm_lines.append("---")
    fm_text = "\n".join(fm_lines)

    slug = "refused-test-subplan"
    stem = f"2026-09-25-{slug}"
    (plans / "MASTER-2026-09-25-refused-test.md").write_text(
        f"{fm_text}\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{stem}](./{stem}.md) |\n",
        encoding="utf-8",
    )

    (plans / f"{stem}.md").write_text(
        "---\n"
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

    # Stub claude: returns at once (no work needed for lock tests).
    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "sleep \"${STUB_HOLD_SECONDS:-1}\"\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result_path = Path(result_file) if result_file else None
    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "result_path": result_path,
        "slug": slug,
        "stem": stem,
    }


def _make_env(world: dict, root: Path, *, extra_env: dict | None = None) -> dict:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    (root / ".claude").mkdir(exist_ok=True)
    if extra_env:
        env.update(extra_env)
    return env


def _run_runner(
    world: dict,
    root: Path,
    *,
    extra_env: dict | None = None,
    max_iterations: int = 1,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    env = _make_env(world, root, extra_env=extra_env)
    return subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", str(max_iterations),
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(root),
        timeout=timeout,
    )


def _read_sentinel(world: dict) -> dict:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    sentinel = runtime / "launcher" / "last-exit.json"
    assert sentinel.exists(), f"no sentinel found at {sentinel}"
    return json.loads(sentinel.read_text(encoding="utf-8"))


def _read_refusal(world: dict) -> dict | None:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    refusal = runtime / "launcher" / "last-refusal.json"
    if not refusal.exists():
        return None
    return json.loads(refusal.read_text(encoding="utf-8"))


def _sentinel_path(world: dict) -> Path:
    return world["data_home"] / "projects" / world["key"] / "runtime" / "launcher" / "last-exit.json"


def _refusal_path(world: dict) -> Path:
    return world["data_home"] / "projects" / world["key"] / "runtime" / "launcher" / "last-refusal.json"


# ── AC-1: refused runner leaves last-exit alone, writes last-refusal ───────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first")
def test_refused_runner_leaves_live_record_and_writes_refusal(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: runner A holds the lock, runner B is refused.

    After B runs:
    - last-exit.json still has A's running state (or A's terminal state)
    - last-refusal.json has B's lock_held
    - B's exit code is 3
    """
    root = tmp_path_factory.mktemp("refused-ac1")
    world = _build_world(root)

    # Start runner A in the background (holds the lock).
    env_a = _make_env(world, root, extra_env={"STUB_HOLD_SECONDS": "30"})
    proc_a = subprocess.Popen(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env_a,
        cwd=str(root),
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    try:
        # Wait for A to acquire the lock and start.
        time.sleep(5)
        assert proc_a.poll() is None, "runner A exited before B could start"

        # Run runner B — should be refused.
        result_b = _run_runner(world, root, timeout=60)
        tail = "\n".join((result_b.stdout + result_b.stderr).splitlines()[-30:])

        # B exits with code 3 (lock_held).
        assert result_b.returncode == 3, (
            f"runner B exit code is {result_b.returncode}, expected 3.\n{tail}"
        )

        # last-exit.json should still reflect runner A (running state),
        # NOT runner B's lock_held.
        sentinel = _read_sentinel(world)
        assert sentinel.get("state") != "lock_held", (
            f"last-exit.json was overwritten by the refused runner: {sentinel}"
        )

        # last-refusal.json should have B's lock_held state.
        refusal = _read_refusal(world)
        assert refusal is not None, (
            f"last-refusal.json not written.\n{tail}"
        )
        assert refusal.get("state") == "lock_held", (
            f"last-refusal.json state is {refusal.get('state')!r}, expected 'lock_held'"
        )

    finally:
        # Clean up runner A.
        proc_a.terminate()
        try:
            proc_a.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc_a.kill()
            proc_a.wait(timeout=5)


# ── AC-2: runner child exits 3 (not lock refusal) ─────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first")
def test_runner_child_exit_3_is_not_misread_as_lock_held(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: a runner whose own child exits 3 (a stub that makes the loop
    exit 3, or a fixture calling the wrapper with a child exiting 3) ⇒ the
    wrapper exits 3, and NO lock_held record or result file is written.

    This tests the ambiguity: exit code 3 from the lock helper means
    lock_held, but exit code 3 from the runner itself is just a runner
    exit. The refusal marker disambiguates.
    """
    root = tmp_path_factory.mktemp("refused-ac2")
    world = _build_world(root)

    # Create a stub claude that exits 3 directly (simulating a runner
    # whose own logic exits 3).
    stub = world["bin"] / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "exit 3\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = _run_runner(world, root, timeout=60)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # The wrapper should exit 3 (runner's exit code passes through).
    assert result.returncode == 3, (
        f"wrapper exit code is {result.returncode}, expected 3.\n{tail}"
    )

    # But NO lock_held record should be written (no refusal marker exists).
    sentinel_path = _sentinel_path(world)
    if sentinel_path.exists():
        sentinel = _read_sentinel(world)
        assert sentinel.get("state") != "lock_held", (
            f"last-exit.json has lock_held despite no refusal marker: {sentinel}"
        )

    # No last-refusal.json should be written.
    refusal_path = _refusal_path(world)
    assert not refusal_path.exists(), (
        f"last-refusal.json was written despite no lock refusal: {refusal_path}"
    )

    # No result file should be written.
    if world["result_path"]:
        assert not world["result_path"].exists(), (
            f"result file was written despite no lock refusal: {world['result_path']}"
        )

    # The wrapper should NOT print "lock helper failed".
    assert "lock helper failed" not in (result.stdout + result.stderr).lower(), (
        f"'lock helper failed' printed for a runner exit 3.\n{tail}"
    )


# ── AC-3: runner exiting 1 passes through without "lock helper failed" ────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first")
def test_runner_exit_1_passes_through_without_lock_helper_message(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: a runner exiting 1 ⇒ the wrapper exits 1 without printing
    "lock helper failed".
    """
    root = tmp_path_factory.mktemp("refused-ac3")
    world = _build_world(root)

    # Create a stub claude that exits 1.
    stub = world["bin"] / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = _run_runner(world, root, timeout=60)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # The wrapper should exit 1 (runner's exit code passes through).
    assert result.returncode == 1, (
        f"wrapper exit code is {result.returncode}, expected 1.\n{tail}"
    )

    # The wrapper should NOT print "lock helper failed".
    assert "lock helper failed" not in (result.stdout + result.stderr).lower(), (
        f"'lock helper failed' printed for a runner exit 1.\n{tail}"
    )