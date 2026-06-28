#!/usr/bin/env python3
"""Tests for plan_lint spec pillar → outcome-AC traceability (--spec mode).

Detects spec doc pillars that lack a verification_tier tag OR have no
outcome-level AC line.  Enforces the spec→plan handoff discipline: a pillar
is not "done" when only its model layer is gated.

Part of sub-plan 2026-06-28-spec-ac-traceability, step 0.
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

from plan_lint import lint_spec_pillar_traceability  # noqa: E402

_PLAN_LINT = SCRIPTS_DIR / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp spec file and run plan_lint.py --spec against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), "--spec", str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── Should fire: missing tier or missing outcome AC ────────────────────────

# Pillar has AC but no verification_tier tag.
_MISSING_TIER = """\
---
plan: test-spec-missing-tier
---

# Test spec

## **Pillar: Tower Upgrades**

- **AC-1**: player can upgrade a placed tower through the inspector.
"""

# Pillar has tier but no AC lines at all.
_MISSING_AC = """\
---
plan: test-spec-missing-ac
---

# Test spec

## **Pillar: Tower Upgrades**
verification_tier: loop-verified

Steps to build the upgrade model.
"""

# Pillar has tier + AC, but AC is compile-only (not outcome-level).
_COMPILE_ONLY_AC = """\
---
plan: test-spec-compile-only
---

# Test spec

## **Pillar: Tower Upgrades**
verification_tier: loop-verified

- **AC-1**: unit tests pass for upgrade logic.
"""


class TestSpecTraceabilityFires:
    def test_missing_tier_flagged(self):
        f = lint_spec_pillar_traceability(_MISSING_TIER, "test-tier")
        assert len(f) == 1, f
        assert "verification_tier" in f[0].lower()

    def test_missing_ac_flagged(self):
        f = lint_spec_pillar_traceability(_MISSING_AC, "test-ac")
        assert len(f) == 1, f
        assert "outcome" in f[0].lower()

    def test_compile_only_ac_flagged(self):
        f = lint_spec_pillar_traceability(_COMPILE_ONLY_AC, "test-compile")
        assert len(f) == 1, f
        assert "outcome" in f[0].lower()


# ── Should NOT fire: pillar has both tier and outcome AC ───────────────────

_CLEAN_SPEC = """\
---
plan: test-spec-clean
---

# Test spec

## **Pillar: Tower Upgrades**
verification_tier: loop-verified

- **AC-1**: player can upgrade a placed tower through the inspector and see effective stats change.
- **AC-2**: click the upgrade button and verify the tower's damage increases.
"""

# Multiple pillars, all clean.
_MULTI_PILLAR_CLEAN = """\
---
plan: test-spec-multi-clean
---

# Test spec

## **Pillar: Tower Upgrades**
tier: loop-verified

- **AC-1**: player can upgrade a tower through the inspector.

## **Pillar: Stage Paths**
tier: device-manual

- **AC-1**: enemies follow the active stage path on each stage.
"""


class TestSpecTraceabilityQuiet:
    def test_clean_spec_not_flagged(self):
        assert lint_spec_pillar_traceability(_CLEAN_SPEC, "test-clean") == []

    def test_multi_pillar_clean_not_flagged(self):
        assert lint_spec_pillar_traceability(_MULTI_PILLAR_CLEAN, "test-multi") == []


# ── Should NOT fire: structural guards ─────────────────────────────────────

# No pillar headings at all — not a spec doc.
_NO_PILLARS = """\
---
plan: test-spec-no-pillars
---

# Test spec

This document has no bold pillar headings.
Just regular text and some AC lines.
- **AC-1**: something compiles.
"""

# Empty body.
_EMPTY_BODY = """\
---
plan: test-spec-empty
---
"""


class TestSpecTraceabilityStructural:
    def test_no_pillars_not_flagged(self):
        assert lint_spec_pillar_traceability(_NO_PILLARS, "test-no-pillars") == []

    def test_empty_body_not_flagged(self):
        assert lint_spec_pillar_traceability(_EMPTY_BODY, "test-empty") == []


# ── CLI --spec entrypoint ──────────────────────────────────────────────────

class TestSpecEntrypoint:
    def test_main_surfaces_missing_tier(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _MISSING_TIER)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "verification_tier" in result.stdout.lower()

    def test_main_clean_on_valid_spec(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _CLEAN_SPEC)
        assert result.returncode == 0
        assert "OK: plan_lint spec clean" in result.stdout

    def test_main_clean_on_no_pillars(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _NO_PILLARS)
        assert result.returncode == 0
        assert "OK: plan_lint spec clean" in result.stdout
