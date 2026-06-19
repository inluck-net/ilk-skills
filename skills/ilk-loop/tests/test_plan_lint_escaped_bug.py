#!/usr/bin/env python3
"""Tests for plan_lint escaped-bug regression gate.

Covers:
  AC-1  regression_for + no checks -> 1 finding
  AC-2  regression_for + frontmatter local_checks -> 0
  AC-3  regression_for + per-step local_checks block -> 0
  AC-4  no regression_for (with and without local_checks) -> 0
  AC-5  function in ALL_CHECKS; message is ASCII
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# --- AC-1: regression_for + no checks -> 1 finding ---

_SUBPLAN_REGRESSION_NO_CHECKS = """\
---
plan: test-escaped-no-checks
regression_for: T-2026-0042
---

# Sub-plan: fix escaped bug

Some fix for an escaped bug, but no local_check declared.
"""

def test_regression_for_without_checks_fails(tmp_path):
    """AC-1: regression_for set, no local_checks -> 1 finding."""
    result = _run_lint(tmp_path, "test-no-checks.md", _SUBPLAN_REGRESSION_NO_CHECKS)
    assert result.returncode == 1, (
        f"Expected non-zero exit for regression_for without checks, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout, (
        f"Expected a WARN line about missing local_check.\nstdout={result.stdout}"
    )
    assert "regression_for" in result.stdout, (
        f"Expected finding to mention regression_for.\nstdout={result.stdout}"
    )


# --- AC-2: regression_for + frontmatter local_checks -> 0 findings ---

_SUBPLAN_REGRESSION_WITH_FM_CHECKS = """\
---
plan: test-escaped-fm-checks
regression_for: T-2026-0042
local_checks:
  - command: python -m pytest tests/test_repro.py -q
    timeout: 60
---

# Sub-plan: fix escaped bug

Fix with a frontmatter local_check.
"""

def test_regression_for_with_frontmatter_checks_passes(tmp_path):
    """AC-2: regression_for + frontmatter local_checks -> no finding."""
    result = _run_lint(tmp_path, "test-fm-checks.md", _SUBPLAN_REGRESSION_WITH_FM_CHECKS)
    assert result.returncode == 0, (
        f"Expected clean exit for regression_for with frontmatter checks, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-3: regression_for + per-step local_checks block -> 0 findings ---

_SUBPLAN_REGRESSION_WITH_STEP_CHECKS = """\
---
plan: test-escaped-step-checks
regression_for: T-2026-0042
---

# Sub-plan: fix escaped bug

### Step 0 -- Fix the bug
```yaml
local_checks:
  - command: python -m pytest tests/test_repro.py -q
    timeout: 60
```
"""

def test_regression_for_with_per_step_checks_passes(tmp_path):
    """AC-3: regression_for + per-step local_checks block -> no finding."""
    result = _run_lint(tmp_path, "test-step-checks.md", _SUBPLAN_REGRESSION_WITH_STEP_CHECKS)
    assert result.returncode == 0, (
        f"Expected clean exit for regression_for with per-step checks, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-4a: no regression_for, no local_checks -> 0 findings ---

_SUBPLAN_NO_REGRESSION_NO_CHECKS = """\
---
plan: test-no-regression-no-checks
---

# Sub-plan: normal fix

A normal sub-plan without regression_for.
"""

def test_no_regression_for_no_checks_passes(tmp_path):
    """AC-4a: no regression_for, no local_checks -> no finding."""
    result = _run_lint(tmp_path, "test-no-reg-no-chk.md", _SUBPLAN_NO_REGRESSION_NO_CHECKS)
    assert result.returncode == 0, (
        f"Expected clean exit for normal sub-plan, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-4b: no regression_for, WITH local_checks -> 0 findings ---

_SUBPLAN_NO_REGRESSION_WITH_CHECKS = """\
---
plan: test-no-regression-with-checks
local_checks:
  - command: python -m pytest tests/test_something.py -q
    timeout: 60
---

# Sub-plan: normal fix with checks

A normal sub-plan with local_checks but no regression_for.
"""

def test_no_regression_for_with_checks_passes(tmp_path):
    """AC-4b: no regression_for, with local_checks -> no finding."""
    result = _run_lint(tmp_path, "test-no-reg-with-chk.md", _SUBPLAN_NO_REGRESSION_WITH_CHECKS)
    assert result.returncode == 0, (
        f"Expected clean exit for normal sub-plan with checks, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-5a: function is in ALL_CHECKS ---

def test_function_in_all_checks():
    """AC-5a: lint_escaped_bug_regression_gate is registered in ALL_CHECKS."""
    import sys as _sys
    _sys.path.insert(0, str(_PLAN_LINT.parent))
    import plan_lint as p
    assert p.lint_escaped_bug_regression_gate in p.ALL_CHECKS, (
        "lint_escaped_bug_regression_gate not found in ALL_CHECKS"
    )


# --- AC-5b: finding message is ASCII ---

def test_finding_message_is_ascii(tmp_path):
    """AC-5b: the finding message contains only ASCII characters."""
    result = _run_lint(tmp_path, "test-ascii.md", _SUBPLAN_REGRESSION_NO_CHECKS)
    assert result.returncode == 1
    for line in result.stdout.splitlines():
        if line.startswith("WARN:"):
            line_bytes = line.encode("utf-8")
            try:
                line_bytes.decode("ascii")
            except UnicodeDecodeError:
                pytest.fail(f"Finding message contains non-ASCII: {line}")


# --- AC-4c: regression_for with empty value -> no finding ---

_SUBPLAN_EMPTY_REGRESSION = """\
---
plan: test-empty-regression
regression_for:
---

# Sub-plan: empty regression_for

regression_for is present but empty -> should not trigger.
"""

def test_empty_regression_for_no_finding(tmp_path):
    """AC-4c: regression_for present but empty -> no finding."""
    result = _run_lint(tmp_path, "test-empty-reg.md", _SUBPLAN_EMPTY_REGRESSION)
    assert result.returncode == 0, (
        f"Expected clean exit for empty regression_for, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- Back-compat: existing plan_lint test files still pass ---

def test_existing_plan_lint_tests_still_pass():
    """AC-4 back-compat: the three existing plan_lint test files pass."""
    test_files = [
        _HERE / "test_plan_lint.py",
        _HERE / "test_plan_lint_brittle_assertion.py",
        _HERE / "test_plan_lint_contract_change.py",
    ]
    for tf in test_files:
        assert tf.exists(), f"Missing test file: {tf}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *[str(f) for f in test_files], "-q", "--timeout=60"],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"Existing plan_lint tests failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
