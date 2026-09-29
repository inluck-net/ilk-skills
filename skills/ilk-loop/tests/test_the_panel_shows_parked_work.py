"""Red-first: an owed parked master keeps its project visible on the xbar panel.

Sub-plan `the-panel-shows-parked-work` (SP1 of
MASTER-2026-09-29e-the-panel-shows-parked-work-execution-plan).

Covers:
  AC-1 — Owed parks are seen.  When pick_active_master's choice is not
         active/queued, status_all scans every master with the
         pending_batches owed-park test.  It takes parked_reason from
         the one promotion would resume first (priority desc, created asc).
  AC-2 — Superseded parks are residue.  A master whose unquoted
         parked_reason starts with "superseded" is skipped by AC-1.
         It still counts in pending_batches, unchanged.
  AC-3 — The 2026-09-29 replay.  Fixture: shipped newest master, one
         violation-parked owing an in-progress sub-plan, two superseded
         owing sub-plans.  status_all returns parked: True, parked_reason
         naming the violation, blocked_reason "parked:ship_integrity_violation:".
         render_xbar emits a visible row.
  AC-4 — Only superseded parks stay hidden.  Same fixture minus the
         violation-parked master ⇒ parked: False, no row rendered.
  AC-5 — The active/queued path is unchanged.  When the chosen master
         is active or queued, the payload is byte-identical to today.
  AC-6 — The idle filter keeps parked rows.  A payload with parked: True
         is never dropped by the idle filter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "xbar"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))

from render_xbar import render_xbar  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────

def _master_text(
    slug: str,
    status: str = "active",
    *,
    priority: int = 5,
    created: str = "2026-09-29T16:00:00+0800",
    parked_reason: str = "",
    subplans: list[str] | None = None,
) -> str:
    """Build minimal MASTER frontmatter text."""
    if subplans is None:
        subplans = [f"2026-09-29-{slug}-sub.md"]
    rows = "\n".join(f"| {i+1} | [{s}](./{s}) | items | 3 | pending |"
                     for i, s in enumerate(subplans))
    registry = (
        "| # | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|\n"
        + rows
    )
    park_line = f"parked_reason: \"{parked_reason}\"\n" if parked_reason else ""
    return (
        f"---\n"
        f"master_plan: {slug}\n"
        f"status: {status}\n"
        f"priority: {priority}\n"
        f"created: {created}\n"
        f"{park_line}"
        f"---\n\n"
        f"# MASTER plan: {slug}\n\n"
        f"## Sub-plan registry\n\n{registry}\n"
    )


def _subplan_text(slug: str, status: str = "in-progress") -> str:
    """Build minimal sub-plan frontmatter text."""
    return (
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: 1\n"
        f"estimated_steps: 3\n"
        f"---\n\n"
        f"# Sub-plan: {slug}\n"
    )


def _build_plans_dir(
    tmp_path: Path,
    masters: list[tuple[str, str]],
    subplans: dict[str, str] | None = None,
) -> Path:
    """Build a fixture plans dir.

    masters: list of (filename, text) pairs.  Files are written in order
             so mtime ascends — the last file has the newest mtime.
    subplans: optional {filename: text} dict for sub-plan files.
              Written BEFORE masters so masters have the highest mtimes.
    """
    plans = tmp_path / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    if subplans:
        for name, text in subplans.items():
            (plans / name).write_text(text, encoding="utf-8")
    for name, text in masters:
        (plans / name).write_text(text, encoding="utf-8")
    return plans


def _build_runtime(tmp_path: Path, plans: Path) -> None:
    """Create minimal runtime dir (empty sentinel, no launcher)."""
    runtime = plans.parent / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)


def _resolve_status(plans: Path) -> dict:
    """Call resolve_project_status on the fixture, returning the dict."""
    from status_all import resolve_project_status
    from unittest.mock import patch

    proj = plans.parent
    runtime = proj / "runtime" / "launcher"
    runtime.mkdir(parents=True, exist_ok=True)
    with patch("status_all.external_launcher_dir", return_value=runtime), \
         patch("status_all.is_blacklisted", return_value={"blacklisted": False}), \
         patch("status_all._read_phase", return_value={}), \
         patch("status_all._roles_block", return_value=[]), \
         patch("status_all._providers_block", return_value=[]), \
         patch("status_all._steer_pause", return_value=(False, "")), \
         patch("status_all._resolve_repo_path", return_value=None):
        return resolve_project_status(proj)


def _row_for(out: str, key: str) -> str:
    """Return the single menu row mentioning key (excluding sub-items)."""
    rows = [line for line in out.splitlines()
            if key in line and not line.startswith("--")]
    assert len(rows) == 1, f"expected 1 row for {key}, got {rows}"
    return rows[0]


# ── AC-1: owed parks are seen ──────────────────────────────────────

def test_owed_park_sets_parked_reason_when_no_active_queued():
    """AC-1: pick_active_master falls back to shipped; owed park is scanned."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        master_v = _master_text(
            "violation-batch", "blocked", priority=5,
            created="2026-09-29T16:00:00+0800",
            parked_reason="ship_integrity_violation: run R slug=violation-sub",
            subplans=["2026-09-29-violation-sub.md"],
        )
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-violation.md", master_v),
        ], subplans={
            "2026-09-29-violation-sub.md": _subplan_text("violation-sub", "in-progress"),
        })
        # Make shipped the newest (highest mtime) so pick_active_master picks it
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["parked"] is True, "violation-parked master must be seen"
    assert "ship_integrity_violation" in (result["parked_reason"] or ""), \
        f"parked_reason must name the violation, got: {result['parked_reason']}"


def test_owed_park_chooses_highest_priority():
    """AC-1: among owed parks, the one with highest priority is chosen."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-low.md", _master_text(
                "low-batch", "blocked", priority=2,
                created="2026-09-29T15:00:00+0800",
                parked_reason="ship_integrity_violation: run R slug=low-sub",
                subplans=["2026-09-29-low-sub.md"],
            )),
            ("MASTER-high.md", _master_text(
                "high-batch", "blocked", priority=5,
                created="2026-09-29T16:00:00+0800",
                parked_reason="ship_integrity_violation: run R slug=high-sub",
                subplans=["2026-09-29-high-sub.md"],
            )),
        ], subplans={
            "2026-09-29-low-sub.md": _subplan_text("low-sub", "in-progress"),
            "2026-09-29-high-sub.md": _subplan_text("high-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["parked"] is True
    assert "MASTER-high.md" in (result.get("active_master") or ""), \
        "highest-priority owed park should be chosen"


# ── AC-2: superseded parks are residue ─────────────────────────────

def test_superseded_park_skipped_by_owed_scan():
    """AC-2: superseded parks are skipped by AC-1 and do not count in pending_batches."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-sup1.md", _master_text(
                "sup1-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup1-sub.md"],
            )),
            ("MASTER-sup2.md", _master_text(
                "sup2-batch", "blocked", priority=2,
                created="2026-09-28T11:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup2-sub.md"],
            )),
        ], subplans={
            "2026-09-28-sup1-sub.md": _subplan_text("sup1-sub", "in-progress"),
            "2026-09-28-sup2-sub.md": _subplan_text("sup2-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    # Superseded parks do NOT count in pending_batches (sub-plan 2 fix)
    assert result["pending_batches"] == 0, \
        f"superseded parks must not count, got pending_batches={result['pending_batches']}"
    # And they don't set parked (no owed non-superseded park exists)
    assert result["parked"] is False, \
        "superseded-only parks must not set parked"


# ── AC-3: the 2026-09-29 replay ────────────────────────────────────

def test_replay_violation_parked_visible():
    """AC-3: the full replay — violation park visible, row rendered."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        master_v = _master_text(
            "violation-batch", "blocked", priority=5,
            created="2026-09-29T16:00:00+0800",
            parked_reason="ship_integrity_violation: run 20260929-160000 slug=violation-sub",
            subplans=["2026-09-29-violation-sub.md"],
        )
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-violation.md", master_v),
            ("MASTER-sup1.md", _master_text(
                "sup1-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup1-sub.md"],
            )),
            ("MASTER-sup2.md", _master_text(
                "sup2-batch", "blocked", priority=2,
                created="2026-09-28T11:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup2-sub.md"],
            )),
        ], subplans={
            "2026-09-29-violation-sub.md": _subplan_text("violation-sub", "in-progress"),
            "2026-09-28-sup1-sub.md": _subplan_text("sup1-sub", "in-progress"),
            "2026-09-28-sup2-sub.md": _subplan_text("sup2-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    # status_all payload
    assert result["parked"] is True, "parked must be True"
    assert "ship_integrity_violation" in (result["parked_reason"] or ""), \
        f"parked_reason must name the violation, got: {result['parked_reason']}"
    assert result.get("blocked_reason", "").startswith("parked:"), \
        f"blocked_reason must start with 'parked:', got: {result.get('blocked_reason')}"
    # render_xbar emits a visible row
    entry = {
        "project_key": "replay-proj",
        "path": "/fake/replay-proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": result.get("active_master", ""),
        "next_subplan": result.get("next_subplan", ""),
        "step": result.get("step", ""),
        "sentinel": {"pid": 0, "state": "shipped", "alive": False},
        "last_class": None,
        "model": "",
        "runnable": result.get("runnable", False),
        "parked": result["parked"],
        "manually_runnable": result.get("manually_runnable", False),
        "blocked": result.get("blocked", False),
        "blocked_reason": result.get("blocked_reason"),
        "pending_batches": result.get("pending_batches", 0),
    }
    out = render_xbar([entry])
    rows = [line for line in out.splitlines()
            if "replay-proj" in line and not line.startswith("--")]
    assert len(rows) == 1, f"expected 1 visible row, got {rows}"


# ── AC-4: only superseded parks stay hidden ────────────────────────

def test_replay_without_violation_park_hidden():
    """AC-4: same fixture minus the violation-parked master ⇒ no row."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-sup1.md", _master_text(
                "sup1-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup1-sub.md"],
            )),
            ("MASTER-sup2.md", _master_text(
                "sup2-batch", "blocked", priority=2,
                created="2026-09-28T11:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup2-sub.md"],
            )),
        ], subplans={
            "2026-09-28-sup1-sub.md": _subplan_text("sup1-sub", "in-progress"),
            "2026-09-28-sup2-sub.md": _subplan_text("sup2-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["parked"] is False, "no violation park ⇒ parked must be False"
    # render_xbar should NOT emit a row (idle filter drops it)
    entry = {
        "project_key": "hidden-proj",
        "path": "/fake/hidden-proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": result.get("active_master", ""),
        "next_subplan": result.get("next_subplan", ""),
        "step": result.get("step", ""),
        "sentinel": {"pid": 0, "state": "shipped", "alive": False},
        "last_class": None,
        "model": "",
        "runnable": result.get("runnable", False),
        "parked": result["parked"],
        "manually_runnable": result.get("manually_runnable", False),
        "blocked": result.get("blocked", False),
        "blocked_reason": result.get("blocked_reason"),
        "pending_batches": result.get("pending_batches", 0),
    }
    out = render_xbar([entry])
    rows = [line for line in out.splitlines()
            if "hidden-proj" in line and not line.startswith("--")]
    assert len(rows) == 0, f"expected no visible row, got {rows}"


# ── AC-5: active/queued path unchanged ─────────────────────────────

def test_active_master_payload_unchanged():
    """AC-5: when chosen master is active, payload is byte-identical to today."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-active.md", _master_text(
                "active-batch", "active", priority=5,
                subplans=["2026-09-29-active-sub.md"],
            )),
        ], subplans={
            "2026-09-29-active-sub.md": _subplan_text("active-sub", "in-progress"),
        })
        result = _resolve_status(plans)
    assert result["active_master"] == "MASTER-active.md"
    assert result["parked"] is False
    assert result["parked_reason"] == ""
    assert result.get("blocked_reason") is None


def test_queued_master_payload_unchanged():
    """AC-5: when chosen master is queued, payload is byte-identical to today."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-queued.md", _master_text(
                "queued-batch", "queued", priority=5,
                subplans=["2026-09-29-queued-sub.md"],
            )),
        ], subplans={
            "2026-09-29-queued-sub.md": _subplan_text("queued-sub", "in-progress"),
        })
        result = _resolve_status(plans)
    assert result["active_master"] == "MASTER-queued.md"
    assert result["parked"] is False
    assert result["parked_reason"] == ""


# ── AC-6: idle filter keeps parked rows ────────────────────────────

def test_idle_filter_keeps_parked_row():
    """AC-6: a payload with parked: True is never dropped by the idle filter."""
    entry = {
        "project_key": "parked-proj",
        "path": "/fake/parked-proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": "MASTER-violation.md",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 0, "state": "shipped", "alive": False},
        "last_class": None,
        "model": "",
        "runnable": False,
        "parked": True,
        "manually_runnable": False,
        "blocked": True,
        "blocked_reason": "parked:ship_integrity_violation:",
        "pending_batches": 1,
    }
    out = render_xbar([entry])
    rows = [line for line in out.splitlines()
            if "parked-proj" in line and not line.startswith("--")]
    assert len(rows) == 1, f"parked row must be visible, got {rows}"


def test_idle_filter_drops_non_parked_idle():
    """AC-6 baseline: a non-parked idle project IS dropped by the idle filter."""
    entry = {
        "project_key": "idle-proj",
        "path": "/fake/idle-proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": "",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 0, "state": "shipped", "alive": False},
        "last_class": None,
        "model": "",
        "runnable": False,
        "parked": False,
        "manually_runnable": False,
        "blocked": False,
        "blocked_reason": None,
        "pending_batches": 0,
    }
    out = render_xbar([entry])
    rows = [line for line in out.splitlines()
            if "idle-proj" in line and not line.startswith("--")]
    assert len(rows) == 0, f"idle non-parked project must be dropped, got {rows}"
