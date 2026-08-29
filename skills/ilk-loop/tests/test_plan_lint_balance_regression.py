#!/usr/bin/env python3
"""Tests for plan_lint balance-regression-flag guard ('balance-drift' shape).

Detects sub-plans that change a core mechanic or tunable formula but contain
no baseline before/after regression assertion.  This is the "balance-drift"
shape where a change silently shifts behaviour without a before/after delta
check.

Part of sub-plan 2026-06-29-planlint-balance-regression-flag, step 0.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_balance_regression_flag  # noqa: E402

_PLAN_LINT = SCRIPTS_DIR / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── Should fire: core-mechanic change, no baseline assertion ─────────────

# Sub-plan adjusts a damage multiplier formula; only gate is a pure unit test.
_DAMAGE_FORMULA = """\
---
plan: test-damage-formula
scope_paths:
  - "src/game/combat.py"
local_checks:
  - command: python3 -m pytest tests/test_combat.py -q
    timeout: 60
---

# Sub-plan: damage formula tuning

Adjust the damage multiplier formula for critical hits.
Unit tests verify the pure calculation logic.
"""

# Sub-plan modifies a pricing coefficient; only gate is a unit test.
_PRICING_COEFFICIENT = """\
---
plan: test-pricing
scope_paths:
  - "src/billing/pricing.py"
local_checks:
  - command: python3 -m pytest tests/test_pricing.py -q
    timeout: 60
---

# Sub-plan: pricing coefficient

Modify the pricing coefficient for tier-2 customers.
"""

# Sub-plan rebalances a scoring threshold.
_SCORING_THRESHOLD = """\
---
plan: test-scoring
scope_paths:
  - "src/game/scoring.py"
local_checks:
  - command: python3 -m pytest tests/test_scoring.py -q
    timeout: 60
---

# Sub-plan: scoring rebalance

Rebalance the scoring threshold for combo bonuses.
"""

# Sub-plan tweaks a rate/weight.
_RATE_WEIGHT = """\
---
plan: test-rate-weight
scope_paths:
  - "src/game/drops.py"
local_checks:
  - command: python3 -m pytest tests/test_drops.py -q
    timeout: 60
---

# Sub-plan: drop rate tuning

Tweak the drop rate weights for rare items.
"""


class TestBalanceRegressionFires:
    def test_damage_formula_flagged(self):
        f = lint_balance_regression_flag(_DAMAGE_FORMULA, "test-damage")
        assert len(f) == 1, f
        assert "balance-drift" in f[0].lower() or "baseline" in f[0].lower()

    def test_pricing_coefficient_flagged(self):
        f = lint_balance_regression_flag(_PRICING_COEFFICIENT, "test-pricing")
        assert len(f) == 1, f

    def test_scoring_threshold_flagged(self):
        f = lint_balance_regression_flag(_SCORING_THRESHOLD, "test-scoring")
        assert len(f) == 1, f

    def test_rate_weight_flagged(self):
        f = lint_balance_regression_flag(_RATE_WEIGHT, "test-rate")
        assert len(f) == 1, f


# ── Should NOT fire: has baseline assertion ──────────────────────────────

# Sub-plan adjusts formula + has a baseline comparison.
_HAS_BASELINE = """\
---
plan: test-has-baseline
scope_paths:
  - "src/game/combat.py"
local_checks:
  - command: python3 -m pytest tests/test_combat.py -q
    timeout: 60
---

# Sub-plan: damage formula tuning

Adjust the damage multiplier formula.
local_checks verify against recorded baseline values.
"""

# Sub-plan modifies coefficient + has before/after assertion.
_HAS_BEFORE_AFTER = """\
---
plan: test-has-before-after
scope_paths:
  - "src/billing/pricing.py"
local_checks:
  - command: python3 -m pytest tests/test_pricing.py -q
    timeout: 60
---

# Sub-plan: pricing coefficient

Modify the pricing coefficient.
Test asserts before-and-after comparison with golden values.
"""

# Sub-plan rebalances + has golden/snapshot compare.
_HAS_GOLDEN = """\
---
plan: test-has-golden
scope_paths:
  - "src/game/scoring.py"
local_checks:
  - command: python3 -m pytest tests/test_scoring.py -q
    timeout: 60
---

# Sub-plan: scoring rebalance

Rebalance the scoring threshold.
Snapshot compare with golden file to catch regressions.
"""

# Sub-plan tweaks + has regression keyword.
_HAS_REGRESSION = """\
---
plan: test-has-regression
scope_paths:
  - "src/game/drops.py"
local_checks:
  - command: python3 -m pytest tests/test_drops.py -q
    timeout: 60
---

# Sub-plan: drop rate tuning

Tweak the drop rate weights.
Regression test compares against recorded baseline.
"""


class TestBalanceRegressionQuiet:
    def test_baseline_not_flagged(self):
        assert lint_balance_regression_flag(_HAS_BASELINE, "test-baseline") == []

    def test_before_after_not_flagged(self):
        assert lint_balance_regression_flag(_HAS_BEFORE_AFTER, "test-before") == []

    def test_golden_not_flagged(self):
        assert lint_balance_regression_flag(_HAS_GOLDEN, "test-golden") == []

    def test_regression_not_flagged(self):
        assert lint_balance_regression_flag(_HAS_REGRESSION, "test-regression") == []


# ── Should NOT fire: structural guards ──────────────────────────────────

# No local_checks commands at all.
_NO_COMMANDS = """\
---
plan: test-no-commands
scope_paths:
  - "src/game/combat.py"
---

# Sub-plan: combat design doc

Design notes only, no local_checks commands.
"""

# Body has no change verb — just documentation.
_NO_CHANGE_VERB = """\
---
plan: test-no-verb
scope_paths:
  - "src/game/combat.py"
local_checks:
  - command: python3 -m pytest tests/test_combat.py -q
    timeout: 60
---

# Sub-plan: combat documentation

Updates documentation for the combat module.
The damage formula is explained but not changed.
"""

# Body has no mechanic noun — just UI changes.
_NO_MECHANIC_NOUN = """\
---
plan: test-no-noun
scope_paths:
  - "src/ui/hud.ts"
local_checks:
  - command: npx vitest run tests/test_hud.ts
    timeout: 60
---

# Sub-plan: HUD refactor

Refactors the HUD rendering layout.
Modifies the component structure.
"""


class TestBalanceRegressionStructural:
    def test_no_commands_not_flagged(self):
        assert lint_balance_regression_flag(_NO_COMMANDS, "test-no-cmds") == []

    def test_no_change_verb_not_flagged(self):
        assert lint_balance_regression_flag(_NO_CHANGE_VERB, "test-no-verb") == []

    def test_no_mechanic_noun_not_flagged(self):
        assert lint_balance_regression_flag(_NO_MECHANIC_NOUN, "test-no-noun") == []


# ── CLI main() entrypoint ───────────────────────────────────────────────

class TestMainEntrypoint:
    def test_main_surfaces_balance_drift(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _DAMAGE_FORMULA)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "balance-drift" in result.stdout.lower() or "baseline" in result.stdout.lower()

    def test_main_clean_on_baseline(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _HAS_BASELINE)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout

    def test_main_clean_on_no_commands(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _NO_COMMANDS)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout
