"""Tests for lint_verification_subplan_hardcodes_suite (sub-plan #2).

A ``batch_verification: true`` sub-plan must RESOLVE the suite command via
``ship_audit._resolve_expected_invocation``, not hand-type one.  Two wrong
full-suite runs on 2026-09-07 came from hand-typing a pytest invocation that
was gh-resolve's, not this repo's.

Fixtures:
  - A verification sub-plan that hard-codes ``python3 -m pytest ... -n 8
    --dist loadfile`` in a gate → must be flagged.
  - A verification sub-plan that restates a pytest invocation in prose → must
    be flagged.
  - A verification sub-plan that resolves via ``_resolve_expected_invocation``
    → must be clean.
  - A non-``batch_verification`` sub-plan that names a specific test file →
    must NOT be flagged (change-scoped sub-plans legitimately name commands).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"

sys.path.insert(0, str(_SCRIPTS))


# ── fixtures ──────────────────────────────────────────────────────────────

# The shape that shipped on gh-resolve: a gate whose command is a literal
# ``python3 -m pytest ... -n 8 --dist loadfile``.
VERIFY_SUBPLAN_HARDCODED_GATE = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan

## At-base rerun

Re-run each failing node id at the base commit:
`git worktree add --detach "$WT" "$BASE_SHA"`

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| _(no failures)_ | - | - | - |

### Step 1

```yaml
local_checks:
  - command: "python3 -m pytest --timeout=120 -n 8 --dist loadfile"
    timeout: 1800
```
"""

# A verification sub-plan that restates a pytest invocation in prose instead
# of resolving it.
VERIFY_SUBPLAN_PROSE_RESTATEMENT = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan

Run the full suite with `python3 -m pytest --timeout=60 --timeout-method=signal`.

## At-base rerun

Re-run each failing node id at the base commit:
`git worktree add --detach "$WT" "$BASE_SHA"`

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| _(no failures)_ | - | - | - |

### Step 1

```yaml
local_checks:
  - command: "python3 scripts/verify_attribution.py record.md"
    timeout: 60
```
"""

# A verification sub-plan that resolves the suite command via the canonical
# resolver — this is the correct form.
VERIFY_SUBPLAN_RESOLVED = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan

Step 0 resolves the suite command via
``ship_audit._resolve_expected_invocation(Path('.'))`` — never hand-type one.

## At-base rerun

Re-run each failing node id at the base commit:
`git worktree add --detach "$WT" "$BASE_SHA"`

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| _(no failures)_ | - | - | - |

### Step 1

```yaml
local_checks:
  - command: "python3 scripts/verify_attribution.py record.md"
    timeout: 60
```
"""

# A non-verification sub-plan that names a specific test file — legitimate.
ALPHA_SUBPLAN_SCOPED = """\
---
plan: alpha
status: pending
current_step: 0
---

# Alpha sub-plan

### Step 0

```yaml
local_checks:
  - command: "python3 -m pytest tests/test_foo.py -q --timeout=60 --timeout-method=signal"
    timeout: 120
```
"""


# ── import the lint under test ────────────────────────────────────────────
#
# The lint function does not exist yet — these tests are RED-first.  Import
# is deferred so the module loads even before the function is added.

def _get_lint():
    """Import the lint function, or skip if not yet implemented."""
    try:
        from plan_lint import lint_verification_subplan_hardcodes_suite
        return lint_verification_subplan_hardcodes_suite
    except ImportError:
        pytest.skip("lint_verification_subplan_hardcodes_suite not yet implemented")


# ── tests ─────────────────────────────────────────────────────────────────

class TestHardcodedGateFlagged:
    """A batch_verification sub-plan with a literal pytest command in a gate is flagged."""

    def test_hardcoded_pytest_in_gate(self):
        lint = _get_lint()
        findings = lint(VERIFY_SUBPLAN_HARDCODED_GATE, "verify")
        assert len(findings) >= 1, (
            f"Expected at least 1 finding for a hardcoded pytest gate; got {findings}"
        )
        msg = findings[0]
        assert "_resolve_expected_invocation" in msg, (
            f"Finding must name the resolver function; got: {msg}"
        )

    def test_hardcoded_gate_is_hard_finding(self):
        """The finding must be HARD — this is not a suggestion."""
        lint = _get_lint()
        findings = lint(VERIFY_SUBPLAN_HARDCODED_GATE, "verify")
        assert findings[0].startswith("HARD "), (
            f"Finding must be HARD; got: {findings[0]}"
        )


class TestProseRestatementFlagged:
    """A batch_verification sub-plan that restates a pytest invocation in prose is flagged."""

    def test_prose_restatement(self):
        lint = _get_lint()
        findings = lint(VERIFY_SUBPLAN_PROSE_RESTATEMENT, "verify")
        assert len(findings) >= 1, (
            f"Expected at least 1 finding for a prose restatement; got {findings}"
        )


class TestResolvedFormClean:
    """A batch_verification sub-plan that references the resolver is clean."""

    def test_resolved_form_no_findings(self):
        lint = _get_lint()
        findings = lint(VERIFY_SUBPLAN_RESOLVED, "verify")
        assert findings == [], (
            f"A resolved-form sub-plan must not be flagged; got: {findings}"
        )


class TestNonVerificationNotFlagged:
    """A sub-plan without batch_verification: true is not flagged."""

    def test_change_scoped_subplan(self):
        lint = _get_lint()
        findings = lint(ALPHA_SUBPLAN_SCOPED, "alpha")
        assert findings == [], (
            f"A non-verification sub-plan must not be flagged; got: {findings}"
        )


class TestRegistration:
    """The lint is registered in ALL_CHECKS."""

    def test_registered_in_all_checks(self):
        from plan_lint import ALL_CHECKS
        lint = _get_lint()
        assert lint in ALL_CHECKS, (
            "lint_verification_subplan_hardcodes_suite must be in ALL_CHECKS"
        )
