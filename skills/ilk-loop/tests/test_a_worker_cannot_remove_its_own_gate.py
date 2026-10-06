"""Pins for gate-snapshot integrity: a worker cannot remove its own gate.

Part of sub-plan ``a-worker-cannot-remove-its-own-gate`` (step 0 of 2).

AC-1: a stub worker deletes step 1's gate from its sub-plan ⇒ the driver
      still runs that gate, the file is restored, and the run exits
      ``ship_integrity_violation``.
AC-2: a stub worker lowers a gate's timeout or edits its command ⇒ the
      same outcome.
AC-3 (control): a stub worker edits only ``## Findings`` ⇒ no violation.
AC-4 (control): an operator edits the gate between runs ⇒ the next run
      uses the new gate.

Harness: same as ``test_verify_without_a_worker.py`` — source the driver
under ``ILK_DOTSOURCE_ONLY=1`` and run its real ``main`` for one iteration.
The agent is stubbed via PATH.
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

SLUG = "gate-snapshot"
STEM = f"2026-10-02c-{SLUG}"

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
    step1_gate_command: str = "true",
    step1_gate_timeout: int = 30,
) -> dict:
    """A project + isolated data home + a stub ``claude`` that edits the gate.

    Two steps, both ``gate_first: true``. Step 0's gate is always green
    (``true``). Step 1's gate is configurable.
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

    (plans / f"MASTER-{SLUG}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {SLUG}-execution\n"
        f"batch_date: 2026-10-02\n"
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
        f"    timeout: {step1_gate_timeout}\n"
        "```\n\n"
        "## Findings\n\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    # The stub agent edits the sub-plan file to remove step 1's gate.
    sub_plan_path = plans / f"{STEM}.md"
    edit_script = root / "edit_gate.py"
    edit_script.write_text(
        "import re, sys\n"
        "path = sys.argv[1]\n"
        "text = open(path, encoding='utf-8').read()\n"
        "text = re.sub(\n"
        "    r'### Step 1.*?```\\n',\n"
        "    '### Step 1 \\u2014 verify attribution\\n\\n',\n"
        "    text, flags=re.DOTALL)\n"
        "open(path, 'w', encoding='utf-8').write(text)\n",
        encoding="utf-8",
    )
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        f"python3 {shlex.quote(str(edit_script))} {shlex.quote(str(sub_plan_path))}\n"
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
        "sub_plan_path": sub_plan_path,
    }


def _build_world_edit_timeout(
    root: Path,
    *,
    step1_gate_command: str = "true",
    step1_gate_timeout: int = 30,
) -> dict:
    """Like _build_world but the stub agent lowers step 1's timeout to 1."""
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

    (plans / f"MASTER-{SLUG}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {SLUG}-execution\n"
        f"batch_date: 2026-10-02\n"
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
        f"    timeout: {step1_gate_timeout}\n"
        "```\n\n"
        "## Findings\n\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    sub_plan_path = plans / f"{STEM}.md"
    edit_script = root / "edit_timeout.py"
    edit_script.write_text(
        "import sys\n"
        "path = sys.argv[1]\n"
        "text = open(path, encoding='utf-8').read()\n"
        "text = text.replace('timeout: 30', 'timeout: 1')\n"
        "open(path, 'w', encoding='utf-8').write(text)\n",
        encoding="utf-8",
    )
    # The stub agent lowers step 1's timeout from 30 to 1.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        f"python3 {shlex.quote(str(edit_script))} {shlex.quote(str(sub_plan_path))}\n"
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
        "sub_plan_path": sub_plan_path,
    }


def _build_world_edit_findings(
    root: Path,
    *,
    step1_gate_command: str = "true",
) -> dict:
    """Control: the stub agent only edits ## Findings, not gates."""
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

    (plans / f"MASTER-{SLUG}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {SLUG}-execution\n"
        f"batch_date: 2026-10-02\n"
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
        "```\n\n"
        "## Findings\n\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    sub_plan_path = plans / f"{STEM}.md"
    edit_script = root / "edit_findings.py"
    edit_script.write_text(
        "import sys\n"
        "path = sys.argv[1]\n"
        "text = open(path, encoding='utf-8').read()\n"
        "text = text.replace('## Findings\\n', '## Findings\\n\\n- test note\\n')\n"
        "open(path, 'w', encoding='utf-8').write(text)\n",
        encoding="utf-8",
    )
    # The stub agent only adds to Findings.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        f"python3 {shlex.quote(str(edit_script))} {shlex.quote(str(sub_plan_path))}\n"
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
        "sub_plan_path": sub_plan_path,
    }


def _run_iteration(world: dict, max_iterations: int = 1) -> subprocess.CompletedProcess:
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run its real main().

    One iteration by default — enough for the gate-first path to run step 0.
    Pass max_iterations=2 when step 1's gate is red and the agent must run.
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


def _gate_snapshot_exists(world: dict) -> bool:
    """Check whether a gates-snapshot file was written for this run."""
    data_home = world["data_home"]
    # The snapshot is written to ${RUN_LOG_DIR}/gates-snapshot-<i>.json
    # which lives under the project's logs/runs/<stamp>/ directory.
    runs_dir = data_home / "projects" / world["key"] / "logs" / "runs"
    if not runs_dir.exists():
        return False
    for run_dir in runs_dir.iterdir():
        if run_dir.is_dir():
            for f in run_dir.iterdir():
                if f.name.startswith("gates-snapshot-") and f.suffix == ".json":
                    return True
    return False


# ── AC-1 (xfail): worker deletes gate ⇒ restored, ship_integrity_violation ──

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(90)  # measured 17.07-17.15 s kills under -n 8 (2026-10-06)
def test_worker_deleting_gate_is_restored_and_parked(tmp_path: Path) -> None:
    """A stub worker deletes step 1's gate from its sub-plan ⇒ the driver
    still runs that gate, the file is restored, and the run exits
    ``ship_integrity_violation``.

    Asserts: the sub-plan file still has step 1's gate after the run,
    the run exits ship_integrity_violation, and the agent was invoked
    (gate-first fell through because the gate was red after restore).
    """
    world = _build_world(tmp_path, step1_gate_command="false")
    proc = _run_iteration(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    combined = proc.stdout + proc.stderr

    # The sub-plan file should be restored — step 1's gate must still exist.
    sub_text = world["sub_plan_path"].read_text(encoding="utf-8")
    assert "timeout:" in sub_text.split("### Step 1")[1].split("##")[0], (
        "step 1's gate was deleted by the worker and NOT restored.\n"
        f"sub-plan content:\n{sub_text}\nlast 40 lines:\n{tail}"
    )

    # The run must exit ship_integrity_violation.
    assert "ship_integrity_violation" in combined, (
        "expected ship_integrity_violation in output, not found.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-2 (xfail): worker edits gate timeout ⇒ restored, ship_integrity ─────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(90)  # measured 17.07-17.15 s kills under -n 8 (2026-10-06)
def test_worker_editing_gate_timeout_is_restored_and_parked(tmp_path: Path) -> None:
    """A stub worker lowers step 1's gate timeout ⇒ the driver restores it
    and exits ``ship_integrity_violation``.

    Asserts: the sub-plan file's step 1 timeout is back to the original
    value after the run, and the run exits ship_integrity_violation.
    """
    world = _build_world_edit_timeout(tmp_path, step1_gate_command="false", step1_gate_timeout=30)
    proc = _run_iteration(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    combined = proc.stdout + proc.stderr

    # The sub-plan file should be restored — step 1's timeout must be 30.
    sub_text = world["sub_plan_path"].read_text(encoding="utf-8")
    step1_section = sub_text.split("### Step 1")[1].split("##")[0]
    assert "timeout: 30" in step1_section, (
        "step 1's timeout was changed to 1 by the worker and NOT restored "
        "to 30.\n"
        f"step 1 section:\n{step1_section}\nlast 40 lines:\n{tail}"
    )

    # The run must exit ship_integrity_violation.
    assert "ship_integrity_violation" in combined, (
        "expected ship_integrity_violation in output, not found.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-3 (control): worker edits Findings only ⇒ no violation ──────────────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(90)  # measured 17.07-17.15 s kills under -n 8 (2026-10-06)
def test_worker_editing_findings_only_no_violation(tmp_path: Path) -> None:
    """A stub worker edits only ``## Findings`` ⇒ no ship_integrity_violation.

    Asserts: the run does NOT exit ship_integrity_violation, and the
    findings edit is preserved.
    """
    world = _build_world_edit_findings(tmp_path, step1_gate_command="false")
    proc = _run_iteration(world, max_iterations=2)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    combined = proc.stdout + proc.stderr

    # No ship_integrity_violation.
    assert "ship_integrity_violation" not in combined, (
        "unexpected ship_integrity_violation for a Findings-only edit.\n"
        f"last 40 lines:\n{tail}"
    )

    # The findings edit should be preserved.
    sub_text = world["sub_plan_path"].read_text(encoding="utf-8")
    assert "- test note" in sub_text, (
        "the worker's Findings edit was not preserved.\n"
        f"sub-plan content:\n{sub_text}"
    )