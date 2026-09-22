#!/usr/bin/env python3
"""RED test — plan_lint tier-forbids-its-evidence gate.

Tests that plan_lint.py flags a sub-plan whose ``verification_tier`` claims
``loop-verified`` while ``must_add_tests: false`` and an empty
``unit_test_targets`` list — the tier asserts evidence while forbidding it.

Part of sub-plan a-tier-cannot-forbid-its-own-evidence (step 0).
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"


def _slug_aligned_name(filename: str, content: str) -> str:
    """Filename whose derived slug matches the fixture's own `plan:` field.

    Added 2026-09-22. plan_lint's slug-identity check (one-subplan-one-slug)
    is a HARD finding when the filename-derived slug disagrees with
    frontmatter `plan:`. These fixtures predate the lint and used arbitrary
    tmp names, so tests asserting "clean" failed for a reason unrelated to
    what they test. MASTER-* names and fixtures with no `plan:` are untouched.
    """
    if filename.startswith("MASTER"):
        return filename
    m = re.search(r"^\s*plan:\s*(\S+)\s*$", content, re.M)
    return (m.group(1).strip("'\"") + ".md") if m else filename


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / _slug_aligned_name(filename, content)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── The measured combination (kira-cloudflare issue-5445-work, 2026-09-15) ──

_SUBPLAN_LOOP_VERIFIED_NO_TESTS = """\
---
plan: test-tier-forbids-evidence
verification_tier: loop-verified
must_add_tests: false
unit_test_targets: []
---

# Sub-plan: test

Some change that shipped loop-verified with no test evidence.
"""


def test_loop_verified_with_no_tests_is_flagged(tmp_path):
    """AC-1: loop-verified + must_add_tests:false + empty unit_test_targets → HARD finding."""
    result = _run_lint(tmp_path, "test-no-tests.md", _SUBPLAN_LOOP_VERIFIED_NO_TESTS)
    assert result.returncode == 1, (
        f"Expected non-zero exit for contradictory tier/evidence, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout or "ERROR" in result.stdout, (
        f"Expected a WARN/ERROR line about tier forbidding its evidence.\nstdout={result.stdout}"
    )


# ── AC-4: compile-only + must_add_tests:false → NOT flagged ────────────────

_SUBPLAN_COMPILE_ONLY = """\
---
plan: test-compile-only
verification_tier: compile-only
must_add_tests: false
unit_test_targets: []
---

# Sub-plan: test

A compile-only sub-plan with no tests — this is the honest declaration.
"""


def test_compile_only_with_no_tests_not_flagged(tmp_path):
    """AC-4: compile-only + must_add_tests:false is legitimate → no finding."""
    result = _run_lint(tmp_path, "test-compile-only.md", _SUBPLAN_COMPILE_ONLY)
    assert result.returncode == 0, (
        f"Expected clean exit for compile-only tier, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings for compile-only tier.\nstdout={result.stdout}"
    )


# ── AC-4b: device-manual + must_add_tests:false → NOT flagged ──────────────

_SUBPLAN_DEVICE_MANUAL = """\
---
plan: test-device-manual
verification_tier: device-manual
must_add_tests: false
unit_test_targets: []
---

# Sub-plan: test

A device-manual sub-plan — no automated tests expected.
"""


def test_device_manual_with_no_tests_not_flagged(tmp_path):
    """AC-4: device-manual + must_add_tests:false is legitimate → no finding."""
    result = _run_lint(tmp_path, "test-device-manual.md", _SUBPLAN_DEVICE_MANUAL)
    assert result.returncode == 0, (
        f"Expected clean exit for device-manual tier, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings for device-manual tier.\nstdout={result.stdout}"
    )


# ── AC-5: loop-verified + must_add_tests:false + NON-EMPTY targets → clean ─

_SUBPLAN_LOOP_VERIFIED_HAS_TARGETS = """\
---
plan: test-loop-verified-with-targets
verification_tier: loop-verified
must_add_tests: false
unit_test_targets:
  - "tests/test_foo.py"
---

# Sub-plan: test

A loop-verified sub-plan that names existing tests — that is evidence.
"""


def test_loop_verified_with_targets_not_flagged(tmp_path):
    """AC-5: loop-verified + must_add_tests:false + non-empty unit_test_targets → no finding."""
    result = _run_lint(tmp_path, "test-has-targets.md", _SUBPLAN_LOOP_VERIFIED_HAS_TARGETS)
    assert result.returncode == 0, (
        f"Expected clean exit for loop-verified with named targets, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings when unit_test_targets is non-empty.\nstdout={result.stdout}"
    )


# ── batch_verification is exempt, and that is correctness ───────────────────
#
# A verification sub-plan's evidence is the SUITE it runs, declared in its
# step-0 gate — not a test it adds. `must_add_tests: false` with an empty
# `unit_test_targets` is exactly how that type is supposed to be declared.
#
# MEASURED 2026-09-16 over 697 sub-plans in 11 projects: 59 flagged, and 49 of
# them (83%) were batch_verification: true. Without the exemption the rule
# fires on every verification sub-plan in every project — a false-positive
# class big enough to train readers to ignore it. With it: 10 of 697 (1.4%),
# which are ordinary work sub-plans that genuinely claim loop-verified while
# forbidding their own evidence.

_SUBPLAN_BATCH_VERIFICATION = """\
---
plan: x-verify
batch_verification: true
verification_tier: loop-verified
must_add_tests: false
unit_test_targets: []
local_checks: []
---

## Steps

### Step 0 — run the suite
"""


def test_batch_verification_subplan_is_not_flagged(tmp_path):
    """The 83% false-positive class."""
    result = _run_lint(tmp_path, "x-verify.md", _SUBPLAN_BATCH_VERIFICATION)
    assert "forbid" not in result.stdout.lower(), (
        "a batch-verification sub-plan must not be flagged for the tier/evidence "
        f"contradiction.\nstdout={result.stdout}"
    )


def test_an_ordinary_subplan_is_still_flagged(tmp_path):
    """Not a weakening — the rule still fires where its premise holds."""
    result = _run_lint(tmp_path, "ordinary.md", _SUBPLAN_LOOP_VERIFIED_NO_TESTS)
    assert result.returncode == 1, (
        f"the rule must still fire on an ordinary sub-plan.\nstdout={result.stdout}"
    )
