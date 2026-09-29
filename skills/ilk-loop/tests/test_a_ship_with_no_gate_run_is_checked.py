"""Pins: a ship with no gate run is checked.

Part of sub-plan ``a-ship-with-no-gate-run-is-checked``.

Acceptance criteria:
  AC-1  A no-gate-ran ship is checked against gate-history.jsonl.
  AC-2  A prior-run ship is untouched.  (PASSES TODAY — unmarked.)
  AC-3  batch_verification sub-plans are included.
  AC-4  The master's status line — shipped while sub-plans not shipped ⇒ reverted.
  AC-5  Replay pins (runner subprocess):
        (a) 28c shape: dispatched slug, stub edits shipped, no step commit,
            only a fail row ⇒ reverted.
        (b) D-427 shape: batch_verification sub-plan + master edited shipped,
            no gate run ⇒ both reverted.
        (c) green control: a pass final-step row in history at a descendant
            head ⇒ kept.
  AC-6  Unchanged: a gate that ran this iteration is enforced as before.
        (PASSES TODAY — unmarked.)

The ``xfail`` tests target the **final-step gate** path specifically.
They construct a sub-plan with NO ``local_checks`` fence so the runner sets
``gate_passed="nogate"`` → scope check → ``"skip"`` → the final-step gate
check is the only enforcement.  Today that check is skipped (guard at
``:3161``), so the ship stands unchecked.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

SLUG = "no-gate-checked"
STEM = f"2026-09-29-{SLUG}"
VERIFY_SLUG = "no-gate-verify"
VERIFY_STEM = f"2026-09-29-{VERIFY_SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    cp = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return cp.stdout.strip()


def _build_world(
    root: Path,
    *,
    batch_verification: bool = False,
    gate_history_fail: bool = True,
    with_step_commit: bool = False,
) -> dict:
    """A project with one dispatched sub-plan (2 steps).

    Step 0 already committed.  The stub agent edits the sub-plan to
    ``shipped`` without running a gate.

    When *with_step_commit* is True the stub also commits step 1 with
    ``[plan:<slug>#step-1]`` — needed for the green control (AC-5c) so
    ``find_last_step_commit`` returns a SHA.

    Gate-history carries either a ``fail`` or ``pass`` row for the final
    step (controlled by *gate_history_fail*).
    """
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Master: active, one sub-plan (two if batch_verification).
    master_body = (
        "---\n"
        "master_plan: 2026-09-29\n"
        "batch_date: 2026-09-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n"
    )
    if batch_verification:
        master_body += (
            f"| 2 | [{VERIFY_SLUG}](./{VERIFY_STEM}.md) |\n"
        )
    (plans / "MASTER-2026-09-29.md").write_text(master_body, encoding="utf-8")

    # Sub-plan: in-progress, step 1 of 2 (step 0 done, step 1 pending).
    # NO local_checks fence — the "no gate ran" shape.  The runner sets
    # gate_passed="nogate" → scope check → "skip" → final-step gate is
    # the only enforcement path.
    bv_flag = "\nbatch_verification: true\n" if batch_verification else ""
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        f"{bv_flag}"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — first step\n\n"
        "Done.\n\n"
        "### Step 1 — final step\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    if batch_verification:
        (plans / f"{VERIFY_STEM}.md").write_text(
            "---\n"
            f"plan: {VERIFY_SLUG}\n"
            "status: in-progress\n"
            "current_step: 1\n"
            "estimated_steps: 2\n"
            "\nbatch_verification: true\n"
            "---\n\n"
            f"# {VERIFY_SLUG}\n\n"
            "### Step 0 — first step\n\n"
            "Done.\n\n"
            "### Step 1 — final step\n\n"
            "Body.\n",
            encoding="utf-8",
        )

    # Write a gate-history.jsonl with a row for the final step.
    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True)
    history_path = launcher_dir / "gate-history.jsonl"

    # For the green control we need the pass row's head_sha to be at or
    # after the step commit.  We write the history AFTER the stub runs
    # (below) so we can capture the step commit SHA.  For the fail case
    # we write it now with a bogus SHA.
    if gate_history_fail:
        history_path.write_text(
            json.dumps({
                "slug": SLUG,
                "step": 1,
                "outcome": "fail",
                "exit_code": 1,
                "head_sha": "deadbeef" * 5,
                "run_id": "prior-run",
                "iteration": 1,
                "timestamp": "2026-09-29T10:00:00+0800",
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"

    # Build the stub agent script.
    stub_lines = [
        "#!/usr/bin/env bash",
        f"SP={str(plans / f'{STEM}.md')!r}",
    ]

    if with_step_commit:
        # Commit step 1 with the trailer (green control shape).
        stub_lines += [
            f"echo 'step 1 work' > {project}/step1.txt",
            f"git -C {project} add step1.txt",
            "git -c user.email=t@example.com -c user.name=t "
            f'-C {project} commit -q -m "feat: step 1 [plan:{SLUG}#step-1]"',
        ]

    # Mark as shipped (no gate run).
    stub_lines += [
        "python3 - \"$SP\" <<'EOP'",
        "import re, sys",
        "from pathlib import Path",
        "p = Path(sys.argv[1]); b = p.read_text()",
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)",
        "b = re.sub(r'^current_step: 1', 'current_step: 2', b, count=1, flags=re.M)",
        "p.write_text(b)",
        "EOP",
    ]

    if batch_verification:
        # Also ship the verify sub-plan and the master.
        stub_lines += [
            f"VP={str(plans / f'{VERIFY_STEM}.md')!r}",
            "python3 - \"$VP\" <<'EOP'",
            "import re, sys",
            "from pathlib import Path",
            "p = Path(sys.argv[1]); b = p.read_text()",
            "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)",
            "b = re.sub(r'^current_step: 1', 'current_step: 2', b, count=1, flags=re.M)",
            "p.write_text(b)",
            "EOP",
            f"MP={str(plans / 'MASTER-2026-09-29.md')!r}",
            "python3 - \"$MP\" <<'EOP'",
            "import re, sys",
            "from pathlib import Path",
            "p = Path(sys.argv[1]); b = p.read_text()",
            "b = re.sub(r'^status: active', 'status: shipped', b, count=1, flags=re.M)",
            "p.write_text(b)",
            "EOP",
        ]

    stub_lines.append("echo 'stub agent done'\n")
    stub.write_text("\n".join(stub_lines), encoding="utf-8")
    stub.chmod(0o755)

    # For the green control, write the pass history row AFTER the stub
    # has been defined (it will run during the iteration).  We use the
    # current HEAD as the pass row's head_sha — the step commit will be
    # a descendant, so the ancestry check passes.
    if not gate_history_fail:
        head_sha = _git(project, "rev-parse", "HEAD")
        history_path.write_text(
            json.dumps({
                "slug": SLUG,
                "step": 1,
                "outcome": "pass",
                "exit_code": 0,
                "head_sha": head_sha,
                "run_id": "prior-run",
                "iteration": 1,
                "timestamp": "2026-09-29T10:00:00+0800",
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    return {
        "project": project, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir,
    }


def _build_world_with_prior_ship(root: Path) -> dict:
    """A project where the sub-plan is ALREADY shipped before the iteration.

    This is the AC-2 / AC-6 shape: a prior-run ship must not be re-litigated.
    """
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-09-29.md").write_text(
        "---\n"
        "master_plan: 2026-09-29\n"
        "batch_date: 2026-09-29\n"
        "status: shipped\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    # Already shipped, no local_checks.
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: shipped\n"
        "current_step: 2\n"
        "estimated_steps: 2\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — first step\n\n"
        "Done.\n\n"
        "### Step 1 — final step\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Gate history with only a fail row.
    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True)
    (launcher_dir / "gate-history.jsonl").write_text(
        json.dumps({
            "slug": SLUG,
            "step": 1,
            "outcome": "fail",
            "exit_code": 1,
            "head_sha": "deadbeef" * 5,
            "run_id": "prior-run",
            "iteration": 1,
            "timestamp": "2026-09-29T10:00:00+0800",
        }, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # No-op agent: does nothing.
    stub.write_text("#!/usr/bin/env bash\necho 'no-op agent'\n", encoding="utf-8")
    stub.chmod(0o755)

    return {
        "project": project, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir,
    }


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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, cwd=str(root),
    )


def _read_subplan_status(world: dict, stem: str = STEM) -> str:
    f = world["plans"] / f"{stem}.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_master_status(world: dict) -> str:
    f = world["plans"] / "MASTER-2026-09-29.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


# ── AC-1: no-gate-ran ship is checked ────────────────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(
    strict=True,
    reason="no-gate-ran ship not checked — guard gate_passed != skip at :3161",
)
def test_no_gate_ran_ship_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: sub-plan shipped with no gate this iteration, only a fail row in
    gate-history ⇒ should be reverted to in-progress.

    The sub-plan has no ``local_checks`` fence, so the runner sets
    ``gate_passed="skip"`` and the final-step gate check is skipped today.
    """
    root = tmp_path_factory.mktemp("no-gate-ran")
    world = _build_world(root, gate_history_fail=True, with_step_commit=False)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"sub-plan status is {status!r}, expected 'in-progress' — "
        f"no-gate-ran ship was not checked.\n{tail}"
    )


# ── AC-2: prior-run ship is untouched (PASSES TODAY) ─────────────────────────

@_NEEDS_GTIMEOUT
def test_prior_run_ship_untouched(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: a sub-plan already shipped before this iteration is never
    re-litigated.  (PASSES TODAY — no xfail.)"""
    root = tmp_path_factory.mktemp("prior-ship")
    world = _build_world_with_prior_ship(root)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "shipped", (
        f"sub-plan status is {status!r}, expected 'shipped' — "
        f"prior-run ship was re-litigated.\n{tail}"
    )


# ── AC-3: batch_verification sub-plans are included ──────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(
    strict=True,
    reason="batch_verification sub-plan not checked when shipped with no gate",
)
def test_batch_verification_ship_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: a batch_verification sub-plan hand-edited to shipped with no gate
    run ⇒ should be reverted."""
    root = tmp_path_factory.mktemp("bv-ship")
    world = _build_world(
        root, batch_verification=True,
        gate_history_fail=True, with_step_commit=False,
    )
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"sub-plan status is {status!r}, expected 'in-progress' — "
        f"batch_verification ship was not checked.\n{tail}"
    )


# ── AC-4: master's status line ───────────────────────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(
    strict=True,
    reason="master shipped while sub-plans not shipped — not reverted to active",
)
def test_master_shipped_while_subplans_not_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-4: master turned shipped while any registry sub-plan is not shipped
    after the checks ⇒ master should be set back to active."""
    root = tmp_path_factory.mktemp("master-ship")
    world = _build_world(
        root, batch_verification=True,
        gate_history_fail=True, with_step_commit=False,
    )
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    master_status = _read_master_status(world)
    assert master_status == "active", (
        f"master status is {master_status!r}, expected 'active' — "
        f"master shipped while sub-plans not shipped was not reverted.\n{tail}"
    )


# ── AC-5a: 28c shape — dispatched slug, no step commit, fail row ⇒ reverted ──

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(
    strict=True,
    reason="dispatched slug with no step commit and fail row not reverted",
)
def test_28c_shape_dispatched_slug_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-5a: dispatched slug, stub agent edits shipped, no step commit,
    only a fail row in history ⇒ reverted."""
    root = tmp_path_factory.mktemp("28c-shape")
    world = _build_world(root, gate_history_fail=True, with_step_commit=False)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"sub-plan status is {status!r}, expected 'in-progress' — "
        f"28c shape not caught.\n{tail}"
    )


# ── AC-5b: D-427 shape — batch_verification + master, no gate ⇒ both reverted ─

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(
    strict=True,
    reason="batch_verification + master ship with no gate not reverted",
)
def test_d427_shape_batch_verify_and_master_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-5b: batch_verification sub-plan plus its master edited shipped,
    no gate run ⇒ both reverted."""
    root = tmp_path_factory.mktemp("d427-shape")
    world = _build_world(
        root, batch_verification=True,
        gate_history_fail=True, with_step_commit=False,
    )
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    sp_status = _read_subplan_status(world)
    verify_status = _read_subplan_status(world, VERIFY_STEM)
    master_status = _read_master_status(world)

    assert sp_status == "in-progress", (
        f"sub-plan status is {sp_status!r}, expected 'in-progress'.\n{tail}"
    )
    assert verify_status == "in-progress", (
        f"verify sub-plan status is {verify_status!r}, expected 'in-progress'.\n{tail}"
    )
    assert master_status == "active", (
        f"master status is {master_status!r}, expected 'active'.\n{tail}"
    )


# ── AC-5c: green control — pass row in history ⇒ kept ───────────────────────

@_NEEDS_GTIMEOUT
def test_green_control_pass_in_history_kept(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-5c: a pass final-step row in gate-history at a descendant head ⇒
    sub-plan stays shipped.  The stub commits step 1 with the trailer so
    ``find_last_step_commit`` returns a SHA, and the pass row's ``head_sha``
    is an ancestor of that commit.  (PASSES TODAY — no xfail.)"""
    root = tmp_path_factory.mktemp("green-control")
    world = _build_world(root, gate_history_fail=False, with_step_commit=True)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "shipped", (
        f"sub-plan status is {status!r}, expected 'shipped' — "
        f"green control failed.\n{tail}"
    )
