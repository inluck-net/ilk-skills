#!/usr/bin/env python3
"""RED test — plan_lint brittle exact-list assertion guard (FM-0002).

Tests that plan_lint.py flags a sub-plan whose local_checks pin an exact
list/set equality against a growing accessor (the FM-0002 shape), while
leaving containment/superset assertions and unrelated checks alone.

Part of sub-plan brittle-assertion-plan-lint (step 0).
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


# --- AC-1: exact-list-equality in local_checks → finding (WARN) ---

_SUBPLAN_BRITTLE_JQ_EQ = """\
---
plan: test-brittle-jq-eq
local_checks:
  - command: jq -e '.activeTypes == ["area", "perimeter"]' result.json
    timeout: 30
---

# Sub-plan: test

A sub-plan that asserts exact equality on a growing list via jq.
"""

_SUBPLAN_BRITTLE_ASSERT_SET = """\
---
plan: test-brittle-assert-set
local_checks:
  - command: python3 -c "assert list_active_types() == ['area', 'perimeter']"
    timeout: 30
---

# Sub-plan: test

A sub-plan that asserts exact equality on a list via Python assert.
"""

_SUBPLAN_BRITTLE_DEEPEQUAL = """\
---
plan: test-brittle-deep-equal
local_checks:
  - command: node -e "assert.deepStrictEqual(active, ['area', 'perimeter'])"
    timeout: 30
---

# Sub-plan: test

A sub-plan that uses deepStrictEqual on a list.
"""


@pytest.mark.parametrize("label,content", [
    ("jq == list", _SUBPLAN_BRITTLE_JQ_EQ),
    ("python assert == list", _SUBPLAN_BRITTLE_ASSERT_SET),
    ("deepStrictEqual list", _SUBPLAN_BRITTLE_DEEPEQUAL),
])
def test_brittle_exact_list_equality_fails(tmp_path, label, content):
    """AC-1: exact-list-equality assertion in local_checks → WARN finding."""
    result = _run_lint(tmp_path, "test-brittle.md", content)
    assert result.returncode == 1, (
        f"[{label}] Expected non-zero exit for brittle assertion, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout, (
        f"[{label}] Expected a WARN about brittle exact-list assertion.\nstdout={result.stdout}"
    )


# --- AC-2: containment/superset assertion → no false positive ---

_SUBPLAN_CONTAINS_JQ = """\
---
plan: test-contains-jq
local_checks:
  - command: jq -e '.activeTypes | contains(["area", "perimeter"])' result.json
    timeout: 30
---

# Sub-plan: test

A sub-plan that correctly uses containment.
"""

_SUBPLAN_SUPERSET_PYTHON = """\
---
plan: test-superset-python
local_checks:
  - command: python3 -c "assert set(list_active_types()) >= {'area', 'perimeter'}"
    timeout: 30
---

# Sub-plan: test

A sub-plan that correctly uses superset assertion.
"""


@pytest.mark.parametrize("label,content", [
    ("jq contains", _SUBPLAN_CONTAINS_JQ),
    ("python superset", _SUBPLAN_SUPERSET_PYTHON),
])
def test_containment_superset_no_false_positive(tmp_path, label, content):
    """AC-2: containment/superset assertion → no finding (no false positive)."""
    result = _run_lint(tmp_path, "test-ok.md", content)
    assert result.returncode == 0, (
        f"[{label}] Expected clean exit for containment assertion, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"[{label}] Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-3: unrelated local_checks → no false positive ---

_SUBPLAN_PYTEST = """\
---
plan: test-pytest
local_checks:
  - command: python3 -m pytest tests/ -q
    timeout: 180
---

# Sub-plan: test

A sub-plan with a normal pytest check.

The gate names a directory, which is a whole suite (pytest collects the whole
tree, so a collection error anywhere under it fails the gate). It therefore
carries the note `lint_wholesuite_gate_baseline` requires: baseline-green on
macOS 2026-08-12. Without it that unrelated lint fires and the "no WARN"
assertion below stops testing the brittle-assertion lint it is written for.
"""

_SUBPLAN_CURL = """\
---
plan: test-curl
local_checks:
  - command: curl -sf http://localhost:8000/health
    timeout: 10
---

# Sub-plan: test

A sub-plan with a curl health check.
"""

_SUBPLAN_GREP = """\
---
plan: test-grep
local_checks:
  - command: grep -q "ready" status.txt
    timeout: 10
---

# Sub-plan: test

A sub-plan with a grep check.
"""


@pytest.mark.parametrize("label,content", [
    ("pytest", _SUBPLAN_PYTEST),
    ("curl", _SUBPLAN_CURL),
    ("grep -q", _SUBPLAN_GREP),
])
def test_unrelated_checks_no_false_positive(tmp_path, label, content):
    """AC-3: unrelated local_checks → no finding (no false positive)."""
    result = _run_lint(tmp_path, "test-unrelated.md", content)
    assert result.returncode == 0, (
        f"[{label}] Expected clean exit for unrelated check, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"[{label}] Expected no warnings.\nstdout={result.stdout}"
    )
