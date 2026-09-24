"""Red-first: one iteration may ship only the sub-plan it was dispatched for.

Part of sub-plan ``one-iteration-ships-one-sub-plan`` (MASTER-2026-09-24d).

The driver names the iteration's sub-plan via ``ILK_ITERATION_SUBPLAN``.
``ship_transition.py --ship <slug>`` refuses a different slug when the
variable is set, so a worker that tries to ship the next sub-plan after its
own gets blocked.

Four acceptance criteria:

  AC-1  with ``ILK_ITERATION_SUBPLAN=a``, ``--ship b`` exits 2 with the
        refusal message, and b's front-matter and marker are unchanged.

  AC-2  ``--ship a`` with the variable = a ⇒ ships as today.  Unset ⇒ ships
        as today (manual ``/ilk`` sessions).

  AC-3  (e2e) a stub worker that ships its dispatched sub-plan AND the next
        ⇒ only the first is ``shipped``, the second stays ``pending``, and
        the log contains the refusal.

  AC-4  the driver's gate-first final-step ship of a verification sub-plan
        still works (``test_verify_without_a_worker.py`` green).

RED-FIRST: AC-1 and AC-3 are ``xfail(strict=True)`` until step 1 lands.
"""
from __future__ import annotations

import os
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
last_updated: 2026-09-24
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}
"""

_MASTER_TEMPLATE = """\
---
title: synthetic
slug: {mslug}
status: {status}
---

# MASTER

| # | Slug | Status |
|---|---|---|
| 1 | [{slug}](./{fname}) | {sub_status} |
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
        slugs = ["alpha"]
    plans = tmp_path / "plans"
    plans.mkdir()
    for slug in slugs:
        fname = f"2026-09-24-{slug}.md"
        (plans / fname).write_text(
            _SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step),
            encoding="utf-8",
        )
    # MASTER pointing at the first slug.
    (plans / "MASTER-2026-09-24-execution-plan.md").write_text(
        _MASTER_TEMPLATE.format(
            mslug="2026-09-24", status="active",
            slug=slugs[0], fname=f"2026-09-24-{slugs[0]}.md",
            sub_status=status,
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


def _marker_exists(repo: Path, slug: str) -> bool:
    return st.find_marker_commit(repo, slug) is not None


# ── AC-1: ILK_ITERATION_SUBPLAN=b ⇒ --ship b works, --ship c refused ────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestRefuseWrongSlug:
    """AC-1: with ``ILK_ITERATION_SUBPLAN=a``, ``ship_transition.py --ship b``
    exits 2 with the refusal message, and b's front-matter and marker are
    unchanged."""

    def test_refuses_mismatched_slug(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"])

        env = {**os.environ, "ILK_ITERATION_SUBPLAN": "alpha"}
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "beta",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )

        assert result.returncode == 2, (
            f"expected exit 2, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "refused" in result.stderr.lower() or "refused" in result.stdout.lower(), (
            f"expected 'refused' in output, got stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "alpha" in result.stderr or "alpha" in result.stdout, (
            "the refusal must name the dispatched sub-plan"
        )

        # beta is untouched.
        assert _status_of(plans, "beta") == "in-progress"
        assert not _marker_exists(repo, "beta")

    def test_refuses_mismatched_slug_programmatic(self, tmp_path: Path) -> None:
        """Same check via the Python API (``ship()`` raises
        ``ShipTransitionError`` when the env var disagrees)."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"])

        os.environ["ILK_ITERATION_SUBPLAN"] = "alpha"
        try:
            with pytest.raises(st.ShipTransitionError):
                st.ship(plans, repo, "beta")
        finally:
            os.environ.pop("ILK_ITERATION_SUBPLAN", None)

        assert _status_of(plans, "beta") == "in-progress"
        assert not _marker_exists(repo, "beta")


# ── AC-2: matching slug or unset ⇒ ships as today ───────────────────────────


class TestShipWhenSlugMatches:
    """AC-2: ``--ship a`` with ``ILK_ITERATION_SUBPLAN=a`` ⇒ ships as today.
    Unset ⇒ ships as today (manual ``/ilk`` sessions)."""

    def test_matching_slug_ships(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha"])

        env = {**os.environ, "ILK_ITERATION_SUBPLAN": "alpha"}
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "alpha",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )

        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _status_of(plans, "alpha") == "shipped"
        assert _marker_exists(repo, "alpha")

    def test_unset_env_ships(self, tmp_path: Path) -> None:
        """When ILK_ITERATION_SUBPLAN is not set, any slug ships (manual /ilk)."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha"])

        env = {k: v for k, v in os.environ.items()
               if k != "ILK_ITERATION_SUBPLAN"}
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "alpha",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )

        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _status_of(plans, "alpha") == "shipped"
        assert _marker_exists(repo, "alpha")

    def test_empty_env_ships(self, tmp_path: Path) -> None:
        """An empty ILK_ITERATION_SUBPLAN is treated as unset."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha"])

        env = {**os.environ, "ILK_ITERATION_SUBPLAN": ""}
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "alpha",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )

        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _status_of(plans, "alpha") == "shipped"


# ── AC-3: e2e — stub worker ships first, second refused ─────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
class TestStubWorkerShipsOneSubPlan:
    """AC-3: a stub worker that tries to ship its dispatched sub-plan AND
    the next one ⇒ only the first is ``shipped``, the second stays
    ``pending``, and the log contains the refusal."""

    def test_worker_cannot_ship_beyond_dispatch(self, tmp_path: Path) -> None:
        """Simulate a worker shipping alpha (its dispatch) then beta.
        Beta must be refused."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["alpha", "beta"])

        # First ship: alpha — the dispatched slug.
        env = {**os.environ, "ILK_ITERATION_SUBPLAN": "alpha"}
        r1 = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "alpha",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )
        assert r1.returncode == 0, f"alpha ship failed: {r1.stderr}"
        assert _status_of(plans, "alpha") == "shipped"

        # Second ship: beta — NOT dispatched, must be refused.
        r2 = subprocess.run(
            [sys.executable, str(_SCRIPTS / "ship_transition.py"),
             "--ship", "beta",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )
        assert r2.returncode == 2, (
            f"expected exit 2 for beta, got {r2.returncode}\n"
            f"stdout: {r2.stdout}\nstderr: {r2.stderr}"
        )

        # alpha shipped, beta untouched.
        assert _status_of(plans, "alpha") == "shipped"
        assert _status_of(plans, "beta") == "in-progress"
        assert not _marker_exists(repo, "beta")

        # The refusal message is visible.
        combined = r2.stdout + r2.stderr
        assert "refused" in combined.lower(), (
            f"expected 'refused' in output: {combined!r}"
        )


# ── AC-4: gate-first final-step ship still works ────────────────────────────


class TestGateFirstShipStillWorks:
    """AC-4: the driver's gate-first final-step ship of a verification
    sub-plan still works.  This is pinned by
    ``test_verify_without_a_worker.py`` — here we verify that the
    programmatic ``ship()`` API is not broken by the env-var check
    when the variable is unset."""

    def test_ship_api_without_env(self, tmp_path: Path) -> None:
        """Direct Python API call, no env var set ⇒ works as before."""
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, ["batch-verify"])

        # Ensure the var is absent.
        os.environ.pop("ILK_ITERATION_SUBPLAN", None)

        result = st.ship(plans, repo, "batch-verify")
        assert result.slug == "batch-verify"
        assert result.status_written is True
        assert result.marker_created is True
        assert _status_of(plans, "batch-verify") == "shipped"
