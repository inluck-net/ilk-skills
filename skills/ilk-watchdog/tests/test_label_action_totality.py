#!/usr/bin/env python3
"""RED test — label vocab drives action totality.

Imports CLASSIFICATION_LABELS from collect.py (the single source of truth for
the final classification label vocabulary) and asserts that
Resolve-WatchdogAction maps EVERY label to a known action (never 'unknown').

This test replaces the hard-coded label list in test_watchdog_action_vocab.ps1
(AC-5) so that adding a label to collect.py without a watchdog branch fails
automatically.

Part of sub-plan label-action-totality-lint (step 0).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────
# Resolve relative to this test file so it works regardless of cwd.
_HERE = Path(__file__).resolve().parent
_SKILL_ROOT = _HERE.parent.parent  # skills/
_COLLECT_PY = _SKILL_ROOT / "ilk-feedback" / "scripts" / "collect.py"
_WATCHDOG_PS1 = _SKILL_ROOT / "ilk-watchdog" / "scripts" / "watchdog.ps1"

# Valid watchdog actions (from Resolve-WatchdogAction in watchdog.ps1)
VALID_ACTIONS = frozenset({"relaunch", "block", "stop-clean", "needs-human", "triage"})


def _import_collect():
    """Import collect.py by path so we don't depend on package structure."""
    spec = importlib.util.spec_from_file_location("collect", str(_COLLECT_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_action_ps(label: str) -> str:
    """Dot-source watchdog.ps1 and call Resolve-WatchdogAction for *label*.

    Uses PowerShell (the native watchdog language). Returns the action string.
    """
    ps_cmd = (
        f"$env:ILK_DOTSOURCE_ONLY = '1'; "
        f". '{_WATCHDOG_PS1}' -ProjectName 'test-dummy'; "
        f"Resolve-WatchdogAction -Class '{label}'"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Resolve-WatchdogAction failed for label '{label}': "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result.stdout.strip()


class TestClassificationLabelsExist:
    """AC-1 / AC-4: collect.py exposes CLASSIFICATION_LABELS."""

    def test_constant_exists(self):
        """collect.py must define CLASSIFICATION_LABELS as an importable name."""
        mod = _import_collect()
        assert hasattr(mod, "CLASSIFICATION_LABELS"), (
            "collect.py does not define CLASSIFICATION_LABELS — "
            "the single-source label vocabulary is missing."
        )

    def test_constant_is_iterable(self):
        """CLASSIFICATION_LABELS must be a non-empty iterable of strings."""
        mod = _import_collect()
        labels = mod.CLASSIFICATION_LABELS
        assert hasattr(labels, "__iter__"), "CLASSIFICATION_LABELS is not iterable"
        labels_list = list(labels)
        assert len(labels_list) > 0, "CLASSIFICATION_LABELS is empty"
        for lbl in labels_list:
            assert isinstance(lbl, str), f"Non-string label: {lbl!r}"


class TestActionTotality:
    """AC-2 / AC-3: every label resolves to a known watchdog action."""

    @pytest.fixture(autouse=True)
    def _load_labels(self):
        mod = _import_collect()
        self.labels = list(mod.CLASSIFICATION_LABELS)

    def test_all_labels_resolve_to_known_action(self):
        """AC-2: every label in CLASSIFICATION_LABELS must resolve to a
        non-unknown action via Resolve-WatchdogAction."""
        for label in self.labels:
            action = _resolve_action_ps(label)
            assert action in VALID_ACTIONS, (
                f"Label '{label}' resolved to '{action}' which is not in "
                f"{VALID_ACTIONS} — the watchdog mapping is incomplete."
            )

    def test_no_label_resolves_to_unknown(self):
        """AC-3 (teeth): explicit check that 'unknown' never appears."""
        for label in self.labels:
            action = _resolve_action_ps(label)
            assert action != "unknown", (
                f"Label '{label}' resolved to 'unknown' — "
                f"a watchdog branch is missing."
            )
