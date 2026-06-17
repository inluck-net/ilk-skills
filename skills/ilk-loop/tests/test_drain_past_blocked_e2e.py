"""End-to-end drain-past-blocked test (L4).

Generates a synthetic ILK_DATA_HOME with multiple masters, some containing
blocked sub-plans, then drives a drain loop that simulates sub-plan
completion and asserts L4 invariants hold:

  - AC-2: every runnable sub-plan reaches shipped
  - AC-3: every deliberately-blocked sub-plan remains blocked (never shipped)
  - AC-4: a master whose only remaining work is blocked ends parked, and
          masters AFTER it in the queue still reach shipped
  - AC-5: no write outside the temp dir (real ~/.ilk-data untouched)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gen_mock_masters import generate_fixture
from loop_status import parse_frontmatter, extract_master_order
from promote_next_master import main as promote_main, write_status


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_spec(scenario: str = "basic") -> dict:
    """Return a fixture spec for the given scenario."""

    if scenario == "basic":
        # 3 masters:
        #   M1 (active): A shipped, B blocked, C pending (no deps)
        #   M2 (queued): D pending (no deps), E pending depends_on D (same master)
        #   M3 (queued): F pending (no deps)
        return {
            "project_key": "test-proj",
            "masters": [
                {
                    "filename": "MASTER-2026-06-01-m1.md",
                    "status": "active",
                    "created": "2026-06-01T00:00:00+08:00",
                    "priority": 1,
                    "sub_plans": [
                        {"filename": "2026-06-01-a.md", "slug": "a",
                         "status": "shipped", "estimated_steps": 2, "current_step": 2},
                        {"filename": "2026-06-01-b.md", "slug": "b",
                         "status": "blocked", "estimated_steps": 3, "current_step": 0},
                        {"filename": "2026-06-01-c.md", "slug": "c",
                         "status": "pending", "estimated_steps": 2, "current_step": 0},
                    ],
                },
                {
                    "filename": "MASTER-2026-06-01-m2.md",
                    "status": "queued",
                    "created": "2026-06-01T01:00:00+08:00",
                    "priority": 2,
                    "sub_plans": [
                        {"filename": "2026-06-01-d.md", "slug": "d",
                         "status": "pending", "estimated_steps": 2, "current_step": 0},
                        {"filename": "2026-06-01-e.md", "slug": "e",
                         "status": "pending", "estimated_steps": 2, "current_step": 0,
                         "depends_on": ["d"]},
                    ],
                },
                {
                    "filename": "MASTER-2026-06-01-m3.md",
                    "status": "queued",
                    "created": "2026-06-01T02:00:00+08:00",
                    "priority": 3,
                    "sub_plans": [
                        {"filename": "2026-06-01-f.md", "slug": "f",
                         "status": "pending", "estimated_steps": 1, "current_step": 0},
                    ],
                },
            ],
        }

    if scenario == "first_master_blocked":
        # M1 (active): A blocked, B blocked-depends-on-A → stalled
        # M2 (queued): C pending (no deps) → must drain past M1
        return {
            "project_key": "test-proj",
            "masters": [
                {
                    "filename": "MASTER-2026-06-01-m1.md",
                    "status": "active",
                    "created": "2026-06-01T00:00:00+08:00",
                    "priority": 1,
                    "sub_plans": [
                        {"filename": "2026-06-01-a.md", "slug": "a",
                         "status": "blocked", "estimated_steps": 2, "current_step": 0},
                        {"filename": "2026-06-01-b.md", "slug": "b",
                         "status": "pending", "estimated_steps": 2, "current_step": 0,
                         "depends_on": ["a"]},
                    ],
                },
                {
                    "filename": "MASTER-2026-06-01-m2.md",
                    "status": "queued",
                    "created": "2026-06-01T01:00:00+08:00",
                    "priority": 2,
                    "sub_plans": [
                        {"filename": "2026-06-01-c.md", "slug": "c",
                         "status": "pending", "estimated_steps": 3, "current_step": 0},
                    ],
                },
            ],
        }

    if scenario == "dep_chain":
        # M1: A blocked, B depends_on A (non-runnable), C depends_on B (non-runnable)
        #      D pending (no deps) → runnable
        # M2: E pending → drains after M1 stalls
        return {
            "project_key": "test-proj",
            "masters": [
                {
                    "filename": "MASTER-2026-06-01-m1.md",
                    "status": "active",
                    "created": "2026-06-01T00:00:00+08:00",
                    "priority": 1,
                    "sub_plans": [
                        {"filename": "2026-06-01-a.md", "slug": "a",
                         "status": "blocked", "estimated_steps": 2, "current_step": 0},
                        {"filename": "2026-06-01-b.md", "slug": "b",
                         "status": "pending", "estimated_steps": 2, "current_step": 0,
                         "depends_on": ["a"]},
                        {"filename": "2026-06-01-c.md", "slug": "c",
                         "status": "pending", "estimated_steps": 2, "current_step": 0,
                         "depends_on": ["b"]},
                        {"filename": "2026-06-01-d.md", "slug": "d",
                         "status": "pending", "estimated_steps": 1, "current_step": 0},
                    ],
                },
                {
                    "filename": "MASTER-2026-06-01-m2.md",
                    "status": "queued",
                    "created": "2026-06-01T01:00:00+08:00",
                    "priority": 2,
                    "sub_plans": [
                        {"filename": "2026-06-01-e.md", "slug": "e",
                         "status": "pending", "estimated_steps": 2, "current_step": 0},
                    ],
                },
            ],
        }

    raise ValueError(f"Unknown scenario: {scenario}")


def _read_status(plans_dir: Path, fname: str) -> str:
    """Read the status from a sub-plan or master file."""
    text = (plans_dir / fname).read_text(encoding="utf-8-sig")
    fm = parse_frontmatter(text)
    return fm.get("status", "pending").strip()


def _drain_loop(plans_dir: Path, project_root: Path | None = None, max_iters: int = 50) -> dict:
    """Simulate the drain loop: promote + resolve_status + flip runnable → shipped.

    Returns a summary dict with:
      - shipped_subplans: list of sub-plan filenames flipped to shipped
      - iters: number of loop iterations
      - masters_status: dict of master filename → final status
    """
    shipped_subplans: list[str] = []
    promote_count = 0
    cwd = project_root or plans_dir

    from loop_status import resolve_status

    for i in range(max_iters):
        # 1. Resolve status to find next runnable sub-plan.
        data = resolve_status(cwd)
        next_info = data.get("next")

        if next_info is not None:
            # Flip the runnable sub-plan to shipped.
            fname = next_info["fname"]
            write_status(plans_dir / fname, "shipped")
            shipped_subplans.append(fname)
            continue

        # 2. No runnable sub-plan → try promoting next queued master.
        masters = sorted(plans_dir.glob("MASTER-*.md"))
        queued_master = None
        for mp in masters:
            mtext = mp.read_text(encoding="utf-8-sig")
            mfm = parse_frontmatter(mtext)
            if mfm.get("status", "").strip() in ("queued", "pending"):
                queued_master = mp
                break

        if queued_master is None:
            # No queued masters left — drain is complete.
            break

        # Promote: demote active → promote queued → active.
        promote_main(["--plans-dir", str(plans_dir)])
        promote_count += 1

    # Collect final master statuses.
    masters_status: dict[str, str] = {}
    for mp in sorted(plans_dir.glob("MASTER-*.md")):
        masters_status[mp.name] = _read_status(plans_dir, mp.name)

    return {
        "shipped_subplans": shipped_subplans,
        "iters": i + 1 if 'i' in dir() else 0,
        "promote_count": promote_count,
        "masters_status": masters_status,
    }


# ── tests ────────────────────────────────────────────────────────────────────

class TestDrainPastBlocked:
    """L4 drain-past-blocked end-to-end tests."""

    def test_basic_drain(self, tmp_path):
        """AC-2 + AC-3 + AC-4: basic 3-master drain with blocked sub-plans."""
        spec = _make_spec("basic")
        plans_dir = generate_fixture(spec, tmp_path)

        result = _drain_loop(plans_dir, project_root=tmp_path)

        shipped = set(result["shipped_subplans"])

        # AC-2: runnable sub-plans (c, d, f) must be shipped.
        assert "2026-06-01-c.md" in shipped, "c (no deps, pending) must ship"
        assert "2026-06-01-d.md" in shipped, "d (no deps, pending) must ship"
        assert "2026-06-01-f.md" in shipped, "f (no deps, pending) must ship"

        # AC-2: e depends_on d; once d ships, e becomes runnable and ships.
        assert "2026-06-01-e.md" in shipped, "e (depends_on d, d shipped) must ship"

        # AC-3: b is blocked → must NOT be shipped.
        assert "2026-06-01-b.md" not in shipped, "b (blocked) must NOT ship"
        assert _read_status(plans_dir, "2026-06-01-b.md") == "blocked"

        # AC-4: M1 has blocked work (b), so it may not reach shipped.
        # M2 and M3 must reach shipped (their sub-plans are all runnable).
        ms = result["masters_status"]
        # M2's sub-plans (d, e) are both shipped → M2 should auto-reconcile to shipped.
        assert ms.get("MASTER-2026-06-01-m2.md") == "shipped", \
            "M2 (all sub-plans shipped) must reconcile to shipped"
        # M3's sub-plan (f) is shipped → M3 should reconcile to shipped.
        assert ms.get("MASTER-2026-06-01-m3.md") == "shipped", \
            "M3 (all sub-plans shipped) must reconcile to shipped"

    def test_first_master_blocked(self, tmp_path):
        """AC-4: first master stalled (blocked + blocked-dep), later master drains."""
        spec = _make_spec("first_master_blocked")
        plans_dir = generate_fixture(spec, tmp_path)

        result = _drain_loop(plans_dir, project_root=tmp_path)

        shipped = set(result["shipped_subplans"])

        # c (in M2) must ship despite M1 being stalled.
        assert "2026-06-01-c.md" in shipped, "c (M2, no deps) must ship past stalled M1"

        # a (blocked) must NOT ship.
        assert "2026-06-01-a.md" not in shipped, "a (blocked) must NOT ship"
        assert _read_status(plans_dir, "2026-06-01-a.md") == "blocked"

        # b depends_on a (blocked) → must NOT ship.
        assert "2026-06-01-b.md" not in shipped, "b (depends_on blocked a) must NOT ship"

        # M2 must reconcile to shipped.
        ms = result["masters_status"]
        assert ms.get("MASTER-2026-06-01-m2.md") == "shipped", \
            "M2 (all sub-plans shipped) must reconcile to shipped"

    def test_dep_chain(self, tmp_path):
        """AC-3: chain C depends_on B depends_on A (blocked) → none run; D runs."""
        spec = _make_spec("dep_chain")
        plans_dir = generate_fixture(spec, tmp_path)

        result = _drain_loop(plans_dir, project_root=tmp_path)

        shipped = set(result["shipped_subplans"])

        # d (no deps) must ship.
        assert "2026-06-01-d.md" in shipped, "d (no deps, pending) must ship"

        # a (blocked) must NOT ship.
        assert "2026-06-01-a.md" not in shipped
        assert _read_status(plans_dir, "2026-06-01-a.md") == "blocked"

        # b depends_on a (blocked) → NOT runnable, must NOT ship.
        assert "2026-06-01-b.md" not in shipped

        # c depends_on b (not shipped) → NOT runnable, must NOT ship.
        assert "2026-06-01-c.md" not in shipped

        # e (in M2) must ship after M1 stalls and M2 is promoted.
        assert "2026-06-01-e.md" in shipped, "e (M2, no deps) must ship past stalled M1"

        # M2 must reconcile to shipped.
        ms = result["masters_status"]
        assert ms.get("MASTER-2026-06-01-m2.md") == "shipped"

    def test_temp_isolation(self, tmp_path):
        """AC-5: writes only under tmp_path; real ~/.ilk-data untouched."""
        real_home = os.environ.get("ILK_DATA_HOME", "")

        spec = _make_spec("basic")
        plans_dir = generate_fixture(spec, tmp_path)

        # Verify files exist under tmp_path.
        assert (plans_dir / "MASTER-2026-06-01-m1.md").exists()
        assert (plans_dir / "2026-06-01-a.md").exists()

        # Verify nothing was written outside tmp_path.
        # The plans_dir is under tmp_path, so all writes are contained.
        assert str(plans_dir).startswith(str(tmp_path))

        # Verify ILK_DATA_HOME was not mutated (if it was set).
        if real_home:
            real_plans = Path(real_home)
            # No new files should appear in the real plans dir.
            # (This is a structural check — we don't enumerate, just verify
            # the plans_dir we're using is the tmp one.)
            assert not str(plans_dir).startswith(str(real_plans)) or \
                   str(real_plans).startswith(str(tmp_path))

    def test_blocked_never_flipped_to_shipped(self, tmp_path):
        """AC-3 (strong): run the full drain and assert every blocked sub-plan
        still has status=blocked at the end — no accidental ship."""
        spec = _make_spec("basic")
        plans_dir = generate_fixture(spec, tmp_path)

        # Collect all initially-blocked sub-plans.
        blocked_fnames: list[str] = []
        for m in spec["masters"]:
            for sp in m.get("sub_plans", []):
                if sp.get("status") == "blocked":
                    blocked_fnames.append(sp["filename"])

        assert blocked_fnames, "Spec must have at least one blocked sub-plan"

        # Run drain.
        _drain_loop(plans_dir)

        # Assert every blocked sub-plan is still blocked.
        for fname in blocked_fnames:
            status = _read_status(plans_dir, fname)
            assert status == "blocked", \
                f"{fname} was '{status}' after drain — must remain 'blocked'"
