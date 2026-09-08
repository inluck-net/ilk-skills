"""Pins for ship_transition.py — the ship transition's two writes, as one.

Shipping a sub-plan writes to two stores that no transaction spans:

  1. the sub-plan front-matter ``status: shipped``, which lives OUTSIDE the
     repo under ``~/.ilk-data/projects/<key>/plans/``;
  2. the marker commit ``chore(plans): <slug> shipped [plan:<slug>#ship]``,
     which lives INSIDE the repo.

Measured 2026-09-08 in gh-resolve: ``MASTER-2026-09-07c`` read ``shipped``
while ``2026-09-07c-conflict-batch-verify.md`` read ``in-progress`` at
``current_step: 2``, with the ``#ship`` marker commit present in the repo.

These pins cover BOTH directions of divergence, because a fix that only
handles one leaves the other silent, and the interruption itself — a
transition killed between its two writes must leave a state the detector
NAMES, not an ambiguous one.

RED-FIRST: authored before ``ship_transition.py`` exists. The module is
imported lazily inside ``_mod()`` so collection succeeds (the step-0 gate is
``--collect-only``) and every test fails at run time until step 1 lands.
"""
from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _mod():
    """Import the module under test lazily — see RED-FIRST above."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return import_module("ship_transition")


# ── fixtures: a synthetic plans dir + repo, the two stores ───────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
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
last_updated: 2026-09-08
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}
"""

MASTER_TEMPLATE = """---
title: synthetic
slug: {mslug}
status: {status}
---

# MASTER

| # | Slug | Status |
|---|---|---|
| 1 | [{slug}](./{fname}) | {sub_status} |
"""


def _make_plans_dir(
    tmp_path: Path,
    slug: str = "conflict-batch-verify",
    status: str = "in-progress",
    step: int = 2,
    master_status: str = "shipped",
) -> Path:
    plans = tmp_path / "plans"
    plans.mkdir()
    fname = f"2026-09-07c-{slug}.md"
    (plans / fname).write_text(
        SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step), encoding="utf-8",
    )
    (plans / "MASTER-2026-09-07c-execution-plan.md").write_text(
        MASTER_TEMPLATE.format(
            mslug="2026-09-07c", status=master_status, slug=slug,
            fname=fname, sub_status=status,
        ),
        encoding="utf-8",
    )
    return plans


def _marker_commit(repo: Path, slug: str) -> str:
    _git(
        repo, "commit", "-q", "--allow-empty",
        "-m", f"chore(plans): {slug} shipped [plan:{slug}#ship]",
    )
    return _git(repo, "rev-parse", "HEAD")


def _status_of(plans: Path, slug: str) -> str:
    path = next(p for p in plans.glob("*.md") if not p.name.startswith("MASTER"))
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no status: line in {path}")


# ── detection: both directions, plus the two consistent states ───────────────

class TestDetect:
    """AC-1/AC-2: an interrupted transition is detectable from the two stores
    alone — and each direction is named, not merely 'inconsistent'."""

    def test_marker_without_status_is_detected(self, tmp_path: Path) -> None:
        """The measured gh-resolve shape: marker commit landed, front-matter
        never flipped. This is the RECOVERABLE residue of the ordered writer."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        sha = _marker_commit(repo, "conflict-batch-verify")

        found = st.detect(plans_dir=plans, repo=repo)
        by_slug = {d.slug: d for d in found}

        assert "conflict-batch-verify" in by_slug, f"not detected: {found!r}"
        d = by_slug["conflict-batch-verify"]
        assert d.kind == st.MARKER_WITHOUT_STATUS
        assert d.marker_commit is not None and sha.startswith(d.marker_commit)
        assert d.frontmatter_shipped is False

    def test_status_without_marker_is_detected(self, tmp_path: Path) -> None:
        """The other direction: front-matter says shipped, the repo has no
        proof. Silent today; ship_audit reports it only as '(!) unproven'."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="shipped")

        found = st.detect(plans_dir=plans, repo=repo)
        by_slug = {d.slug: d for d in found}

        assert "conflict-batch-verify" in by_slug, f"not detected: {found!r}"
        d = by_slug["conflict-batch-verify"]
        assert d.kind == st.STATUS_WITHOUT_MARKER
        assert d.marker_commit is None
        assert d.frontmatter_shipped is True

    def test_both_halves_present_is_consistent(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="shipped")
        _marker_commit(repo, "conflict-batch-verify")

        assert st.detect(plans_dir=plans, repo=repo) == []

    def test_neither_half_present_is_consistent(self, tmp_path: Path) -> None:
        """Work in flight is not a divergence."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        assert st.detect(plans_dir=plans, repo=repo) == []


# ── the interruption itself ──────────────────────────────────────────────────

class TestInterruption:
    """AC-2: the transition killed between its two writes leaves a state the
    detector RECOGNISES — the intent marker distinguishes 'we were mid-ship'
    from 'someone edited a file by hand'."""

    def test_intent_marker_survives_the_kill_and_is_read_back(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        st.write_intent(plans_dir=plans, slug="conflict-batch-verify", repo=repo)
        # <-- the iteration is killed at the timeout boundary here
        intent = st.read_intent(plans_dir=plans)

        assert intent is not None
        assert intent["slug"] == "conflict-batch-verify"

    def test_interrupted_after_marker_is_named_not_ambiguous(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        st.write_intent(plans_dir=plans, slug="conflict-batch-verify", repo=repo)
        _marker_commit(repo, "conflict-batch-verify")
        # <-- killed before the front-matter write

        d = next(d for d in st.detect(plans_dir=plans, repo=repo)
                 if d.slug == "conflict-batch-verify")
        assert d.kind == st.MARKER_WITHOUT_STATUS
        assert d.interrupted is True, "an in-flight intent must be reported"

    def test_a_completed_transition_clears_its_intent(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        st.ship(plans_dir=plans, repo=repo, slug="conflict-batch-verify")

        assert st.read_intent(plans_dir=plans) is None
        assert st.detect(plans_dir=plans, repo=repo) == []


# ── the ordered writer ───────────────────────────────────────────────────────

class TestShip:
    """AC-1: the two writes are one ordered operation with a defined outcome
    after an interruption at any point."""

    def test_ship_writes_both_halves(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        result = st.ship(plans_dir=plans, repo=repo, slug="conflict-batch-verify")

        assert _status_of(plans, "conflict-batch-verify") == "shipped"
        log = _git(repo, "log", "--format=%s")
        assert "[plan:conflict-batch-verify#ship]" in log
        assert result.marker_commit

    def test_the_marker_commit_lands_before_the_status_write(self, tmp_path: Path) -> None:
        """The documented order, pinned. Marker first, because its residue is
        repairable forward from evidence; the reverse residue would need a
        commit fabricated to converge."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        seen: list[str] = []

        def _boom(*a, **kw):
            seen.append("status")
            raise RuntimeError("killed between the two writes")

        # ship must NOT swallow a failed status write — the intent marker is
        # what stays behind, and the caller has to know the half landed.
        with pytest.raises(RuntimeError):
            st.ship(
                plans_dir=plans, repo=repo, slug="conflict-batch-verify",
                _write_status=_boom,
            )

        # It got as far as attempting the status write, which means the marker
        # was already durable — the residue is the recoverable direction.
        assert seen == ["status"]
        d = next(d for d in st.detect(plans_dir=plans, repo=repo)
                 if d.slug == "conflict-batch-verify")
        assert d.kind == st.MARKER_WITHOUT_STATUS
        assert d.interrupted is True, "the uncleared intent must still be visible"
        # ...and that residue is the one repair can converge from evidence.
        assert st.repair(plans_dir=plans, repo=repo, apply=True)[0].applied is True
        assert _status_of(plans, "conflict-batch-verify") == "shipped"

    def test_ship_is_idempotent(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        st.ship(plans_dir=plans, repo=repo, slug="conflict-batch-verify")
        st.ship(plans_dir=plans, repo=repo, slug="conflict-batch-verify")

        markers = [
            line for line in _git(repo, "log", "--format=%s").splitlines()
            if "[plan:conflict-batch-verify#ship]" in line
        ]
        assert len(markers) == 1, f"marker commit duplicated: {markers}"

    def test_ship_refuses_an_unknown_slug(self, tmp_path: Path) -> None:
        """A slug with no sub-plan file must not produce a marker commit for
        work that does not exist — that is D2's forgery hazard."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")

        with pytest.raises(st.ShipTransitionError):
            st.ship(plans_dir=plans, repo=repo, slug="a-slug-that-is-not-here")
        assert "#ship]" not in _git(repo, "log", "--format=%s")


# ── repair ───────────────────────────────────────────────────────────────────

class TestRepair:
    """AC-3: --repair converges an already-diverged pair, and REFUSES rather
    than guesses when the two stores disagree about something it cannot
    decide from evidence."""

    def test_dry_run_is_the_default_and_mutates_nothing(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")

        actions = st.repair(plans_dir=plans, repo=repo)  # no apply=

        assert actions and actions[0].slug == "conflict-batch-verify"
        assert actions[0].applied is False
        assert _status_of(plans, "conflict-batch-verify") == "in-progress"

    def test_apply_converges_marker_without_status(self, tmp_path: Path) -> None:
        """The real gh-resolve pair's shape: the commit is durable proof the
        work completed, so repair moves the front-matter forward to match."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")

        actions = st.repair(plans_dir=plans, repo=repo, apply=True)

        assert actions[0].applied is True
        assert _status_of(plans, "conflict-batch-verify") == "shipped"
        assert st.detect(plans_dir=plans, repo=repo) == []

    def test_apply_is_idempotent(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")

        st.repair(plans_dir=plans, repo=repo, apply=True)
        assert st.repair(plans_dir=plans, repo=repo, apply=True) == []

    def test_repair_refuses_status_without_marker(self, tmp_path: Path) -> None:
        """No evidence the work happened: fabricating a marker commit would
        forge the audit trail, and downgrading the status would destroy a
        genuine ship whose trailer was mistyped (D2). Name it and refuse."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="shipped")

        actions = st.repair(plans_dir=plans, repo=repo, apply=True)

        assert len(actions) == 1
        a = actions[0]
        assert a.refused is True
        assert a.applied is False
        assert a.slug == "conflict-batch-verify"
        assert a.reason, "a refusal must say why"
        assert _status_of(plans, "conflict-batch-verify") == "shipped"
        log = _git(repo, "log", "--format=%s")
        assert "#ship]" not in log, "refusal must not fabricate a marker commit"

    def test_a_refusal_is_reported_as_nonzero_from_the_cli(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="shipped")

        rc = subprocess.run(
            [sys.executable, str(SCRIPTS / "ship_transition.py"), "--repair",
             "--plans-dir", str(plans), "--repo", str(repo)],
            capture_output=True, text=True,
        )
        assert rc.returncode != 0, rc.stdout + rc.stderr
        assert "conflict-batch-verify" in rc.stdout + rc.stderr


class TestOnlyInterrupted:
    """The driver's automatic mode must not out-vote a deliberate revert.

    test_ship_integrity reverts shipped -> in-progress when a sub-plan's gate
    is red, and the marker commit stays in history. That residue is
    indistinguishable BY CONTENT from an interrupted transition; only the
    intent marker tells them apart.
    """

    def test_a_gate_revert_is_left_alone(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")  # ship happened...
        # ...and ship_integrity reverted the status. No intent marker.

        actions = st.repair(
            plans_dir=plans, repo=repo, apply=True, only_interrupted=True,
        )

        assert actions == []
        assert _status_of(plans, "conflict-batch-verify") == "in-progress"

    def test_an_interrupted_transition_is_converged(self, tmp_path: Path) -> None:
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        st.write_intent(plans_dir=plans, slug="conflict-batch-verify", repo=repo)
        _marker_commit(repo, "conflict-batch-verify")
        # <-- killed before the front-matter write; intent still on disk

        actions = st.repair(
            plans_dir=plans, repo=repo, apply=True, only_interrupted=True,
        )

        assert len(actions) == 1 and actions[0].applied is True
        assert _status_of(plans, "conflict-batch-verify") == "shipped"
        assert st.read_intent(plans_dir=plans) is None

    def test_the_operator_cli_still_sees_an_old_residue(self, tmp_path: Path) -> None:
        """--repair without --only-interrupted is the operator's tool: it names
        the real gh-resolve pair, which predates any intent marker."""
        st = _mod()
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")

        actions = st.repair(plans_dir=plans, repo=repo)
        assert [a.slug for a in actions] == ["conflict-batch-verify"]


class TestCli:
    def test_repair_json_names_each_pair(self, tmp_path: Path) -> None:
        st = _mod()  # noqa: F841 — module must exist for the CLI to run
        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, status="in-progress")
        _marker_commit(repo, "conflict-batch-verify")

        cp = subprocess.run(
            [sys.executable, str(SCRIPTS / "ship_transition.py"), "--repair",
             "--plans-dir", str(plans), "--repo", str(repo), "--json"],
            capture_output=True, text=True,
        )
        payload = json.loads(cp.stdout)
        assert payload["actions"][0]["slug"] == "conflict-batch-verify"
        assert payload["actions"][0]["applied"] is False
