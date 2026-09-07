"""Tests for batch-verification sub-plan lints (SP6, decomposition-principles.md §12/§16).

10 tests covering:
  AC-1: template exists, has ``batch_verification: true``, and carries step-0/step-1.
  AC-2: ``lint_master_has_verification_subplan`` emits HARD finding when absent.
  AC-3: same lint emits HARD finding when present but not last.
  AC-4: a master whose last registry entry has the marker yields 0 findings.
  AC-9: the master-level lint is registered in the ``--master`` path.

  AC-10: the template MEASURES attribution -- ``## At-base rerun`` + worktree --
         and ``lint_verification_attribution_unmeasured`` is clean on it.
  AC-11: a verification sub-plan with no at-base rerun is a HARD finding.
  AC-12: a gate that greps the record's rendered ``Attributed regressions: N``
         count is a HARD finding, in every spelling the real one used.
  AC-13: neither finding fires on a sub-plan without the marker.
  AC-14: the sub-plan lint is registered in ``ALL_CHECKS``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
_PLAN_LINT = _SCRIPTS / "plan_lint.py"
_TEMPLATE = _HERE.parent / "templates" / "batch-verification-subplan.md"

sys.path.insert(0, str(_SCRIPTS))


# ── AC-1: template exists and has the right shape ────────────────────────

class TestAC1Template:
    """The batch-verification sub-plan template exists and is well-formed."""

    def test_template_exists_with_marker_and_steps(self):
        """Template exists, declares batch_verification: true, and has both steps."""
        assert _TEMPLATE.exists(), (
            f"Template not found at {_TEMPLATE}"
        )
        text = _TEMPLATE.read_text(encoding="utf-8")
        assert "batch_verification: true" in text, (
            "Template must declare 'batch_verification: true' in frontmatter"
        )
        assert "### Step 0" in text, "Template must have '### Step 0'"
        assert "### Step 1" in text, "Template must have '### Step 1'"


# ── AC-2, AC-3, AC-4: master-level lint ──────────────────────────────────

MASTER_NO_VERIFICATION = """\
---
title: Test batch
slug: 2026-08-29-test
status: queued
base_branch: main
master_plan: 2026-08-29-master
---

# MASTER plan: test

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [2026-08-29-alpha.md](./2026-08-29-alpha.md) | X | 3 | pending |
| 2 | 2 | [2026-08-29-beta.md](./2026-08-29-beta.md) | Y | 4 | pending |
"""

MASTER_VERIFICATION_NOT_LAST = """\
---
title: Test batch
slug: 2026-08-29-test
status: queued
base_branch: main
master_plan: 2026-08-29-master
---

# MASTER plan: test

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [2026-08-29-verify.md](./2026-08-29-verify.md) | V | 2 | pending |
| 2 | 2 | [2026-08-29-alpha.md](./2026-08-29-alpha.md) | X | 3 | pending |
"""

MASTER_VERIFICATION_LAST = """\
---
title: Test batch
slug: 2026-08-29-test
status: queued
base_branch: main
master_plan: 2026-08-29-master
---

# MASTER plan: test

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [2026-08-29-alpha.md](./2026-08-29-alpha.md) | X | 3 | pending |
| 2 | 2 | [2026-08-29-verify.md](./2026-08-29-verify.md) | V | 2 | pending |
"""

# A WELL-FORMED verification sub-plan: it MEASURES attribution. The at-base
# rerun section and its worktree mechanism are required by
# lint_verification_attribution_unmeasured, so a fixture without them is no
# longer a valid verification sub-plan.
VERIFY_SUBPLAN = """\
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
"""

ALPHA_SUBPLAN = """\
---
plan: alpha
status: pending
current_step: 0
---

# Alpha sub-plan
"""


def _run_lint_master(tmp_path: Path, master: str, subplans: dict[str, str]) -> subprocess.CompletedProcess:
    mp = tmp_path / "MASTER-2026-08-29-execution-plan.md"
    mp.write_text(textwrap.dedent(master), encoding="utf-8")
    paths = []
    for name, content in subplans.items():
        sp = tmp_path / name
        sp.write_text(textwrap.dedent(content), encoding="utf-8")
        paths.append(str(sp))
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), "--master", str(mp), *paths],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


class TestAC2NoVerificationSubplan:
    """A master without a batch_verification sub-plan is a HARD finding."""

    def test_hard_finding_when_absent(self, tmp_path):
        r = _run_lint_master(tmp_path, MASTER_NO_VERIFICATION, {
            "2026-08-29-alpha.md": ALPHA_SUBPLAN,
        })
        assert r.returncode != 0, f"Expected non-zero exit; stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        out = r.stdout + r.stderr
        assert "batch_verification" in out.lower() or "verification sub-plan" in out.lower(), (
            f"Expected finding about missing verification sub-plan; got:\n{out}"
        )


class TestAC3VerificationNotLast:
    """A verification sub-plan that is not last in registry order is a HARD finding."""

    def test_hard_finding_when_not_last(self, tmp_path):
        r = _run_lint_master(tmp_path, MASTER_VERIFICATION_NOT_LAST, {
            "2026-08-29-verify.md": VERIFY_SUBPLAN,
            "2026-08-29-alpha.md": ALPHA_SUBPLAN,
        })
        assert r.returncode != 0, f"Expected non-zero exit; stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        out = r.stdout + r.stderr
        assert "last" in out.lower() or "not last" in out.lower() or "order" in out.lower(), (
            f"Expected finding about verification sub-plan not being last; got:\n{out}"
        )


class TestAC4VerificationLastClean:
    """A master whose last registry entry has batch_verification yields 0 findings."""

    def test_no_finding_when_last(self, tmp_path):
        # Precondition: the lint function must exist for this test to be meaningful.
        text = _PLAN_LINT.read_text(encoding="utf-8")
        assert "def lint_master_has_verification_subplan" in text, (
            "lint_master_has_verification_subplan not yet defined — "
            "AC-4 cannot be verified until AC-2/AC-3 are implemented"
        )
        r = _run_lint_master(tmp_path, MASTER_VERIFICATION_LAST, {
            "2026-08-29-alpha.md": ALPHA_SUBPLAN,
            "2026-08-29-verify.md": VERIFY_SUBPLAN,
        })
        out = r.stdout + r.stderr
        assert "verification sub-plan" not in out.lower() or "ok" in out.lower(), (
            f"Expected no verification-sub-plan finding; got:\n{out}"
        )


# ── AC-9: lint registration ──────────────────────────────────────────────

class TestAC9Registration:
    """The master-level lint is registered in the --master path."""

    def test_function_defined(self):
        """lint_master_has_verification_subplan is defined in plan_lint.py."""
        text = _PLAN_LINT.read_text(encoding="utf-8")
        assert "def lint_master_has_verification_subplan" in text, (
            "lint_master_has_verification_subplan not defined in plan_lint.py"
        )

    def test_master_level_lint_in_master_path(self):
        """lint_master_has_verification_subplan is called in the --master code path."""
        text = _PLAN_LINT.read_text(encoding="utf-8")
        assert "for msg in lint_master_has_verification_subplan" in text, (
            "lint_master_has_verification_subplan not called in --master path"
        )


# -- AC-10..AC-14: attribution must be measured, not argued -----------------
#
# gh-resolve batch-2026-09-07 shipped a PASS over 2 real regressions of 4454
# tests. The authored sub-plan stated the attribution rule in prose and
# required no rerun; both failing node ids pass at the base commit (2 passed in
# 0.14s, detached worktree), so both were attributed under the sub-plan's own
# rule. The step-1 gate then read back the agent's own "Attributed
# regressions: 0" sentence. These tests pin both halves.

from plan_lint import (  # noqa: E402
    ALL_CHECKS,
    lint_verification_attribution_unmeasured,
)

# The shape that shipped the false PASS: marker present, rule stated in prose,
# no rerun anywhere.
VERIFY_SUBPLAN_UNMEASURED = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan

**Attribution rule:** a failure is attributed to this batch iff it fails now,
passed at the batch's base commit, and is not in `baseline_red`.
"""

# The self-graded gate, in the exact spelling the real one used.
VERIFY_SUBPLAN_SELF_GRADED = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan

## At-base rerun

`git worktree add --detach "$WT" "$BASE_SHA"`

| node id | at base | in baseline_red | attributed |
|---|---|---|---|
| _(no failures)_ | - | - | - |

### Step 1

```yaml
local_checks:
  - command: "python3 -c \\"import re,sys; m=re.search(r'[Aa]ttributed regressions?:\\\\s*(\\\\d+)', t); sys.exit(0 if (m and m.group(1)=='0') else 1)\\""
    timeout: 1800
```
"""

NO_MARKER_SUBPLAN = """\
---
plan: alpha
status: pending
current_step: 0
---

# Alpha sub-plan — change-scoped, owes no baseline comparison.
"""


class TestAC10TemplateMeasuresAttribution:
    """The template carries the at-base rerun, and the lint is clean on it."""

    def test_template_has_rerun_section_and_mechanism(self):
        text = _TEMPLATE.read_text(encoding="utf-8")
        assert "## At-base rerun" in text, (
            "Template must carry a '## At-base rerun' section — the heading is "
            "the artifact step 1's gate parses"
        )
        assert "git worktree add" in text, (
            "Template must name the rerun mechanism (git worktree add)"
        )

    def test_lint_clean_on_template(self):
        text = _TEMPLATE.read_text(encoding="utf-8")
        findings = lint_verification_attribution_unmeasured(text, "template")
        assert findings == [], (
            f"The template must satisfy its own lint; got: {findings}"
        )


class TestAC11UnmeasuredAttribution:
    """A verification sub-plan with no at-base rerun is a HARD finding."""

    def test_hard_finding_when_rerun_absent(self):
        findings = lint_verification_attribution_unmeasured(
            VERIFY_SUBPLAN_UNMEASURED, "verify"
        )
        assert len(findings) == 1, (
            f"Expected exactly 1 finding for an unmeasured sub-plan; got {findings}"
        )
        msg = findings[0]
        assert msg.startswith("HARD "), f"Finding must be HARD; got: {msg}"
        assert "At-base rerun" in msg, f"Finding must name the missing section; got: {msg}"
        assert "git worktree add" in msg, f"Finding must name the mechanism; got: {msg}"

    def test_stating_the_rule_in_prose_is_not_enough(self):
        """The prose rule is present in the fixture and must not satisfy the lint."""
        assert "passed at the batch's base commit" in VERIFY_SUBPLAN_UNMEASURED
        assert lint_verification_attribution_unmeasured(
            VERIFY_SUBPLAN_UNMEASURED, "verify"
        ), "Restating the attribution rule must not count as measuring it"

    def test_no_finding_when_rerun_present(self):
        assert lint_verification_attribution_unmeasured(VERIFY_SUBPLAN, "verify") == []


class TestAC12SelfGradedGate:
    """A gate reading the record's rendered attributed count is a HARD finding."""

    def test_hard_finding_on_real_gate_spelling(self):
        findings = lint_verification_attribution_unmeasured(
            VERIFY_SUBPLAN_SELF_GRADED, "verify"
        )
        assert len(findings) == 1, (
            f"Expected exactly the self-graded-gate finding (the rerun section "
            f"is present in this fixture); got {findings}"
        )
        msg = findings[0]
        assert msg.startswith("HARD "), f"Finding must be HARD; got: {msg}"
        assert "self-graded" in msg, f"Finding must name the defect; got: {msg}"

    @pytest.mark.parametrize("spelling", [
        "Attributed regressions:",
        "attributed regression:",
        # The real gate wrote its own case-insensitive character class and a
        # regex quantifier. Both defeated a literal ``attributed`` pattern and
        # a plain ``regressions?`` pattern respectively; pin them.
        "[Aa]ttributed regressions?:",
        "[aA]ttributed regression?:",
    ])
    def test_every_spelling_is_caught(self, spelling):
        subplan = VERIFY_SUBPLAN.replace(
            "# Verification sub-plan",
            "# Verification sub-plan\n\n"
            "```yaml\nlocal_checks:\n"
            f"  - command: \"python3 -c 'grep {spelling} 0'\"\n"
            "    timeout: 60\n```",
            1,
        )
        findings = lint_verification_attribution_unmeasured(subplan, "verify")
        assert any("self-graded" in f for f in findings), (
            f"Spelling {spelling!r} was not caught; got: {findings}"
        )

    def test_script_named_for_attribution_is_not_flagged(self):
        """A gate that DELEGATES to a script must not be mistaken for the prose grep."""
        subplan = VERIFY_SUBPLAN.replace(
            "# Verification sub-plan",
            "# Verification sub-plan\n\n"
            "```yaml\nlocal_checks:\n"
            "  - command: \"python3 scripts/verify_attribution.py record.md\"\n"
            "    timeout: 60\n```",
            1,
        )
        assert lint_verification_attribution_unmeasured(subplan, "verify") == []


class TestAC13NoFalseFireWithoutMarker:
    """Neither finding fires on a sub-plan that is not the verification sub-plan."""

    def test_no_marker_no_findings(self):
        assert lint_verification_attribution_unmeasured(NO_MARKER_SUBPLAN, "alpha") == []

    def test_no_marker_with_prose_count_gate(self):
        """Even a prose-count gate is silent without the marker — not this lint's business."""
        subplan = NO_MARKER_SUBPLAN.replace(
            "# Alpha sub-plan",
            "```yaml\nlocal_checks:\n"
            "  - command: \"grep 'Attributed regressions: 0' rec.md\"\n"
            "    timeout: 60\n```\n\n# Alpha sub-plan",
            1,
        )
        assert lint_verification_attribution_unmeasured(subplan, "alpha") == []


class TestAC14Registration:
    """The sub-plan lint runs as part of ALL_CHECKS."""

    def test_registered_in_all_checks(self):
        assert lint_verification_attribution_unmeasured in ALL_CHECKS, (
            "lint_verification_attribution_unmeasured must be in ALL_CHECKS or "
            "lint_file will never call it"
        )
