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

import plan_lint  # noqa: E402
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


# -- a dead instrument must not read as a clean result ------------------------

class TestLoadFailureIsNotSilence:
    """A crashed gate_cost must not make a plan lint CLEAN on budget.

    Before this, `_load_timing_data` returned the same empty dict for "the
    corpus has no measurements yet" and "the measurement tool crashed", and
    `lint_gate_budget` returns no findings for that dict on the auto path.
    So a broken instrument produced silence, and silence is indistinguishable
    from "no budget problems" — the same defect class as a gate reporting
    green because it could not parse its own command.
    """

    # A REAL sub-plan: local_checks must sit in a ```yaml fence or the
    # command never parses, and then these tests would pass on an early
    # return rather than on the behaviour they claim to check.
    _PLAN = OVER_BUDGET

    def test_failed_load_reports(self) -> None:
        data = {"schema": None, "per_project": {}, "_auto_loaded": True,
                "_load_failed": "gate_cost exited 1: boom"}
        findings = plan_lint.lint_gate_budget(self._PLAN, "sp1", timing_data=data)
        assert len(findings) == 1
        assert "NOT CHECKED" in findings[0]
        assert "gate_cost exited 1" in findings[0], "the reason must survive"

    def test_empty_corpus_stays_silent_on_the_auto_path(self) -> None:
        """The benign case must not become noisy — a new project has no data."""
        data = {"schema": 3, "per_project": {}, "_auto_loaded": True}
        assert plan_lint.lint_gate_budget(self._PLAN, "sp1", timing_data=data) == []

    def test_the_two_are_distinguishable(self) -> None:
        failed = {"schema": None, "per_project": {}, "_auto_loaded": True,
                  "_load_failed": "TimeoutExpired"}
        empty = {"schema": 3, "per_project": {}, "_auto_loaded": True}
        assert plan_lint.lint_gate_budget(self._PLAN, "sp1", timing_data=failed) != \
               plan_lint.lint_gate_budget(self._PLAN, "sp1", timing_data=empty)

    def test_loader_marks_a_nonzero_exit_as_failed(self, monkeypatch) -> None:
        """End-to-end through the loader, not just the consumer's contract."""
        import subprocess as _sp

        class _R:
            returncode = 2
            stdout = ""
            stderr = "gate_cost: --project 'typo' not found under /x (23 projects present)."

        monkeypatch.setattr(plan_lint, "_TIMING_CACHE", None)
        monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())
        data = plan_lint._load_timing_data()
        assert data.get("_load_failed"), "a non-zero exit must be recorded as a failure"
        assert "not found" in data["_load_failed"]
        monkeypatch.setattr(plan_lint, "_TIMING_CACHE", None)


# -- the finding carries the spread, so a peak is not read as a cost ----------

class TestFindingCarriesSpread:
    """A WARN must let the reader tell "slow now" from "was slow once".

    Measured on gh-resolve 2026-09-05: of 12 files whose max exceeds a 60s
    budget, 10 have a median under it.  A finding that shows only the peak
    sends a planner to set a gate timeout from an artifact — which is how a
    0.29s guard nearly got a 120s gate woven into 7 sub-plans.
    """

    def _data(self, **over) -> dict:
        entry = {"file": "tests/test_drain.py", "max_s": 198.57, "p50_s": 0.96,
                 "measured_invocations": 44, "max_run": "20260825-234253",
                 "max_age_days": 11}
        entry.update(over)
        return {"schema": 3, "_auto_loaded": True,
                "per_project": {"p": {"per_file": [entry]}}}

    def test_stale_ceiling_is_labelled(self) -> None:
        f = lint_gate_budget(OVER_BUDGET, "sp1", timing_data=self._data())[0]
        assert "PEAK" in f
        assert "median 1.0s over 44 runs" in f
        assert "11d old" in f
        assert "one slow run, not a slow file" in f

    def test_genuinely_slow_file_is_not_explained_away(self) -> None:
        """When the median is also over budget, no reassuring caveat."""
        f = lint_gate_budget(
            OVER_BUDGET, "sp1",
            timing_data=self._data(max_s=113.81, p50_s=103.41,
                                   measured_invocations=12, max_age_days=1),
        )[0]
        assert "median 103.4s" in f
        assert "one slow run" not in f, (
            "a file whose median is over budget must not be excused"
        )

    def test_still_fires_on_the_peak(self) -> None:
        """Judgment call pinned: the WARN keys on max, not median.

        A median would drop a genuinely slow file whose samples are mostly
        cheap partial runs.  If this is ever changed to fire on p50, that is
        a deliberate decision and this test should change with it.
        """
        assert lint_gate_budget(OVER_BUDGET, "sp1", timing_data=self._data()), (
            "a peak over budget must still warn even with a tiny median"
        )

    def test_schema_2_producer_degrades_cleanly(self) -> None:
        """No spread fields — the finding reads as before, asserting nothing."""
        data = {"schema": 2, "_auto_loaded": True, "per_project": {"p": {"per_file": [
            {"file": "tests/test_drain.py", "max_s": 198.57}]}}}
        f = lint_gate_budget(OVER_BUDGET, "sp1", timing_data=data)[0]
        assert "median" not in f and "old" not in f
        assert "(budget: 60s). Consider" in f, "separator lost on the no-spread path"
