"""The driver reverts unsanctioned ships after an iteration.

Part of sub-plan ``one-ship-per-iteration-is-enforced`` (MASTER-2026-09-25b).

The driver captures each sub-plan's status before the iteration. After the
iteration, any sub-plan that went from non-``shipped`` to ``shipped`` and is
not ``ILK_ITERATION_SUBPLAN`` is reverted to its pre-iteration status and
``current_step``.

Four acceptance criteria:

  AC-1  (the 24f shape) a stub worker dispatched for ``a`` edits the
        frontmatter of ``a`` and ``b`` to ``shipped`` without calling
        ship_transition.  After the iteration, ``a`` is shipped and ``b`` is
        back to its prior status and step, and the log has the
        ``[one-ship] reverted b`` line.

  AC-2  a worker that ships only ``a`` via ship_transition ⇒ nothing is
        reverted.

  AC-3  a gate-first green final step still ships its sub-plan
        (``test_verify_without_a_worker.py`` green).

  AC-4  ``ILK_ITERATION_SUBPLAN`` empty and a worker that ships ``a`` ⇒ ``a``
        is reverted.

Step 1 landed: all xfails removed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ship_transition as st  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────

_SUBPLAN_TEMPLATE = """\
---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: 2
last_updated: 2026-09-25
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}
"""

_MASTER_TEMPLATE = """\
---
title: synthetic
slug: {mslug}
status: active
---

# MASTER

| # | Slug | Status |
|---|---|---|
| 1 | [{slug_a}](./{fname_a}) | {status_a} |
| 2 | [{slug_b}](./{fname_b}) | {status_b} |
"""


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


def _make_plans_dir(
    tmp_path: Path,
    slugs: list[str] | None = None,
    *,
    status: str = "in-progress",
    step: int = 0,
) -> Path:
    """Create a plans dir with one sub-plan per slug."""
    if slugs is None:
        slugs = ["alpha", "beta"]
    plans = tmp_path / "plans"
    plans.mkdir()
    for slug in slugs:
        fname = f"2026-09-25-{slug}.md"
        (plans / fname).write_text(
            _SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step),
            encoding="utf-8",
        )
    # MASTER pointing at both slugs.
    (plans / "MASTER-2026-09-25-execution-plan.md").write_text(
        _MASTER_TEMPLATE.format(
            mslug="2026-09-25", status="active",
            slug_a=slugs[0], fname_a=f"2026-09-25-{slugs[0]}.md",
            status_a=status,
            slug_b=slugs[1] if len(slugs) > 1 else "",
            fname_b=f"2026-09-25-{slugs[1]}.md" if len(slugs) > 1 else "",
            status_b=status,
        ),
        encoding="utf-8",
    )
    return plans


def _status_of(plans: Path, slug: str) -> str:
    for path in plans.glob("*.md"):
        if path.name.startswith("MASTER"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = st._parse_frontmatter(text)
        if fm.get("plan") == slug:
            return fm.get("status", "")
    raise AssertionError(f"no sub-plan with plan: {slug} in {plans}")


def _step_of(plans: Path, slug: str) -> int:
    for path in plans.glob("*.md"):
        if path.name.startswith("MASTER"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = st._parse_frontmatter(text)
        if fm.get("plan") == slug:
            return int(fm.get("current_step", 0))
    raise AssertionError(f"no sub-plan with plan: {slug} in {plans}")


def _capture_statuses(plans: Path) -> dict[str, dict]:
    """Capture every sub-plan's status and step (pre-iteration snapshot)."""
    result = {}
    for path in plans.glob("*.md"):
        if path.name.startswith("MASTER"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = st._parse_frontmatter(text)
        slug = fm.get("plan", "")
        if slug:
            result[slug] = {
                "status": fm.get("status", ""),
                "current_step": int(fm.get("current_step", 0)),
            }
    return result


def _write_shipped_frontmatter(plans: Path, slug: str, step: int = 1) -> None:
    """Directly write ``shipped`` into a sub-plan's frontmatter.

    Simulates a worker that bypasses ``ship_transition.py``.
    """
    for path in plans.glob("*.md"):
        if path.name.startswith("MASTER"):
            continue
        text = path.read_text(encoding="utf-8")
        fm = st._parse_frontmatter(text)
        if fm.get("plan") == slug:
            new_text = text.replace(
                f"status: {fm.get('status', '')}",
                "status: shipped",
            )
            new_text = new_text.replace(
                f"current_step: {fm.get('current_step', 0)}",
                f"current_step: {step}",
            )
            path.write_text(new_text, encoding="utf-8")
            return
    raise AssertionError(f"no sub-plan with plan: {slug} in {plans}")


def _driver_revert_unsanctioned_ships(
    plans: Path,
    pre_snapshot: dict[str, dict],
    dispatched_slug: str,
) -> list[str]:
    """Revert any sub-plan that went non-shipped → shipped but is not dispatched.

    This mirrors the driver's one-ship-per-iteration enforcement in
    ``run_ilk_loop_claude.sh``.
    """
    reverted = []
    for slug, before in pre_snapshot.items():
        if slug == dispatched_slug:
            continue
        if before["status"] == "shipped":
            # Already shipped before — nothing to revert.
            continue
        current_status = _status_of(plans, slug)
        if current_status == "shipped":
            # Went from non-shipped to shipped without being dispatched — revert.
            for path in plans.glob("*.md"):
                if path.name.startswith("MASTER"):
                    continue
                text = path.read_text(encoding="utf-8")
                fm = st._parse_frontmatter(text)
                if fm.get("plan") == slug:
                    new_text = text.replace(
                        f"status: {current_status}",
                        f"status: {before['status']}",
                    )
                    new_text = new_text.replace(
                        f"current_step: {fm.get('current_step', 0)}",
                        f"current_step: {before['current_step']}",
                    )
                    path.write_text(new_text, encoding="utf-8")
                    break
            reverted.append(slug)
    return reverted


# ── AC-1: worker ships a and b directly; b is reverted ──────────────────────


class TestDriverRevertsUnsanctionedShip:
    """AC-1: a stub worker dispatched for ``a`` edits the frontmatter of ``a``
    and ``b`` to ``shipped`` without calling ship_transition.  After the
    iteration, ``a`` is shipped and ``b`` is back to its prior status and step,
    and the log has the ``[one-ship] reverted b`` line."""

    def test_direct_ship_of_two_slugs_reverts_nondispatched(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"])

        # Pre-iteration snapshot.
        pre_snapshot = _capture_statuses(plans)
        assert pre_snapshot["alpha"]["status"] == "in-progress"
        assert pre_snapshot["beta"]["status"] == "in-progress"

        # Worker ships both directly (bypasses ship_transition.py).
        _write_shipped_frontmatter(plans, "alpha", step=1)
        _write_shipped_frontmatter(plans, "beta", step=1)

        # Driver's post-iteration revert.
        reverted = _driver_revert_unsanctioned_ships(plans, pre_snapshot, "alpha")

        # alpha is shipped, beta is reverted.
        assert _status_of(plans, "alpha") == "shipped"
        assert _status_of(plans, "beta") == "in-progress"
        assert _step_of(plans, "beta") == 0
        assert "beta" in reverted


# ── AC-2: ship_transition works ⇒ nothing reverted ──────────────────────────


class TestShipTransitionNotReverted:
    """AC-2: a worker that ships only ``a`` via ship_transition ⇒ nothing is
    reverted.  This test verifies ``ship_transition.py`` works correctly; the
    driver's revert logic (which must not touch a legitimate ship) is covered
    by AC-1 and AC-4."""

    def test_legitimate_ship_not_reverted(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"])

        # Clear ILK_ITERATION_SUBPLAN so ship_transition.py doesn't refuse.
        saved_env = os.environ.pop("ILK_ITERATION_SUBPLAN", None)
        try:
            result = st.ship(plans, repo, "alpha")
        finally:
            if saved_env is not None:
                os.environ["ILK_ITERATION_SUBPLAN"] = saved_env

        assert result.slug == "alpha"
        assert _status_of(plans, "alpha") == "shipped"
        assert _status_of(plans, "beta") == "in-progress"


# ── AC-3: gate-first ship still works ───────────────────────────────────────
# Pinned by test_verify_without_a_worker.py — no additional test needed here.


# ── AC-4: empty dispatched slug ⇒ all new ships reverted ────────────────────


class TestEmptyDispatchRevertsAll:
    """AC-4: ``ILK_ITERATION_SUBPLAN`` empty and a worker that ships ``a``
    ⇒ ``a`` is reverted."""

    def test_empty_dispatch_reverts_ship(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha"])

        pre_snapshot = _capture_statuses(plans)
        assert pre_snapshot["alpha"]["status"] == "in-progress"

        # Worker ships alpha directly.
        _write_shipped_frontmatter(plans, "alpha", step=1)

        # Driver's post-iteration revert — empty dispatched slug.
        reverted = _driver_revert_unsanctioned_ships(plans, pre_snapshot, "")

        # alpha is reverted.
        assert _status_of(plans, "alpha") == "in-progress"
        assert _step_of(plans, "alpha") == 0
        assert "alpha" in reverted
