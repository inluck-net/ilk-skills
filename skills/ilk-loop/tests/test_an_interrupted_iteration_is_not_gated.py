"""Pin that an interrupted iteration with 0 commits is not gated.

Part of sub-plan ``an-interrupted-iteration-is-not-gated`` (step 0).

Four acceptance criteria.  AC-1, AC-2, AC-4 are red-first pins
(``xfail(strict=True)``): they test behaviour that does not exist yet.
AC-3 is a control that must pass today.

The mechanism under test:
- ``_should_gate_iteration <completed> <total_new>`` returns 1 (do not gate)
  when ``completed`` is 0 and ``total_new`` is 0, and 0 otherwise.
- When it says no, the post-iteration local_checks block is skipped, and the
  runner logs one line explaining why.
- An amended iteration continues the run (no ``local_checks_failed_no_commits``).
- An amendment loop is bounded by the no-progress streak (3).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_REPO_ROOT = _TESTS.parent.parent
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

import sys as _sys
if str(_SCRIPTS) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS))
import ilk_paths


def _setup_project(tmp_path: Path) -> dict[str, Path]:
    """Set up a git project with external plans dir, matching the runner's expectations.

    Returns a dict with 'project', 'plans', 'data_home', 'key', 'bin' paths.
    """
    project = tmp_path / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(project), capture_output=True)
    (project / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(project), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(project),
                   capture_output=True, env={**os.environ,
                                             "GIT_AUTHOR_NAME": "test",
                                             "GIT_AUTHOR_EMAIL": "t@t",
                                             "GIT_COMMITTER_NAME": "test",
                                             "GIT_COMMITTER_EMAIL": "t@t"})

    data_home = tmp_path / "ilk-data"
    old = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
    finally:
        if old is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = old

    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


def _write_sub_plan(path: Path, *, current_step: int = 0,
                    status: str = "in-progress",
                    body_above_findings: str = "## Steps\n\n### Step 0\n\nDo thing.\n",
                    findings: str = "## Findings\n\n_(none)_\n",
                    local_checks: str = "") -> None:
    """Write a minimal sub-plan file.

    *local_checks* is raw YAML text inserted into the frontmatter block.
    When non-empty, it must include the ``local_checks:`` key and its list.
    """
    text = (
        f"---\n"
        f"plan: test-slug\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: 2\n"
        f"last_updated: 2026-10-03\n"
    )
    if local_checks:
        text += local_checks
    text += (
        f"---\n"
        f"\n"
        f"# Sub-plan: test\n"
        f"\n"
        f"{body_above_findings}"
        f"\n"
        f"{findings}"
    )
    path.write_text(text, encoding="utf-8")


def _write_master(path: Path, *, sub_plan_filename: str = "2026-10-03-test-slug.md",
                  body_above_tables: str = "## Strategy\n\nDo the thing.\n") -> None:
    """Write a minimal MASTER plan file."""
    text = (
        f"---\n"
        f"master_plan: 2026-10-03-execution\n"
        f"batch_date: 2026-10-03\n"
        f"status: active\n"
        f"total_tickets: 0\n"
        f"---\n"
        f"\n"
        f"# MASTER plan\n"
        f"\n"
        f"{body_above_tables}"
        f"\n"
        f"## Sub-plan registry\n"
        f"\n"
        f"| # | File | Status |\n"
        f"|---|---|---|\n"
        f"| 1 | {sub_plan_filename} | pending |\n"
    )
    path.write_text(text, encoding="utf-8")


def _source_runner_fn(fn_name: str, *args: str,
                      env: dict | None = None,
                      timeout: int = 10) -> subprocess.CompletedProcess:
    """Source the runner and call a shell function, returning the result.

    Uses ``ILK_DOTSOURCE_ONLY=1`` to exit after sourcing (no main loop).
    """
    runner_path = str(_RUNNER)
    call_args = " ".join(f'"{a}"' for a in args)
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{runner_path}'\n"
        f"{fn_name} {call_args}\n"
    )
    merged_env = dict(os.environ)
    merged_env["ILK_DOTSOURCE_ONLY"] = "1"
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=merged_env, timeout=timeout,
    )


def _run_runner(args: list[str], *, cwd: str, env: dict, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run the runner script, capturing output via temp files instead of pipes."""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".stdout", delete=False) as out_f, \
         tempfile.NamedTemporaryFile(mode="w+", suffix=".stderr", delete=False) as err_f:
        out_path, err_path = out_f.name, err_f.name
    try:
        with open(out_path, "w") as out_f, open(err_path, "w") as err_f:
            proc = subprocess.Popen(
                ["bash", *args],
                stdout=out_f, stderr=err_f,
                cwd=cwd, env=env,
            )
            proc.wait(timeout=timeout)
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            stdout = f.read()
        with open(err_path, "r", encoding="utf-8", errors="replace") as f:
            stderr = f.read()
        return subprocess.CompletedProcess(
            args=args, returncode=proc.returncode,
            stdout=stdout, stderr=stderr,
        )
    finally:
        os.unlink(out_path)
        os.unlink(err_path)


def _make_stub_claude(bin_dir: Path, *, script: str) -> Path:
    """Create a stub ``claude`` binary with custom behaviour."""
    stub = bin_dir / "claude"
    stub.write_text(script, encoding="utf-8")
    stub.chmod(0o755)
    return stub


# ── AC-1: _should_gate_iteration returns correct values ──────────────────────

class TestAC1ShouldGateIteration:
    """AC-1: ``_should_gate_iteration <completed>`` returns 1 (do not gate)
    when completed=0 (interrupted), and 0 otherwise.

    NOT gated on $total_new — the commit count is an input to TRAILER
    SCANNING, not a precondition for gating.  See the "NOT gated on $total_new"
    comment in the driver's local_checks guard.
    """

    def test_zero_returns_skip(self) -> None:
        """_should_gate_iteration 0 returns 1 (skip the gate)."""
        result = _source_runner_fn("_should_gate_iteration", "0")
        assert result.returncode == 0, f"sourcing failed: {result.stderr}"
        assert result.stdout.strip() == "1", (
            f"expected 1 (skip) for 0, got: {result.stdout.strip()}"
        )

    def test_one_returns_gate(self) -> None:
        """_should_gate_iteration 1 returns 0 (gate normally)."""
        result = _source_runner_fn("_should_gate_iteration", "1")
        assert result.returncode == 0, f"sourcing failed: {result.stderr}"
        assert result.stdout.strip() == "0", (
            f"expected 0 (gate) for 1, got: {result.stdout.strip()}"
        )


# ── AC-2: runtime — interrupted iteration skips gate ────────────────────────

class TestAC2RuntimeInterruptedSkipsGate:
    """AC-2 (runtime): the real driver, run with ``--run-local-checks
    --max-iterations 2``.  The fixture sub-plan's step gate is ``false``.
    A stub claude edits the sub-plan above ``## Findings`` on its first call,
    then sleeps; on its second call writes a marker and exits 0.

    Results: no "no commit trailers found; gating" line for iteration 1;
    the skipped line appears; the stub ran twice; the final state is not
    ``local_checks_failed_no_commits``.

    Red-first: the gate-skip logic does not exist yet.
    """

    @pytest.mark.timeout(120)
    def test_interrupted_iteration_skips_gate(self, tmp_path: Path) -> None:
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]

        # Sub-plan with step gate = false (no local_checks).
        _write_sub_plan(plans_dir / "2026-10-03-test-slug.md")
        _write_master(plans_dir / "MASTER-2026-10-03-execution-plan.md",
                      sub_plan_filename="2026-10-03-test-slug.md")

        marker_file = tmp_path / "iteration-marker.txt"

        # Stub claude: first call edits the plan (triggers amendment watcher),
        # second call writes a marker and exits 0.
        stub_script = (
            "#!/usr/bin/env bash\n"
            f"if [ ! -f '{marker_file}' ]; then\n"
            f"  echo 'first-call' > '{marker_file}'\n"
            # Edit the sub-plan above ## Findings to trigger amendment.
            # Use python for inode-safe write (sed -i on macOS creates new inode).
            f"  python3 -c \"import pathlib; p=pathlib.Path('{plans_dir}/2026-10-03-test-slug.md'); t=p.read_text().replace('Do thing.','AMENDED.'); p.write_text(t)\"\n"
            "  sleep 120\n"
            "else\n"
            f"  echo 'second-call' >> '{marker_file}'\n"
            "  exit 0\n"
            "fi\n"
        )
        _make_stub_claude(world["bin"], script=stub_script)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)
        env.pop("ILK_SKILL_HOME", None)

        result = _run_runner(
            [str(_RUNNER),
             "--project-path", str(world["project"]),
             "--max-iterations", "2",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            cwd=str(world["project"]), env=env, timeout=180,
        )

        combined = result.stdout + result.stderr

        # No "no commit trailers found; gating" for the interrupted iteration.
        assert "no commit trailers found; gating" not in combined, (
            "interrupted iteration should not trigger gate fallback"
        )

        # The skipped line appears.
        assert "skipped: iteration interrupted" in combined, (
            "expected gate-skip log line"
        )

        # The stub ran twice (marker file should have two lines).
        assert marker_file.exists(), "marker file not created"
        marker_text = marker_file.read_text()
        assert "first-call" in marker_text and "second-call" in marker_text, (
            f"stub did not run twice: {marker_text}"
        )

        # Final state is not local_checks_failed_no_commits.
        assert "local_checks_failed_no_commits" not in combined, (
            "interrupted iteration must not end with local_checks_failed_no_commits"
        )


# ── AC-3: control — completed iteration still gated ─────────────────────────

class TestAC3CompletedIterationStillGated:
    """AC-3 (control): a completed iteration (stub exits 0 at once, no commit)
    with a red gate is still gated and still ends
    ``local_checks_failed_no_commits``.

    This is the 2026-09-08 behaviour at runner lines 5894-5903.
    Must pass today (no xfail).
    """

    @pytest.mark.timeout(120)
    def test_completed_no_commit_still_gated(self, tmp_path: Path) -> None:
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]

        # Sub-plan with a red gate (local_checks that will fail).
        red_gate_yaml = (
            "local_checks:\n"
            "  - command: \"exit 1\"\n"
            "    timeout: 30\n"
        )
        _write_sub_plan(plans_dir / "2026-10-03-test-slug.md",
                        local_checks=red_gate_yaml)
        _write_master(plans_dir / "MASTER-2026-10-03-execution-plan.md",
                      sub_plan_filename="2026-10-03-test-slug.md")

        # Stub claude exits 0 immediately with no commits.
        stub_script = "#!/usr/bin/env bash\nexit 0\n"
        _make_stub_claude(world["bin"], script=stub_script)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)
        env.pop("ILK_SKILL_HOME", None)

        result = _run_runner(
            [str(_RUNNER),
             "--project-path", str(world["project"]),
             "--run-local-checks",
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            cwd=str(world["project"]), env=env, timeout=120,
        )

        combined = result.stdout + result.stderr

        # A completed iteration with 0 commits and a red gate should still
        # end with local_checks_failed_no_commits.
        assert "local_checks_failed_no_commits" in combined or "local_checks_failed" in combined, (
            f"expected gate failure in output, got: {combined[-500:]}"
        )


# ── AC-4: amendment loop bounded by no-progress ─────────────────────────────

class TestAC4AmendmentLoopBounded:
    """AC-4: an amendment on every iteration, with ``--max-iterations 5``:
    the run stops with ``no-progress`` by iteration 3 and never with
    ``local_checks_failed_no_commits``.

    Red-first: the amendment-continues logic does not exist yet.
    """

    @pytest.mark.timeout(180)
    def test_amendment_loop_stops_at_no_progress(self, tmp_path: Path) -> None:
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]

        _write_sub_plan(plans_dir / "2026-10-03-test-slug.md",
                        body_above_findings="## Steps\n\n### Step 0\n\nOriginal content.\n")
        _write_master(plans_dir / "MASTER-2026-10-03-execution-plan.md",
                      sub_plan_filename="2026-10-03-test-slug.md")

        # Stub claude: just sleeps (the watcher needs time to detect changes).
        _make_stub_claude(world["bin"], script="#!/usr/bin/env bash\nsleep 120\n")

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)
        env.pop("ILK_SKILL_HOME", None)

        # Edit the plan from a background thread to trigger the amendment watcher.
        # The watcher detects fingerprint changes and kills the stub.
        import threading

        iteration_count = [0]

        def amend_plan() -> None:
            """Edit the plan every ~10s to trigger the amendment watcher."""
            while True:
                time.sleep(10)
                iteration_count[0] += 1
                _write_sub_plan(
                    plans_dir / "2026-10-03-test-slug.md",
                    body_above_findings=(
                        f"## Steps\n\n### Step 0\n\n"
                        f"AMENDED iteration {iteration_count[0]}.\n"
                    ),
                )

        editor = threading.Thread(target=amend_plan, daemon=True)
        editor.start()

        result = _run_runner(
            [str(_RUNNER),
             "--project-path", str(world["project"]),
             "--max-iterations", "5",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            cwd=str(world["project"]), env=env, timeout=180,
        )

        combined = result.stdout + result.stderr

        # Should stop with no-progress (not local_checks_failed_no_commits).
        assert "no-progress" in combined, (
            f"expected no-progress stop, got: {combined[-500:]}"
        )
        assert "local_checks_failed_no_commits" not in combined, (
            "amendment loop must never end with local_checks_failed_no_commits"
        )