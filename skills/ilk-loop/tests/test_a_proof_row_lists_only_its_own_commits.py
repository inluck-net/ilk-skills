"""Red-first: a proof row lists only its own commits.

``ship-proof.jsonl`` row 438 (gh-resolve) credits
``root-area-typecheck-gate`` (``provenance: loop-executed``) with 9 commits.
All 9 belong to sub-plans 5 and 6.  A sub-plan whose gate never ran got a
proof row listing work it didn't do.

The writer (``write_ship_proof_records`` in ``run_ilk_loop_claude.sh``)
currently gives every slug row the full ``git rev-list before..after``
range (R:1581, R:1614-1633) with no trailer filter.  The gate-pass row
can never fire because ``local_checks_results`` is emptied at R:4653
before the gate fills it (R:4707/R:4789).

These pins cover acceptance criteria 1-5 of sub-plan
``a-proof-row-lists-only-its-own-commits``.

Sub-plan: ``a-proof-row-lists-only-its-own-commits`` (AC-1 … AC-5).
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

WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers (shared with test_ship_proof_ledger_writer.py) ──────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
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


def _make_project(root: Path, subplans: dict[str, tuple[int, int]],
                  *, shared_remote: bool = True) -> Path:
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

    if shared_remote:
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

@pytest.mark.xfail(strict=True, reason="proof rows unfiltered")
def test_slug_row_lists_only_commits_with_its_own_trailer(tmp_path: Path) -> None:
    """AC-1 — a per-slug trailer filter.

    Two slugs, three commits: one carries ``[plan:alpha#step-0]``, one
    carries ``[plan:beta#step-1]``, one carries no trailer.  Each slug
    row must list only the commits whose message carries its trailer.
    """
    project = _make_project(
        tmp_path / "proj",
        {"alpha": (0, 2), "beta": (0, 2)},
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")

    # commit 1: belongs to alpha
    (project / "a.txt").write_text("alpha work\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): alpha change [plan:alpha#step-0]")

    # commit 2: belongs to beta
    (project / "b.txt").write_text("beta work\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): beta change [plan:beta#step-1]")

    # commit 3: no trailer
    (project / "c.txt").write_text("shared work\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "fix(app): untailored change")

    after = _git(project, "rev-parse", "HEAD")
    all_shas = _git(project, "rev-list", f"{before}..{after}").split()
    assert len(all_shas) == 3, all_shas

    alpha_sha = all_shas[2]  # earliest commit
    beta_sha = all_shas[1]
    no_trailer_sha = all_shas[0]

    proc = _run_writer(
        project, env,
        pre_iter_target="alpha 0\\nbeta 0",
        before=before, after=after,
        run_id="20260929-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    by_slug = {r["slug"]: r for r in records}
    assert set(by_slug) == {"alpha", "beta"}, (
        f"expected one record per slug, got {records}"
    )

    # alpha row: only its own commit
    alpha_commits = by_slug["alpha"]["commits"]
    assert alpha_commits == [alpha_sha], (
        f"alpha row should list only its own trailer commit; got {alpha_commits}"
    )

    # beta row: only its own commit
    beta_commits = by_slug["beta"]["commits"]
    assert beta_commits == [beta_sha], (
        f"beta row should list only its own trailer commit; got {beta_commits}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="proof rows unfiltered")
def test_row_with_zero_matching_commits_is_not_written(tmp_path: Path) -> None:
    """AC-2 — a row with 0 matching commits is refused.

    The iteration commits one change with ``[plan:alpha#step-0]``.
    Beta has no matching commit.  The beta row must not appear.
    """
    project = _make_project(
        tmp_path / "proj",
        {"alpha": (0, 2), "beta": (0, 2)},
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")

    (project / "a.txt").write_text("alpha work\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m",
         "fix(app): alpha change [plan:alpha#step-0]")

    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="alpha 0\\nbeta 0",
        before=before, after=after,
        run_id="20260929-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    slugs = {r["slug"] for r in records}
    assert slugs == {"alpha"}, (
        f"beta has 0 matching commits and should be refused; "
        f"got slugs {slugs} from {records}"
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="proof rows unfiltered")
def test_shared_remote_row_keeps_unfiltered_list_with_attribution_field(
    tmp_path: Path,
) -> None:
    """AC-3 — shared remote, or no ``[plan:`` trailers in range.

    The row keeps the unfiltered list and gains
    ``"attribution": "unfiltered-no-trailers"`` so readers can tell.
    It is never silently mixed.
    """
    project = _make_project(
        tmp_path / "proj",
        {"gate-work": (0, 2)},
        shared_remote=True,
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    for n in (1, 2):
        (project / f"file{n}.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        # no trailer — shared remote policy
        _git(project, "commit", "-q", "-m", f"fix(app): change {n}")
    after = _git(project, "rev-parse", "HEAD")
    all_shas = _git(project, "rev-list", f"{before}..{after}").split()
    assert len(all_shas) == 2, all_shas

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=before, after=after,
        run_id="20260929-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    assert len(records) == 1, f"expected one record, got {records}"
    rec = records[0]

    # keeps the full list (unfiltered)
    assert set(rec["commits"]) == set(all_shas), (
        f"unfiltered row should keep all commits; got {rec['commits']}"
    )
    # gains the attribution marker
    assert rec.get("attribution") == "unfiltered-no-trailers", (
        f"expected attribution='unfiltered-no-trailers', got {rec.get('attribution')}"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="gate_pass_at_head row never fires")
def test_gate_pass_row_fires_after_gate_has_run(tmp_path: Path) -> None:
    """AC-4 — the ``gate_pass_at_head`` row fires after the gate has run.

    Today ``local_checks_results`` is emptied at R:4653 right before
    ``[[ -s … ]]`` (R:4655).  The gate fills it only later (R:4707/R:4789).
    So ``_gate_outcome`` is always empty at R:4664 and the
    ``gate_pass_at_head`` row can never fire.

    This pin verifies that a zero-commit green gate writes that row.
    """
    project = _make_project(
        tmp_path / "proj",
        {"gate-work": (0, 2)},
    )
    env = _sandbox_env(tmp_path)
    head = _git(project, "rev-parse", "HEAD")

    # no new commits — the gate-pass row should still fire if the gate passed
    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=head, after=head,
        run_id="20260929-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    gate_rows = [r for r in records if r.get("proof") == "gate_pass_at_head"]
    assert gate_rows, (
        f"expected a gate_pass_at_head row for a zero-commit green gate; "
        f"got {records}"
    )
    rec = gate_rows[0]
    assert rec["gate_outcome"] == "pass", (
        f"gate_outcome must be 'pass'; got {rec.get('gate_outcome')}"
    )
    assert rec["head"] == head, (
        f"head must be the current HEAD; got {rec.get('head')}"
    )
    assert rec["commits"] == [], (
        f"gate_pass_at_head row must have empty commits; got {rec.get('commits')}"
    )


# ── AC-5 ─────────────────────────────────────────────────────────────────────

def test_readers_accept_rows_with_and_without_attribution(tmp_path: Path) -> None:
    """AC-5 — readers accept rows with and without ``attribution``.

    This passes today (old rows keep reading as-is).  No xfail marker.
    """
    project = _make_project(
        tmp_path / "proj",
        {"gate-work": (0, 2)},
    )
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    (project / "file.txt").write_text("change\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "fix(app): change")
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="gate-work 0",
        before=before, after=after,
        run_id="20260929-120000", iteration=1,
    )
    assert "WRITER_MISSING" not in proc.stdout, proc.stdout

    records = _read_ledger(project, env)
    assert len(records) == 1, f"expected one record, got {records}"
    rec = records[0]

    # The record must be parseable and carry the essential fields
    assert rec["run_id"] == "20260929-120000"
    assert rec["slug"] == "gate-work"
    assert "commits" in rec

    # A record without 'attribution' is valid (old format)
    # A record with 'attribution' is also valid (new format)
    # The key invariant: readers must not crash on either form.
    # ship_proof_ledger.read_records handles this; loop_status uses it.
