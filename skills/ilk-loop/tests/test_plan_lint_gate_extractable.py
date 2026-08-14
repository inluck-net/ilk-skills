#!/usr/bin/env python3
"""Tests for plan_lint gate-extractability check.

Pins the gap where plan_lint validates gate TEXT but never asks whether the
runtime parser can actually EXTRACT the declared commands.

Fixtures (all must produce 0 findings TODAY — the check does not exist yet):
  a  per-step yaml block with local_checks: [] → runtime extracts 0 (xfail)
  b  frontmatter local_checks: [] → runtime extracts 0 (xfail)
  c  colon-space command like grep -q 'cron: "30 10 * * *"' → must stay silent
  d  backtick-led out_of_scope block → must stay silent
  e  body declares 3 command: lines, runtime extracts 2 (xfail)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_local_checks import parse_local_checks_block  # noqa: E402
from plan_lint import lint_gate_extractable  # noqa: E402


# ── Fixture a: per-step gate block with items but 0 extractable commands (AC-2) ─

FIXTURE_A = """\
---
plan: test-unextractable-perstep-gate
status: pending
---

# Sub-plan: unextractable per-step gate

### Step 0 — Do the thing
```yaml
local_checks:
  - timeout: 30
```
"""


def test_fixture_a_perstep_empty():
    """Fixture a: per-step local_checks with no command → runtime extracts 0."""
    import re
    fence = re.search(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```",
        FIXTURE_A,
        re.MULTILINE | re.DOTALL,
    )
    assert fence is not None
    extracted = parse_local_checks_block(fence.group(1))
    assert extracted == [], (
        f"Runtime should extract 0 from gate with no command, got {extracted}"
    )


def test_fixture_a_finds_empty_perstep():
    """Fixture a: per-step local_checks with no command → finding (AC-2)."""
    findings = lint_gate_extractable(FIXTURE_A, "test-unextractable-perstep-gate")
    assert len(findings) >= 1, (
        f"Expected finding for unextractable per-step gate, got {findings}"
    )
    assert "HARD" in findings[0]
    assert "Step 0" in findings[0]


# ── Fixture b: frontmatter local_checks with items but 0 extractable (AC-3) ───

FIXTURE_B = """\
---
plan: test-unextractable-fm-gate
status: pending
local_checks:
  - timeout: 30
---

# Sub-plan: unextractable frontmatter gate

Some prose here.
"""


def test_fixture_b_fm_empty():
    """Fixture b: frontmatter local_checks with no command → runtime extracts 0."""
    extracted = parse_local_checks_block(FIXTURE_B)
    assert extracted == [], (
        f"Runtime should extract 0 from fm gate with no command, got {extracted}"
    )


def test_fixture_b_finds_empty_fm():
    """Fixture b: frontmatter local_checks with no command → finding (AC-3)."""
    findings = lint_gate_extractable(FIXTURE_B, "test-unextractable-fm-gate")
    assert len(findings) >= 1, (
        f"Expected finding for unextractable frontmatter gate, got {findings}"
    )
    assert "HARD" in findings[0]
    assert "frontmatter" in findings[0].lower()


# ── Deliberate empty markers must stay silent ─────────────────────────────────

FIXTURE_EMPTY_FM = """\
---
plan: test-deliberate-empty-fm
status: pending
local_checks: []
---

# Sub-plan: deliberate empty frontmatter

No frontmatter gate — gates are in per-step blocks.
"""

FIXTURE_EMPTY_PERSTEP = """\
---
plan: test-deliberate-empty-perstep
status: pending
---

# Sub-plan: deliberate empty per-step

### Step 0 — No gate for this step
```yaml
local_checks: []
```
"""


def test_deliberate_empty_fm_silent():
    """local_checks: [] in frontmatter → 0 findings (deliberate marker)."""
    findings = lint_gate_extractable(FIXTURE_EMPTY_FM, "test-deliberate-empty-fm")
    assert findings == [], (
        f"Expected 0 findings for deliberate empty fm, got {findings}"
    )


def test_deliberate_empty_perstep_silent():
    """local_checks: [] in per-step → 0 findings (deliberate marker)."""
    findings = lint_gate_extractable(FIXTURE_EMPTY_PERSTEP, "test-deliberate-empty-perstep")
    assert findings == [], (
        f"Expected 0 findings for deliberate empty perstep, got {findings}"
    )


# ── Fixture c: colon-space command → must stay silent (AC-4) ──────────────────

FIXTURE_C = """\
---
plan: test-colon-space
status: pending
---

# Sub-plan: cron grep with colon-space

### Step 0 — Check cron schedule
```yaml
local_checks:
  - command: grep -q 'cron: "30 10 * * *"' config.yml
    timeout: 30
```
"""


def test_fixture_c_colon_space_silent():
    """Fixture c: colon-space command must produce 0 findings (AC-4)."""
    # Verify the runtime extracts it correctly
    import re
    fence = re.search(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```",
        FIXTURE_C,
        re.MULTILINE | re.DOTALL,
    )
    assert fence is not None
    extracted = parse_local_checks_block(fence.group(1))
    assert len(extracted) == 1, (
        f"Runtime should extract 1 command, got {len(extracted)}: {extracted}"
    )
    assert "cron" in extracted[0].get("command", ""), (
        f"Extracted command should contain 'cron', got {extracted[0]}"
    )


# ── Fixture d: backtick-led out_of_scope → must stay silent (AC-4) ────────────

FIXTURE_D_MASTER = """\
---
master_plan: test-backtick-master
batch_date: 2026-08-14
status: active
---

# MASTER plan: test backtick out_of_scope

## Out of scope

- `out_of_scope:` with a backtick-led item
- Any `.ps1` change
"""


def test_fixture_d_backtick_silent():
    """Fixture d: backtick-led out_of_scope in master → 0 findings (AC-4)."""
    # Verify the runtime can still parse local_checks from a sub-plan that
    # also contains backtick-led items.  The backtick block is not a local_checks
    # block so parse_local_checks_block should return [].
    extracted = parse_local_checks_block(FIXTURE_D_MASTER)
    assert extracted == [], (
        f"Runtime should extract 0 from master with backtick items, got {extracted}"
    )


# ── Fixture e: 3 command: lines declared, 2 extracted (AC-5) ─────────────────
#
# A yaml block has 3 ``command:`` lines (regex finds all 3), but the second
# list item has a duplicate ``command:`` key — the runtime parser's dict
# overwrites the first, so it extracts only 2.  This is the count mismatch.

FIXTURE_E = """\
---
plan: test-count-mismatch
status: pending
---

# Sub-plan: count mismatch between declared and extracted

### Step 0 — Run tests
```yaml
local_checks:
  - command: echo one
    timeout: 10
    command: echo two
    timeout: 10
  - command: echo three
    timeout: 10
```
"""


def test_fixture_e_extraction_count():
    """Fixture e: 3 command: lines in yaml but runtime extracts 2."""
    import re
    # Count command: lines the regex sees in the yaml block
    fence = re.search(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```",
        FIXTURE_E,
        re.MULTILINE | re.DOTALL,
    )
    assert fence is not None
    declared = re.findall(r"command:\s*(.+)", fence.group(1))
    assert len(declared) == 3, (
        f"Expected 3 command: lines in yaml block, got {len(declared)}"
    )
    # Count what the runtime parser actually extracts
    extracted = parse_local_checks_block(fence.group(1))
    assert len(extracted) == 2, (
        f"Runtime should extract 2 (duplicate key overwrites), got {len(extracted)}"
    )


def test_fixture_e_finds_count_mismatch():
    """Fixture e: count mismatch should produce a finding (AC-5)."""
    findings = lint_gate_extractable(FIXTURE_E, "test-count-mismatch")
    assert len(findings) >= 1, (
        f"Expected finding for count mismatch, got {findings}"
    )
    assert "mismatch" in findings[0].lower()
    assert "3" in findings[0] and "2" in findings[0]


# ── Sanity: the runtime parser itself works on the refuted shapes ─────────────

def test_parser_handles_colon_in_value():
    """The runtime parser uses partition(':') — first colon only."""
    block = """\
local_checks:
  - command: grep -q 'cron: "30 10 * * *"' config.yml
    timeout: 30
"""
    result = parse_local_checks_block(block)
    assert len(result) == 1
    cmd = result[0]["command"]
    assert cmd == """grep -q 'cron: "30 10 * * *"' config.yml""", (
        f"Parser should preserve full command, got: {cmd!r}"
    )


def test_parser_handles_empty_inline():
    """local_checks: [] returns empty list."""
    assert parse_local_checks_block("local_checks: []") == []
    assert parse_local_checks_block("local_checks: [ ]") == []


# ── AC-10: CLI-level test — drive plan_lint.py over a fixture file ─────────────

def test_cli_finds_unextractable_gate(tmp_path):
    """AC-10: plan_lint.py CLI reports the finding for an empty gate block."""
    import subprocess

    fixture = tmp_path / "test-cli-gate.md"
    fixture.write_text(FIXTURE_B, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "plan_lint.py"), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (
        f"plan_lint.py should exit 1 on unextractable gate, "
        f"got rc={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "HARD" in result.stdout
    assert "frontmatter" in result.stdout.lower() or "local_checks" in result.stdout.lower()
