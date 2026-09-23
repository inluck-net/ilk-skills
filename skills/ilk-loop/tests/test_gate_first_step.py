"""Pins for the gate-first fast path and its red escape hatch.

Part of sub-plan `a-gate-first-step-runs-before-the-agent` (steps 0-3 of 4).

AC-1: a sub-plan whose current step declares ``gate_first: true`` and whose
      gate passes advances ``current_step`` with ZERO agent invocations.
AC-2: the same sub-plan with a FAILING gate invokes the agent exactly once,
      and ``current_step`` does not advance on the gate's account.
AC-4: the green path leaves a commit carrying ``[plan:<slug>#step-N]``, created
      with ``--allow-empty``, and advances ``current_step`` on disk.

The fast path itself landed in step 1 (``attempt_gate_first_fast_path`` at
``run_ilk_loop_claude.sh:1632``); these pins hold it to its contract.

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


def _build_world(root: Path, gate_command: str = "true") -> dict:
    """A project + isolated data home + a counting stub `claude`.

    The sub-plan is in-progress at step 0, and step 0 declares
    `gate_first: true` with gate command ``gate_command`` — trivially green
    `true` by default, `false` for the red escape-hatch fixture. The stub
    agent appends to a counter file every time it runs and nothing else: on
    the green path that file never appears, on the red path it appears once.
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
        f"  - command: \"{gate_command}\"\n"
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


# ── AC-2: a red gate-first gate still reaches the agent ─────────────────────

@_NEEDS_GTIMEOUT
def test_a_red_gate_first_gate_still_reaches_the_agent(tmp_path: Path) -> None:
    """The escape hatch. Gate-first is a fast path, not a replacement: the
    agent is needed when the suite cannot start at all (typecheck/compile
    failure) and when step 1 has attributed failures to fix. A red gate must
    fall through to exactly one agent invocation and must NOT advance
    `current_step` on the gate's account.
    """
    world = _build_world(tmp_path, gate_command="false")
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]
    assert counter.exists(), (
        "the gate-first gate was RED but the stub agent never ran. A red "
        "gate must fall through to the agent — that is the escape hatch for "
        "a suite that cannot start at all (typecheck/compile failure) or "
        "attributed failures to fix.\n"
        f"last 40 lines:\n{tail}"
    )
    invocations = [
        line for line in counter.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(invocations) == 1, (
        f"expected exactly ONE agent invocation for a red gate-first gate, "
        f"got {len(invocations)}: {invocations!r}. The fast path must fall "
        f"through once, not retry and not skip.\nlast 40 lines:\n{tail}"
    )
    # The step did not advance on the gate's account. The only advance is
    # `advance_subplan_current_step` on the green path; the stub agent does
    # not move the pointer either, so `current_step` must still read 0. An
    # advance here would mean a RED gate discharged the step.
    sub_file = world["plans"] / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
    assert m, f"the sub-plan lost its current_step pointer:\n{body}"
    assert int(m.group(1)) == 0, (
        f"current_step advanced to {m.group(1)} after a RED gate-first gate. "
        f"The fast path's marker-commit + advance is only for a green gate — "
        f"a red gate must leave the pointer alone so the step stays "
        f"outstanding for the agent.\nlast 40 lines:\n{tail}"
    )
    # Non-vacuity: every assertion above also holds for an ordinary dispatch
    # that never entered the fast path. The pin only means something if the
    # gate-first path ENGAGED and then fell through — which is what the
    # driver's own line emits once `step_declares_gate_first` has matched.
    assert "declares gate_first: true" in proc.stdout, (
        "the gate-first fast path never engaged (no "
        "'[gate-first] ... declares gate_first: true' line). An ordinary "
        "dispatch would satisfy every assertion above, so this pin would be "
        "vacuous. The marker was not seen on the fixture's step 0.\n"
        f"last 40 lines:\n{tail}"
    )
    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure, not a "
        f"gate-first verdict.\nlast 40 lines:\n{tail}"
    )


# ── AC-4: the green path's marker commit and step advance are real ──────────

@_NEEDS_GTIMEOUT
def test_the_green_path_commits_a_marker_and_advances_current_step(tmp_path: Path) -> None:
    """AC-4. The green path's two durable effects live on disk, not on stdout.

    The verification record lands outside the repo by design (Contract 2b), so
    an ``--allow-empty`` commit carrying ``[plan:<slug>#step-N]`` is the only
    in-repo proof the step was discharged (Contract 9) — and the sub-plan's
    ``current_step`` pointer is the only record that the step is no longer
    outstanding. A driver that printed "[gate-first] green" and wrote neither
    would re-run a gate that already passed on the next iteration, which is
    exactly the 89-iteration / 22.8h cost this sub-plan removes.
    """
    world = _build_world(tmp_path)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    trailer = f"[plan:{SLUG}#step-0]"

    def _git_out(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(world["project"]), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        ).stdout

    # Non-vacuity: the fast path must have run to GREEN. Both effects are
    # written inside `attempt_gate_first_fast_path` only after
    # `gate_first_results_are_green` — a partial or never-entered run must
    # not satisfy the assertions below.
    assert "declares gate_first: true" in proc.stdout, (
        "the gate-first fast path never engaged (no "
        "'[gate-first] ... declares gate_first: true' line). Without it the "
        "assertions below would be checking an ordinary dispatch.\n"
        f"last 40 lines:\n{tail}"
    )
    assert "[gate-first] green -- advancing without invoking the agent" in proc.stdout, (
        "the fast path did not reach its GREEN epilogue. The marker commit "
        "and the pointer advance are only written after the gate passes; a "
        "partial run must not count.\n"
        f"last 40 lines:\n{tail}"
    )

    # -- the marker commit exists and carries the step trailer (Contract 9).
    # Match over the FULL message (`%B` == subject + body), as
    # `ship_audit.check_step_commits` does — a subject-only predicate misses
    # body-placed trailers.
    marker_shas = [
        sha for sha in _git_out("rev-list", "HEAD").split()
        if trailer in _git_out("log", "-1", "--format=%B", sha)
    ]
    assert marker_shas, (
        f"no commit carries the step trailer {trailer!r}. The green path's "
        f"only in-repo proof is this marker commit — ship_audit matches it "
        f"with re.escape'd exact equality and reports the step unproven "
        f"without it.\nlog:\n{_git_out('log', '--oneline')}\n"
        f"last 40 lines:\n{tail}"
    )

    # -- and it is empty: `--allow-empty` is the contract (AC-4), not a
    # detail. The verification record lands outside the repo, so the commit
    # must carry no diff of its own.
    for sha in marker_shas:
        names = _git_out("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        assert not names.strip(), (
            f"marker commit {sha[:12]} changes files ({names.strip()!r}). It "
            f"must be created with --allow-empty: the verification record "
            f"lands outside the repo (Contract 2b), so this commit carries no "
            f"diff.\nlast 40 lines:\n{tail}"
        )

    # -- current_step advanced in the sub-plan file on disk --
    sub_file = world["plans"] / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
    assert m, f"the sub-plan lost its current_step pointer:\n{body}"
    assert int(m.group(1)) == 1, (
        f"current_step reads {m.group(1)} after a GREEN gate-first gate that "
        f"discharged step 0. The pointer must advance to step+1 — an "
        f"unchanged pointer leaves the step outstanding and the next "
        f"iteration re-runs a gate that already passed.\nlast 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure, not a "
        f"gate-first verdict.\nlast 40 lines:\n{tail}"
    )
