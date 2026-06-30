"""Tests for skipped-by-operator terminal status in loop_status.py.

AC-5: a sub-plan with status: skipped-by-operator is treated as terminal
(skipped by the picker like shipped/blocked) but renders distinctly from
blocked (icon [--] instead of [XX]).

Verifies:
  1. A master with one skipped-by-operator + one pending → picker returns
     the pending one (the skipped one is never picked).
  2. The skipped sub-plan appears in the status table with [--] icon.
  3. A master where ALL non-shipped sub-plans are skipped-by-operator
     is treated as "nothing runnable" (not stalled).
  4. master_has_nonshipped treats skipped-by-operator as non-shipped
     (the master does NOT auto-reconcile to shipped).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from loop_status import resolve_status, main as loop_status_main, STATUS_ICONS


# ── fixtures ──────────────────────────────────────────────────────────────────

MASTER_SKIPPED_AND_PENDING = """\
---
master_plan: 2026-07-01-batch
status: active
batch_date: 2026-07-01
---

# MASTER plan: 2026-07-01-batch

| # | Sub-plan | Pri |
|---|---|---|
| 1 | [2026-07-01-alpha](./2026-07-01-alpha.md) | P0 |
| 2 | [2026-07-01-beta](./2026-07-01-beta.md) | P1 |
"""

MASTER_ALL_SKIPPED = """\
---
master_plan: 2026-07-02-batch
status: active
batch_date: 2026-07-02
---

# MASTER plan: 2026-07-02-batch

| # | Sub-plan | Pri |
|---|---|---|
| 1 | [2026-07-02-alpha](./2026-07-02-alpha.md) | P0 |
| 2 | [2026-07-02-beta](./2026-07-02-beta.md) | P1 |
"""


def _write_plan(path: Path, status: str, cur: int = 0, est: int = 1) -> None:
    path.write_text(
        f"---\nplan: {path.stem}\nstatus: {status}\n"
        f"current_step: {cur}\nestimated_steps: {est}\n---\n\n# {path.stem}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def plans_with_skipped(tmp_path: Path) -> Path:
    """Plans dir: one skipped + one pending sub-plan."""
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-07-01-batch.md").write_text(MASTER_SKIPPED_AND_PENDING, encoding="utf-8")
    _write_plan(d / "2026-07-01-alpha.md", "skipped-by-operator")
    _write_plan(d / "2026-07-01-beta.md", "pending")
    return tmp_path


@pytest.fixture()
def plans_all_skipped(tmp_path: Path) -> Path:
    """Plans dir: both sub-plans skipped-by-operator."""
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-07-02-batch.md").write_text(MASTER_ALL_SKIPPED, encoding="utf-8")
    _write_plan(d / "2026-07-02-alpha.md", "skipped-by-operator")
    _write_plan(d / "2026-07-02-beta.md", "skipped-by-operator")
    return tmp_path


# ── AC-5a: picker skips skipped-by-operator ───────────────────────────────────

def test_picker_returns_pending_not_skipped(plans_with_skipped):
    """When one sub-plan is skipped-by-operator and one is pending,
    the picker returns the pending one as next."""
    result = resolve_status(plans_with_skipped)

    assert result["next"] is not None, "picker should find a next sub-plan"
    assert result["next"]["fname"] == "2026-07-01-beta.md"
    assert result["queue_exit"] == 1


# ── AC-5b: distinct icon ─────────────────────────────────────────────────────

def test_skipped_icon_is_distinct():
    """skipped-by-operator uses [--] icon, not [XX] (blocked)."""
    assert STATUS_ICONS["skipped-by-operator"] == "[--]"
    assert STATUS_ICONS["skipped-by-operator"] != STATUS_ICONS["blocked"]


# ── AC-5c: all skipped → nothing runnable (not stalled) ──────────────────────

def test_all_skipped_nothing_runnable(plans_all_skipped):
    """When every non-shipped sub-plan is skipped-by-operator, the master
    is not stalled — it's 'nothing runnable' (exit 0)."""
    result = resolve_status(plans_all_skipped)

    assert result["next"] is None
    assert result["queue_exit"] == 0
    # Verify it's not reported as stalled.
    assert not result.get("stalled", False)


# ── AC-5d: master_has_nonshipped still sees skipped plans ────────────────────

def test_master_has_nonshipped_sees_skipped(tmp_path):
    """master_has_nonshipped treats skipped-by-operator as non-shipped.
    The master does NOT auto-reconcile to shipped when skipped plans exist."""
    from plan_status import master_has_nonshipped

    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-07-01-batch.md").write_text(MASTER_SKIPPED_AND_PENDING, encoding="utf-8")
    _write_plan(d / "2026-07-01-alpha.md", "skipped-by-operator")
    _write_plan(d / "2026-07-01-beta.md", "shipped")

    master = d / "MASTER-2026-07-01-batch.md"
    assert master_has_nonshipped(master, d) is True


# ── text output rendering ────────────────────────────────────────────────────

def test_skipped_renders_in_table(plans_with_skipped, monkeypatch, capsys):
    """The skipped sub-plan appears in the text output with [--] icon."""
    monkeypatch.chdir(plans_with_skipped)
    monkeypatch.setattr(sys, "argv", ["loop_status.py"])
    exit_code = loop_status_main()

    captured = capsys.readouterr()
    out = captured.out

    # The skipped plan appears with [--] icon and the status string.
    assert "[--]" in out
    assert "skipped-by-operator" in out
    # The pending plan is the next action.
    assert "2026-07-01-beta.md" in out
    assert exit_code == 1
