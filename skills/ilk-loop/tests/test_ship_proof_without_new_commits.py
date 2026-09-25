"""Red-first: a green gate proves a step even with 0 new commits.

On a shared remote, an iteration whose gate passes but produces no commits
writes no ledger row (``total_new > 0`` guard at
``run_ilk_loop_claude.sh:4355``).  ``ship_integrity`` then reports
``ship_integrity_violation`` because the shipped sub-plan has no proof.

Measured on rezmac key
``users-chad-projects-keyreply-kira-cloudflare-resolver-857a9e9``:
run ``20260925-140059``, iter 1 and 2 both had ``new_commits 0`` and gate
pass, exit ``ship_integrity_violation``; ``ship-proof.jsonl`` had 11 rows
and none for ``issue-6747``.

AC-1  ``gate_pass_at_head`` row is written when gate passes with 0 new
      commits, and ``ship_integrity`` reports no violation.
AC-2  (control) no row when the gate is absent or failing.
AC-3  ``check_step_commits`` accepts a ``gate_pass_at_head`` row for the
      steps it names, and only with ``gate_outcome: "pass"`` and
      ``proof: "gate_pass_at_head"``.
AC-4  each early exit in the writer prints ``! [ship-proof]`` on stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import ship_audit

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _subplan(plans: Path, slug: str, current_step: int, steps: int) -> Path:
    sp = plans / f"2026-09-25-{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {steps}\n"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        + "".join(f"### Step {n} — work\n\nBody.\n\n" for n in range(steps)),
        encoding="utf-8",
    )
    return sp


def _make_project(root: Path, subplans: dict[str, tuple[int, int]]) -> Path:
    """A git repo with ``docs/plans`` holding a MASTER + the named sub-plans.

    *subplans* maps slug -> (current_step, estimated_steps).
    """
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True)
    registry = "\n".join(
        f"| {n} | [2026-09-25-{slug}.md](./2026-09-25-{slug}.md) | pending |"
        for n, slug in enumerate(subplans, start=1)
    )
    (plans / "MASTER-2026-09-25-proof.md").write_text(
        "---\n"
        "master_plan: 2026-09-25-proof\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: proof\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"{registry}\n",
        encoding="utf-8",
    )
    for slug, (cur, steps) in subplans.items():
        _subplan(plans, slug, cur, steps)

    # A shared remote — the condition under which trailers do not exist.
    (root / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root* so nothing reads ~/.ilk-data."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    """The ledger's directory, resolved the way the runner resolves it."""
    resolver = SCRIPTS / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env,
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
    gate_outcome: str | None = None,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call the ledger writer for one iteration.

    When *gate_outcome* is set (e.g. ``"pass"`` or ``"fail"``), it is passed
    as a fourth argument to the writer — the seam step 1 adds.
    """
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")

    gate_arg = f"'{gate_outcome}'" if gate_outcome else ""

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
{WRITER_FUNC} '{heads_dir / "before"}' '{heads_dir / "after"}' {iteration} {gate_arg}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env, cwd=str(project),
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

@pytest.mark.xfail(strict=True, reason="red-first: gate_pass_at_head row not yet implemented")
def test_gate_pass_at_head_row_written_for_zero_new_commits(tmp_path: Path) -> None:
    """AC-1 — a green gate with 0 new commits writes a ``gate_pass_at_head`` row.

    The row carries ``proof: "gate_pass_at_head"``, ``gate_outcome: "pass"``,
    ``provenance: "loop-executed"``, and ``commits: []``.  ``ship_integrity``
    must then report no violation for this sub-plan.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (3, 3)})
    env = _sandbox_env(tmp_path)
    head = _git(project, "rev-parse", "HEAD")

    # before == after (0 new commits), gate passes.
    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=head, after=head,
        run_id="20260925-140000", iteration=1,
        gate_outcome="pass",
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected one gate_pass_at_head row for a green gate with 0 commits, "
        f"got {records}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rec = records[0]
    assert rec["slug"] == "gate-work"
    assert rec["commits"] == [], (
        "a gate_pass_at_head row carries empty commits (no new SHAs)"
    )
    assert rec["proof"] == "gate_pass_at_head", (
        f"proof must be 'gate_pass_at_head', got {rec.get('proof')}"
    )
    assert rec["gate_outcome"] == "pass", (
        f"gate_outcome must be 'pass', got {rec.get('gate_outcome')}"
    )
    assert rec["provenance"] == "loop-executed", (
        f"provenance must be 'loop-executed', got {rec.get('provenance')}"
    )
    assert rec.get("head"), (
        "the row must carry the HEAD SHA at gate time"
    )
    assert rec["repo"] == str(project)
    assert rec["run_id"] == "20260925-140000"
    assert rec["iteration"] == 1


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_no_row_when_gate_absent_and_zero_new_commits(tmp_path: Path) -> None:
    """AC-2 (control) — no gate, 0 new commits ⇒ no row.

    This is the current behavior and should pass today.  A
    ``gate_pass_at_head`` row is written only when the gate actually passed.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (0, 3)})
    env = _sandbox_env(tmp_path)
    head = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=head, after=head,
        run_id="20260925-140000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert records == [], (
        f"an iteration with 0 commits and no gate must not write a row; "
        f"got {records}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: check_step_commits does not yet accept gate_pass_at_head")
def test_check_step_commits_accepts_gate_pass_at_head_row(tmp_path: Path) -> None:
    """AC-3 — ``check_step_commits`` accepts a ``gate_pass_at_head`` row.

    The row must carry ``gate_outcome: "pass"`` and ``proof: "gate_pass_at_head"``
    for the reader to accept it.  A row with ``gate_outcome: "fail"`` or a
    different ``proof`` value must NOT be accepted.
    """
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    # The row: a valid gate_pass_at_head record for steps [0, 2).
    head = _git(project, "rev-parse", "HEAD")
    gate_row = {
        "run_id": "20260925-140000",
        "iteration": 1,
        "slug": "gate-work",
        "repo": str(project),
        "step_from": 0,
        "step_to": 2,
        "commits": [],
        "provenance": "loop-executed",
        "proof": "gate_pass_at_head",
        "head": head,
        "gate_outcome": "pass",
    }

    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1], cwd=project, ledger_records=[gate_row],
    )
    assert missing == [], (
        "a gate_pass_at_head row with gate_outcome='pass' must attribute "
        f"the steps it names. Got present={present} missing={missing}"
    )
    assert set(present) == {0, 1}, (
        f"both steps 0 and 1 must be attributed. Got present={present}"
    )

    # A row with gate_outcome != "pass" must NOT be accepted.
    fail_row = {**gate_row, "gate_outcome": "fail"}
    present2, missing2 = ship_audit.check_step_commits(
        "gate-work", [0, 1], cwd=project, ledger_records=[fail_row],
    )
    assert missing2 == [0, 1], (
        "a row with gate_outcome='fail' must NOT attribute steps. "
        f"Got present={present2} missing={missing2}"
    )

    # A row with proof != "gate_pass_at_head" must NOT be accepted.
    other_row = {**gate_row, "proof": "other"}
    present3, missing3 = ship_audit.check_step_commits(
        "gate-work", [0, 1], cwd=project, ledger_records=[other_row],
    )
    assert missing3 == [0, 1], (
        "a row with proof != 'gate_pass_at_head' must NOT attribute steps. "
        f"Got present={present3} missing={missing3}"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first: early exit announcements not yet implemented")
def test_early_exit_announces_on_stderr(tmp_path: Path) -> None:
    """AC-4 — each early exit in the writer prints ``! [ship-proof]`` on stderr.

    The writer has several silent early exits.  Each must announce itself so
    an empty ledger is distinguishable from "the writer skipped".

    This test exercises the "no commits and no green gate" path (heads equal,
    no ``gate_outcome``).
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (0, 3)})
    env = _sandbox_env(tmp_path)
    head = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=head, after=head,
        run_id="20260925-140000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert records == [], (
        f"no rows expected; got {records}"
    )
    assert "! [ship-proof]" in proc.stderr, (
        "the writer must announce on stderr when it writes no rows; "
        f"stderr was:\n{proc.stderr}"
    )