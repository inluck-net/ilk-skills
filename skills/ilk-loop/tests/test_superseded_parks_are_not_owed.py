"""Red-first: a superseded park is not counted as owed work.

Sub-plan `superseded-parks-are-not-owed` (SP2 of
MASTER-2026-09-29e-the-panel-shows-parked-work-execution-plan).

Covers:
  AC-1 — A master whose unquoted parked_reason starts with "superseded"
         (case-insensitive) does not count in pending_batches.
  AC-2 — The 16:48 replay: fixture with one active master, one
         violation-parked owing work, two superseded-parked owing work
         ⇒ pending_batches == 2.
  AC-3 — Unchanged: a violation park or operator park still counts,
         and a project with only superseded parks + shipped masters
         reads pending_batches == 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "xbar"))

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
    """Build a fixture plans dir."""
    plans = tmp_path / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    if subplans:
        for name, text in subplans.items():
            (plans / name).write_text(text, encoding="utf-8")
    for name, text in masters:
        (plans / name).write_text(text, encoding="utf-8")
    return plans


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


# ── AC-1: superseded parks don't count ──────────────────────────────

def test_superseded_park_excluded_from_pending_batches():
    """AC-1: a superseded-parked master does not count in pending_batches."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-sup.md", _master_text(
                "sup-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup-sub.md"],
            )),
        ], subplans={
            "2026-09-28-sup-sub.md": _subplan_text("sup-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["pending_batches"] == 0, \
        f"superseded park must not count, got pending_batches={result['pending_batches']}"


def test_superseded_case_insensitive():
    """AC-1: parked_reason 'Superseded …' (capital S) is also excluded."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-sup.md", _master_text(
                "sup-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="Superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup-sub.md"],
            )),
        ], subplans={
            "2026-09-28-sup-sub.md": _subplan_text("sup-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["pending_batches"] == 0, \
        f"case-insensitive superseded must not count, got pending_batches={result['pending_batches']}"


# ── AC-2: the 16:48 replay ─────────────────────────────────────────

def test_replay_pending_batches_excludes_superseded():
    """AC-2: active + violation-parked +2 superseded ⇒ pending_batches == 2."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-active.md", _master_text(
                "active-batch", "active", priority=5,
                subplans=["2026-09-29-active-sub.md"],
            )),
            ("MASTER-violation.md", _master_text(
                "violation-batch", "blocked", priority=4,
                created="2026-09-29T15:00:00+0800",
                parked_reason="ship_integrity_violation: run R slug=violation-sub",
                subplans=["2026-09-29-violation-sub.md"],
            )),
            ("MASTER-sup1.md", _master_text(
                "sup1-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: kept parts re-planned as MASTER-2026-09-29a",
                subplans=["2026-09-28-sup1-sub.md"],
            )),
            ("MASTER-sup2.md", _master_text(
                "sup2-batch", "blocked", priority=2,
                created="2026-09-28T11:00:00+0800",
                parked_reason="superseded 2026-09-29: kept parts re-planned as MASTER-2026-09-29a",
                subplans=["2026-09-28-sup2-sub.md"],
            )),
        ], subplans={
            "2026-09-29-active-sub.md": _subplan_text("active-sub", "in-progress"),
            "2026-09-29-violation-sub.md": _subplan_text("violation-sub", "in-progress"),
            "2026-09-28-sup1-sub.md": _subplan_text("sup1-sub", "in-progress"),
            "2026-09-28-sup2-sub.md": _subplan_text("sup2-sub", "in-progress"),
        })
        result = _resolve_status(plans)
    assert result["pending_batches"] == 2, \
        f"active + violation = 2, got pending_batches={result['pending_batches']}"
    # Badge renders +2
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
        "parked": result.get("parked", False),
        "manually_runnable": result.get("manually_runnable", False),
        "blocked": result.get("blocked", False),
        "blocked_reason": result.get("blocked_reason"),
        "pending_batches": result["pending_batches"],
    }
    out = render_xbar([entry])
    rows = [line for line in out.splitlines()
            if "replay-proj" in line and not line.startswith("--")]
    assert len(rows) == 1, f"expected 1 row, got {rows}"
    assert "+2" in rows[0], f"badge must show +2, got {rows[0]}"


# ── AC-3: unchanged paths ──────────────────────────────────────────

def test_violation_park_still_counts():
    """AC-3: a violation park still counts in pending_batches."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-violation.md", _master_text(
                "violation-batch", "blocked", priority=4,
                created="2026-09-29T15:00:00+0800",
                parked_reason="ship_integrity_violation: run R slug=violation-sub",
                subplans=["2026-09-29-violation-sub.md"],
            )),
        ], subplans={
            "2026-09-29-violation-sub.md": _subplan_text("violation-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["pending_batches"] == 1, \
        f"violation park must count, got pending_batches={result['pending_batches']}"


def test_only_superseded_and_shipped_reads_zero():
    """AC-3: only superseded parks + shipped masters ⇒ pending_batches == 0."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plans = _build_plans_dir(tmp, [
            ("MASTER-shipped.md", _master_text("shipped-batch", "shipped", priority=3)),
            ("MASTER-sup.md", _master_text(
                "sup-batch", "blocked", priority=2,
                created="2026-09-28T10:00:00+0800",
                parked_reason="superseded 2026-09-29: by shipped-batch",
                subplans=["2026-09-28-sup-sub.md"],
            )),
        ], subplans={
            "2026-09-28-sup-sub.md": _subplan_text("sup-sub", "in-progress"),
        })
        (plans / "MASTER-shipped.md").touch()
        result = _resolve_status(plans)
    assert result["pending_batches"] == 0, \
        f"only superseded ⇒ 0, got pending_batches={result['pending_batches']}"
