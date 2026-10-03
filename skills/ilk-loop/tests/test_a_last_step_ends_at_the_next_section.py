"""Tests for _extract_step_sections: the last step ends at the next ## heading.

AC-1..AC-3 are xfail(strict=True) until the reader is fixed (step 1).
AC-4..AC-6 are control tests that pass on the current code.

Regression: the last step's section ran to end-of-body, swallowing
`## Findings` and triggering a false "lacks --allow-empty" finding on
any implementation step whose prose mentioned no file path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import (  # noqa: E402
    _extract_step_sections,
    lint_no_diff_step,
)
from plan_preflight import _check_step_commit_feasibility  # noqa: E402


# ── Fixture bodies ───────────────────────────────────────────────────────────

# A two-step sub-plan whose last step is followed by ## Findings.
# Step 1 has no file path and no --allow-empty.
TWO_STEP_THEN_FINDINGS = """\
---
plan: example-slug
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: example

## Steps

### Step 0 — write the code
- Edit `skills/foo.py` per spec.
- Commit: `feat(foo): add bar [plan:example-slug#step-0]`

### Step 1 — record the result
- Document findings below.
- Commit: `docs(example): record result [plan:example-slug#step-1]`

## Findings

FINDINGS-TAIL prose goes here.
"""

# A step whose own text mentions "## Findings" (the phrase inside the step).
STEP_MENTIONS_FINDINGS = """\
---
plan: example-slug
status: pending
current_step: 0
estimated_steps: 1
---

# Sub-plan: example

## Steps

### Step 0 — investigate
- Record the result in `## Findings` below.
- Commit: `docs(example): investigate [plan:example-slug#step-0]`

## Findings

_(empty)_
"""

# A step with a fenced block containing a ## heading — the fence must
# not end the section.
STEP_WITH_FENCED_HEADINGS = """\
---
plan: example-slug
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: example

## Steps

### Step 0 — write the test
- Write `tests/test_foo.py`.
- Commit: `test(foo): add test [plan:example-slug#step-0]`

### Step 1 — record
```markdown
## At-base rerun
Record shape here.
```
More prose after the fence.
- Commit: `docs(example): record [plan:example-slug#step-1]`

## Findings

_(empty)_
"""

# Two steps with a sub-heading inside step 0.
STEP_WITH_SUB_HEADING = """\
---
plan: example-slug
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: example

## Steps

### Step 0 — implement
- Edit `skills/foo.py`.
#### Sub-heading
Details inside step 0.
- Commit: `feat(foo): add bar [plan:example-slug#step-0]`

### Step 1 — verify
- Run the tests.
- Commit: `test(foo): verify [plan:example-slug#step-1]`
"""


# ── AC-1 (xfail): last step section does not contain ## Findings ────────────


def test_ac1_last_step_section_excludes_findings():
    """The last step's section must not include `## Findings` or its tail."""
    sections = _extract_step_sections(TWO_STEP_THEN_FINDINGS)
    # Find step 1's section.
    step1_sections = [s for s in sections if s[0] == 1]
    assert len(step1_sections) == 1, "expected exactly one step-1 section"
    _, _, section_text = step1_sections[0]
    assert "## Findings" not in section_text, (
        "last step section must not include '## Findings'"
    )
    assert "FINDINGS-TAIL" not in section_text, (
        "last step section must not include text after '## Findings'"
    )


# ── AC-2 (xfail): lint_no_diff_step returns no false finding ────────────────


def test_ac2_lint_no_diff_step_clean_on_last_step():
    """A last step with no file path and no --allow-empty, followed by
    ## Findings, should NOT trigger lint_no_diff_step (the Findings section
    is not part of the step)."""
    findings = lint_no_diff_step(TWO_STEP_THEN_FINDINGS, "example-slug")
    assert findings == [], f"expected no findings, got: {findings}"


# ── AC-3 (xfail): same through plan_preflight ────────────────────────────────


def test_ac3_preflight_clean_on_last_step():
    """Same as AC-2 but through _check_step_commit_feasibility."""
    findings = _check_step_commit_feasibility({"example-slug": TWO_STEP_THEN_FINDINGS})
    assert findings == [], f"expected no findings, got: {findings}"


# ── AC-4 (control): step that mentions "## Findings" in its text ─────────────


def test_ac4_step_mentioning_findings_still_flagged():
    """A step whose own text says 'record the result in ## Findings' still
    triggers lint_no_diff_step (the phrase is inside the step, not a real
    section boundary)."""
    findings = lint_no_diff_step(STEP_MENTIONS_FINDINGS, "example-slug")
    assert len(findings) == 1, (
        f"expected exactly one finding for a step mentioning '## Findings', got: {findings}"
    )
    assert "lacks --allow-empty" in findings[0]


# ── AC-5 (control): fenced ## heading does not end the section ───────────────


def test_ac5_fenced_heading_stays_in_section():
    """A ```markdown fence containing '## At-base rerun' must not end the
    step section — the text after the fence stays in step 1's section."""
    sections = _extract_step_sections(STEP_WITH_FENCED_HEADINGS)
    step1_sections = [s for s in sections if s[0] == 1]
    assert len(step1_sections) == 1
    _, _, section_text = step1_sections[0]
    # The fenced heading is inside the section.
    assert "## At-base rerun" in section_text
    # The text after the fence is also still in the section.
    assert "More prose after the fence" in section_text


# ── AC-6 (control): sub-heading stays in step; two steps split correctly ─────


def test_ac6_sub_heading_stays_in_step():
    """A #### Sub-heading inside a step stays in that step's section,
    and two steps still split at ### Step 1."""
    sections = _extract_step_sections(STEP_WITH_SUB_HEADING)
    assert len(sections) == 2, f"expected 2 sections, got {len(sections)}"
    step0_no, _, step0_text = sections[0]
    step1_no, _, step1_text = sections[1]
    assert step0_no == 0
    assert step1_no == 1
    # Sub-heading stays in step 0.
    assert "#### Sub-heading" in step0_text
    assert "Details inside step 0" in step0_text
    # Step 1 has its own content.
    assert "Run the tests" in step1_text