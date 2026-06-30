#!/usr/bin/env python3
"""Tests for plan_lint UI-promise-wiring guard ('promise-without-wiring' shape).

Detects sub-plans that introduce a UI affordance/prompt that advertises a
capability (key hint, button label, shortcut, indicator) but whose local_checks
and body contain no wiring/trigger assertion (event handler, keybind, click,
press_key, e2e).  This is the "promise-without-wiring" shape where the UI
prompts the user to act but nothing is actually bound.

Part of sub-plan 2026-06-29-planlint-ui-promise-wiring, step 0.
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

from plan_lint import lint_ui_promise_wiring  # noqa: E402

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


# ── Should fire: affordance advertised, no wiring ───────────────────────

# "press C for the codex" with only a pure-unit pytest gate.
_PRESS_KEY_NO_WIRING = """\
---
plan: test-press-key-no-wire
scope_paths:
  - "src/ui/level_select.py"
local_checks:
  - command: python -m pytest tests/test_level_select.py -q
    timeout: 60
---

# Sub-plan: level select codex hint

The level-select screen shows "press C for the codex" as a key hint.
Unit tests verify the level-select rendering.
"""

# Button labeled with tooltip, no event binding.
_BUTTON_TOOLTIP_NO_WIRING = """\
---
plan: test-button-tooltip
scope_paths:
  - "src/ui/settings.py"
local_checks:
  - command: python -m pytest tests/test_settings.py -q
    timeout: 60
---

# Sub-plan: settings button

Adds a button labeled "Reset" with a tooltip explaining the action.
Unit tests verify the button renders correctly.
"""

# Speed indicator display, no binding.
_INDICATOR_NO_WIRING = """\
---
plan: test-indicator
scope_paths:
  - "src/ui/hud.py"
local_checks:
  - command: python -m pytest tests/test_hud.py -q
    timeout: 60
---

# Sub-plan: speed indicator

Displays a ×3 speed indicator in the HUD.
Tests verify the indicator value updates.
"""

# Chinese: "按E打开背包" with no wiring.
_CHINESE_PRESS_NO_WIRING = """\
---
plan: test-chinese-press
scope_paths:
  - "src/ui/inventory.py"
local_checks:
  - command: python -m pytest tests/test_inventory.py -q
    timeout: 60
---

# Sub-plan: inventory hint

显示"按E打开背包"提示。
单元测试验证UI渲染。
"""


class TestUiPromiseWiringFires:
    def test_press_key_no_wiring_flagged(self):
        f = lint_ui_promise_wiring(_PRESS_KEY_NO_WIRING, "test-press")
        assert len(f) == 1, f
        assert "promise-without-wiring" in f[0].lower() or "wiring" in f[0].lower()

    def test_button_tooltip_no_wiring_flagged(self):
        f = lint_ui_promise_wiring(_BUTTON_TOOLTIP_NO_WIRING, "test-btn")
        assert len(f) == 1, f

    def test_indicator_no_wiring_flagged(self):
        f = lint_ui_promise_wiring(_INDICATOR_NO_WIRING, "test-indicator")
        assert len(f) == 1, f

    def test_chinese_press_no_wiring_flagged(self):
        f = lint_ui_promise_wiring(_CHINESE_PRESS_NO_WIRING, "test-cn")
        assert len(f) == 1, f


# ── Should NOT fire: has wiring assertion ───────────────────────────────

# "press C" with a press_key e2e assertion.
_HAS_PRESS_KEY = """\
---
plan: test-has-press-key
scope_paths:
  - "src/ui/level_select.py"
local_checks:
  - command: python -m pytest tests/test_level_select.py -q
    timeout: 60
---

# Sub-plan: level select codex hint

The level-select screen shows "press C for the codex".
AC: press_key("c") and take_snapshot to verify codex opens.
"""

# Button with click assertion.
_HAS_CLICK = """\
---
plan: test-has-click
scope_paths:
  - "src/ui/settings.py"
local_checks:
  - command: python -m pytest tests/test_settings.py -q
    timeout: 60
---

# Sub-plan: settings button

Adds a button labeled "Reset" with a tooltip.
AC: click the reset button and verify settings revert.
"""

# Key hint with event handler binding.
_HAS_HANDLER = """\
---
plan: test-has-handler
scope_paths:
  - "src/ui/hud.py"
local_checks:
  - command: python -m pytest tests/test_hud.py -q
    timeout: 60
---

# Sub-plan: HUD shortcut

Key hint "press H for help" is wired via addEventListener("keydown", ...).
Tests verify the handler toggles the help panel.
"""

# Chinese wiring assertion.
_HAS_CHINESE_WIRING = """\
---
plan: test-chinese-wire
scope_paths:
  - "src/ui/inventory.py"
local_checks:
  - command: python -m pytest tests/test_inventory.py -q
    timeout: 60
---

# Sub-plan: inventory hint

显示"按E打开背包"提示。
AC: 模拟按E键，验证背包界面打开。
"""


class TestUiPromiseWiringQuiet:
    def test_press_key_not_flagged(self):
        assert lint_ui_promise_wiring(_HAS_PRESS_KEY, "test-press") == []

    def test_click_not_flagged(self):
        assert lint_ui_promise_wiring(_HAS_CLICK, "test-click") == []

    def test_handler_not_flagged(self):
        assert lint_ui_promise_wiring(_HAS_HANDLER, "test-handler") == []

    def test_chinese_wiring_not_flagged(self):
        assert lint_ui_promise_wiring(_HAS_CHINESE_WIRING, "test-cn-wire") == []


# ── Should NOT fire: structural guards ──────────────────────────────────

# No commands at all.
_NO_COMMANDS = """\
---
plan: test-no-commands
scope_paths:
  - "src/ui/hud.py"
---

# Sub-plan: HUD design doc

Design notes: "press C for the codex" is a planned feature.
"""

# No affordance advertisement in body.
_NO_ADVERTISEMENT = """\
---
plan: test-no-ad
scope_paths:
  - "src/ui/hud.py"
local_checks:
  - command: python -m pytest tests/test_hud.py -q
    timeout: 60
---

# Sub-plan: HUD refactor

Refactors the HUD rendering pipeline. No new UI elements added.
"""


class TestUiPromiseWiringStructural:
    def test_no_commands_not_flagged(self):
        assert lint_ui_promise_wiring(_NO_COMMANDS, "test-no-cmds") == []

    def test_no_advertisement_not_flagged(self):
        assert lint_ui_promise_wiring(_NO_ADVERTISEMENT, "test-no-ad") == []


# ── CLI main() entrypoint ───────────────────────────────────────────────

class TestMainEntrypoint:
    def test_main_surfaces_promise_without_wiring(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _PRESS_KEY_NO_WIRING)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "promise-without-wiring" in result.stdout.lower() or "wiring" in result.stdout.lower()

    def test_main_clean_on_wiring_present(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _HAS_PRESS_KEY)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout

    def test_main_clean_on_no_advertisement(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _NO_ADVERTISEMENT)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout
