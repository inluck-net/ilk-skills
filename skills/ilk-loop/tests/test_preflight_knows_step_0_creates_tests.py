"""Red-first pins: a test file that step 0 creates is not a preflight FAIL.

Part of `preflight-knows-step-0-creates-tests` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

Pins AC-1, AC-2, AC-3 from the sub-plan:
  AC-1: step 0 writes `tests/test_new.py`, file absent ⇒ 0 FAILs, 1 INFO.
  AC-2: absent file not mentioned by any step ⇒ FAIL.
  AC-3: present file ⇒ no finding.

Drives ``plan_preflight._check_declared_paths`` directly (not the CLI),
because the change is about the path-checking logic, not about argument
parsing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_preflight import _check_declared_paths  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

# Step 0 writes tests/test_new.py; unit_test_targets declares it.
# File does not exist on disk => currently FAILs; after fix => 0 FAILs, 1 INFO.
STEP0_WRITES_TEST = """\
---
plan: alpha
status: pending
current_step: 0
estimated_steps: 2
unit_test_targets:
  - "tests/test_new.py"
---

# Sub-plan: alpha

## Steps

### Step 0 — red-first pins
- Write `tests/test_new.py`. AC-1 gets @pytest.mark.xfail.
- Commit: `test(alpha): pin that step 0 creates the file [plan:alpha#step-0]`

### Step 1 — implement
- Edit `src/module.py`.
- Commit: `feat(alpha): implement [plan:alpha#step-1]`
"""

# File not mentioned by any step; unit_test_targets declares it.
# Must always FAIL (the protection #36 is about is kept).
UNMENTIONED_ABSENT = """\
---
plan: beta
status: pending
current_step: 0
estimated_steps: 2
unit_test_targets:
  - "tests/test_forbidden.py"
---

# Sub-plan: beta

## Steps

### Step 0 — implement
- Edit `src/module.py`.
- Commit: `feat(beta): implement [plan:beta#step-0]`

### Step 1 — verify
- Commit: `test(beta): verify [plan:beta#step-1]`
"""


# ── tests ────────────────────────────────────────────────────────────────────


def test_step0_creates_absent_file_is_info_not_fail(tmp_path: Path) -> None:
    """AC-1: step 0 writes `tests/test_new.py`, file absent => 0 FAILs, 1 INFO."""
    project_root = tmp_path
    findings = _check_declared_paths(
        {"alpha": STEP0_WRITES_TEST},
        project_root,
    )
    fails = [f for f in findings if "does not exist" in f]
    assert fails == [], (
        f"Expected 0 FAILs for step-0-created file, got: {fails}"
    )
    infos = [f for f in findings if "INFO" in f and "step 0" in f.lower()]
    assert len(infos) == 1, (
        f"Expected 1 INFO for step-0-created file, got: {findings}"
    )


def test_unmentioned_absent_file_still_fails(tmp_path: Path) -> None:
    """AC-2: absent file not mentioned by step 0 => FAIL."""
    project_root = tmp_path
    findings = _check_declared_paths(
        {"beta": UNMENTIONED_ABSENT},
        project_root,
    )
    fails = [f for f in findings if "does not exist" in f]
    assert len(fails) == 1, (
        f"Expected 1 FAIL for unmentioned absent file, got: {findings}"
    )


# Gate command variant: step 0 creates a test file referenced in local_checks.
GATE_CMD_STEP0_WRITES = """\
---
plan: gamma
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: gamma

## Steps

### Step 0 — red-first pins
- Write `tests/test_gate.py`. AC-1 gets @pytest.mark.xfail.
- Commit: `test(gamma): pin [plan:gamma#step-0]`

### Step 1 — implement

```yaml
local_checks:
  - command: "python3 -m pytest tests/test_gate.py -q"
    timeout: 300
```

- Edit `src/module.py`.
- Commit: `feat(gamma): implement [plan:gamma#step-1]`
"""


def test_gate_cmd_step0_creates_absent_file_is_info(tmp_path: Path) -> None:
    """Gate command referencing step-0-created file => 0 FAILs, 1 INFO."""
    findings = _check_declared_paths(
        {"gamma": GATE_CMD_STEP0_WRITES},
        tmp_path,
    )
    fails = [f for f in findings if "does not exist" in f]
    assert fails == [], f"Expected 0 FAILs, got: {fails}"
    infos = [f for f in findings if "INFO" in f and "step 0" in f.lower()]
    assert len(infos) == 1, f"Expected 1 INFO, got: {findings}"


GATE_CMD_UNMENTIONED = """\
---
plan: delta
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan: delta

## Steps

### Step 0 — implement
- Edit `src/module.py`.
- Commit: `feat(delta): implement [plan:delta#step-0]`

### Step 1 — verify

```yaml
local_checks:
  - command: "python3 -m pytest tests/test_missing.py -q"
    timeout: 300
```

- Commit: `test(delta): verify [plan:delta#step-1]`
"""


def test_gate_cmd_unmentioned_absent_file_still_fails(tmp_path: Path) -> None:
    """Gate command referencing unmentioned absent file => FAIL."""
    findings = _check_declared_paths(
        {"delta": GATE_CMD_UNMENTIONED},
        tmp_path,
    )
    fails = [f for f in findings if "does not exist" in f]
    assert len(fails) == 1, f"Expected 1 FAIL, got: {findings}"


def test_present_file_has_no_finding(tmp_path: Path) -> None:
    """AC-3: present file => no finding."""
    project_root = tmp_path
    (project_root / "tests").mkdir()
    (project_root / "tests" / "test_new.py").write_text("# placeholder")
    findings = _check_declared_paths(
        {"alpha": STEP0_WRITES_TEST},
        project_root,
    )
    assert findings == [], (
        f"Expected no findings for present file, got: {findings}"
    )
