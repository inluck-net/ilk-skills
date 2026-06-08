"""Tests for the shipped-unverified classification (AC-5).

Covers:
  (a) clean batch with all loop-verified   ⇒ clean-success
  (b) clean batch with a device-manual tier ⇒ shipped-unverified
  (c) legacy batch with no tier field       ⇒ clean-success (back-compat)
  (d) two masters — scoped to active master (AC-4)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add the scripts dir so we can import collect
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
_LOOP_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_LOOP_SCRIPTS_DIR))

import collect  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────


def _write_subplan(
    plans_dir: Path,
    slug: str,
    *,
    status: str = "shipped",
    tier: str | None = None,
) -> Path:
    """Create a minimal sub-plan .md with the given frontmatter."""
    lines = ["---"]
    lines.append(f"plan: {slug}")
    lines.append(f"status: {status}")
    if tier is not None:
        lines.append(f"verification_tier: {tier}")
    lines.append("current_step: 3")
    lines.append("estimated_steps: 3")
    lines.append("---")
    lines.append(f"# {slug}\n")
    p = plans_dir / f"2026-06-08-{slug}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    """Create a temp plans dir with a MASTER file so _has_master() passes."""
    d = tmp_path / "plans"
    d.mkdir()
    (d / "MASTER-2026-06-08-test.md").write_text(
        "---\nmaster_plan: 2026-06-08-test\n---\n# test\n",
        encoding="utf-8",
    )
    return d


# ── batch_unverified_tiers ──────────────────────────────────────────────────


def test_all_loop_verified_returns_empty(plans_dir: Path) -> None:
    _write_subplan(plans_dir, "alpha", tier="loop-verified")
    _write_subplan(plans_dir, "beta", tier="loop-verified")

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        result = collect.batch_unverified_tiers(plans_dir.parent)
    assert result == []


def test_device_manual_detected(plans_dir: Path) -> None:
    _write_subplan(plans_dir, "alpha", tier="loop-verified")
    _write_subplan(plans_dir, "beta", tier="device-manual")

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "_loop_status", None):
            result = collect.batch_unverified_tiers(plans_dir.parent)
    assert len(result) == 1
    assert result[0]["plan"] == "beta"
    assert result[0]["tier"] == "device-manual"


def test_compile_only_detected(plans_dir: Path) -> None:
    _write_subplan(plans_dir, "gamma", tier="compile-only")

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "_loop_status", None):
            result = collect.batch_unverified_tiers(plans_dir.parent)
    assert len(result) == 1
    assert result[0]["tier"] == "compile-only"


def test_legacy_no_tier_treated_as_loop_verified(plans_dir: Path) -> None:
    """Legacy sub-plans without verification_tier field ⇒ back-compat loop-verified."""
    _write_subplan(plans_dir, "old-plan")  # no tier kwarg

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        result = collect.batch_unverified_tiers(plans_dir.parent)
    assert result == []


def test_pending_subplan_ignored(plans_dir: Path) -> None:
    """Non-shipped sub-plans are skipped even if they have a non-loop tier."""
    _write_subplan(plans_dir, "wip", status="pending", tier="device-manual")

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        result = collect.batch_unverified_tiers(plans_dir.parent)
    assert result == []


def test_no_plans_dir_returns_empty(tmp_path: Path) -> None:
    with patch.object(collect, "_find_plans_dir", return_value=(None, "")):
        result = collect.batch_unverified_tiers(tmp_path)
    assert result == []


# ── classify integration ────────────────────────────────────────────────────


def _make_clean_iters() -> list[dict]:
    """Minimal iter list that _classify_core would label clean-success."""
    return [
        {
            "run_id": "20260608-120000",
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 3,
            "stop_reason": "already-shipped",
            "duration_sec": 120,
        },
    ]


def test_clean_batch_classifies_clean_success(plans_dir: Path) -> None:
    """All loop-verified shipped sub-plans ⇒ clean-success."""
    _write_subplan(plans_dir, "alpha", tier="loop-verified")

    iters = _make_clean_iters()
    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, plans_dir.parent)
    assert label == "clean-success"
    assert "unverified_sub_plans" not in facts


def test_device_manual_subplan_downgrades_to_shipped_unverified(plans_dir: Path) -> None:
    """A shipped sub-plan with device-manual tier ⇒ shipped-unverified."""
    _write_subplan(plans_dir, "alpha", tier="loop-verified")
    _write_subplan(plans_dir, "beta", tier="device-manual")

    iters = _make_clean_iters()
    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "_loop_status", None):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, plans_dir.parent)
    assert label == "shipped-unverified"
    assert len(facts["unverified_sub_plans"]) == 1
    assert facts["unverified_sub_plans"][0]["plan"] == "beta"
    assert facts["unverified_sub_plans"][0]["tier"] == "device-manual"


def test_legacy_no_tier_classifies_clean_success(plans_dir: Path) -> None:
    """Legacy sub-plans with no verification_tier ⇒ back-compat clean-success."""
    _write_subplan(plans_dir, "old-plan")  # no tier

    iters = _make_clean_iters()
    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, plans_dir.parent)
    assert label == "clean-success"
    assert "unverified_sub_plans" not in facts


# ── AC-4: two-master scoping ─────────────────────────────────────────────────


def _write_master(
    plans_dir: Path,
    slug: str,
    subplan_files: list[str],
    *,
    status: str = "active",
) -> Path:
    """Create a minimal MASTER .md referencing the given sub-plan files."""
    lines = ["---"]
    lines.append(f"master_plan: {slug}")
    lines.append(f"status: {status}")
    lines.append("---")
    lines.append(f"# MASTER {slug}\n")
    lines.append("## Sub-plan registry\n")
    lines.append("| # | Slug | Status |")
    lines.append("|---|---|---|")
    for i, fname in enumerate(subplan_files, 1):
        lines.append(f"| {i} | {fname} | pending |")
    p = plans_dir / f"MASTER-{slug}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_two_masters_scoped_to_active_master(tmp_path: Path) -> None:
    """AC-4: a non-loop-verified shipped sub-plan in a DIFFERENT master
    must NOT be pulled into the active master's run classification.

    Setup:
      - Master A (active): all loop-verified sub-plans
      - Master B (shipped): has a device-manual sub-plan
    Assert: classifying master A's run ⇒ clean-success (B's device-manual
    sub-plan is NOT returned).
    """
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()

    # Master A (active) — all loop-verified
    master_a = _write_master(plans_dir, "2026-06-08-clean-batch", [
        "2026-06-08-alpha.md",
        "2026-06-08-beta.md",
    ], status="active")
    _write_subplan(plans_dir, "alpha", tier="loop-verified")
    _write_subplan(plans_dir, "beta", tier="loop-verified")

    # Master B (shipped) — has a device-manual sub-plan
    _write_master(plans_dir, "2026-06-07-old-batch", [
        "2026-06-08-gamma.md",
    ], status="shipped")
    _write_subplan(plans_dir, "gamma", tier="device-manual")

    # Mock loop_status to select master A as active and extract its registry
    mock_ls = types.ModuleType("loop_status")
    mock_ls.pick_active_master = MagicMock(return_value=(master_a, {}))  # type: ignore[attr-defined]
    mock_ls.extract_master_order = MagicMock(  # type: ignore[attr-defined]
        return_value=["2026-06-08-alpha.md", "2026-06-08-beta.md"],
    )

    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "_loop_status", mock_ls):
            result = collect.batch_unverified_tiers(plans_dir.parent)
    # Master A's sub-plans are all loop-verified ⇒ empty
    assert result == []

    # Now verify classify integration: clean-success (not shipped-unverified)
    iters = _make_clean_iters()
    with patch.object(collect, "_find_plans_dir", return_value=(plans_dir, "external")):
        with patch.object(collect, "_loop_status", mock_ls):
            with patch.object(collect, "collect_self_hosting_facts", return_value={}):
                label, facts = collect.classify(iters, None, plans_dir.parent)
    assert label == "clean-success"
    assert "unverified_sub_plans" not in facts
