"""Tests for the effective iteration-window resolution at launch time.

AC-1 (the window is the window): with a config ``iteration_timeout_min`` and
an active master whose sub-plans declare
``recommended_iteration_timeout_min``, the resolved window is the maximum of
the config value and the highest declaration, hard-capped at
``ILK_MAX_ITERATION_TIMEOUT_MIN``.

Three fixture projects:
  A. no declarations → stays at config value.
  B. declarations of 90 and 45 → resolved window is 90.
  C. declaration of 300 → caps at 120.

These tests drive a Python helper that is called by ``launch.sh``; the helper
is expected to exist at
``skills/ilk-launcher/scripts/_resolve_effective_window.py``.
The tests run RED against the current tree (the module does not yet exist).
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

# Resolve sibling modules via the scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_plan(dirpath: Path, filename: str, body: str) -> None:
    """Write a plan file with the given body (including frontmatter)."""
    (dirpath / filename).write_text(textwrap.dedent(body), encoding="utf-8")


def _make_project(
    tmp_path: Path,
    *,
    config_timeout: int = 30,
    master_body: str | None = None,
    sub_plans: dict[str, str] | None = None,
) -> Path:
    """Create a minimal project with plans dir and config.

    Uses the in-tree fallback layout (``docs/plans/``) so that
    ``find_plans_dir``'s walk-up resolution finds it without needing a
    git repository or external plans directory.
    Returns the project root path.
    """
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # .ilk-launch.json
    launch_cfg = {"iteration_timeout_min": config_timeout}
    (tmp_path / ".ilk-launch.json").write_text(
        json.dumps(launch_cfg), encoding="utf-8"
    )

    # Master plan
    if master_body is not None:
        _write_plan(plans_dir, "MASTER-test.md", master_body)

    # Sub-plans
    for fname, body in (sub_plans or {}).items():
        _write_plan(plans_dir, fname, body)

    return tmp_path


# ── fixture plan bodies ──────────────────────────────────────────────────────

MASTER_NO_DECLS = """\
    ---
    title: test-master
    slug: test-master
    created: 2026-09-20T23:10:00+08:00
    status: active
    priority: null
    pause_after_ship: false
    supervised_only: false
    base_branch: main
    branch: null
    goal: test
    out_of_scope: []
    cross_cutting_invariants: []
    ---

    # MASTER plan: test

    ## Sub-plan registry

    | # | Sub-plan | File | Status |
    |---|---|---|---|
    | 1 | sub-a | 2026-09-20-sub-a.md | pending |
"""

SUB_A_NO_DECL = """\
    ---
    plan: sub-a
    status: pending
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-09-20
    ---

    # Sub-plan: no declaration
"""

SUB_A_DECL_90 = """\
    ---
    plan: sub-a
    status: pending
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-09-20
    recommended_iteration_timeout_min: 90
    ---

    # Sub-plan: declares 90
"""

SUB_B_DECL_45 = """\
    ---
    plan: sub-b
    status: pending
    current_step: 0
    tickets: []
    priority: P1
    estimated_steps: 1
    last_updated: 2026-09-20
    recommended_iteration_timeout_min: 45
    ---

    # Sub-plan: declares 45
"""

SUB_A_DECL_300 = """\
    ---
    plan: sub-a
    status: pending
    current_step: 0
    tickets: []
    priority: P0
    estimated_steps: 2
    last_updated: 2026-09-20
    recommended_iteration_timeout_min: 300
    ---

    # Sub-plan: declares 300
"""

MASTER_TWO_DECLS = """\
    ---
    title: test-master
    slug: test-master
    created: 2026-09-20T23:10:00+08:00
    status: active
    priority: null
    pause_after_ship: false
    supervised_only: false
    base_branch: main
    branch: null
    goal: test
    out_of_scope: []
    cross_cutting_invariants: []
    ---

    # MASTER plan: test

    ## Sub-plan registry

    | # | Sub-plan | File | Status |
    |---|---|---|---|
    | 1 | sub-a | 2026-09-20-sub-a.md | pending |
    | 2 | sub-b | 2026-09-20-sub-b.md | pending |
"""


# ── tests ────────────────────────────────────────────────────────────────────

class TestAC1_WindowResolution:
    """AC-1: effective window = max(config, max declaration), capped at 120."""

    def test_no_declarations_stays_at_config(self, tmp_path: Path) -> None:
        """No sub-plans declare recommended_iteration_timeout_min → config value."""
        from _resolve_effective_window import resolve_effective_timeout

        project = _make_project(
            tmp_path / "proj",
            config_timeout=30,
            master_body=MASTER_NO_DECLS,
            sub_plans={"2026-09-20-sub-a.md": SUB_A_NO_DECL},
        )
        result = resolve_effective_timeout(project)
        assert result == 30

    def test_subplan_declarations_override_config(self, tmp_path: Path) -> None:
        """Config 30, sub-plans declare 90 and 45 → resolved window is 90."""
        from _resolve_effective_window import resolve_effective_timeout

        project = _make_project(
            tmp_path / "proj",
            config_timeout=30,
            master_body=MASTER_TWO_DECLS,
            sub_plans={
                "2026-09-20-sub-a.md": SUB_A_DECL_90,
                "2026-09-20-sub-b.md": SUB_B_DECL_45,
            },
        )
        result = resolve_effective_timeout(project)
        assert result == 90

    def test_oversized_declaration_is_capped(self, tmp_path: Path) -> None:
        """Declaration of 300 → capped at 120."""
        from _resolve_effective_window import resolve_effective_timeout

        project = _make_project(
            tmp_path / "proj",
            config_timeout=30,
            master_body=MASTER_NO_DECLS,
            sub_plans={"2026-09-20-sub-a.md": SUB_A_DECL_300},
        )
        result = resolve_effective_timeout(project)
        assert result == 120
