"""Red-first pin: a green gate-first step must not invoke the agent.

Part of sub-plan `a-gate-first-step-runs-before-the-agent` (step 0 of 4).

AC-1: a sub-plan whose current step declares ``gate_first: true`` and whose
      gate passes advances ``current_step`` with ZERO agent invocations.

RED today — the driver dispatches unconditionally (``invoke_claude_iteration``
at ``run_ilk_loop_claude.sh:3133``), so the stub agent runs and the counter
file appears.

Harness: source the driver under ``ILK_DOTSOURCE_ONLY=1`` and run its real
``main`` for one iteration (the ``test_exit_state_vocabulary.py`` pattern,
aimed at the dispatch site rather than a classifier). The agent is stubbed
via PATH — the ``RunnerSandbox`` idea from
``test_iteration_record_precedes_work.py``: the runner still spawns a child,
still bounds it with ``gtimeout``, and still takes the same branches. Only
the agent's content is fake.

The scaffold placeholder ``test_placeholder_replaced_by_step_0`` is deleted
here, not kept beside these tests. A gate that passes while it is present is
vacuous.
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

SLUG = "a-gate-first-step"
#: loop_status.py reads the master's registry by matching `YYYY-MM-DD-*.md`
#: references in its body, so the FILE needs the date prefix even though the
#: `plan:` slug does not carry one.
STEM = f"2026-09-23-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── world: a real project + a stub agent that only counts invocations ───────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(root: Path) -> dict:
    """A project + isolated data home + a counting stub `claude`.

    The sub-plan is in-progress at step 0, and step 0 declares
    `gate_first: true` with a trivially green gate (`true`). The stub agent
    appends to a counter file every time it runs and nothing else — if the
    driver honours the marker, that file never appears.
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

    (plans / "MASTER-2026-09-23-gate-first-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-23-gate-first-execution\n"
        "batch_date: 2026-09-23\n"
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
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "local_checks: []\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "## Steps\n\n"
        "### Step 0 — the gate runs before the agent\n\n"
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
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run its real main().

    ILK_RUN_LOCK_HELD=1 skips the lock helper's `bash "$0"` re-exec, which
    would re-enter the wrong script when the driver is sourced rather than
    executed. Everything else — preflight, dispatch, gates, sentinel — is
    the production path.
    """
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


# ── AC-1: a green gate-first step invokes no agent ──────────────────────────

@_NEEDS_GTIMEOUT
def test_a_green_gate_first_step_invokes_no_agent(tmp_path: Path) -> None:
    """The whole cost this batch removes: 89 iterations / 22.8h of agent
    wall-clock spent on a step whose gate already does the work.

    Asserted on the counter file itself, not on the driver's stdout: an
    iteration that never dispatched leaves no stub-agent trace.
    """
    world = _build_world(tmp_path)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]
    assert not counter.exists(), (
        "the stub agent ran (counter file is present) for a step that "
        "declares `gate_first: true` with a passing gate. The driver "
        "dispatched before running the gate — the 89-iteration / 22.8h cost "
        "this sub-plan removes.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )
    # Positive control on the harness itself: if the stub never became
    # reachable, an absent counter is vacuous. A run that DID dispatch must
    # create the file — that is the RED state this pin is written against.
    # We cannot observe that here (the pin is that it does not happen), so
    # the stub's executability is checked instead.
    assert os.access(world["bin"] / "claude", os.X_OK), (
        "the stub agent is not executable — an absent counter would mean "
        "the harness never offered it, not that the driver skipped it"
    )
    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure, not a "
        f"gate-first verdict.\nlast 40 lines:\n{tail}"
    )
