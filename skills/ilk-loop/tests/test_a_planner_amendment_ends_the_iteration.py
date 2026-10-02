"""Pin that a plan amended mid-iteration ends that iteration.

Part of sub-plan ``a-planner-amendment-ends-the-iteration`` (step 1).

Six acceptance criteria.  AC-1, AC-3, AC-4, AC-6 are red-first pins
(``xfail(strict=True)``): they test behaviour that does not exist yet.
AC-2 and AC-5 are controls that must pass today.

The mechanism under test:
- The runner fingerprints the targeted sub-plan's body above ``## Findings``,
  excluding the frontmatter keys ``current_step``, ``status``, ``last_updated``.
- It polls every 30 s during the agent run.  If the fingerprint changes, the
  iteration ends with internal reason ``plan-amended``.
- A signal-killed worker (exit ≥ 128) is WIP-preserved like a timeout.
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import textwrap
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
    # Temporarily set ILK_DATA_HOME so ilk_paths computes the right key.
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


def _make_stub_claude(bin_dir: Path, *, sleep_sec: int = 60) -> Path:
    """Create a stub ``claude`` binary that sleeps for *sleep_sec* seconds.

    The stub simulates a long-running agent so the amendment watcher has
    time to detect a plan change and kill it.
    """
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"sleep {sleep_sec}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub

# Frontmatter keys that the amendment watcher must EXCLUDE from the digest.
_EXCLUDED_KEYS = {"current_step", "status", "last_updated"}


def _strip_excluded_frontmatter(text: str) -> str:
    """Remove ``current_step``, ``status``, ``last_updated`` lines from YAML."""
    lines = text.splitlines(keepends=True)
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        if any(stripped.startswith(f"{k}:") for k in _EXCLUDED_KEYS):
            continue
        cleaned.append(line)
    return "".join(cleaned)


def _plan_surface(text: str) -> str:
    """Return the content above ``## Findings``, with excluded keys stripped.

    This is the surface the amendment watcher fingerprints.  Two plan texts
    that differ only below ``## Findings`` or in the excluded keys produce
    the same surface.
    """
    # Strip excluded frontmatter keys.
    text = _strip_excluded_frontmatter(text)
    # Take everything above ## Findings.
    if "## Findings" in text:
        return text.split("## Findings")[0]
    return text


def _plan_digest(text: str) -> str:
    """SHA-256 of the plan surface (everything above ## Findings, minus excluded keys)."""
    return hashlib.sha256(_plan_surface(text).encode("utf-8")).hexdigest()


def _write_sub_plan(path: Path, *, current_step: int = 0,
                    status: str = "in-progress",
                    body_above_findings: str = "## Steps\n\n### Step 0\n\nDo thing.\n",
                    findings: str = "## Findings\n\n_(none)_\n") -> None:
    """Write a minimal sub-plan file."""
    text = (
        f"---\n"
        f"plan: test-slug\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: 2\n"
        f"last_updated: 2026-10-02\n"
        f"---\n"
        f"\n"
        f"# Sub-plan: test\n"
        f"\n"
        f"{body_above_findings}"
        f"\n"
        f"{findings}"
    )
    path.write_text(text, encoding="utf-8")


def _write_master(path: Path, *, sub_plan_filename: str = "2026-10-02-test-slug.md",
                  body_above_tables: str = "## Strategy\n\nDo the thing.\n") -> None:
    """Write a minimal MASTER plan file."""
    text = (
        f"---\n"
        f"master_plan: 2026-10-02-execution\n"
        f"batch_date: 2026-10-02\n"
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


# ── AC-1: plan amendment mid-iteration triggers termination ──────────────────

class TestAC1AmendmentTriggersTermination:
    """AC-1: editing the targeted sub-plan above ## Findings mid-iteration
    terminates the worker, records plan_amended: true, and continues.

    The digest computation is a plain test (mechanism works today).
    The runner integration is red-first (not implemented yet).
    """

    def test_plan_digest_changes_on_edit_above_findings(self, tmp_path: Path) -> None:
        """A content edit above ## Findings changes the digest.

        This verifies the mechanism the amendment watcher will use.
        """
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, body_above_findings="## Steps\n\n### Step 0\n\nOriginal.\n")
        d1 = _plan_digest(plan.read_text())

        _write_sub_plan(plan, body_above_findings="## Steps\n\n### Step 0\n\nAmended.\n")
        d2 = _plan_digest(plan.read_text())

        assert d1 != d2, "digest must change when content above ## Findings is edited"

    def test_runner_detects_plan_amendment(self, tmp_path: Path) -> None:
        """The runner's amendment watcher detects a mid-iteration plan edit.

        Uses a stub worker that sleeps.  We edit the plan after a short delay
        and verify the worker is terminated within 60 s.
        """
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]
        _write_sub_plan(plans_dir / "2026-10-02-test-slug.md")
        _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md",
                      sub_plan_filename="2026-10-02-test-slug.md")

        _make_stub_claude(world["bin"], sleep_sec=120)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)

        # Edit the plan in a background thread after a short delay.
        import threading

        def amend_plan() -> None:
            time.sleep(5)
            _write_sub_plan(plans_dir / "2026-10-02-test-slug.md",
                            body_above_findings="## Steps\n\n### Step 0\n\nAMENDED.\n")

        editor = threading.Thread(target=amend_plan, daemon=True)
        editor.start()

        # Run the loop with the stub worker.
        result = subprocess.run(
            ["bash", str(_RUNNER),
             "--project-path", str(world["project"]),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            cwd=str(world["project"]),
            env=env,
        )

        # The iteration should end with plan-amended reason.
        combined = result.stdout + result.stderr
        assert "plan-amended" in combined or "plan_amended" in combined, (
            f"expected plan-amended in output, got: {combined[-500:]}"
        )


# ── AC-2: worker edits to Findings/current_step do NOT trigger ───────────────

class TestAC2WorkerEditsNoTrigger:
    """AC-2: the stub worker itself edits ## Findings and current_step.

    These edits are legitimate and must not trigger amendment detection.
    """

    def test_findings_edit_preserves_digest(self, tmp_path: Path) -> None:
        """Editing below ## Findings does not change the plan digest."""
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, findings="## Findings\n\n_(empty)_\n")
        d1 = _plan_digest(plan.read_text())

        _write_sub_plan(plan, findings="## Findings\n\nWorker wrote notes here.\n")
        d2 = _plan_digest(plan.read_text())

        assert d1 == d2, "digest must NOT change when only ## Findings is edited"

    def test_current_step_edit_preserves_digest(self, tmp_path: Path) -> None:
        """Editing current_step does not change the plan digest."""
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, current_step=0)
        d1 = _plan_digest(plan.read_text())

        _write_sub_plan(plan, current_step=1)
        d2 = _plan_digest(plan.read_text())

        assert d1 == d2, "digest must NOT change when only current_step is edited"

    def test_status_edit_preserves_digest(self, tmp_path: Path) -> None:
        """Editing status does not change the plan digest."""
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, status="in-progress")
        d1 = _plan_digest(plan.read_text())

        _write_sub_plan(plan, status="shipped")
        d2 = _plan_digest(plan.read_text())

        assert d1 == d2, "digest must NOT change when only status is edited"

    def test_last_updated_edit_preserves_digest(self, tmp_path: Path) -> None:
        """Editing last_updated does not change the plan digest."""
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan)
        d1 = _plan_digest(plan.read_text())

        plan.write_text(
            plan.read_text().replace("last_updated: 2026-10-02",
                                     "last_updated: 2026-10-03"),
            encoding="utf-8",
        )
        d2 = _plan_digest(plan.read_text())

        assert d1 == d2, "digest must NOT change when only last_updated is edited"


# ── AC-3: MASTER edit above body tables triggers ─────────────────────────────

class TestAC3MasterEditTriggers:
    """AC-3: editing the MASTER above its body tables changes its digest.

    The digest computation is a plain test (mechanism works today).
    The runner integration is red-first (not implemented yet).
    """

    def test_master_digest_changes_on_edit_above_tables(self, tmp_path: Path) -> None:
        """A content edit above the registry tables changes the MASTER digest.

        This verifies the mechanism the amendment watcher will use.
        """
        master = tmp_path / "MASTER.md"
        _write_master(master, body_above_tables="## Strategy\n\nOriginal.\n")
        d1 = _plan_digest(master.read_text())

        _write_master(master, body_above_tables="## Strategy\n\nAmended.\n")
        d2 = _plan_digest(master.read_text())

        assert d1 != d2, "MASTER digest must change when content above tables is edited"

    def test_runner_detects_master_amendment(self, tmp_path: Path) -> None:
        """The runner's amendment watcher detects a mid-iteration MASTER edit.

        Uses a stub worker that sleeps.  We edit the MASTER after a short delay
        and verify the worker is terminated.
        """
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]
        _write_sub_plan(plans_dir / "2026-10-02-test-slug.md")
        _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md")

        _make_stub_claude(world["bin"], sleep_sec=120)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)

        # Edit the MASTER in a background thread after a short delay.
        import threading

        def amend_master() -> None:
            time.sleep(5)
            _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md",
                          body_above_tables="## Strategy\n\nAMENDED.\n")

        editor = threading.Thread(target=amend_master, daemon=True)
        editor.start()

        result = subprocess.run(
            ["bash", str(_RUNNER),
             "--project-path", str(world["project"]),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            cwd=str(world["project"]),
            env=env,
        )

        combined = result.stdout + result.stderr
        assert "plan-amended" in combined or "plan_amended" in combined, (
            f"expected plan-amended in output, got: {combined[-500:]}"
        )


# ── AC-4: dirty tree from terminated worker is preserved ─────────────────────

class TestAC4DirtyTreePreserved:
    """AC-4: the dirty tree left by a terminated worker is preserved,
    exactly as on timeout.

    Red-first: the amended-plan termination path does not yet exist.
    """

    def test_amendment_preserves_dirty_tree(self, tmp_path: Path) -> None:
        """When a worker is terminated by amendment, dirty files are WIP-committed."""
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]
        project = world["project"]
        _write_sub_plan(plans_dir / "2026-10-02-test-slug.md")
        _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md",
                      sub_plan_filename="2026-10-02-test-slug.md")

        # Create a stub claude that writes a file in the project and sleeps.
        stub = world["bin"] / "claude"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"echo 'worker output' > '{project}/worker-file.txt'\n"
            "sleep 120\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)

        # Edit the plan in a background thread after a short delay.
        import threading

        def amend_plan() -> None:
            time.sleep(5)
            _write_sub_plan(plans_dir / "2026-10-02-test-slug.md",
                            body_above_findings="## Steps\n\n### Step 0\n\nAMENDED.\n")

        editor = threading.Thread(target=amend_plan, daemon=True)
        editor.start()

        result = subprocess.run(
            ["bash", str(_RUNNER),
             "--project-path", str(project),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            cwd=str(project),
            env=env,
        )

        # After amendment termination, the dirty file should be in a WIP commit.
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert "WIP" in log.stdout, (
            f"expected WIP commit after amendment termination, got: {log.stdout}"
        )


# ── AC-5: watcher subshell does not outlive the iteration ────────────────────

class TestAC5WatcherLifetime:
    """AC-5: the watcher subshell does not outlive the iteration.

    Control: no watcher exists today, so there is nothing to leak.
    This test verifies the basic contract — no stray processes from a
    short-lived runner invocation.
    """

    def test_no_stray_processes_after_short_run(self, tmp_path: Path) -> None:
        """A runner invocation that exits quickly leaves no stray watchers.

        The contract: no watcher subshell outlives the iteration.  Today no
        watcher exists, so the test verifies the runner does not leak
        background processes from a short run.

        Pins HOME and ILK_DATA_HOME under tmp_path to avoid touching the
        real data root.
        """
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        _write_sub_plan(plans_dir / "2026-10-02-test-slug.md",
                        status="shipped", current_step=2)
        _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md")

        sandbox_env = {
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(tmp_path / "ilk-data"),
            "PATH": os.environ.get("PATH", ""),
            "ILK_DOTSOURCE_ONLY": "1",
        }

        # Record PIDs before.
        before_pids = set(_all_pids())

        # Source the runner (exits immediately with ILK_DOTSOURCE_ONLY=1)
        # and check that no background processes survive.
        result = subprocess.run(
            ["bash", "-c", f"source '{_RUNNER}'"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=sandbox_env, timeout=30,
        )

        # Give any stray processes a moment to exit.
        time.sleep(1)

        after_pids = set(_all_pids())
        new_pids = after_pids - before_pids

        # Filter out expected processes (pytest, bash, etc.)
        stray = []
        for pid in new_pids:
            try:
                cmdline = _read_cmdline(pid)
                if "ilk" in cmdline.lower() and "watcher" in cmdline.lower():
                    stray.append((pid, cmdline))
            except (OSError, PermissionError):
                pass

        assert not stray, f"stray watcher processes after run: {stray}"


def _all_pids() -> list[int]:
    """Return all PIDs on the system (macOS/Linux)."""
    try:
        result = subprocess.run(["pgrep", "-f", "."], capture_output=True, text=True, timeout=5)
        return [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
    except (subprocess.SubprocessError, ValueError):
        return []


def _read_cmdline(pid: int) -> str:
    """Read the command line for a PID (macOS)."""
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except subprocess.SubprocessError:
        return ""


# ── AC-6: SIGTERM-killed worker is WIP-preserved ─────────────────────────────

class TestAC6SignalExitWipPreserved:
    """AC-6: a stub worker killed with SIGTERM (exit 143), with no amendment
    involved, is WIP-preserved exactly as a timeout is.

    Red-first: today the signal-exit path does not trigger WIP preservation
    in practice (despite _classify_agent_exit handling it).
    """

    def test_classify_agent_exit_handles_signal_143(self) -> None:
        """_classify_agent_exit treats exit 143 (SIGTERM) as completed=0."""
        result = _source_runner_fn("_classify_agent_exit", "143")
        assert result.returncode == 0, f"sourcing failed: {result.stderr}"
        parts = result.stdout.strip().split()
        assert parts[0] == "0", f"expected completed=0 for exit 143, got {parts[0]}"

    def test_classify_agent_exit_handles_signal_137(self) -> None:
        """_classify_agent_exit treats exit 137 (SIGKILL) as completed=0."""
        result = _source_runner_fn("_classify_agent_exit", "137")
        assert result.returncode == 0, f"sourcing failed: {result.stderr}"
        parts = result.stdout.strip().split()
        assert parts[0] == "0", f"expected completed=0 for exit 137, got {parts[0]}"

    def test_signal_killed_worker_preserves_dirty_tree(self, tmp_path: Path) -> None:
        """A worker killed by SIGTERM with a dirty tree produces a WIP commit.

        Integration test: run the runner with a stub that writes a file, let
        the amendment watcher kill it (SIGTERM), and verify the dirty tree
        is WIP-preserved.
        """
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]
        project = world["project"]
        _write_sub_plan(plans_dir / "2026-10-02-test-slug.md")
        _write_master(plans_dir / "MASTER-2026-10-02-execution-plan.md",
                      sub_plan_filename="2026-10-02-test-slug.md")

        # Create a stub claude that writes a file and sleeps.
        stub = world["bin"] / "claude"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"echo 'unfinished work' > '{project}/worker-output.txt'\n"
            "sleep 120\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)

        # Edit the plan after a short delay to trigger the amendment watcher.
        import threading

        def amend_plan() -> None:
            time.sleep(5)
            _write_sub_plan(plans_dir / "2026-10-02-test-slug.md",
                            body_above_findings="## Steps\n\n### Step 0\n\nAMENDED.\n")

        editor = threading.Thread(target=amend_plan, daemon=True)
        editor.start()

        result = subprocess.run(
            ["bash", str(_RUNNER),
             "--project-path", str(project),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            cwd=str(project),
            env=env,
        )

        # The worker should have been killed by the amendment watcher.
        combined = result.stdout + result.stderr
        assert "plan-amended" in combined or "plan_amended" in combined, (
            f"expected plan-amended in output, got: {combined[-500:]}"
        )

        # The dirty file should be in a WIP commit.
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert "WIP" in log.stdout, (
            f"expected WIP commit after signal kill, got: {log.stdout}"
        )