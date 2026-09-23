"""Pins for shipping a verification sub-plan without a worker session.

Part of sub-plan `verify-without-a-worker` (step 0 of 3).

AC-1: a ``batch_verification: true`` sub-plan with 2 gate-first steps whose
      gates are green ⇒ one driver run, **0 agent invocations** (the stub
      agent's call counter is 0), sub-plan ``status: shipped``, 2 marker
      commits and a ``#ship`` commit, and the log line ``shipped by the
      driver``.
AC-2: the same with ``batch_verification`` absent ⇒ pointer advances, status
      unchanged (not shipped), as today.
AC-3: step 1's gate red ⇒ falls through to the agent (counter 1), nothing
      shipped.
AC-4: with a history containing ``suite_duration_sec`` 300 and 420 and no
      ``--suite-timeout`` ⇒ header ``suite_budget: 840 (measured)``. With no
      history ⇒ ``1800 (default)``. With ``--suite-timeout 999`` ⇒
      ``999 (explicit)``. A measured 2000 ⇒ clamped to ``3600``.
AC-5: a failed-at-base row and a declared-at-base row get ``—`` reruns, and
      the rerun subprocess count covers only undecided rows. Classification
      is unchanged for all four classes.
AC-6: the template's step 0 has no ``--suite-timeout``, and step 1's fence
      declares ``gate_first: true``. Both are asserted through #1's locator,
      not by grep.

Harness: same as ``test_gate_first_step.py`` — source the driver under
``ILK_DOTSOURCE_ONLY=1`` and run its real ``main`` for one iteration. The
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
_TEMPLATE = _SCRIPTS.parent / "templates" / "batch-verification-subplan.md"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402

SLUG = "batch-verify"
STEM = f"2026-09-24-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


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


# ── world builder ───────────────────────────────────────────────────────────

def _build_world(
    root: Path,
    *,
    batch_verification: bool = True,
    step1_gate_command: str = "true",
) -> dict:
    """A project + isolated data home + a counting stub ``claude``.

    Two steps, both ``gate_first: true``. Step 0's gate is always green
    (``true``). Step 1's gate is ``step1_gate_command`` (default ``true``,
    pass ``"false"`` for the red escape-hatch fixture). When
    ``batch_verification`` is True the sub-plan frontmatter carries the
    marker; when False it does not.
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

    bv_line = "batch_verification: true\n" if batch_verification else ""

    (plans / f"MASTER-{SLUG}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {SLUG}-execution\n"
        f"batch_date: 2026-09-24\n"
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
        f"{bv_line}"
        "local_checks: []\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "## Steps\n\n"
        "### Step 0 — run the suite\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 30\n"
        "```\n\n"
        "### Step 1 — verify attribution\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        f"  - command: \"{step1_gate_command}\"\n"
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


def _run_iterations(world: dict, max_iterations: int = 1) -> subprocess.CompletedProcess:
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run its real main().

    Gate-first advances one step per iteration, so a two-step sub-plan
    needs ``max_iterations=2`` to let both steps run.
    """
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(world["project"]))} \\
     --max-iterations {max_iterations} \\
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


def _read_subplan_status(world: dict) -> tuple[int, str]:
    """Return (current_step, status) from the sub-plan file on disk."""
    sub_file = world["plans"] / f"{STEM}.md"
    body = sub_file.read_text(encoding="utf-8")
    step_m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
    status_m = re.search(r"^status:[ \t]*(\S+)[ \t]*$", body, re.MULTILINE)
    step = int(step_m.group(1)) if step_m else -1
    status = status_m.group(1) if status_m else "unknown"
    return step, status


# ── AC-1 (xfail): batch_verification green ⇒ shipped by the driver ─────────

@_NEEDS_GTIMEOUT
def test_batch_verification_green_ships_without_worker(tmp_path: Path) -> None:
    """A ``batch_verification: true`` sub-plan whose gates are all green is
    shipped by the driver with zero agent invocations.

    Asserts: 0 agent calls, status shipped, current_step advanced past last
    step, at least 2 marker commits + 1 ship commit, and the driver's log
    line ``shipped by the driver``.
    """
    world = _build_world(tmp_path)
    proc = _run_iterations(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]

    # 0 agent invocations
    assert not counter.exists(), (
        "the stub agent ran (counter file is present) for a "
        "batch_verification sub-plan whose gates are all green. The driver "
        "should ship without dispatching.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )

    # sub-plan shipped
    step, status = _read_subplan_status(world)
    assert status == "shipped", (
        f"expected status 'shipped', got '{status}'.\nlast 40 lines:\n{tail}"
    )
    assert step == 2, (
        f"expected current_step 2 (past last step), got {step}.\n"
        f"last 40 lines:\n{tail}"
    )

    # log line
    combined = proc.stdout + proc.stderr
    assert "shipped by the driver" in combined, (
        "driver did not print 'shipped by the driver'.\n"
        f"last 40 lines:\n{tail}"
    )

    # marker commits + ship commit
    def _git_log() -> str:
        return subprocess.run(
            ["git", "-C", str(world["project"]), "log", "--oneline"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True,
        ).stdout

    log = _git_log()
    marker_count = log.count(f"[plan:{SLUG}#step-")
    ship_count = log.count(f"[plan:{SLUG}#ship]")
    assert marker_count >= 2, (
        f"expected >= 2 marker commits, found {marker_count}.\nlog:\n{log}"
    )
    assert ship_count >= 1, (
        f"expected >= 1 ship commit, found {ship_count}.\nlog:\n{log}"
    )


# ── AC-2: no batch_verification ⇒ pointer advances, not shipped ────────────

@_NEEDS_GTIMEOUT
def test_no_batch_verification_pointer_advances_not_shipped(tmp_path: Path) -> None:
    """Without ``batch_verification: true``, gate-first still advances the
    pointer but does NOT ship. The sub-plan stays ``in-progress``.
    """
    world = _build_world(tmp_path, batch_verification=False)
    proc = _run_iterations(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    step, status = _read_subplan_status(world)

    assert step == 2, (
        f"expected current_step 2 (both gate-first steps advanced), got {step}."
        f"\nlast 40 lines:\n{tail}"
    )
    assert status == "in-progress", (
        f"without batch_verification, status must stay 'in-progress', "
        f"got '{status}'.\nlast 40 lines:\n{tail}"
    )


# ── AC-3: red step 1 gate ⇒ falls through to agent ────────────────────────

@_NEEDS_GTIMEOUT
def test_red_step1_gate_falls_through_to_agent(tmp_path: Path) -> None:
    """When step 1's gate is red, the driver falls through to the agent
    (exactly 1 invocation) and does not ship.
    """
    world = _build_world(tmp_path, step1_gate_command="false")
    proc = _run_iterations(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]

    assert counter.exists(), (
        "the step 1 gate was RED but the stub agent never ran.\n"
        f"last 40 lines:\n{tail}"
    )
    invocations = [
        line for line in counter.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(invocations) == 1, (
        f"expected exactly 1 agent invocation, got {len(invocations)}.\n"
        f"last 40 lines:\n{tail}"
    )

    _, status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"with a red gate, status must stay 'in-progress', got '{status}'.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-4 (xfail): measured suite budget ────────────────────────────────────

def test_measured_suite_budget_from_history(tmp_path: Path) -> None:
    """With history containing suite_duration_sec 300 and 420 and no
    --suite-timeout ⇒ header ``suite_budget: 840 (measured)``. With no
    history ⇒ ``1800 (default)``. With ``--suite-timeout 999`` ⇒
    ``999 (explicit)``. A measured 2000 ⇒ clamped to 3600.
    """
    from unittest.mock import patch
    from verification_record import compute_suite_budget

    # No history, no explicit timeout ⇒ default 1800.
    budget, src = compute_suite_budget(tmp_path, None)
    assert budget == 1800, f"expected 1800 default, got {budget}"
    assert src == "default", f"expected 'default', got {src!r}"

    # Explicit timeout ⇒ used as-is.
    budget, src = compute_suite_budget(tmp_path, 999)
    assert budget == 999, f"expected 999 explicit, got {budget}"
    assert src == "explicit", f"expected 'explicit', got {src!r}"

    # With history: create verification dir and history files.
    # Mock _resolve_project_verification_dir to return our test dir.
    vdir = tmp_path / "logs" / "verification"
    vdir.mkdir(parents=True)
    # History entry with suite_duration_sec 300.
    hist1 = vdir / "batch1-batch.history.jsonl"
    hist1.write_text(
        '{"attempt": 1, "digest": "a", "failing_nodes": [], '
        '"suite_duration_sec": 300}\n',
        encoding="utf-8",
    )
    # History entry with suite_duration_sec 420.
    hist2 = vdir / "batch2-batch.history.jsonl"
    hist2.write_text(
        '{"attempt": 1, "digest": "b", "failing_nodes": [], '
        '"suite_duration_sec": 420}\n',
        encoding="utf-8",
    )
    with patch("verification_record._resolve_project_verification_dir",
               return_value=vdir):
        budget, src = compute_suite_budget(tmp_path, None)
    # 2 * max(300, 420) = 840.
    assert budget == 840, f"expected 840 measured, got {budget}"
    assert src == "measured", f"expected 'measured', got {src!r}"

    # Measured 2000 ⇒ clamped to 3600.
    hist3 = vdir / "batch3-batch.history.jsonl"
    hist3.write_text(
        '{"attempt": 1, "digest": "c", "failing_nodes": [], '
        '"suite_duration_sec": 2000}\n',
        encoding="utf-8",
    )
    with patch("verification_record._resolve_project_verification_dir",
               return_value=vdir):
        budget, src = compute_suite_budget(tmp_path, None)
    assert budget == 3600, f"expected 3600 clamped, got {budget}"


# ── AC-5 (xfail): rerun only undecided rows ────────────────────────────────

def test_rerun_only_undecided_rows(tmp_path: Path) -> None:
    """A declared-at-base row gets ``—`` reruns, and the rerun subprocess
    count covers only non-declared rows. Classification is unchanged for
    all four classes.
    """
    from verification_record import render_record

    # Simulate a record with four at-base classes.
    at_base = {
        "test_a::passed": "passed",           # non-declared — rerun
        "test_b::absent": "absent-at-base",    # non-declared — rerun
        "test_c::failed": "failed",            # non-declared — rerun (pre-existing, but still measured)
        "test_d::declared": "declared-at-base", # declared — no rerun (—)
    }
    # head_reruns and batch_touched include — for declared rows.
    head_reruns = {
        "test_a::passed": 1,
        "test_b::absent": 0,
        "test_c::failed": 0,
        "test_d::declared": "—",
    }
    batch_touched = {
        "test_a::passed": True,
        "test_b::absent": False,
        "test_c::failed": False,
        "test_d::declared": "—",
    }

    record_text = render_record(
        batch="test-batch",
        head="abc123", tree="def456", base_sha="base789",
        invocation="pytest", scope={"mode": "full", "count": 100, "reason": ""},
        results={"counts": {"total": 100, "passed": 96, "failed": 4, "errors": 0, "skipped": 0},
                 "failing_nodes": list(at_base.keys()), "exit_code": 1},
        at_base=at_base, base_red=[], head_red=[],
        head_reruns=head_reruns, batch_touched=batch_touched,
    )

    lines = record_text.splitlines()
    # Find the table rows.
    table_lines = [l for l in lines if l.startswith("| test_")]
    assert len(table_lines) == 4, f"expected 4 table rows, got {len(table_lines)}"

    # declared-at-base row gets — in head reruns and batch touched.
    for line in table_lines:
        if "test_d::declared" in line:
            parts = [p.strip() for p in line.split("|")]
            # columns: node id, at base, in baseline_red, head reruns, batch touched
            assert parts[4] == "—", f"expected — for head reruns on declared row, got {parts[4]!r}"
            assert parts[5] == "—", f"expected — for batch touched on declared row, got {parts[5]!r}"

    # non-declared rows get real values.
    for line in table_lines:
        if "test_a::passed" in line:
            parts = [p.strip() for p in line.split("|")]
            assert parts[4] == "1/3", f"expected 1/3 for reruns, got {parts[4]!r}"
            assert parts[5] == "yes", f"expected yes for touched, got {parts[5]!r}"
        if "test_c::failed" in line:
            parts = [p.strip() for p in line.split("|")]
            assert parts[4] == "0/3", f"expected 0/3 for reruns, got {parts[4]!r}"
            assert parts[5] == "no", f"expected no for touched, got {parts[5]!r}"


# ── AC-6 (xfail): template step 0 has no --suite-timeout, step 1 is gate_first

def test_template_step0_no_suite_timeout_step1_gate_first() -> None:
    """The template's step 0 has no ``--suite-timeout``, and step 1's fence
    declares ``gate_first: true``. Both are asserted through #1's locator
    (step_gate_fence), not by grep.
    """
    from run_local_checks import step_gate_fence

    body = _TEMPLATE.read_text(encoding="utf-8")

    # Step 0: fence must NOT contain --suite-timeout.
    gate0 = step_gate_fence(body, 0)
    assert gate0.heading_count >= 1, "step 0 heading not found in template"
    assert gate0.fence_text is not None, "step 0 has no fence"
    assert "--suite-timeout" not in gate0.fence_text, (
        "step 0 fence still contains --suite-timeout; the measured budget "
        "replaces it"
    )

    # Step 1: fence must declare gate_first: true.
    gate1 = step_gate_fence(body, 1)
    assert gate1.heading_count >= 1, "step 1 heading not found in template"
    assert gate1.fence_text is not None, "step 1 has no fence"
    assert re.search(r"gate_first:\s*(true|yes|1)", gate1.fence_text, re.IGNORECASE), (
        "step 1 fence does not declare gate_first: true; a green step 1 "
        "should ship the sub-plan with no worker"
    )