"""Red-first: a red gate on a `shipped` sub-plan must end the run, and classify.

This is the whole defect, end to end.  ``kira-cloudflare`` run
``20260828-211346`` shipped a sub-plan 3/3 with every declared gate red and
reported ``all-shipped``.  The work was probably fine; the *proof mechanism*
was blind, and its blindness did not stop the ship.

Three things have to hold for a red gate to actually stop a run:

1. the blocking record must be readable
   (``test_gate_record_format_contract.py``),
2. the results file must still exist when enforcement reads it
   (``test_gate_results_survive_enforcement.py``), and
3. enforcement must run *before* the early ``break`` on ``iter_stop_reason``
   (``run_ilk_loop_claude.sh:2305-2308`` vs ``:2310-2317``) — the one case
   where it is most needed is exactly the case that skips it.

Then the stop reason has to survive the postmortem: ``ship_integrity_violation``
appears in 2 of 532 tracked files (both runners) and in 0 classifier files, so
``collect.py``'s ``_SENTINEL_FAILURE_MAP`` (``:1281``) launders it into
whatever the generic heuristics say.

AC-5, AC-6, AC-7, AC-8 of sub-plan ``a-red-gate-cannot-ship-a-subplan``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"
WATCHDOG = _REPO / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"

sys.path.insert(0, str(_REPO / "skills" / "ilk-feedback" / "scripts"))
import collect  # noqa: E402

SLUG = "a-subplan-with-a-red-gate"
#: loop_status.py reads the master's registry by matching `YYYY-MM-DD-*.md`
#: references in its body, so the FILE needs the date prefix even though the
#: `plan:` slug does not carry one.
STEM = f"2026-08-29-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── the end-to-end harness ───────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
    )


def _build_world(root: Path) -> dict:
    """A project + isolated data home + a stub `claude`, ready for one iteration.

    The sub-plan is already at ``status: shipped`` with a gate that always
    fails, and the stub agent lands a commit carrying the
    ``[plan:<slug>#step-0]`` trailer the gate discovery reads.  That is the
    field scenario reduced to its smallest form.
    """
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    sys.path.insert(0, str(RUNNER.parent))
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-08-29-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-execution\n"
        "batch_date: 2026-08-29\n"
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
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"python3 -c 'raise SystemExit(1)'\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # The stub agent does what a worker does on its last step: land a
    # trailered commit and mark the sub-plan shipped.  It never runs the gate
    # — that is the driver's job, and the whole point of the defect.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{STEM}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"--allow-empty -m 'feat: the work [plan:{SLUG}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


def _run_one_iteration(world: dict, root: Path) -> subprocess.CompletedProcess:
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
    return subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=env, cwd=str(root),
    )


@pytest.fixture(scope="module")
def red_gate_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One real runner iteration over a shipped sub-plan whose gate is red."""
    root = tmp_path_factory.mktemp("red-gate-run")
    world = _build_world(root)
    proc = _run_one_iteration(world, root)
    # The runner resolves the sentinel through `external_launcher_dir` while
    # the documented path is `<data>/runtime/last-exit.json`.  That divergence
    # is F4 (sub-plan `the-sentinel-has-one-path`) and is NOT this sub-plan's
    # to fix, so read whichever exists rather than asserting a location here.
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    sentinel = next((c for c in candidates if c.is_file()), None)
    return {
        "proc": proc,
        "sentinel": json.loads(sentinel.read_text(encoding="utf-8"))
        if sentinel is not None else None,
        "sentinel_path": sentinel or " or ".join(str(c) for c in candidates),
        "subplan": world["plans"] / f"{STEM}.md",
    }


# ── AC-5: the run stops, and says why ───────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_red_gate_ends_the_run_as_a_ship_integrity_violation(
    red_gate_run: dict,
) -> None:
    """AC-5 — asserted on the stop reason itself, not on whether enforcement ran."""
    proc = red_gate_run["proc"]
    sentinel = red_gate_run["sentinel"]
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    assert sentinel is not None, (
        f"the runner wrote no sentinel at {red_gate_run['sentinel_path']}.\n{tail}"
    )
    assert sentinel.get("state") == "ship_integrity_violation", (
        "a sub-plan sat at `status: shipped` with its declared gate red and "
        f"the run ended at state={sentinel.get('state')!r}. "
        "`all-shipped` here is the 20260828-211346 field failure exactly.\n"
        f"last 30 lines:\n{tail}"
    )


# ── AC-6: and the ship is taken back ────────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_the_unproven_subplan_is_reverted_to_in_progress(
    red_gate_run: dict,
) -> None:
    """AC-6 — stopping the run is not enough; the false ship must be undone."""
    body = red_gate_run["subplan"].read_text(encoding="utf-8")
    m = re.search(r"^status:\s*(\S+)", body, re.MULTILINE)
    status = m.group(1) if m else "<none>"
    tail = "\n".join(
        (red_gate_run["proc"].stdout + red_gate_run["proc"].stderr).splitlines()[-30:]
    )
    assert status == "in-progress", (
        f"{SLUG} is still `{status}` after its gate came back red. The next "
        "run reads it as done and the work is never re-verified.\n"
        f"last 30 lines:\n{tail}"
    )


# ── AC-7 + AC-8: the stop reason survives the postmortem ────────────────────

def test_ship_integrity_violation_classifies_as_shipped_unverified() -> None:
    """AC-7 — `collect.py` must not launder the violation into a generic label.

    AC-8 rides along: the classification is only worth anything if the
    watchdog refuses to auto-relaunch on it.

    Correction to AC-8 as written in the sub-plan (which expected `block`
    reached via the `*` fail-safe): ``watchdog.sh:316-319`` has an EXPLICIT
    ``shipped-unverified)`` arm returning ``needs-human``, and
    ``test_watchdog_classify.py:458`` already pins it.  The substance of AC-8
    — a red gate is never auto-relaunched — holds; the expected value does
    not.  This test asserts what the source actually says.
    """
    iters = [{
        "run_id": "20260828-211346", "iteration": 3, "exit_code": 0,
        "new_commits_total": 4, "duration_sec": 900,
        "local_checks": {"outcome": "fail", "command": "bunx vitest run"},
    }]
    sentinel = {
        "state": "ship_integrity_violation",
        "run_id": "20260828-211346",
        "iteration": 3,
    }

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "shipped-unverified", (
        f"sentinel state=ship_integrity_violation classified as {label!r}. "
        "It is absent from collect.py:1281's _SENTINEL_FAILURE_MAP, so the "
        "one signal that a ship was unproven is laundered by the postmortem. "
        "`shipped-unverified` already exists at collect.py:530 — no new label."
    )
    assert facts.get("reason") == "sentinel terminal state", (
        f"expected the authoritative-sentinel path; got {facts!r}"
    )

    action = subprocess.run(
        ["bash", "-c",
         f"source '{WATCHDOG}' >/dev/null 2>&1; classify_action shipped-unverified"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    ).stdout.strip()
    assert action == "needs-human", (
        f"watchdog.sh routes shipped-unverified to {action!r}; it must not be "
        "on the relaunch whitelist or a run that shipped unproven work is "
        "restarted as if nothing happened"
    )


# ── a rejected gate invalidates the ship intent ─────────────────────────────
#
# Sub-plan `a-rejected-gate-invalidates-ship-intent`, step 0 — RED-FIRST.
#
# `b587507` closed the *recovery* half: `retire_completed_intent()` retires an
# intent whose transition demonstrably completed, on the next `repair()` pass.
# This is the *write-path* half. The runner calls `converge_ship_transition` at
# run_ilk_loop_claude.sh:3273, deliberately BEFORE integrity enforcement, so
# the retirement lands on the pass after the one a gate rejection is racing.
# Clearing the intent where the revert happens makes the invariant true at the
# moment the decision is made rather than true again later.
#
# Three fixtures below each drive the REAL runner once, because the change
# lives in a Bash branch and a test that imports `ship_transition` never
# executes it.

INTENT_FILENAME = ".ship-intent.json"
OTHER_SLUG = "a-different-subplan-entirely"


def _red_gate_run_with_intent(root: Path, intent_slug: str) -> dict:
    """One real runner iteration over a red-gate ship, with an intent present.

    The intent is written BEFORE the run, naming *intent_slug* — either the
    slug whose gate will be rejected, or an unrelated one.
    """
    world = _build_world(root)
    (world["plans"] / INTENT_FILENAME).write_text(
        json.dumps({"slug": intent_slug,
                    "repo": str(world["project"]),
                    "pid": 999999}),
        encoding="utf-8",
    )
    proc = _run_one_iteration(world, root)
    return {
        "proc": proc,
        "intent": world["plans"] / INTENT_FILENAME,
        "subplan": world["plans"] / f"{STEM}.md",
    }


@pytest.fixture(scope="module")
def rejected_with_own_intent(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """An intent naming the very slug whose gate comes back red."""
    return _red_gate_run_with_intent(
        tmp_path_factory.mktemp("red-gate-own-intent"), SLUG
    )


@pytest.fixture(scope="module")
def rejected_with_other_intent(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """An intent naming a DIFFERENT slug, in flight while A is rejected."""
    return _red_gate_run_with_intent(
        tmp_path_factory.mktemp("red-gate-other-intent"), OTHER_SLUG
    )


@_NEEDS_GTIMEOUT
def test_a_rejected_gate_clears_the_intent_for_its_own_slug(
    rejected_with_own_intent: dict,
) -> None:
    """Objective 1 — the intent for a rejected slug does not survive the run."""
    d = rejected_with_own_intent
    tail = "\n".join((d["proc"].stdout + d["proc"].stderr).splitlines()[-30:])
    assert not d["intent"].exists(), (
        f"{INTENT_FILENAME} still names {SLUG!r} after its gate was rejected "
        "and its status reverted. A surviving intent is positive evidence of "
        "an interrupted transition, so the next `repair(--only-interrupted)` "
        "reads a deliberate un-ship as a crash and re-ships it.\n"
        f"intent={d['intent'].read_text(encoding='utf-8') if d['intent'].exists() else ''}\n"
        f"last 30 lines:\n{tail}"
    )


@_NEEDS_GTIMEOUT
def test_a_rejection_does_not_disturb_another_slugs_intent(
    rejected_with_other_intent: dict,
) -> None:
    """Objective 3 — rejecting A must not destroy an intent naming B.

    The falsifier for a fix that over-reaches: clearing unconditionally is
    cheaper to write and re-creates the class of bug this batch is closing,
    because B's transition may be genuinely in flight.
    """
    d = rejected_with_other_intent
    tail = "\n".join((d["proc"].stdout + d["proc"].stderr).splitlines()[-30:])
    assert d["intent"].exists(), (
        f"the intent naming {OTHER_SLUG!r} was destroyed by a gate rejection "
        f"for {SLUG!r}. Only the rejected slug's own intent is stale; another "
        "slug's may describe a transition that is still mid-write.\n"
        f"last 30 lines:\n{tail}"
    )
    payload = json.loads(d["intent"].read_text(encoding="utf-8"))
    assert payload.get("slug") == OTHER_SLUG, (
        f"the intent survived but no longer names {OTHER_SLUG!r}: {payload!r}"
    )


@_NEEDS_GTIMEOUT
def test_the_revert_also_moves_current_step_back(
    rejected_with_own_intent: dict,
) -> None:
    """The 2026-09-15 addendum — status and pointer must revert together.

    On kira-cloudflare run 20260915-112812 step 1's gate went red at 12:19:53
    while the agent had written `current_step: 2` at 12:19:13, 40 seconds
    earlier. The status was reverted; the pointer was not, so the plan recorded
    the red step as done and a resume started past it.

    Read BOTH fields: a revert that leaves the pointer ahead of the status is
    the defect, and either field alone is satisfiable without the other.
    """
    d = rejected_with_own_intent
    body = d["subplan"].read_text(encoding="utf-8")
    status = (re.search(r"^status:\s*(\S+)", body, re.MULTILINE) or [None, "<none>"])[1]
    step = (re.search(r"^current_step:\s*(\S+)", body, re.MULTILINE) or [None, "<none>"])[1]
    tail = "\n".join((d["proc"].stdout + d["proc"].stderr).splitlines()[-30:])

    assert (status, step) == ("in-progress", "0"), (
        f"after the gate for step 0 was rejected the sub-plan reads "
        f"status={status!r} current_step={step!r}; expected "
        f"('in-progress', '0'). The stub agent bumped the pointer to 1 before "
        "the gate fired, and a pointer left ahead of the reverted status means "
        "the red step is never revalidated.\n"
        f"last 30 lines:\n{tail}"
    )
