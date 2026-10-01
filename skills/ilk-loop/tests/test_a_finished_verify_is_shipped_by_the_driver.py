"""A finished batch-verify sub-plan is shipped by the driver, not handed to a worker.

Regression for 2026-10-01 20:20: a-red-is-blamed-on-its-owner-verify was
pending at current_step 2/2; the driver had no gate-first step to run, the
worker was refused by ship_transition, and the sub-plan stalled until
current_step was reset by hand.

When a ``batch_verification: true`` sub-plan has every step discharged
(``current_step >= estimated_steps``) but is still ``pending``, the
gate-first fast path targets the **last** step (``estimated_steps - 1``).
Its gate re-runs (a fresh measurement, never a stale record), and with
every step discharged the driver ships — no agent needed.

Harness: same as ``test_gate_first_step.py`` — source the driver under
``ILK_DOTSOURCE_ONLY=1`` and run its ``main`` for one iteration.  The
agent is stubbed via PATH.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402

SLUG = "a-finished-verify"
STEM = f"2026-10-01-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── helpers (shared pattern with test_gate_first_step.py) ──────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(
    root: Path,
    *,
    last_gate_command: str = "true",
    last_step_gate_first: bool = True,
) -> dict:
    """A project + isolated data home + a counting stub ``claude``.

    The sub-plan is ``batch_verification: true``, ``pending``,
    ``current_step: 2``, ``estimated_steps: 2`` — every step discharged but
    unshipped.  Step 1 (the last step) declares ``gate_first: true`` with
    gate ``last_gate_command`` by default.
    """
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-10-01-finished-verify-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-01-finished-verify-execution\n"
        "batch_date: 2026-10-01\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    last_step_fence = (
        "gate_first: true\n" if last_step_gate_first else ""
    )
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: pending\n"
        "current_step: 2\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "batch_verification: true\n"
        "local_checks: []\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "## Steps\n\n"
        "### Step 0 — first step\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 30\n"
        "```\n\n"
        "Body.\n\n"
        "### Step 1 — final step\n\n"
        "```yaml\n"
        f"{last_step_fence}"
        "local_checks:\n"
        f"  - command: \"{last_gate_command}\"\n"
        "    timeout: 30\n"
        "```\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "counter": counter,
        "root": root,
    }


class _scoped_data_home:
    """Pin ILK_DATA_HOME for a block (ilk_paths.project_key reads it)."""

    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home
        self._prev: str | None = None

    def __enter__(self) -> Path:
        self._prev = os.environ.get("ILK_DATA_HOME")
        os.environ["ILK_DATA_HOME"] = str(self._data_home)
        return self._data_home

    def __exit__(self, *exc: object) -> None:
        if self._prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = self._prev


def _run_one_iteration(world: dict) -> subprocess.CompletedProcess:
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run its real main()."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(world["project"]))} \\
     --max-iterations 1 \\
     --iteration-timeout-min 1 \\
     --model test-model \\
     --run-local-checks
echo "MAIN_RC=$?"
"""
    env = {
        **os.environ,
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(world["root"]),
        "ILK_DATA_HOME": str(world["data_home"]),
    }
    env.pop("ILK_DATA_DIR", None)
    env.pop("ILK_DOTSOURCE_ONLY", None)
    (world["root"] / ".claude").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=str(world["root"]),
    )


# ── AC-1: all steps discharged + last step gate_first + green gate → shipped ─

@pytest.mark.xfail(strict=True, reason="red-first")
@_NEEDS_GTIMEOUT
def test_a_finished_verify_with_green_gate_is_shipped_by_the_driver(tmp_path: Path) -> None:
    """The 20:20 shape.  Every step discharged, sub-plan pending, last step
    gate_first with a green gate.  The driver re-runs the last step's gate,
    ships, and invokes zero agents.
    """
    world = _build_world(tmp_path)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]

    # No agent invocation.
    assert not counter.exists(), (
        "the stub agent ran for a finished batch-verify sub-plan whose last "
        "step declares gate_first: true with a passing gate. The driver "
        "should have shipped it without dispatching a worker.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )

    # The sub-plan is now shipped.
    sub_file = world["plans"] / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    m = re.search(r"^status:[ \t]*(\S+)", body, re.MULTILINE)
    assert m and m.group(1) == "shipped", (
        f"sub-plan status is {m.group(1) if m else 'missing!'} after the "
        f"driver ran a green gate on a finished batch-verify. It must be "
        f"shipped.\nlast 40 lines:\n{tail}"
    )

    # A [plan:<slug>#ship] commit exists.
    def _git_out(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(world["project"]), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        ).stdout

    ship_trailer = f"[plan:{SLUG}#ship]"
    ship_shas = [
        sha for sha in _git_out("rev-list", "HEAD").split()
        if ship_trailer in _git_out("log", "-1", "--format=%B", sha)
    ]
    assert ship_shas, (
        f"no commit carries the ship trailer {ship_trailer!r}. The driver "
        f"must create a ship marker when shipping a batch-verify sub-plan.\n"
        f"log:\n{_git_out('log', '--oneline')}\nlast 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-2: all steps discharged + last step gate_first + red gate → unshipped ─

@pytest.mark.xfail(strict=True, reason="red-first")
@_NEEDS_GTIMEOUT
def test_a_finished_verify_with_red_gate_stays_unshipped(tmp_path: Path) -> None:
    """Red gate on the last step.  The sub-plan stays pending and no agent
    is invoked (the driver cannot fix a red gate by dispatching a worker).
    """
    world = _build_world(tmp_path, last_gate_command="false")
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]

    # No agent invocation.
    assert not counter.exists(), (
        "the stub agent ran for a finished batch-verify whose last-step "
        "gate was RED. The driver cannot fix a red gate — it must not "
        "dispatch.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )

    # The sub-plan stays pending.
    sub_file = world["plans"] / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    m = re.search(r"^status:[ \t]*(\S+)", body, re.MULTILINE)
    assert m and m.group(1) == "pending", (
        f"sub-plan status is {m.group(1) if m else 'missing!'} — a red gate "
        f"must leave it pending.\nlast 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-3: last step without gate_first → cannot ship, 0 agent calls ─────────

@pytest.mark.xfail(strict=True, reason="red-first")
@_NEEDS_GTIMEOUT
def test_a_finished_verify_without_gate_first_cannot_ship_from_driver(tmp_path: Path) -> None:
    """Last step does not declare gate_first.  The driver logs the refusal
    and ends the iteration without dispatching a worker.
    """
    world = _build_world(tmp_path, last_step_gate_first=False)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]

    # No agent invocation.
    assert not counter.exists(), (
        "the stub agent ran for a finished batch-verify whose last step "
        "is NOT gate_first. The driver cannot ship it and must not "
        "dispatch.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )

    # The refusal log line.
    assert "cannot ship it" in proc.stdout, (
        "the driver did not log the 'cannot ship it' refusal for a finished "
        "batch-verify whose last step is not gate_first.\n"
        f"last 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-4 (control): a normal pending verify at step 0 behaves as today ──────

@_NEEDS_GTIMEOUT
def test_a_normal_pending_verify_at_step_zero_behaves_as_today(tmp_path: Path) -> None:
    """Control.  A batch-verify sub-plan at current_step 0 — the fast path
    targets step 0's gate-first as it does today.  No xfail: this must pass
    before the fix to prove the harness works.
    """
    project = tmp_path / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = tmp_path / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-10-01-control-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-01-control-execution\n"
        "batch_date: 2026-10-01\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "batch_verification: true\n"
        "local_checks: []\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "## Steps\n\n"
        "### Step 0 — gate-first step\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 30\n"
        "```\n\n"
        "Body.\n\n"
        "### Step 1 — later work\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 30\n"
        "```\n",
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "agent-invocations.txt"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    world = {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "counter": counter,
        "root": tmp_path,
    }
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])

    # Step 0's gate-first ran and no agent was dispatched.
    assert not counter.exists(), (
        "the stub agent ran for a step-0 gate-first with a passing gate.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )
    assert "declares gate_first: true" in proc.stdout, (
        "the gate-first fast path never engaged for step 0.\n"
        f"last 40 lines:\n{tail}"
    )

    # current_step advanced to 1.
    sub_file = plans / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
    assert m and int(m.group(1)) == 1, (
        f"current_step is {m.group(1) if m else 'missing'} — expected 1 "
        f"after a green gate-first on step 0.\nlast 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure.\n"
        f"last 40 lines:\n{tail}"
    )