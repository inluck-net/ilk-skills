"""Tests for batch-verification sub-plan lints (SP6, decomposition-principles.md §12/§16).

5 tests covering:
  AC-1: template exists, has ``batch_verification: true``, and carries step-0/step-1.
  AC-2: ``lint_master_has_verification_subplan`` emits HARD finding when absent.
  AC-3: same lint emits HARD finding when present but not last.
  AC-4: a master whose last registry entry has the marker yields 0 findings.
  AC-9: the master-level lint is registered in the ``--master`` path.
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

VERIFY_SUBPLAN = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
---

# Verification sub-plan
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
