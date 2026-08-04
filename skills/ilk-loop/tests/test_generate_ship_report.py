"""Tests for the Definition-of-Done (DoD) outcome banner in generate_ship_report.py.

Covers the three acceptance criteria:
  AC-1: Sub-plan with user-facing outcome + outcome-level AC renders DoD block
        with restated outcome and matched AC id.
  AC-2: Sub-plan with user-facing outcome but NO outcome-level AC renders
        [WARN] outcome not verified at outcome level.
  AC-3: Non-user-facing sub-plan (pure refactor/docs) omits the DoD block.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generate_ship_report import dod_section, build_report


# ── Fixtures ─────────────────────────────────────────────────────────────────

_FIXTURE_USER_FACING_WITH_AC = """\
---
plan: test-upgrade-feature
status: shipped
current_step: 5
estimated_steps: 5
tickets:
  - T-2026-0010
scope_paths:
  - "src/game/towers/tower.ts"
  - "src/ui/hud.ts"
local_checks:
  - command: pytest -q
    timeout: 120
---

# Upgrade installed defenders

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| T-2026-0010 | Upgrade installed defenders | 体验优化 | P1 | tower.ts |

## Objectives

- Allow the player to upgrade a placed tower through the inspector and see
  effective stats change.

## Steps

### Step 0
- Add upgrade button to inspector UI.

## Acceptance criteria

- **AC-1**: Player can click upgrade on a placed tower through the inspector; effective damage changes.
- **AC-2**: upgradeCost() returns correct cost per tier.
- **AC-3**: Unit tests for canUpgrade() pass.
"""

_FIXTURE_USER_FACING_NO_AC = """\
---
plan: test-orphaned-model
status: shipped
current_step: 3
estimated_steps: 3
tickets:
  - T-2026-0020
scope_paths:
  - "src/game/towers/tower.ts"
local_checks:
  - command: pytest -q
    timeout: 120
---

# Different rails per stage

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| T-2026-0020 | Different rails per stage | 新功能 | P1 | registry.ts |

## Objectives

- Each stage uses its own distinct path for enemy movement.

## Steps

### Step 0
- Add path arrays to stage registry.

## Acceptance criteria

- **AC-1**: path arrays are defined for each stage.
- **AC-2**: computePathCells returns correct cells per stage.
- **AC-3**: Unit tests for stage registry pass.
"""

_FIXTURE_NON_USER_FACING = """\
---
plan: test-pure-refactor
status: shipped
current_step: 2
estimated_steps: 2
tickets:
  - T-2026-0030
scope_paths:
  - "src/utils/helpers.ts"
local_checks:
  - command: pytest -q
    timeout: 120
---

# Refactor helper utilities

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| T-2026-0030 | Refactor helper utilities | 重构 | P2 | helpers.ts |

## Steps

### Step 0
- Extract common patterns into shared helpers.

## Acceptance criteria

- **AC-1**: helpers.ts exports the expected functions.
- **AC-2**: All existing tests still pass.
"""


# ── AC-1: outcome-verified ───────────────────────────────────────────────────

class TestDodAc1OutcomeVerified:
    """AC-1: user-facing outcome + outcome-level AC renders DoD with matched AC."""

    def test_dod_section_contains_outcome_and_ac(self):
        result = dod_section(_FIXTURE_USER_FACING_WITH_AC)
        assert "## Definition of Done" in result
        assert "Outcome:" in result
        assert "Verified by:" in result
        # The matched AC should mention "click" (a consumer entry-point keyword).
        assert "click" in result.lower() or "upgrade" in result.lower()

    def test_dod_section_does_not_contain_warn(self):
        result = dod_section(_FIXTURE_USER_FACING_WITH_AC)
        assert "[WARN]" not in result

    def test_dod_section_restates_outcome(self):
        result = dod_section(_FIXTURE_USER_FACING_WITH_AC)
        # Should restate the outcome from the Objectives section.
        assert "upgrade" in result.lower() or "tower" in result.lower()


# ── AC-2: outcome-unverified (WARN) ─────────────────────────────────────────

class TestDodAc2OutcomeUnverified:
    """AC-2: user-facing outcome but NO outcome-level AC renders [WARN]."""

    def test_dod_section_contains_warn(self):
        result = dod_section(_FIXTURE_USER_FACING_NO_AC)
        assert "## Definition of Done" in result
        assert "[WARN]" in result
        assert "outcome not verified at outcome level" in result

    def test_dod_section_contains_outcome_text(self):
        result = dod_section(_FIXTURE_USER_FACING_NO_AC)
        assert "Outcome:" in result
        # Should mention the outcome from Objectives or title.
        assert "stage" in result.lower() or "rails" in result.lower() or "path" in result.lower()

    def test_dod_section_contains_orphaned_model_hint(self):
        result = dod_section(_FIXTURE_USER_FACING_NO_AC)
        assert "orphaned model" in result.lower()


# ── AC-3: non-user-facing (no DoD block) ────────────────────────────────────

class TestDodAc3NonUserFacing:
    """AC-3: non-user-facing sub-plan (pure refactor/docs) → DoD block omitted."""

    def test_dod_section_returns_empty(self):
        result = dod_section(_FIXTURE_NON_USER_FACING)
        assert result == ""

    def test_dod_section_no_definition_header(self):
        result = dod_section(_FIXTURE_NON_USER_FACING)
        assert "Definition of Done" not in result


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestDodEdgeCases:
    """Additional edge cases for robustness."""

    def test_empty_body_returns_empty(self):
        result = dod_section("---\nplan: empty\n---\n")
        assert result == ""

    def test_no_tickets_table_returns_empty(self):
        """Sub-plan without a tickets table and no user-facing verbs."""
        text = """\
---
plan: no-table
status: shipped
---

# Some task

## Steps

### Step 0
- Do something.
"""
        result = dod_section(text)
        assert result == ""

    def test_user_facing_type_but_no_outcome_uses_title(self):
        """If ticket type is user-facing but no Objectives/AC verb, use title."""
        text = """\
---
plan: title-fallback
status: shipped
tickets:
  - T-2026-0040
---

# Render the new HUD overlay

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| T-2026-0040 | New HUD overlay | 体验优化 | P1 | hud.ts |

## Steps

### Step 0
- Add overlay component.
"""
        result = dod_section(text)
        # Should have a DoD block with the title as outcome.
        assert "## Definition of Done" in result
        assert "HUD" in result or "overlay" in result.lower()


# ── Integration: build_report includes DoD when present ──────────────────────

class TestBuildReportDodIntegration:
    """build_report includes/excludes DoD section based on sub-plan content."""

    @pytest.fixture()
    def project_dir(self, tmp_path: Path) -> Path:
        """Create a minimal project dir with two git commits."""
        import subprocess

        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True, encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
            check=True,
            capture_output=True, encoding="utf-8",
        )
        # First commit (base).
        (tmp_path / "dummy.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "dummy.txt"], check=True, capture_output=True, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "base"],
            check=True,
            capture_output=True, encoding="utf-8",
        )
        # Second commit (head) — so HEAD~1..HEAD is a valid range.
        (tmp_path / "dummy.txt").write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "dummy.txt"], check=True, capture_output=True, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "head"],
            check=True,
            capture_output=True, encoding="utf-8",
        )
        return tmp_path

    def _make_reviewer(self, tmp_path: Path) -> Path:
        r = tmp_path / "reviewer.md"
        r.write_text(
            "## 1. AC verdicts\n\n| AC | verdict |\n|---|---|\n| AC-1 | PASS |\n",
            encoding="utf-8",
        )
        return r

    def test_build_report_with_user_facing_includes_dod(self, project_dir: Path):
        reviewer = self._make_reviewer(project_dir)
        report = build_report(
            project=project_dir,
            sub_plan_path=project_dir / "sub.md",
            sub_plan_text=_FIXTURE_USER_FACING_WITH_AC,
            base="HEAD~1",
            head="HEAD",
            reviewer_report_path=reviewer,
            reviewer_text=reviewer.read_text(encoding="utf-8"),
            test_results_path=None,
            ci_url="",
            ci_state="unknown",
            iteration=1,
        )
        assert "DEFINITION OF DONE" in report
        assert "Outcome:" in report

    def test_build_report_without_user_facing_excludes_dod(self, project_dir: Path):
        reviewer = self._make_reviewer(project_dir)
        report = build_report(
            project=project_dir,
            sub_plan_path=project_dir / "sub.md",
            sub_plan_text=_FIXTURE_NON_USER_FACING,
            base="HEAD~1",
            head="HEAD",
            reviewer_report_path=reviewer,
            reviewer_text=reviewer.read_text(encoding="utf-8"),
            test_results_path=None,
            ci_url="",
            ci_state="unknown",
            iteration=1,
        )
        assert "DEFINITION OF DONE" not in report
