"""Tests for loop_status.extract_master_order — slug-collision regression.

Bug: extract_master_order computed master_own_fname = "{master_plan_slug}.md"
and excluded that from the sub-plan list.  But the actual master file is
always MASTER-<...>-execution-plan.md, NEVER <slug>.md.  So a sub-plan
legitimately named <slug>.md got dropped.

AC-1: colliding-slug master includes the sub-plan (when file exists on disk).
AC-2: actual MASTER-*.md files are still excluded.
AC-3: non-colliding masters are unchanged.
AC-4: phantom master_plan slug from title line is excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from loop_status import extract_master_order


# ── fixtures ──────────────────────────────────────────────────────────────────

# A master whose master_plan slug matches one of its sub-plan filenames.
COLLIDING_MASTER = """\
---
master_plan: 2026-06-22-tray-idle-filter
status: active
batch_date: 2026-06-22
---

# MASTER — Tray idle filter

| # | Sub-plan | Pri |
|---|---|---|
| 1 | [2026-06-22-tray-idle-filter](./2026-06-22-tray-idle-filter.md) | P0 |
| 2 | [2026-06-22-fix-nits](./2026-06-22-fix-nits.md) | P1 |
"""

# A master with no slug collision (the normal case).
NORMAL_MASTER = """\
---
master_plan: 2026-06-20-batch
status: active
batch_date: 2026-06-20
---

# MASTER plan: 2026-06-20-batch

| # | Sub-plan | Pri |
|---|---|---|
| 1 | [2026-06-20-alpha](./2026-06-20-alpha.md) | P0 |
| 2 | [2026-06-20-beta](./2026-06-20-beta.md) | P1 |
"""

# A master that references itself by the MASTER-*.md filename in the body.
SELF_REF_MASTER = """\
---
master_plan: 2026-06-19-m1
status: active
---

See [MASTER-2026-06-19-execution-plan](./MASTER-2026-06-19-execution-plan.md).

| # | Sub-plan |
|---|---|
| 1 | [2026-06-19-s1](./2026-06-19-s1.md) |
"""


# ── AC-1: colliding slug is NOT excluded when file exists on disk ─────────────

def test_ac1_colliding_slug_included_with_plans_dir(tmp_path):
    """A sub-plan whose filename == master_plan slug is included when the
    file exists on disk (plans_dir provided)."""
    # Create the colliding file on disk.
    (tmp_path / "2026-06-22-tray-idle-filter.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-22-fix-nits.md").write_text("---\nstatus: pending\n---\n")
    result = extract_master_order(COLLIDING_MASTER, plans_dir=tmp_path)
    assert "2026-06-22-tray-idle-filter.md" in result, (
        f"AC-1 FAIL: colliding slug dropped. Got: {result}"
    )


def test_ac1_colliding_slug_excluded_without_plans_dir():
    """Without plans_dir, the master_plan slug is always excluded (back-compat)."""
    result = extract_master_order(COLLIDING_MASTER)
    assert "2026-06-22-tray-idle-filter.md" not in result


def test_ac1_colliding_slug_not_alone(tmp_path):
    """The non-colliding sibling is also present."""
    (tmp_path / "2026-06-22-tray-idle-filter.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-22-fix-nits.md").write_text("---\nstatus: pending\n---\n")
    result = extract_master_order(COLLIDING_MASTER, plans_dir=tmp_path)
    assert "2026-06-22-fix-nits.md" in result


# ── AC-2: MASTER-*.md files are still excluded ────────────────────────────────

def test_ac2_master_file_excluded():
    """MASTER-*.md filenames in the body must NOT appear in the sub-plan list."""
    result = extract_master_order(SELF_REF_MASTER)
    for fname in result:
        assert not fname.startswith("MASTER"), (
            f"AC-2 FAIL: MASTER file {fname!r} leaked into sub-plan list"
        )


def test_ac2_only_real_subplans():
    """Only date-prefixed sub-plan slugs are returned."""
    result = extract_master_order(SELF_REF_MASTER)
    assert result == ["2026-06-19-s1.md"]


# ── AC-3: non-colliding masters unchanged ─────────────────────────────────────

def test_ac3_normal_order_preserved():
    """A master with no collision extracts sub-plans in appearance order.
    The phantom master_plan slug from the title line is excluded."""
    result = extract_master_order(NORMAL_MASTER)
    assert result == ["2026-06-20-alpha.md", "2026-06-20-beta.md"]


def test_ac3_normal_order_with_plans_dir(tmp_path):
    """Same result when plans_dir is provided (no phantom to resolve)."""
    (tmp_path / "2026-06-20-alpha.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-20-beta.md").write_text("---\nstatus: pending\n---\n")
    result = extract_master_order(NORMAL_MASTER, plans_dir=tmp_path)
    assert result == ["2026-06-20-alpha.md", "2026-06-20-beta.md"]


def test_ac3_dedup():
    """Duplicate references are deduped (first occurrence wins)."""
    master = """\
---
master_plan: 2026-06-21-x
---

| # | Sub-plan |
|---|---|
| 1 | [2026-06-21-a](./2026-06-21-a.md) |
| 2 | [2026-06-21-a](./2026-06-21-a.md) |
"""
    result = extract_master_order(master)
    assert result == ["2026-06-21-a.md"]


# ── AC-4: phantom master_plan slug from title line is excluded ────────────────

def test_ac4_phantom_slug_excluded_without_plans_dir():
    """Without plans_dir, the master_plan slug is always excluded."""
    result = extract_master_order(NORMAL_MASTER)
    assert "2026-06-20-batch.md" not in result


def test_ac4_phantom_slug_excluded_when_missing(tmp_path):
    """With plans_dir, a non-existent master_plan slug is excluded as phantom."""
    # Only create the real sub-plans, not the phantom.
    (tmp_path / "2026-06-20-alpha.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-20-beta.md").write_text("---\nstatus: pending\n---\n")
    result = extract_master_order(NORMAL_MASTER, plans_dir=tmp_path)
    assert "2026-06-20-batch.md" not in result


def test_ac4_phantom_slug_kept_when_exists(tmp_path):
    """With plans_dir, a master_plan slug that exists on disk is kept
    (it's a legitimate sub-plan, not a phantom)."""
    (tmp_path / "2026-06-20-batch.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-20-alpha.md").write_text("---\nstatus: pending\n---\n")
    (tmp_path / "2026-06-20-beta.md").write_text("---\nstatus: pending\n---\n")
    result = extract_master_order(NORMAL_MASTER, plans_dir=tmp_path)
    assert "2026-06-20-batch.md" in result
