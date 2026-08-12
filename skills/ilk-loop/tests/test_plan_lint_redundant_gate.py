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

- Run `python3 -m pytest -q` and record counts.
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

- Run `python3 -m pytest -q` and record counts.
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


# ── redundant-gate lint ───────────────────────────────────────────────

from plan_lint import lint_redundant_gate  # noqa: E402
import plan_lint  # noqa: E402


class TestRedundantGate:
    """Verify the redundant-gate lint catches duplicates and respects narrowness."""

    def test_ac1_duplicate_perstep_flagged(self):
        findings = lint_redundant_gate(DUPLICATE_PERSTEP, "test-dup-perstep")
        assert len(findings) >= 1, findings
        assert "pytest" in findings[0]

    def test_ac2_duplicate_frontmatter_flagged(self):
        findings = lint_redundant_gate(DUPLICATE_FRONTMATTER, "test-dup-frontmatter")
        assert len(findings) >= 1, findings
        assert "pytest" in findings[0]

    def test_ac3_narrower_body_no_finding(self):
        findings = lint_redundant_gate(NARROWER_BODY, "test-narrower")
        assert findings == []

    def test_ac4_prose_mention_no_finding(self):
        findings = lint_redundant_gate(PROSE_MENTION, "test-prose")
        assert findings == []

    def test_ac5_zero_write_targets_flagged(self):
        findings = lint_redundant_gate(ZERO_WRITE_TARGETS_STEP3, "zero-write-targets")
        assert len(findings) == 1, findings
        assert "pytest" in findings[0]
        assert "step" in findings[0].lower()


class TestRedundantGatePrecision:
    """Regression: the first implementation matched the gate as a SUBSTRING.

    Found 2026-08-12 by running the shipped lint over the real corpus: it fired
    on 182 of 333 sub-plans (55%).  Because it searched for the gate's own text
    anywhere in the body, then compared that *match* against the gate, the
    narrower-command check (AC-3) was unreachable dead code and there was no
    instruction-vs-prose distinction at all (AC-4).  Both ACs had passing unit
    tests, because the shipped fixtures were not representative.

    After the fix: 62 of 333 (19%), all spot-checked as genuine.
    """

    GATE = "python3 -m pytest -q"

    def _plan(self, body_line: str) -> str:
        return (
            "---\n"
            "plan: demo\n"
            "status: pending\n"
            "local_checks:\n"
            f"  - command: {self.GATE}\n"
            "    timeout: 300\n"
            "---\n"
            "\n# Sub-plan: demo\n"
            "\n### Step 0 — Do the thing\n"
            f"{body_line}\n"
        )

    def _findings(self, body_line: str):
        return plan_lint.lint_redundant_gate(self._plan(body_line), "demo")

    # AC-3 — a strictly narrower run is a legitimate fast inner-loop check.
    def test_narrower_command_with_extra_args_is_not_flagged(self):
        f = self._findings("- Run `python3 -m pytest -q tests/test_classify.py`.")
        assert f == [], f"narrower command flagged: {f}"

    # AC-4 — prose that names a command is not an instruction to run it.
    def test_prose_condition_is_not_flagged(self):
        f = self._findings("- Full-suite `python3 -m pytest -q` green (no regressions).")
        assert f == [], f"prose condition flagged: {f}"

    def test_prose_duration_is_not_flagged(self):
        f = self._findings("- The suite `python3 -m pytest -q` takes ~15 minutes.")
        assert f == [], f"prose duration flagged: {f}"

    def test_acceptance_criterion_line_is_not_flagged(self):
        f = self._findings("- **AC-9**: `python3 -m pytest -q` passes on macOS.")
        assert f == [], f"AC line flagged: {f}"

    # A gate block is body text; leaving it in makes every gate match itself.
    def test_clean_plan_with_only_a_perstep_gate_is_silent(self):
        text = (
            "---\nplan: demo\nstatus: pending\n"
            "local_checks:\n  - command: python3 -m pytest tests/test_a.py -q\n    timeout: 300\n"
            "---\n\n# Sub-plan: demo\n\n### Step 0 — Edit\n\n"
            "```yaml\nlocal_checks:\n  - command: python3 -m pytest tests/test_b.py -q\n"
            "    timeout: 300\n```\n"
            "- Edit `src/thing.py` so it handles the empty case.\n"
        )
        f = plan_lint.lint_redundant_gate(text, "demo")
        assert f == [], f"self-matched its own gate block: {f}"

    # Still catches the real thing, in both spellings.
    def test_true_duplicate_still_flagged(self):
        assert len(self._findings("- Run the full suite: `python3 -m pytest -q`. Record counts.")) == 1

    def test_rerun_spelling_still_flagged(self):
        assert len(self._findings("- Re-run `python3 -m pytest -q`.")) == 1

    def test_shell_fence_still_flagged(self):
        assert len(self._findings("```bash\npython3 -m pytest -q\n```")) == 1
