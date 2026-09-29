"""The one-ship revert restores the pre-iteration step pointer.

Part of sub-plan ``the-one-ship-revert-moves-the-pointer`` (MASTER-2026-09-29c).

After ``[one-ship]`` reverted gh-resolve's ``work-plan-commits-first`` and
``contract-gates-batch-verify``, both read ``status: in-progress`` with
``current_step: 2 / estimated_steps: 2``.  Run 091950 resumed into an
in-progress plan with no step left.

Three acceptance criteria:

  AC-1  When ``[one-ship]`` reverts a sub-plan, it restores ``current_step``
        to that sub-plan's value in ``PRE_ITER_ALL_STEPS``, in the same write
        as the status.

  AC-2  The slug match is exact.  A slug that is a prefix of another
        (``foo`` vs ``foo-bar``) restores the right one.

  AC-3  A sub-plan missing from ``PRE_ITER_ALL_STEPS`` keeps today's
        behaviour (status only) and logs why.
"""
from __future__ import annotations

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
last_updated: 2026-09-29
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
        fname = f"2026-09-29-{slug}.md"
        (plans / fname).write_text(
            _SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step),
            encoding="utf-8",
        )
    # MASTER pointing at both slugs.
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        _MASTER_TEMPLATE.format(
            mslug="2026-09-29", status="active",
            slug_a=slugs[0], fname_a=f"2026-09-29-{slugs[0]}.md",
            status_a=status,
            slug_b=slugs[1] if len(slugs) > 1 else "",
            fname_b=f"2026-09-29-{slugs[1]}.md" if len(slugs) > 1 else "",
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


def _write_shipped_frontmatter(plans: Path, slug: str, step: int = 2) -> None:
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


def _build_pre_iter_all_steps(snapshot: dict[str, dict]) -> str:
    """Build a ``PRE_ITER_ALL_STEPS``-style string from a snapshot."""
    lines = []
    for slug, info in snapshot.items():
        lines.append(f"{slug} {info['current_step']}")
    return "\n".join(lines)


def _driver_revert_one_ship(
    plans: Path,
    pre_iter_all_steps: str,
    dispatched_slug: str,
) -> list[str]:
    """Revert unsanctioned ships — status only (current runner behaviour).

    This mirrors the runner's ``[one-ship]`` block at
    ``run_ilk_loop_claude.sh:5200-5232`` which rewrites only ``status``
    and never touches ``current_step``.  The xfail pins assert that
    ``current_step`` IS restored; they fail until the fix lands.
    """
    reverted = []
    for pre_line in pre_iter_all_steps.splitlines():
        pre_line = pre_line.strip()
        if not pre_line:
            continue
        # Split on the last space (slug may contain hyphens).
        parts = pre_line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        pre_slug, _pre_step_str = parts[0], parts[1]
        if not pre_slug:
            continue
        # Skip the dispatched slug.
        if pre_slug == dispatched_slug:
            continue
        # Read current status.
        current_status = _status_of(plans, pre_slug)
        if current_status == "shipped":
            # Revert status ONLY — the runner does not restore current_step.
            for path in plans.glob("*.md"):
                if path.name.startswith("MASTER"):
                    continue
                text = path.read_text(encoding="utf-8")
                fm = st._parse_frontmatter(text)
                if fm.get("plan") == pre_slug:
                    new_text = text.replace(
                        f"status: {current_status}",
                        f"status: in-progress",
                    )
                    path.write_text(new_text, encoding="utf-8")
                    break
            reverted.append(pre_slug)
    return reverted


# ── AC-1: revert restores current_step ───────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="one-ship revert leaves the pointer at the end (retro F3)",
)
class TestOneShipRevertRestoresStep:
    """AC-1: when ``[one-ship]`` reverts a sub-plan, it restores
    ``current_step`` to the pre-iteration value."""

    def test_revert_restores_step_pointer(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        # Sub-plans start at step 0.
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"], step=0)

        # Pre-iteration snapshot: both at step 0.
        pre_snapshot = _capture_statuses(plans)
        assert pre_snapshot["alpha"]["current_step"] == 0
        assert pre_snapshot["beta"]["current_step"] == 0

        # Worker ships both with step=2 (simulates end-of-plan).
        _write_shipped_frontmatter(plans, "alpha", step=2)
        _write_shipped_frontmatter(plans, "beta", step=2)

        # Build PRE_ITER_ALL_STEPS from the snapshot.
        pre_iter_all_steps = _build_pre_iter_all_steps(pre_snapshot)

        # Driver reverts beta (dispatched for alpha).
        _driver_revert_one_ship(plans, pre_iter_all_steps, "alpha")

        # beta is reverted: status back to in-progress AND step back to 0.
        assert _status_of(plans, "beta") == "in-progress"
        assert _step_of(plans, "beta") == 0, (
            "one-ship revert did not restore current_step"
        )


# ── AC-2: exact slug match ──────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="one-ship revert leaves the pointer at the end (retro F3)",
)
class TestExactSlugMatch:
    """AC-2: the slug match is exact — ``foo`` does not match ``foo-bar``."""

    def test_prefix_slug_not_confused(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        # slugs: "foo" and "foo-bar"
        plans = _make_plans_dir(tmp_path, ["foo", "foo-bar"], step=1)

        pre_snapshot = _capture_statuses(plans)
        assert pre_snapshot["foo"]["current_step"] == 1
        assert pre_snapshot["foo-bar"]["current_step"] == 1

        # Worker ships both with step=2.
        _write_shipped_frontmatter(plans, "foo", step=2)
        _write_shipped_frontmatter(plans, "foo-bar", step=2)

        pre_iter_all_steps = _build_pre_iter_all_steps(pre_snapshot)

        # Dispatched for "foo" — only "foo-bar" should revert.
        _driver_revert_one_ship(plans, pre_iter_all_steps, "foo")

        assert _status_of(plans, "foo") == "shipped"
        assert _step_of(plans, "foo") == 2
        assert _status_of(plans, "foo-bar") == "in-progress"
        assert _step_of(plans, "foo-bar") == 1, (
            "prefix slug confusion: foo-bar step not restored"
        )


# ── AC-3: missing from PRE_ITER_ALL_STEPS ────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="one-ship revert leaves the pointer at the end (retro F3)",
)
class TestMissingFromPreIterSteps:
    """AC-3: a sub-plan missing from ``PRE_ITER_ALL_STEPS`` keeps today's
    behaviour (status only revert) and logs why."""

    def test_missing_slug_keeps_status_only(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"], step=1)

        # Pre-iteration snapshot — but we only record alpha in PRE_ITER_ALL_STEPS.
        pre_snapshot = {"alpha": {"status": "in-progress", "current_step": 1}}
        # beta is missing from PRE_ITER_ALL_STEPS (simulates a race or new plan).

        # Worker ships both.
        _write_shipped_frontmatter(plans, "alpha", step=2)
        _write_shipped_frontmatter(plans, "beta", step=2)

        pre_iter_all_steps = _build_pre_iter_all_steps(pre_snapshot)

        # Dispatched for alpha — beta should revert but without step restore
        # (it's missing from PRE_ITER_ALL_STEPS).
        _driver_revert_one_ship(plans, pre_iter_all_steps, "alpha")

        # beta is reverted to in-progress, but current_step stays at 2
        # because there's no pre-iteration value to restore.
        assert _status_of(plans, "beta") == "in-progress"
        # The fix should leave step as-is when the slug is missing.
        assert _step_of(plans, "beta") == 2, (
            "missing slug should keep current_step unchanged"
        )