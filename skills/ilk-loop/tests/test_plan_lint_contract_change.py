#!/usr/bin/env python3
"""RED test — plan_lint contract-change gate.

Tests that plan_lint.py flags a sub-plan whose scope_paths touch a
contract-governed file but whose body doesn't reference the contract docs.

Part of sub-plan contract-change-qc-gate (step 0).
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
    )


# --- AC-2: contract-governed scope without contract-doc reference → finding ---

_SUBPLAN_CONTRACT_NO_REF = """\
---
plan: test-contract-no-ref
scope_paths:
  - "skills/ilk-feedback/scripts/collect.py"
---

# Sub-plan: test

Some change to collect.py.
"""

def test_contract_governed_scope_without_ref_fails(tmp_path):
    """AC-2: scope touches contract-governed file, no contract-doc reference → finding."""
    result = _run_lint(tmp_path, "test-no-ref.md", _SUBPLAN_CONTRACT_NO_REF)
    assert result.returncode == 1, (
        f"Expected non-zero exit for contract-governed scope without ref, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" in result.stdout or "ERROR" in result.stdout, (
        f"Expected a WARN/ERROR line about missing contract-doc reference.\nstdout={result.stdout}"
    )


# --- AC-3: contract-governed scope WITH contract-doc reference → clean ---

_SUBPLAN_CONTRACT_WITH_REF = """\
---
plan: test-contract-with-ref
scope_paths:
  - "skills/ilk-feedback/scripts/collect.py"
---

# Sub-plan: test

Some change to collect.py.

## Reference reading

- `skills/ilk-loop/references/orchestration-collaboration.md`
"""

def test_contract_governed_scope_with_ref_passes(tmp_path):
    """AC-3: scope touches contract-governed file, contract-doc referenced → no finding."""
    result = _run_lint(tmp_path, "test-with-ref.md", _SUBPLAN_CONTRACT_WITH_REF)
    assert result.returncode == 0, (
        f"Expected clean exit for contract-governed scope with ref, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )


# --- AC-4: non-contract scope → no false positive ---

_SUBPLAN_NON_CONTRACT = """\
---
plan: test-non-contract
scope_paths:
  - "docs/README.md"
---

# Sub-plan: test

Some docs change.
"""

def test_non_contract_scope_no_false_positive(tmp_path):
    """AC-4: scope doesn't touch contract-governed files → no finding."""
    result = _run_lint(tmp_path, "test-non-contract.md", _SUBPLAN_NON_CONTRACT)
    assert result.returncode == 0, (
        f"Expected clean exit for non-contract scope, "
        f"got exit {result.returncode}.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "WARN" not in result.stdout, (
        f"Expected no warnings.\nstdout={result.stdout}"
    )
