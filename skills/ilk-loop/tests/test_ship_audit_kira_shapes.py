"""Red-first: the three pv3 false-positive shapes and the 09-08c regression.

All four tests assert POST-fix behaviour and are RED under current code
by design — red-first is the point.

Regression lineage: 2026-09-18 retro §2, §5.  Measured on keyreply/
kira-cloudflare pv3: 13 re-dispatch runs, ~2.6 h of runner wall time.

Fixture pattern borrowed from test_ship_audit_ledger_gap.py — each shape
is a tmp git repo with the minimal commits and ledger records needed to
reproduce the failure.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ship_audit


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _project(
    root: Path,
    *,
    slug: str = "pv3-work",
    steps: int = 4,
    trailered_steps: "set[int] | None" = None,
    ship_marker: bool = True,
) -> "tuple[Path, list[str]]":
    """Create a tmp repo with one commit per step.

    ``trailered_steps=None`` means every step carries a plan trailer.
    An empty set means none do (shared-remote shape — trailers stripped).
    A non-empty set means only those steps carry trailers.
    ``ship_marker=True`` appends the unconditional ``#ship`` commit.
    """
    if trailered_steps is None:
        trailered_steps = set(range(steps))
    project = root / "proj"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    shas: "list[str]" = []
    for n in range(steps):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        msg = f"fix(app): step {n} work"
        if n in trailered_steps:
            msg += f" [plan:{slug}#step-{n}]"
        _git(project, "commit", "-q", "-m", msg)
        shas.append(_git(project, "rev-parse", "HEAD"))

    if ship_marker:
        _git(project, "commit", "-q", "--allow-empty", "-m",
             f"chore(plans): {slug} shipped [plan:{slug}#ship]")
        shas.append(_git(project, "rev-parse", "HEAD"))
    return project, shas


def _ledger(slug: str, project: Path, shas: "list[str]",
            step_from: int, step_to: int) -> list[dict]:
    """Build a single ledger record spanning [step_from, step_to)."""
    return [{
        "run_id": "test-run",
        "iteration": 1,
        "slug": slug,
        "repo": str(project),
        "step_from": step_from,
        "step_to": step_to,
        "commits": shas[step_from:step_to],
    }]


# ── AC-1 shapes (must audit clean after fix) ─────────────────────────────────

def test_pv3_authz_ledger_only_audits_clean(tmp_path: Path) -> None:
    """pv3-authz: 3 steps, all in ledger, no step trailers.

    The shared-remote policy stripped every [plan:<slug>#step-N].  The
    audit's discriminator sees the unconditional #ship marker, wrongly
    treats it as "this slug carries trailers", disables the ledger union,
    and reports steps 0-2 as missing — 13 re-dispatch runs.
    """
    slug = "pv3-authz"
    project, shas = _project(
        tmp_path, slug=slug, steps=3, trailered_steps=set(), ship_marker=True,
    )
    ledger = _ledger(slug, project, shas, 0, 3)

    present, missing = ship_audit.check_step_commits(
        slug, [0, 1, 2], cwd=project, ledger_records=ledger,
    )
    assert missing == [], (
        f"ledger [0,3) must prove all 3 steps. Got present={present} missing={missing}"
    )


def test_pv3_dispatch_ledger_only_audits_clean(tmp_path: Path) -> None:
    """pv3-dispatch: 4 steps, all in ledger, no step trailers.

    Same mechanism as pv3-authz — discriminator matches #ship, disables
    the ledger union, steps 0-3 reported missing.
    """
    slug = "pv3-dispatch"
    project, shas = _project(
        tmp_path, slug=slug, steps=4, trailered_steps=set(), ship_marker=True,
    )
    ledger = _ledger(slug, project, shas, 0, 4)

    present, missing = ship_audit.check_step_commits(
        slug, [0, 1, 2, 3], cwd=project, ledger_records=ledger,
    )
    assert missing == [], (
        f"ledger [0,4) must prove all 4 steps. Got present={present} missing={missing}"
    )


def test_pv3_flow_revert_mixed_trailers_and_ledger_audits_clean(
    tmp_path: Path,
) -> None:
    """pv3-flow-revert: 6 steps, trailers on 4/5, ledger on 0-3.

    After the discriminator fix (F1), the #ship marker is no longer
    treated as step evidence, so the ledger union runs.  After per-record
    trust (F2), ledger record [0,4) has no step-trailers in its range and
    is trusted — proves steps 0-3.  Steps 4-5 are proven by their own
    step trailers.  All 6 steps proven.
    """
    slug = "pv3-flow-revert"
    project, shas = _project(
        tmp_path, slug=slug, steps=6, trailered_steps={4, 5}, ship_marker=True,
    )
    ledger = _ledger(slug, project, shas, 0, 4)

    present, missing = ship_audit.check_step_commits(
        slug, [0, 1, 2, 3, 4, 5], cwd=project, ledger_records=ledger,
    )
    assert missing == [], (
        f"steps 0-3 via ledger, 4-5 via trailers. Got present={present} missing={missing}"
    )


# ── AC-2 regression (must remain flagged after fix) ──────────────────────────

def test_0908c_shape_record_spanning_trailer_steps_is_rejected(
    tmp_path: Path,
) -> None:
    """09-08c regression: ledger [0,4) but steps 0/1/3 carry trailers.

    Measured 2026-09-08 on batch 2026-09-08c: a ledger record claiming
    ``step_from:0, step_to:4`` (3 SHAs) attributed step 2 for a sub-plan
    carrying trailers on steps 0, 1, and 3 with no commit at all for step
    2, and the audit returned ``missing_steps=[]`` — a false pass.

    A ledger record whose [step_from, step_to) range includes a step
    carrying a step-trailer must NOT be trusted for that range.  Step 2
    has no trailer and no trusted ledger coverage — it is a real gap.
    """
    slug = "pv3-gap"
    project, shas = _project(
        tmp_path, slug=slug, steps=4, trailered_steps={0, 1, 3}, ship_marker=True,
    )
    ledger = _ledger(slug, project, shas, 0, 4)

    present, missing = ship_audit.check_step_commits(
        slug, [0, 1, 2, 3], cwd=project, ledger_records=ledger,
    )
    assert missing == [2], (
        "step 2 has no trailer and its ledger record overlaps trailer'd "
        f"steps 0/1/3 — must be flagged. Got present={present} missing={missing}"
    )
