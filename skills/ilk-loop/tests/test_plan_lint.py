"""Tests for plan_lint.py — planner degrade-discipline lints.

Hermetic: in-memory sub-plan text fixtures. Includes a regression fixture
mirroring the original uccargo SP5 (env_prereq figma + AC-GUARD fallback),
which MUST be flagged by the contradiction check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import (  # noqa: E402
    lint_envprereq_fallback_contradiction,
    lint_block_when_default_exists,
    lint_file,
)

# ── fixtures ────────────────────────────────────────────────────────

# The real uccargo SP5 shape: hard figma env_prereq + AC-GUARD fallback.
UCCARGO_SP5 = """---
plan: public-pages-announcements-privacy
status: blocked
env_prereqs:
  - description: "chrome-devtools MCP connected"
    verify_cmd: "claude mcp list | grep -q chrome-devtools"
  - description: "figma MCP connected"
    verify_cmd: "claude mcp list | grep -q figma"
---

# Sub-plan: public pages

**Step 0 self-guards**: if figma is unavailable, implement to the existing
help/terms pattern.

- **AC-GUARD**: If step 0 finds no Figma design, implement /announcements and
  /privacy to the existing help/terms pattern and proceeds.
"""

# Softened version: figma is optional (comment), no hard figma env_prereq.
SOFTENED = """---
plan: public-pages-softened
status: in-progress
env_prereqs:
  - description: "chrome-devtools MCP connected"
    verify_cmd: "claude mcp list | grep -q chrome-devtools"
# figma is OPTIONAL: if absent, implement to the help/terms pattern.
---

# Sub-plan

**AC-GUARD**: If no figma design, implement to the help/terms pattern.
"""

# Hard chrome-devtools gate with NO fallback for it -> not a contradiction.
HARD_NO_FALLBACK = """---
plan: needs-browser
env_prereqs:
  - description: "chrome-devtools MCP connected"
    verify_cmd: "claude mcp list | grep -q chrome-devtools"
---

# Sub-plan
- **AC-VIS**: take_snapshot of the rendered page.
"""

# A step that sets blocked, but a safe default exists -> block-anti-pattern.
BLOCK_WITH_DEFAULT = """---
plan: blocky
---
# Sub-plan
- figma probe; if not found, set `status: blocked` and stop.
- Otherwise implement to the help/terms pattern (the safe default).
"""

# Blocked used with NO documented default -> acceptable (un-closeable gap).
BLOCK_NO_DEFAULT = """---
plan: legit-block
---
# Sub-plan
- The upstream vendor API has no sandbox; set `status: blocked` until access is granted.
"""


# ── contradiction check ─────────────────────────────────────────────

class TestContradiction:
    def test_uccargo_sp5_flagged(self):
        f = lint_envprereq_fallback_contradiction(UCCARGO_SP5, "uccargo-sp5")
        assert any("figma" in m for m in f), f
        # chrome-devtools has no fallback -> must NOT be flagged
        assert not any("chrome-devtools" in m for m in f), f

    def test_softened_not_flagged(self):
        # figma is optional via comment, no hard figma env_prereq
        assert lint_envprereq_fallback_contradiction(SOFTENED, "softened") == []

    def test_hard_gate_no_fallback_not_flagged(self):
        assert lint_envprereq_fallback_contradiction(HARD_NO_FALLBACK, "needs-browser") == []


# ── block-when-default check ────────────────────────────────────────

class TestBlockWhenDefault:
    def test_block_with_default_flagged(self):
        f = lint_block_when_default_exists(BLOCK_WITH_DEFAULT, "blocky")
        assert len(f) == 1, f

    def test_block_no_default_not_flagged(self):
        assert lint_block_when_default_exists(BLOCK_NO_DEFAULT, "legit-block") == []

    def test_softened_no_block_step(self):
        # SOFTENED documents a fallback but has no "set status: blocked" step
        assert lint_block_when_default_exists(SOFTENED, "softened") == []


# ── file driver ─────────────────────────────────────────────────────

def test_lint_file_reads_and_flags(tmp_path: Path):
    p = tmp_path / "2026-06-13-sp5.md"
    p.write_text(UCCARGO_SP5, encoding="utf-8")
    findings = lint_file(p)
    assert findings, "expected the uccargo SP5 fixture to produce findings"
    assert all("2026-06-13-sp5" in m for m in findings)


def test_lint_file_bom(tmp_path: Path):
    p = tmp_path / "sp.md"
    p.write_text(HARD_NO_FALLBACK, encoding="utf-8-sig")
    assert lint_file(p) == []
