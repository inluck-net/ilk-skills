"""Red-first: the driver must record which commits belong to which step.

``SKILL.md``'s shared-remote trailer policy strips ``[plan:<slug>#step-N]``
from commit messages on any repo whose ``.ilk-remote-type`` is ``shared``.
``ship_audit.check_step_commits`` (``ship_audit.py:70-92``) matches a step's
commit *only* by that trailer, so on a shared remote every sub-plan ships
``(!) unproven`` no matter how many real commits it has.  Measured on run
``20260828-211346``: 3 real commits, audited as ``0 of 3 authored steps
committed``.

The fix (MASTER judgment call 1) is a runtime ledger sidecar at
``<external_launcher_dir>/ship-proof.jsonl``, written by the driver, which
already holds every field: ``PRE_ITER_TARGET``
(``run_ilk_loop_claude.sh:1929``) gives slug + step-before, and
``heads_before_file`` / ``heads_after_file`` (``:1920``, ``:1991``) give the
SHA range.

These three tests cover the WRITER half:

  AC-1  one record per worked sub-plan, carrying run_id, iteration, slug,
        repo, step_from, step_to and the iteration's commit SHAs
  AC-2  no commits in the iteration ⇒ no record
  AC-5  two slugs worked in one iteration ⇒ two records

Sub-plan: ``a-shared-remote-ship-can-be-proven`` (AC-1, AC-2, AC-5).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The seam step 1 adds to the runner.  Named here so every assertion below
#: fails loudly (rather than vacuously passing on an absent function) until it
#: exists — an undefined shell function also writes no ledger, which is
#: exactly what AC-2 asserts.
WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _subplan(plans: Path, slug: str, current_step: int, steps: int) -> Path:
    sp = plans / f"2026-08-29-{slug}.md"
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
        f"| {n} | [2026-08-29-{slug}.md](./2026-08-29-{slug}.md) | pending |"
        for n, slug in enumerate(subplans, start=1)
    )
    (plans / "MASTER-2026-08-29-ledger.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-ledger\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ledger\n\n"
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
    resolver = RUNNER.parent / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True, text=True, timeout=60, env=env,
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
        capture_output=True, text=True, timeout=120, env=env, cwd=str(project),
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

def test_productive_iteration_writes_one_record_per_subplan(tmp_path: Path) -> None:
    """AC-1 — the record carries every field attribution needs.

    Attribution has to survive a commit message that, by policy, says nothing
    about the plan.  Every field below is one the driver already holds at
    ``:1991``; none is re-derived from a commit message.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (3, 3)})
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    for n in (1, 2):
        (project / f"file{n}.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        # A shared-remote message: no [plan:...#step-N] trailer, by policy.
        _git(project, "commit", "-q", "-m", f"fix(app): change {n}")
    after = _git(project, "rev-parse", "HEAD")
    shas = _git(project, "rev-list", f"{before}..{after}").split()
    assert len(shas) == 2, shas

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=before, after=after,
        run_id="20260829-120000", iteration=4,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner — the ledger writer "
        f"(step 1) has not landed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert len(records) == 1, (
        f"expected exactly one ledger record for one worked sub-plan, got "
        f"{records}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rec = records[0]
    assert rec["run_id"] == "20260829-120000"
    assert rec["iteration"] == 4
    assert rec["slug"] == "gate-work"
    assert Path(rec["repo"]) == project
    assert rec["step_from"] == 0, (
        "step_from must be the step the iteration STARTED on (PRE_ITER_TARGET)"
    )
    assert rec["step_to"] == 3, (
        "step_to must be the sub-plan's current_step after the iteration — "
        "the half-open [step_from, step_to) range is what the audit reads"
    )
    assert set(rec["commits"]) == set(shas), (
        f"commits must be the iteration's before..after range; got "
        f"{rec['commits']}, expected {shas}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_unproductive_iteration_writes_no_record(tmp_path: Path) -> None:
    """AC-2 — an iteration that committed nothing must not claim a step.

    A record with an empty ``commits`` list would prove a step that has no
    commit, which is the precise failure the audit exists to catch.
    """
    project = _make_project(tmp_path / "proj", {"gate-work": (0, 3)})
    env = _sandbox_env(tmp_path)
    head = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=head, after=head,
        run_id="20260829-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner — this assertion would "
        "otherwise pass vacuously (an absent function also writes nothing).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    assert records == [], (
        f"an iteration with zero new commits wrote {records}; a ledger record "
        "is a claim that commits exist for a step range"
    )


# ── AC-5 ─────────────────────────────────────────────────────────────────────

def test_two_slugs_in_one_iteration_write_two_records(tmp_path: Path) -> None:
    """AC-5 — one record per slug, not one per iteration.

    This is judgment call 1's falsifier in the MASTER: a runner that commits
    for more than one slug in a single iteration needs a record per slug, not
    a schema change.  The reader's tolerance is asserted separately
    (``test_ship_proof_ledger_attribution.py``); this is the writer half.
    """
    project = _make_project(
        tmp_path / "proj",
        {"gate-work": (2, 2), "second-work": (3, 3)},
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    (project / "file.txt").write_text("change\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "fix(app): work for both sub-plans")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0\\nsecond-work 1",
        before=before, after=after,
        run_id="20260829-120000", iteration=7,
    )
    assert "WRITER_MISSING" not in proc.stdout, (
        f"{WRITER_FUNC} is not defined in the runner.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    records = _read_ledger(project, env)
    by_slug = {r["slug"]: r for r in records}
    assert set(by_slug) == {"gate-work", "second-work"}, (
        f"expected one record per worked slug, got {records}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert all(r["iteration"] == 7 for r in records), (
        "both records belong to the same iteration — the reader must tolerate "
        "more than one record per (run_id, iteration)"
    )
    assert by_slug["gate-work"]["step_from"] == 0
    assert by_slug["gate-work"]["step_to"] == 2
    assert by_slug["second-work"]["step_from"] == 1
    assert by_slug["second-work"]["step_to"] == 3
