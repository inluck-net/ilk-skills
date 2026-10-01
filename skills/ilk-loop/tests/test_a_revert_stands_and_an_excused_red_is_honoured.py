"""A reverted ship stays reverted, and an excused red backs the final step.

Part of sub-plan ``a-revert-stands-and-an-excused-red-is-honoured``
(MASTER-2026-10-01b).

Four acceptance criteria, verified against the real
``ship_transition.repair`` and ``ship_integrity.check_final_step_gate``
functions.

The tests use tmp_path repos and synthetic data — no loop runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ship_transition  # noqa: E402
import ship_integrity  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
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


def _make_plans_dir(tmp_path: Path, slug: str, status: str = "in-progress") -> Path:
    plans = tmp_path / "plans"
    plans.mkdir()
    subplan = plans / f"2026-10-01-{slug}.md"
    subplan.write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: 1\n"
        f"estimated_steps: 2\n---\n\n# {slug}\n",
        encoding="utf-8",
    )
    return plans


def _make_ship_reverts(plans: Path, entries: list[dict]) -> None:
    """Write a ship-reverts.jsonl next to the plans dir."""
    path = plans.parent / "runtime" / "launcher"
    path.mkdir(parents=True, exist_ok=True)
    reverts = path / "ship-reverts.jsonl"
    with reverts.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_gate_row(
    slug: str,
    step: int,
    outcome: str = "pass",
    head_sha: str | None = None,
    exit_code: int = 0,
    attribution: dict | None = None,
) -> dict:
    """Build a synthetic gate row."""
    row: dict = {
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
    }
    if head_sha is not None:
        row["head_sha"] = head_sha
    if attribution is not None:
        row["attribution"] = attribution
    return row


# ── AC-1: repair --apply in a worker session refuses ─────────────────────────

class TestRepairWorkerSession:
    """AC-1: repair(apply=True) in a worker session raises unless
    only_interrupted=True and it's the dispatched slug."""

    def test_apply_raises_in_worker_session(self, tmp_path: Path) -> None:
        """With ILK_ITERATION_SUBPLAN=a, repair(apply=True) raises."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, "my-slug")
        # Create a marker commit so there's something to repair
        _git(repo, "commit", "--allow-empty", "-m",
             "chore(plans): my-slug shipped [plan:my-slug#ship]")

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ILK_ITERATION_SUBPLAN", "a")
            with pytest.raises(ship_transition.ShipTransitionError):
                ship_transition.repair(plans, repo, apply=True)

    def test_apply_raises_for_different_slug(self, tmp_path: Path) -> None:
        """With ILK_ITERATION_SUBPLAN=a, repair(apply=True, only_interrupted=True)
        on slug b raises."""
        repo = _make_repo(tmp_path)
        slug = "slug-b"
        plans = _make_plans_dir(tmp_path, slug)
        _git(repo, "commit", "--allow-empty", "-m",
             f"chore(plans): {slug} shipped [plan:{slug}#ship]")

        # Write an intent marker to make this an interrupted ship
        intent = plans / ship_transition.INTENT_FILENAME
        intent.write_text(json.dumps({"slug": slug}), encoding="utf-8")

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ILK_ITERATION_SUBPLAN", "a")
            with pytest.raises(ship_transition.ShipTransitionError):
                ship_transition.repair(
                    plans, repo, apply=True, only_interrupted=True)

    def test_dry_run_allowed_in_worker_session(self, tmp_path: Path) -> None:
        """Dry run (no --apply) is allowed in a worker session.
        This is already the existing behaviour — no xfail needed."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, "my-slug")
        _git(repo, "commit", "--allow-empty", "-m",
             "chore(plans): my-slug shipped [plan:my-slug#ship]")

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ILK_ITERATION_SUBPLAN", "a")
            # Should not raise
            actions = ship_transition.repair(plans, repo, apply=False)
            assert len(actions) >= 0  # just checking it doesn't raise


# ── AC-2: reverted ship stays reverted ───────────────────────────────────────

class TestRevertedShipStaysReverted:
    """AC-2: a marker-without-status whose slug has a ship-reverts.jsonl
    row stays in-progress (reverted-ship kind)."""

    def test_reverted_marker_not_repaired(self, tmp_path: Path) -> None:
        """A marker commit + revert row ⇒ repair reports reverted-ship."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        plans = _make_plans_dir(tmp_path, slug, status="in-progress")

        # Create the marker commit
        _git(repo, "commit", "--allow-empty", "-m",
             f"chore(plans): {slug} shipped [plan:{slug}#ship]")
        marker_sha = _git(repo, "rev-parse", "HEAD")

        # Write a revert entry
        _make_ship_reverts(plans, [{
            "slug": slug,
            "ship_commit": marker_sha,
            "timestamp": "2026-10-01T22:00:00",
            "reason": "gate failed",
        }])

        # Clear worker session env vars to test non-worker path
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("ILK_ITERATION_SUBPLAN", raising=False)
            mp.delenv("ILK_WORKER_SESSION", raising=False)
            actions = ship_transition.repair(plans, repo, apply=True)

        assert len(actions) == 1
        assert actions[0].slug == slug
        assert actions[0].kind == ship_transition.MARKER_WITHOUT_STATUS
        assert not actions[0].applied
        assert "reverted-ship" in (actions[0].reason or "")

        # Verify status unchanged
        subplan = plans / f"2026-10-01-{slug}.md"
        assert "in-progress" in subplan.read_text()


# ── AC-3: interrupted ship still converges (control) ─────────────────────────

class TestInterruptedShipConverges:
    """AC-3: an interrupted ship (intent marker, no revert row) still
    converges. This is the existing behaviour — no xfail."""

    def test_interrupted_converges(self, tmp_path: Path) -> None:
        """An intent marker without a revert row ⇒ repair converges."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        plans = _make_plans_dir(tmp_path, slug, status="in-progress")

        # Create the marker commit
        _git(repo, "commit", "--allow-empty", "-m",
             f"chore(plans): {slug} shipped [plan:{slug}#ship]")

        # Write an intent marker (no revert row)
        intent = plans / ship_transition.INTENT_FILENAME
        intent.write_text(json.dumps({"slug": slug}), encoding="utf-8")

        # Clear worker session env vars to test non-worker path
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("ILK_ITERATION_SUBPLAN", raising=False)
            mp.delenv("ILK_WORKER_SESSION", raising=False)
            actions = ship_transition.repair(plans, repo, apply=True)

        assert len(actions) == 1
        assert actions[0].slug == slug
        assert actions[0].applied
        assert actions[0].kind == ship_transition.MARKER_WITHOUT_STATUS

        # Verify status moved to shipped
        subplan = plans / f"2026-10-01-{slug}.md"
        assert "shipped" in subplan.read_text()


# ── AC-4: check_final_step_gate honours attribution ──────────────────────────

class TestFinalStepGateHonoursAttribution:
    """AC-4: check_final_step_gate accepts a row whose attribution.verdict
    is inherited or pre-existing, as evaluate_ship does."""

    def test_pre_existing_accepted(self, tmp_path: Path) -> None:
        """outcome=fail, attribution pre-existing ⇒ True."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        head_sha = _git(repo, "rev-parse", "HEAD")

        row = _make_gate_row(
            slug, step=1, outcome="fail", head_sha=head_sha,
            attribution={
                "verdict": "pre-existing",
                "iteration_base": "abc1234",
                "nodes": [
                    {"node_id": "test_a", "verdict": "pre-existing"},
                    {"node_id": "test_b", "verdict": "pre-existing"},
                ],
            },
        )

        result = ship_integrity.check_final_step_gate(
            [row], slug, final_step=1, last_step_sha=head_sha, cwd=repo)
        assert result is True

    def test_inherited_accepted(self, tmp_path: Path) -> None:
        """outcome=fail, attribution inherited ⇒ True."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        head_sha = _git(repo, "rev-parse", "HEAD")

        row = _make_gate_row(
            slug, step=1, outcome="fail", head_sha=head_sha,
            attribution={
                "verdict": "inherited",
                "owner_sha": "def5678",
                "owner_slug": "other-slug",
                "nodes": [
                    {"node_id": "test_a", "verdict": "inherited"},
                ],
            },
        )

        result = ship_integrity.check_final_step_gate(
            [row], slug, final_step=1, last_step_sha=head_sha, cwd=repo)
        assert result is True

    def test_mixed_verdict_rejected(self, tmp_path: Path) -> None:
        """One node pre-existing, one owned ⇒ False.
        This already works (outcome != pass), no xfail needed."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        head_sha = _git(repo, "rev-parse", "HEAD")

        row = _make_gate_row(
            slug, step=1, outcome="fail", head_sha=head_sha,
            attribution={
                "verdict": "pre-existing",
                "nodes": [
                    {"node_id": "test_a", "verdict": "pre-existing"},
                    {"node_id": "test_b", "verdict": "owned"},
                ],
            },
        )

        result = ship_integrity.check_final_step_gate(
            [row], slug, final_step=1, last_step_sha=head_sha, cwd=repo)
        assert result is False

    def test_owned_rejected(self, tmp_path: Path) -> None:
        """All nodes owned ⇒ False (this iteration caused the red).
        This already works (outcome != pass), no xfail needed."""
        repo = _make_repo(tmp_path)
        slug = "my-slug"
        head_sha = _git(repo, "rev-parse", "HEAD")

        row = _make_gate_row(
            slug, step=1, outcome="fail", head_sha=head_sha,
            attribution={
                "verdict": "owned",
                "nodes": [
                    {"node_id": "test_a", "verdict": "owned"},
                ],
            },
        )

        result = ship_integrity.check_final_step_gate(
            [row], slug, final_step=1, last_step_sha=head_sha, cwd=repo)
        assert result is False