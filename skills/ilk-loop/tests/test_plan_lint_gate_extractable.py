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

from run_local_checks import (  # noqa: E402
    parse_local_checks_block,
    extract_step_local_checks,
)


# ── Fixture a: per-step gate block with 0 extractable commands (AC-2) ─────────

FIXTURE_A = """\
---
plan: test-empty-perstep-gate
status: pending
---

# Sub-plan: empty per-step gate

### Step 0 — Do the thing
```yaml
local_checks: []
```
"""


def test_fixture_a_perstep_empty():
    """Fixture a: per-step local_checks: [] → runtime extracts 0 commands."""
    # Verify the runtime extracts 0 from the yaml block
    import re
    fence = re.search(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```",
        FIXTURE_A,
        re.MULTILINE | re.DOTALL,
    )
    assert fence is not None
    extracted = parse_local_checks_block(fence.group(1))
    assert extracted == [], (
        f"Runtime should extract 0 from empty local_checks, got {extracted}"
    )


@pytest.mark.xfail(
    reason="gate-extractability lint does not exist yet (step 1 adds it)",
    strict=True,
)
def test_fixture_a_finds_empty_perstep():
    """Fixture a: per-step local_checks: [] should produce a finding (AC-2)."""
    # Will be filled in step 1 when the lint exists
    raise NotImplementedError("lint not yet implemented")


# ── Fixture b: frontmatter local_checks: [] → 0 extractable (AC-3) ───────────

FIXTURE_B = """\
---
plan: test-empty-fm-gate
status: pending
local_checks: []
---

# Sub-plan: empty frontmatter gate

Some prose here.
"""


def test_fixture_b_fm_empty():
    """Fixture b: frontmatter local_checks: [] → runtime extracts 0 commands."""
    extracted = parse_local_checks_block(FIXTURE_B)
    assert extracted == [], (
        f"Runtime should extract 0 from empty fm local_checks, got {extracted}"
    )


@pytest.mark.xfail(
    reason="gate-extractability lint does not exist yet (step 1 adds it)",
    strict=True,
)
def test_fixture_b_finds_empty_fm():
    """Fixture b: frontmatter local_checks: [] should produce a finding (AC-3)."""
    raise NotImplementedError("lint not yet implemented")


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
  - command: echo two
    timeout: 10
```

### Step 1 — Document the third command

The third check runs: `command: echo three` but lives outside a yaml fence.
"""


def test_fixture_e_extraction_count():
    """Fixture e: body has 3 command: lines but only 2 in yaml fences."""
    import re
    # Count command: lines in the full text
    all_cmd_lines = re.findall(r"command:\s*.+", FIXTURE_E)
    assert len(all_cmd_lines) == 3, (
        f"Expected 3 command: lines in text, got {len(all_cmd_lines)}"
    )
    # Count what the runtime actually extracts (yaml fences only)
    extracted = []
    for fence in re.finditer(
        r"^```(?:yaml|yml)?\s*\n(.*?)^```",
        FIXTURE_E,
        re.MULTILINE | re.DOTALL,
    ):
        extracted.extend(parse_local_checks_block(fence.group(1)))
    assert len(extracted) == 2, (
        f"Runtime should extract 2 commands from yaml fences, got {len(extracted)}"
    )


@pytest.mark.xfail(
    reason="gate-extractability lint does not exist yet (step 1 adds it)",
    strict=True,
)
def test_fixture_e_finds_count_mismatch():
    """Fixture e: count mismatch should produce a finding (AC-5)."""
    raise NotImplementedError("lint not yet implemented")


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
