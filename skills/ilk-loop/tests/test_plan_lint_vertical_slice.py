#!/usr/bin/env python3
"""Tests for plan_lint vertical-slice AC guard ('orphaned model' shape).

Detects sub-plans that add a model/logic capability whose every local_check
is a pure-unit test with no consumer entry-point keyword (UI hit-test, CLI
verb, HTTP route, e2e sim).  This is the "orphaned model" shape where the
model compiles and unit-tests pass but nothing proves a player/user can
actually reach it.

Part of sub-plan 2026-06-28-planlint-vertical-slice-anti-hardcode, step 0.
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

from plan_lint import lint_vertical_slice_ac  # noqa: E402

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


# ── Should fire: model-only, no consumer AC ────────────────────────────

# Sub-plan adds a Python function in a logic module; only gate is a unit test.
_MODEL_ONLY_PYTHON = """\
---
plan: test-model-only-python
scope_paths:
  - "src/game/towers/tower.py"
local_checks:
  - command: python3 -m pytest tests/test_tower.py -q
    timeout: 60
---

# Sub-plan: tower upgrade model

Adds upgrade(econ) and upgradeCost() to Tower.
Unit tests verify the pure model logic.
"""

# Sub-plan adds a TS exported function; only gate is vitest.
_MODEL_ONLY_TS = """\
---
plan: test-model-only-ts
scope_paths:
  - "src/game/stages/registry.ts"
local_checks:
  - command: /opt/homebrew/bin/npx vitest run tests/registry.spec.ts
    timeout: 60
---

# Sub-plan: stage registry

export function computePathCells(path: Point[]): Set<string>
Unit tests verify path cell computation.
"""

# Sub-plan adds a class; only gate is a pure-unit pytest on the class.
_MODEL_CLASS_ONLY = """\
---
plan: test-model-class
scope_paths:
  - "src/game/enemy.py"
local_checks:
  - command: python3 -m pytest tests/test_enemy.py -q
    timeout: 60
---

# Sub-plan: enemy class

class Enemy:
    def advance(self, dt: float) -> None:
        ...
Pure unit tests for Enemy.advance.
"""


class TestVerticalSliceAcFires:
    def test_model_only_python_flagged(self):
        f = lint_vertical_slice_ac(_MODEL_ONLY_PYTHON, "test-model-py")
        assert len(f) == 1, f
        assert "orphaned model" in f[0].lower() or "consumer" in f[0].lower()

    def test_model_only_ts_flagged(self):
        f = lint_vertical_slice_ac(_MODEL_ONLY_TS, "test-model-ts")
        assert len(f) == 1, f

    def test_model_class_only_flagged(self):
        f = lint_vertical_slice_ac(_MODEL_CLASS_ONLY, "test-model-class")
        assert len(f) == 1, f


# ── Should NOT fire: has consumer entry-point ───────────────────────────

# local_check includes a click (chrome-devtools) — real UI entry point.
_HAS_CLICK = """\
---
plan: test-has-click
scope_paths:
  - "src/game/towers/tower.py"
local_checks:
  - command: python3 -m pytest tests/test_tower.py -q
    timeout: 60
---

# Sub-plan: tower upgrade

Adds upgrade(econ) to Tower.
AC: click the upgrade button in the inspector and verify effective stats change.
"""

# local_check includes a curl — real HTTP entry point.
_HAS_CURL = """\
---
plan: test-has-curl
scope_paths:
  - "src/api/leaderboard.py"
local_checks:
  - command: python3 -m pytest tests/test_leaderboard.py -q
    timeout: 60
---

# Sub-plan: leaderboard API

Adds a leaderboard endpoint.
AC: curl http://localhost:8000/api/leaderboard returns 200 with scores.
"""

# local_check includes a CLI invocation.
_HAS_CLI = """\
---
plan: test-has-cli
scope_paths:
  - "src/cli/export.py"
local_checks:
  - command: python3 -m pytest tests/test_export.py -q
    timeout: 60
---

# Sub-plan: export CLI

Adds export command to the CLI.
AC: invoke 'mytool export --format csv' and verify output.
"""

# local_check includes an e2e spec reference.
_HAS_E2E = """\
---
plan: test-has-e2e
scope_paths:
  - "src/game/towers/tower.py"
local_checks:
  - command: npx playwright test tests/e2e/upgrade.spec.ts
    timeout: 120
---

# Sub-plan: tower upgrade e2e

Adds upgrade capability.
E2E spec verifies upgrade flow through the UI.
"""

# local_check includes integration keyword.
_HAS_INTEGRATION = """\
---
plan: test-has-integration
scope_paths:
  - "src/game/stages/registry.ts"
local_checks:
  - command: python3 -m pytest tests/test_stage_integration.py -q
    timeout: 60
---

# Sub-plan: stage registry integration

Integration test verifies enemy follows the active stage path.
"""


class TestVerticalSliceAcQuiet:
    def test_click_not_flagged(self):
        assert lint_vertical_slice_ac(_HAS_CLICK, "test-click") == []

    def test_curl_not_flagged(self):
        assert lint_vertical_slice_ac(_HAS_CURL, "test-curl") == []

    def test_cli_not_flagged(self):
        assert lint_vertical_slice_ac(_HAS_CLI, "test-cli") == []

    def test_e2e_not_flagged(self):
        assert lint_vertical_slice_ac(_HAS_E2E, "test-e2e") == []

    def test_integration_not_flagged(self):
        assert lint_vertical_slice_ac(_HAS_INTEGRATION, "test-integ") == []


# ── Should NOT fire: structural guards ──────────────────────────────────

# UI-only scope_paths — not a model/logic module.
_UI_ONLY_PATHS = """\
---
plan: test-ui-only
scope_paths:
  - "src/ui/hud.ts"
  - "src/ui/inspector.ts"
local_checks:
  - command: /opt/homebrew/bin/npx vitest run tests/test_hud.ts
    timeout: 60
---

# Sub-plan: HUD refactor

Refactors the HUD rendering. All scope is UI-layer.
"""

# No scope_paths at all.
_NO_SCOPE_PATHS = """\
---
plan: test-no-scope
local_checks:
  - command: python3 -m pytest tests/test_misc.py -q
    timeout: 60
---

# Sub-plan: misc cleanup

General cleanup with no specific scope_paths.
"""

# No local_checks commands.
_NO_COMMANDS = """\
---
plan: test-no-commands
scope_paths:
  - "src/game/towers/tower.py"
---

# Sub-plan: tower design doc

Design notes only, no local_checks commands.
"""

# Body has no def/class/export — just docs.
_NO_SYMBOL = """\
---
plan: test-no-symbol
scope_paths:
  - "src/game/towers/tower.py"
local_checks:
  - command: python3 -m pytest tests/test_tower.py -q
    timeout: 60
---

# Sub-plan: tower documentation

Updates documentation for the tower module. No new code added.
"""


class TestVerticalSliceAcStructural:
    def test_ui_only_paths_not_flagged(self):
        assert lint_vertical_slice_ac(_UI_ONLY_PATHS, "test-ui") == []

    def test_no_scope_paths_not_flagged(self):
        assert lint_vertical_slice_ac(_NO_SCOPE_PATHS, "test-no-scope") == []

    def test_no_commands_not_flagged(self):
        assert lint_vertical_slice_ac(_NO_COMMANDS, "test-no-cmds") == []

    def test_no_symbol_not_flagged(self):
        assert lint_vertical_slice_ac(_NO_SYMBOL, "test-no-sym") == []


# ── CLI main() entrypoint ───────────────────────────────────────────────

class TestMainEntrypoint:
    def test_main_surfaces_model_only(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _MODEL_ONLY_PYTHON)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "orphaned model" in result.stdout.lower() or "consumer" in result.stdout.lower()

    def test_main_clean_on_consumer_ac(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _HAS_CLICK)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout

    def test_main_clean_on_ui_only(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _UI_ONLY_PATHS)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout
