"""Pin that a sub-plan ships only after its gate passes.

Regression for 2026-10-07 MASTER-2026-10-07k: ship markers were written
before the driver's gate ran, and a red gate still read ``status: shipped``.

  AC-1  ship_transition.ship() refuses when ILK_WORKER_SESSION=1 for EVERY
        sub-plan (not just batch_verification).  Status and git history are
        untouched on refusal.
  AC-2  After the post-iteration gate passes and current_step >= estimated_steps,
        the driver calls ship_transition.py --ship <slug> without
        ILK_WORKER_SESSION.  On a red gate the driver never ships; the
        sub-plan stays in-progress and no #ship marker commit is created.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ship_transition  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


SUBPLAN_TEMPLATE = """---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: 2
last_updated: 2026-10-07
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}
"""


def _make_plans_dir(
    tmp_path: Path,
    slug: str = "my-slug",
    status: str = "in-progress",
    step: int = 2,
) -> Path:
    plans = tmp_path / "plans"
    plans.mkdir()
    fname = f"2026-10-07-{slug}.md"
    (plans / fname).write_text(
        SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step),
        encoding="utf-8",
    )
    return plans


# ── AC-1: ship refuses in worker session for every sub-plan ──────────────────


def test_ship_refuses_in_worker_session_for_regular_subplan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: ship() refuses when dispatched as a worker for a non-batch_verification sub-plan."""
    repo = _make_repo(tmp_path)
    plans = _make_plans_dir(tmp_path, slug="regular-slug", status="in-progress", step=2)
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "regular-slug")
    with pytest.raises(ship_transition.ShipTransitionError, match="refused.*driver"):
        ship_transition.ship(plans, repo, "regular-slug")


def test_ship_refuses_in_worker_session_status_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: refusal leaves status untouched."""
    repo = _make_repo(tmp_path)
    plans = _make_plans_dir(tmp_path, slug="my-slug", status="in-progress", step=2)
    subplan = plans / "2026-10-07-my-slug.md"
    before = subplan.read_text(encoding="utf-8")
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "my-slug")
    with pytest.raises(ship_transition.ShipTransitionError):
        ship_transition.ship(plans, repo, "my-slug")
    after = subplan.read_text(encoding="utf-8")
    assert before == after, "status was changed on refusal"


def test_ship_refuses_in_worker_session_no_marker_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: refusal creates no #ship marker commit."""
    repo = _make_repo(tmp_path)
    plans = _make_plans_dir(tmp_path, slug="my-slug", status="in-progress", step=2)
    before_sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "my-slug")
    with pytest.raises(ship_transition.ShipTransitionError):
        ship_transition.ship(plans, repo, "my-slug")
    after_sha = _git(repo, "rev-parse", "HEAD")
    assert before_sha == after_sha, "a commit was created on refusal"


# ── AC-2: driver ships only after gate passes ────────────────────────────────


def test_ship_succeeds_without_worker_session(tmp_path: Path) -> None:
    """AC-2 (control): ship() succeeds when ILK_WORKER_SESSION is not set."""
    repo = _make_repo(tmp_path)
    plans = _make_plans_dir(tmp_path, slug="ok-slug", status="in-progress", step=2)
    result = ship_transition.ship(plans, repo, "ok-slug")
    assert result.slug == "ok-slug"
    # Verify marker commit exists
    log = _git(repo, "log", "--oneline", "-1")
    assert "ok-slug" in log and "#ship" in log