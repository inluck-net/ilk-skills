"""Tests for the redundant-gate lint: a step must not run a command its gate already runs.

AC-1: duplicate command in a per-step local_checks block
AC-2: duplicate against frontmatter local_checks
AC-3: narrower body command (must NOT fire)
AC-4: prose mention only (must NOT fire)
AC-5: the real gh-resolve case — zero-write-targets step 3

The lint does not exist yet, so positive cases are xfail(strict=True).
Flip to pass in step 1 when lint_redundant_gate is implemented.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import _extract_all_local_checks_commands  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────

# AC-1: duplicate command in a per-step local_checks block.
DUPLICATE_PERSTEP = """\
---
plan: test-dup-perstep
status: in-progress
---

### Step 0

```yaml
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
```

- Run the full suite. Record counts.
"""

# AC-2: duplicate against frontmatter local_checks.
DUPLICATE_FRONTMATTER = """\
---
plan: test-dup-frontmatter
status: in-progress
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
---

### Step 0

- Run `python3 -m pytest -q` and record counts.
"""

# AC-3: narrower body command (must NOT fire).
NARROWER_BODY = """\
---
plan: test-narrower
status: in-progress
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
---

### Step 0

- Run `pytest tests/test_writeback.py` to verify the change.
"""

# AC-4: prose mention only (must NOT fire).
PROSE_MENTION = """\
---
plan: test-prose
status: in-progress
local_checks:
  - command: python3 -m pytest -q
    timeout: 300
---

### Step 0

The suite takes ~15 minutes and covers 2152 tests.
"""

# AC-5: the real gh-resolve case — zero-write-targets step 3 as authored.
# python3 -m pytest -q in both the gate and the body.
ZERO_WRITE_TARGETS_STEP3 = """\
---
plan: zero-write-targets
status: in-progress
---

### Step 3

```yaml
local_checks:
  - command: python3 -m pytest -q
    timeout: 900
```

- Run the **full** suite. Record counts.
"""


# ── extractor sanity (these always pass) ─────────────────────────────

class TestExtractorSanity:
    """Verify _extract_all_local_checks_commands sees per-step gates."""

    def test_perstep_gate_visible(self):
        cmds = _extract_all_local_checks_commands(DUPLICATE_PERSTEP)
        assert any("pytest" in c for c in cmds), cmds

    def test_frontmatter_gate_visible(self):
        cmds = _extract_all_local_checks_commands(DUPLICATE_FRONTMATTER)
        assert any("pytest" in c for c in cmds), cmds

    def test_zero_write_targets_gate_visible(self):
        cmds = _extract_all_local_checks_commands(ZERO_WRITE_TARGETS_STEP3)
        assert any("pytest" in c for c in cmds), cmds


# ── redundant-gate lint (xfail until step 1) ─────────────────────────

def _call_lint(text, slug):
    """Call lint_redundant_gate if it exists, else return [] (no findings)."""
    try:
        from plan_lint import lint_redundant_gate
        return lint_redundant_gate(text, slug)
    except ImportError:
        return []


class TestRedundantGate:
    """Positive cases xfail(strict=True) until step 1 implements the lint."""

    @pytest.mark.xfail(strict=True, reason="lint not implemented yet")
    def test_ac1_duplicate_perstep_flagged(self):
        findings = _call_lint(DUPLICATE_PERSTEP, "test-dup-perstep")
        assert len(findings) >= 1, findings
        assert "pytest" in findings[0]

    @pytest.mark.xfail(strict=True, reason="lint not implemented yet")
    def test_ac2_duplicate_frontmatter_flagged(self):
        findings = _call_lint(DUPLICATE_FRONTMATTER, "test-dup-frontmatter")
        assert len(findings) >= 1, findings
        assert "pytest" in findings[0]

    def test_ac3_narrower_body_no_finding(self):
        findings = _call_lint(NARROWER_BODY, "test-narrower")
        assert findings == []

    def test_ac4_prose_mention_no_finding(self):
        findings = _call_lint(PROSE_MENTION, "test-prose")
        assert findings == []

    @pytest.mark.xfail(strict=True, reason="lint not implemented yet")
    def test_ac5_zero_write_targets_flagged(self):
        findings = _call_lint(ZERO_WRITE_TARGETS_STEP3, "zero-write-targets")
        assert len(findings) == 1, findings
        assert "pytest" in findings[0]
        assert "step" in findings[0].lower()
