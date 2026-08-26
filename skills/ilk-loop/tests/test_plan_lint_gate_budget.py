"""Tests for lint_gate_budget — flag a gate whose test file is measured over budget.

AC-1: over-budget file produces a finding naming file, measured seconds, budget, and -k suggestion.
AC-2: under-budget file produces no finding.
AC-3: unmeasured file produces a distinct 'unmeasured' note (different from AC-1 and AC-2).
AC-4: no timing data at all says so once, naming what it searched; no per-file findings.
AC-5: budget is overridable per project; effective value appears in finding text.
AC-6: finding is a warning, not a hard finding.
AC-8: baseline regression check — existing findings unchanged except for new budget findings.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import lint_gate_budget  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

# Timing data fixture: simulates gate_cost --by-test-file --json output.
TIMING_DATA = {
    "schema": 1,
    "per_project": {
        "test-project": {
            "per_file": [
                {"file": "tests/test_drain.py", "invocations": 43, "total_s": 713.1, "max_s": 199.0},
                {"file": "tests/test_fast.py", "invocations": 10, "total_s": 15.0, "max_s": 2.0},
            ],
            "single_file_invocations": 53,
            "total_pytest_invocations": 60,
        }
    },
}

# Sub-plan with a step that runs a test file measured OVER budget (199s > 60s).
OVER_BUDGET = """\
---
plan: example-over-budget
status: pending
current_step: 0
---

# Sub-plan: example

## Steps

### Step 0 — Run the slow test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_drain.py -q
    timeout: 300
```
- Ran the test.
"""

# Sub-plan with a step that runs a test file measured UNDER budget (2s < 60s).
UNDER_BUDGET = """\
---
plan: example-under-budget
status: pending
current_step: 0
---

# Sub-plan: example

## Steps

### Step 0 — Run the fast test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_fast.py -q
    timeout: 60
```
- Ran the test.
"""

# Sub-plan with a step that runs a test file with NO measurement.
UNMEASURED = """\
---
plan: example-unmeasured
status: pending
current_step: 0
---

# Sub-plan: example

## Steps

### Step 0 — Run an unmeasured test
```yaml
local_checks:
  - command: python3 -m pytest tests/test_unknown.py -q
    timeout: 60
```
- Ran the test.
"""

# Sub-plan with custom budget override.
CUSTOM_BUDGET = """\
---
plan: example-custom-budget
status: pending
current_step: 0
gate_budget_seconds: 200
---

# Sub-plan: example

## Steps

### Step 0 — Run test with custom budget
```yaml
local_checks:
  - command: python3 -m pytest tests/test_drain.py -q
    timeout: 300
```
- Ran the test.
"""


# ── AC-1: over-budget file produces a finding ────────────────────────────────

def test_over_budget_produces_finding() -> None:
    findings = lint_gate_budget(OVER_BUDGET, "example-over-budget", TIMING_DATA)
    assert len(findings) == 1
    assert "tests/test_drain.py" in findings[0]
    assert "199" in findings[0]  # measured seconds
    assert "60" in findings[0]   # budget
    assert "-k" in findings[0]   # suggestion


def test_over_budget_finding_is_warning() -> None:
    """AC-6: finding is a warning, not a hard finding."""
    findings = lint_gate_budget(OVER_BUDGET, "example-over-budget", TIMING_DATA)
    assert len(findings) == 1
    assert not findings[0].startswith("HARD")


# ── AC-2: under-budget file produces no finding ─────────────────────────────

def test_under_budget_no_finding() -> None:
    findings = lint_gate_budget(UNDER_BUDGET, "example-under-budget", TIMING_DATA)
    assert findings == []


# ── AC-3: unmeasured file produces distinct note ─────────────────────────────

def test_unmeasured_produces_distinct_note() -> None:
    findings = lint_gate_budget(UNMEASURED, "example-unmeasured", TIMING_DATA)
    assert len(findings) == 1
    assert "tests/test_unknown.py" in findings[0]
    assert "unmeasured" in findings[0].lower() or "no measurement" in findings[0].lower()
    # Must be distinguishable from AC-1 (over-budget) and AC-2 (no finding).
    assert "199" not in findings[0]  # not the over-budget message


def test_unmeasured_is_warning() -> None:
    """AC-6: unmeasured note is also a warning, not a hard finding."""
    findings = lint_gate_budget(UNMEASURED, "example-unmeasured", TIMING_DATA)
    assert len(findings) == 1
    assert not findings[0].startswith("HARD")


# ── AC-4: no timing data at all says so once ─────────────────────────────────

def test_no_timing_data_says_so_once() -> None:
    empty_data = {"schema": 1, "per_project": {}}
    findings = lint_gate_budget(OVER_BUDGET, "example-over-budget", empty_data)
    assert len(findings) == 1
    assert "no timing data" in findings[0].lower() or "no measurements" in findings[0].lower()


def test_no_timing_data_names_search_space() -> None:
    """AC-4: the message names what it searched."""
    empty_data = {"schema": 1, "per_project": {}}
    findings = lint_gate_budget(OVER_BUDGET, "example-over-budget", empty_data)
    assert len(findings) == 1
    # Should mention the project or search context.


# ── AC-5: budget is overridable per project ──────────────────────────────────

def test_custom_budget_honoured() -> None:
    """With budget 200s, the 199s file is under budget — no finding."""
    findings = lint_gate_budget(CUSTOM_BUDGET, "example-custom-budget", TIMING_DATA)
    assert findings == []


def test_custom_budget_appears_in_finding() -> None:
    """When over budget, the effective value appears in the finding text."""
    # Make the file cost 250s (over the custom 200s budget).
    data = {
        "schema": 1,
        "per_project": {
            "test-project": {
                "per_file": [
                    {"file": "tests/test_drain.py", "invocations": 1, "total_s": 250.0, "max_s": 250.0},
                ],
                "single_file_invocations": 1,
                "total_pytest_invocations": 1,
            }
        },
    }
    findings = lint_gate_budget(CUSTOM_BUDGET, "example-custom-budget", data)
    assert len(findings) == 1
    assert "200" in findings[0]  # custom budget value


# ── AC-8: baseline regression check ──────────────────────────────────────────

def test_baseline_unchanged() -> None:
    """AC-8: existing findings over the fixed corpus are unchanged except for new budget findings."""
    baseline_path = Path(__file__).resolve().parent / "fixtures" / "gate_budget_baseline.json"
    baseline = json.loads(baseline_path.read_text())

    # Run plan_lint over the corpus.
    plans_dir = Path.home() / ".ilk-data" / "projects" / "users-chad-projects-github-inluck-net-ilk-skills" / "plans"
    corpus_files = [plans_dir / f for f in baseline["corpus"]]
    master_file = plans_dir / baseline["master"]

    # Import the full lint_file function.
    from plan_lint import lint_file

    actual_findings = []
    for f in corpus_files:
        if f.exists():
            for msg in lint_file(str(f), master_text=master_file.read_text()):
                actual_findings.append({"file": f.name, "message": msg})

    # Extract baseline finding messages.
    baseline_messages = {f["message"] for f in baseline["findings"]}

    # Every baseline finding must still appear in the actual output.
    for bf in baseline["findings"]:
        found = any(bf["message"] in af["message"] for af in actual_findings)
        assert found, f"Baseline finding lost: {bf}"


# ── AC-9: the lint is reachable from the real pipeline ──────────────────────
#
# Added 2026-08-26 (/ilk-ship Phase 0).  lint_gate_budget was fully
# implemented and unit-tested, but `grep -n lint_gate_budget plan_lint.py`
# returned only the `def` line — it was never added to ALL_CHECKS.  A real
# over-budget plan produced 0 budget findings.  That is exactly the
# "orphaned model" shape plan_lint itself lints for: the model exists, its
# unit tests pass, and nothing reaches it.

def test_lint_gate_budget_is_registered_in_all_checks() -> None:
    import plan_lint
    assert plan_lint.lint_gate_budget in plan_lint.ALL_CHECKS, (
        "lint_gate_budget is implemented and unit-tested but not wired into "
        "ALL_CHECKS, so lint_file() never calls it"
    )


def test_pipeline_does_not_spam_a_no_data_note_per_subplan(tmp_path) -> None:
    """Wiring it must not make every plan report 'no timing data'.

    Calling it with timing_data=None takes the AC-4 branch, so a naive
    registration would append that note to every sub-plan in every batch.
    """
    import plan_lint
    sp = tmp_path / "some-plan.md"
    sp.write_text(
        "---\nplan: some-plan\nstatus: pending\nlocal_checks: []\n---\n\n"
        "# some-plan\n\n### Step 0 — work\n\n"
        "```yaml\nlocal_checks:\n  - command: python3 -m pytest tests/test_x.py -q\n"
        "    timeout: 60\n```\n",
        encoding="utf-8",
    )
    findings = plan_lint.lint_file(sp)
    nodata = [f for f in findings if "no timing data available" in f]
    assert not nodata, (
        "the pipeline reported 'no timing data' — it is passing None instead "
        f"of loading the measurements.  Findings: {nodata}"
    )
