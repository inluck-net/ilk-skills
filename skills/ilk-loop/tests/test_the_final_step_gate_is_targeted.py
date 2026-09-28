"""Red-first: a shipped sub-plan's final step is targeted by the ledger.

When ``ship_transition.py`` ships a sub-plan it writes ``status: shipped`` but
does **not** bump ``current_step``.  The ship-proof ledger writer
(``write_ship_proof_records``) reads ``current_step`` from ``loop_status --json``
as ``step_to``, so a sub-plan shipped at ``current_step: N-1`` with
``estimated_steps: N`` gets a ledger row ending at ``N-1`` — and step ``N-1``'s
per-step ``local_checks`` are never targeted.

The fix: when the probe reports ``status: shipped`` **or** the iteration's new
commits carry ``[plan:<slug>#ship]``, use ``estimated_steps`` as ``step_to``.

  AC-1  shipped sub-plan, ``estimated_steps: 2``, ``current_step: 1`` ⇒
        ``step_to: 2`` in the ledger row (xfail: red-first)
  AC-2  control — not shipped, same values ⇒ ``step_to: 1`` (passes today)
  AC-3  ``#ship`` trailer in new commits but probe says ``in-progress`` ⇒
        ``step_to: 2`` (xfail: red-first)
  AC-4  ``estimated_steps`` missing ⇒ ``step_to`` stays ``current_step``
        and the warning line is printed (xfail: red-first)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The seam the test depends on.  Named so every assertion fails loudly
#: (rather than vacuously passing on an absent function) until it exists.
WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _make_shipped_project(
    root: Path,
    *,
    slug: str = "final-step",
    current_step: int = 1,
    status: str = "shipped",
    estimated_steps: int = 2,
) -> Path:
    """A git repo with a MASTER and a shipped sub-plan at ``current_step: 1``."""
    project = root / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-09-28-final.md").write_text(
        "---\n"
        "master_plan: 2026-09-28-final\n"
        "batch_date: 2026-09-28\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: final\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"| 1 | [2026-09-28-{slug}.md](./2026-09-28-{slug}.md) | {status} |\n",
        encoding="utf-8",
    )
    # Sub-plan at step 1 of 2, shipped via ship_transition.py which does NOT
    # bump current_step.  Step 1 declares per-step local_checks (the gate
    # that never runs under the current writer).
    est_line = f"estimated_steps: {estimated_steps}\n" if estimated_steps >= 0 else ""
    (plans / f"2026-09-28-{slug}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"{est_line}"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        "### Step 0 — initial work\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step0-gate\n    timeout: 30\n```\n\n"
        "### Step 1 — final step with gate\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step1-gate\n    timeout: 30\n```\n\n",
        encoding="utf-8",
    )
    # Personal remote — trailers exist and the commit includes #step-0.
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    return project


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root*."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    """The ledger's directory, resolved the way the runner resolves it."""
    resolver = RUNNER.parent / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return Path(json.loads(proc.stdout)["external_launcher_dir"])


def _run_writer(
    project: Path,
    env: dict[str, str],
    *,
    pre_iter_target: str,
    before: str,
    after: str,
    run_id: str,
    iteration: int,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call the ledger writer for one iteration."""
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")

    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='{run_id}'
LOOP_STATUS_SCRIPT='{RUNNER.parent / "loop_status.py"}'
PRE_ITER_TARGET=$'{pre_iter_target}'
set +e
declare -F {WRITER_FUNC} >/dev/null || {{ echo "WRITER_MISSING"; exit 90; }}
{WRITER_FUNC} '{heads_dir / "before"}' '{heads_dir / "after"}' {iteration}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


def _read_ledger(project: Path, env: dict[str, str]) -> list[dict]:
    ledger = _launcher_dir(project, env) / "ship-proof.jsonl"
    if not ledger.exists():
        return []
    out = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ── AC-1 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_shipped_subplan_final_step_is_targeted(tmp_path: Path) -> None:
    """AC-1 — a shipped sub-plan's ledger row has step_to == estimated_steps.

    The repro shape: sub-plan shipped via ``ship_transition.py`` at
    ``current_step: 1`` (estimated_steps: 2).  The writer must emit
    ``step_to: 2`` so ``get_local_check_targets`` (via the ledger) targets
    step 1.
    """
    project = _make_shipped_project(tmp_path)
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    # Step 0 commit
    (project / "step0.txt").write_text("step 0\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): initial work [plan:final-step#step-0]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="final-step 0",
        before=before, after=after,
        run_id="20260928-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected 1 ledger record, got {len(records)}: {records}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rec = records[0]
    assert rec["slug"] == "final-step"
    assert rec["step_to"] == 2, (
        "a shipped sub-plan must cover its final step in the ledger row: "
        "step_to == estimated_steps (2), not current_step (1).\n"
        f"record: {rec}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_unshipped_subplan_uses_current_step(tmp_path: Path) -> None:
    """AC-2 — control: not shipped, same values ⇒ step_to == current_step.

    This is the existing behaviour and must pass before the fix.
    """
    project = _make_shipped_project(
        tmp_path, status="in-progress", current_step=1,
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    (project / "step0.txt").write_text("step 0\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): initial work [plan:final-step#step-0]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="final-step 0",
        before=before, after=after,
        run_id="20260928-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected 1 record, got {len(records)}: {records}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rec = records[0]
    assert rec["step_from"] == 0
    assert rec["step_to"] == 1, (
        "an unshipped sub-plan must use current_step (1) as step_to, "
        "not estimated_steps.\n"
        f"record: {rec}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ship_trailer_in_new_commits_overrides_step_to(tmp_path: Path) -> None:
    """AC-3 — ``#ship`` trailer in the iteration's commits ⇒ step_to == 2.

    Covers the case where the ship marker exists in git but the probe still
    reports ``in-progress`` (e.g. the agent wrote the marker but
    ``ship_transition.py``'s status write lagged or was refused).
    """
    project = _make_shipped_project(
        tmp_path, status="in-progress", current_step=1,
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    # Step 0 commit
    (project / "step0.txt").write_text("step 0\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): initial work [plan:final-step#step-0]")
    # Ship marker commit (no step trailer — just the ship)
    (project / "ship.txt").write_text("ship\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "chore(plans): final-step shipped [plan:final-step#ship]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="final-step 0",
        before=before, after=after,
        run_id="20260928-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected 1 record, got {len(records)}: {records}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rec = records[0]
    assert rec["step_to"] == 2, (
        "a #ship trailer in new commits must trigger estimated_steps as step_to "
        "even when the probe still reports in-progress.\n"
        f"record: {rec}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_missing_estimated_steps_falls_back_to_current_step(
    tmp_path: Path,
) -> None:
    """AC-4 — ``estimated_steps`` absent from probe ⇒ step_to stays
    ``current_step`` and the ``! [ship-proof] … unresolved`` line is printed.

    The writer must not guess.  A missing field is an honest "I don't know",
    not a signal to use 0 or to skip the row silently.
    """
    project = _make_shipped_project(
        tmp_path, current_step=1, estimated_steps=-1,  # -1 = omit the field
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    (project / "step0.txt").write_text("step 0\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): initial work [plan:final-step#step-0]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="final-step 0",
        before=before, after=after,
        run_id="20260928-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    stderr = proc.stderr
    assert "unresolved" in stderr.lower() or "estimated_steps" in stderr.lower(), (
        "the writer must print a warning when estimated_steps cannot be "
        "resolved for a shipped sub-plan.\n"
        f"stderr: {stderr}\nstdout: {proc.stdout}"
    )

    records = _read_ledger(project, env)
    if records:
        rec = records[0]
        assert rec["step_to"] == 1, (
            "without estimated_steps, step_to must fall back to current_step (1).\n"
            f"record: {rec}\nstderr: {stderr}"
        )