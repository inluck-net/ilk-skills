"""Scope claim is checked — AC-1 through AC-5.

Pins the contract that a batch-verification sub-plan's declared scope
(`--scope full|auto` in step 0) is compared against the record's
`suite_scope:` field.

AC-1  Declared full, record scoped ⇒ finding that names both values.
AC-2  Declared full, record full ⇒ no finding.
AC-3  Declared auto (or absent) ⇒ no finding whatever the record says.
AC-4  Record absent or unparseable ⇒ a finding that says so.
AC-5  Finding names the sub-plan, the declared scope and the recorded scope.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── The lint function is added in step 1.  Import will succeed after that
#    commit lands.  Until then, every test in this file is RED (ModuleNotFoundError).
from plan_lint import lint_scope_claim_vs_record  # type: ignore[import-untyped]  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

# A sub-plan that declares --scope full in step 0.
DECLARED_FULL = """\
---
plan: batch-2026-09-17-verify
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-17
verification_tier: loop-verified
scope_paths:
  - "skills/**/*.py"
---

# Sub-plan: batch verification — full suite

Part of [MASTER-2026-09-17](./MASTER-2026-09-17-execution-plan.md).

## Steps

### Step 0 — Run the full suite, record the result

```yaml
local_checks:
  - command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py --project . --batch batch-2026-09-17 --base-sha abc123 --run-suite --scope full --suite-timeout 1800"
    timeout: 1800
```

## Findings

_(the worker writes narrative here)_
"""

# A sub-plan that declares --scope auto in step 0.
DECLARED_AUTO = """\
---
plan: batch-2026-09-16-verify
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-16
verification_tier: loop-verified
scope_paths:
  - "skills/**/*.py"
---

# Sub-plan: batch verification — scoped

## Steps

### Step 0 — Run the suite, record the result

```yaml
local_checks:
  - command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py --project . --batch batch-2026-09-16 --base-sha def456 --run-suite --scope auto --suite-timeout 1800"
    timeout: 1800
```

## Findings
"""

# A sub-plan that omits --scope entirely (legacy shape).
NO_SCOPE_DECLARED = """\
---
plan: batch-2026-09-15-verify
batch_verification: true
status: pending
current_step: 0
priority: P0
estimated_steps: 2
last_updated: 2026-09-15
verification_tier: loop-verified
scope_paths:
  - "skills/**/*.py"
---

# Sub-plan: batch verification

## Steps

### Step 0 — Run the suite, record the result

```yaml
local_checks:
  - command: "python3 <skill-root>/ilk-loop/scripts/verification_record.py --project . --batch batch-2026-09-15 --base-sha 789abc --run-suite --suite-timeout 1800"
    timeout: 1800
```

## Findings
"""

# Record shapes.
RECORD_SCOPED = """\
# Batch verification record — batch-2026-09-17

record_writer: verify_attribution
batch: batch-2026-09-17
verified_head: f0e965d61112bcdec5bce6b9c72ecff1d4fa1a34
verified_tree: 01e6b338655085b91acd60a6d6a132ab9222f333
base_sha: 8c46737
suite_invocation: python3 -m pytest --timeout=60 --timeout-method=signal
suite_scope: scoped
selection_size: 14
suite_total: 321
suite_passed: 317
suite_failed: 4
suite_errors: 0
suite_skipped: 0

## At-base rerun

| node id | at base | in baseline_red |
|---|---|---|
| tests/test_foo.py::test_bar | passed | no |
"""

RECORD_FULL = """\
# Batch verification record — batch-2026-09-17

record_writer: verify_attribution
batch: batch-2026-09-17
verified_head: f0e965d61112bcdec5bce6b9c72ecff1d4fa1a34
verified_tree: 01e6b338655085b91acd60a6d6a132ab9222f333
base_sha: 8c46737
suite_invocation: python3 -m pytest --timeout=60 --timeout-method=signal
suite_scope: full
selection_size: 0
suite_total: 3207
suite_passed: 3175
suite_failed: 25
suite_errors: 7
suite_skipped: 0

## At-base rerun

_(no failures)_
"""


def _write_record(tmp_path: Path, name: str, content: str) -> Path:
    """Write a fixture record to tmp_path and return its path."""
    rec = tmp_path / "records" / name
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(content, encoding="utf-8")
    return rec


# ── AC-1: declared full, record scoped ⇒ finding ────────────────────────────

def test_ac1_declared_full_record_scoped(tmp_path: Path):
    """AC-1: declared full, record scoped ⇒ finding that names both values."""
    rec = _write_record(tmp_path, "batch-2026-09-17-batch.md", RECORD_SCOPED)
    findings = lint_scope_claim_vs_record(
        DECLARED_FULL, "batch-2026-09-17-verify",
        record_path=rec,
    )
    assert findings, "expected a finding when declared=full but record=scoped"
    assert any("full" in f and "scoped" in f for f in findings), (
        f"expected finding to name both scopes, got: {findings}"
    )


# ── AC-2: declared full, record full ⇒ no finding ──────────────────────────

def test_ac2_declared_full_record_full(tmp_path: Path):
    """AC-2: declared full, record full ⇒ no finding."""
    rec = _write_record(tmp_path, "batch-2026-09-17-batch.md", RECORD_FULL)
    findings = lint_scope_claim_vs_record(
        DECLARED_FULL, "batch-2026-09-17-verify",
        record_path=rec,
    )
    assert not findings, f"expected no finding when both are full, got: {findings}"


# ── AC-3: declared auto (or absent) ⇒ no finding ───────────────────────────

def test_ac3_declared_auto_record_scoped(tmp_path: Path):
    """AC-3: declared auto ⇒ no finding whatever the record says."""
    rec = _write_record(tmp_path, "batch.md", RECORD_SCOPED)
    findings = lint_scope_claim_vs_record(
        DECLARED_AUTO, "batch-2026-09-16-verify",
        record_path=rec,
    )
    assert not findings, f"expected no finding with --scope auto, got: {findings}"


def test_ac3_no_scope_declared_record_scoped(tmp_path: Path):
    """AC-3: scope absent (legacy) ⇒ no finding."""
    rec = _write_record(tmp_path, "batch.md", RECORD_SCOPED)
    findings = lint_scope_claim_vs_record(
        NO_SCOPE_DECLARED, "batch-2026-09-15-verify",
        record_path=rec,
    )
    assert not findings, f"expected no finding with no --scope, got: {findings}"


# ── AC-4: record absent or unparseable ⇒ finding ───────────────────────────

def test_ac4_record_absent(tmp_path: Path):
    """AC-4: record absent ⇒ finding that says so."""
    absent = tmp_path / "records" / "nonexistent.md"
    findings = lint_scope_claim_vs_record(
        DECLARED_FULL, "batch-2026-09-17-verify",
        record_path=absent,
    )
    assert findings, "expected a finding when record is absent"
    assert any("absent" in f or "missing" in f or "not found" in f for f in findings), (
        f"expected finding to say record is absent, got: {findings}"
    )


def test_ac4_record_unparseable(tmp_path: Path):
    """AC-4: record unparseable ⇒ finding that says so."""
    bad = _write_record(tmp_path, "bad.md", "not a valid record\n")
    findings = lint_scope_claim_vs_record(
        DECLARED_FULL, "batch-2026-09-17-verify",
        record_path=bad,
    )
    assert findings, "expected a finding when record is unparseable"
    assert any("unparseable" in f or "unreadable" in f or "missing" in f for f in findings), (
        f"expected finding to say record is unparseable, got: {findings}"
    )


# ── AC-5: finding names sub-plan, declared scope, recorded scope ────────────

def test_ac5_finding_names_subplan_declared_recorded(tmp_path: Path):
    """AC-5: the finding names the sub-plan slug, declared scope and recorded scope."""
    rec = _write_record(tmp_path, "batch-2026-09-17-batch.md", RECORD_SCOPED)
    findings = lint_scope_claim_vs_record(
        DECLARED_FULL, "batch-2026-09-17-verify",
        record_path=rec,
    )
    assert findings, "expected at least one finding"
    f = findings[0]
    assert "batch-2026-09-17-verify" in f, (
        f"expected finding to name the sub-plan slug, got: {f}"
    )
    assert "full" in f, f"expected finding to name declared scope 'full', got: {f}"
    assert "scoped" in f, f"expected finding to name recorded scope 'scoped', got: {f}"