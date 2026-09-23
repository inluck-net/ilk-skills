r"""Red-first pins: a gate that extracts 0 checks must not pass.

Part of sub-plan `a-gate-cannot-pass-empty` (step 0 of 3).

Three shapes cause `extract_step_local_checks` to return [] for a step that
declares local_checks:

  C1 colon:      `### Step 0: title` — heading regex misses it
  C2 fence-before-yaml: a ```python block then ```yaml; the python block's
                 closing ``` matches as an untagged opener
  C3 duplicate:  `### Step 0 — …` inside quoted prose before the real heading;
                 the first match wins

These tests pin that the locator finds the gate in each shape.  All three
are `xfail(strict=True)` — they fail today because the locator uses
`^###\s+Step\s+N(\s|—|-|$)` which misses C1 and shares the other blind
spots.  Step 1 of this sub-plan will make them pass.

AC-3 (a step with no local_checks anywhere → 0 checks, exit 0) is unmarked:
it holds today.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402
from ilk_paths import external_plans_dir, resolve_project_key  # noqa: E402
from plan_lint import lint_gate_extractable  # noqa: E402
from plan_preflight import preflight_batch  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_project(tmp_path: Path, fixture: str, slug: str) -> Path:
    """Create a project with an external plans dir holding one sub-plan."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    plans = Path(external_plans_dir(resolve_project_key(proj)))
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"2026-09-24-{slug}.md").write_text(fixture, encoding="utf-8")
    (plans / "MASTER-2026-09-24-execution-plan.md").write_text(
        f"---\nstatus: active\n---\n# m\n- [x](./2026-09-24-{slug}.md)\n",
        encoding="utf-8",
    )
    return proj


# ── Fixtures ────────────────────────────────────────────────────────────────

# C1: colon after step number — heading regex `^###\s+Step\s+N(\s|—|-|$)` misses it
C1_FIXTURE = """\
---
plan: test-c1-colon
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: C1 colon test

### Step 0: the heading has a colon
```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 1 — later work
```yaml
local_checks:
  - command: "true"
    timeout: 30
```
"""

# C2: a ```python fence before the ```yaml fence; the python fence's closer
# matches as an untagged opener, and the captured "fence" is the prose between
C2_FIXTURE = """\
---
plan: test-c2-fence-before-yaml
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: C2 fence-before-yaml test

### Step 0 — the gate has a python block before it
```python
# example code
x = 1
```

```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 1 — later work
```yaml
local_checks:
  - command: "true"
    timeout: 30
```
"""

# C3: duplicate heading — the first one (in quoted prose) wins, so the real
# gate is invisible.  The spurious heading uses an em dash, which the current
# heading regex `^###\s+Step\s+N(\s|—|-|$)` DOES match.  The locator finds
# the first heading's fence (the quoted example) instead of the real step's.
# After the fix, the locator uses the LAST match and finds the real fence.
C3_FIXTURE = """\
---
plan: test-c3-duplicate-heading
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: C3 duplicate heading test

### Step 0 — this is quoted prose, not a real step

```yaml
local_checks:
  - command: "echo quoted"
    timeout: 30
```

### Step 0 — the real step
```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 1 — later work
```yaml
local_checks:
  - command: "true"
    timeout: 30
```
"""

# AC-2: local_checks declared in a ```json fence — the locator cannot resolve it
AC2_FIXTURE = """\
---
plan: test-ac2-json-fence
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: AC-2 json fence test

### Step 0 — the gate sits in a json fence
```json
{
  "local_checks": [
    {"command": "true", "timeout": 30}
  ]
}
```

### Step 1 — later work
```yaml
local_checks:
  - command: "true"
    timeout: 30
```
"""

# AC-3: a step with no local_checks anywhere — must return 0 checks, exit 0
AC3_FIXTURE = """\
---
plan: test-ac3-no-gate
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: AC-3 no gate test

### Step 0 — this step has no gate at all

Body text, no fence with local_checks.

### Step 1 — also no gate

More body text.
"""

# Duplicate heading fixture for AC-4 (plan_lint)
DUPLICATE_HEADING_FIXTURE = """\
---
plan: test-duplicate-heading-lint
status: pending
current_step: 0
estimated_steps: 2
local_checks: []
---

# Sub-plan: duplicate heading lint test

### Step 0 — real step
```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 0 — duplicate
```yaml
local_checks:
  - command: "true"
    timeout: 30
```
"""


# ── AC-1: C1, C2, C3 each extract exactly their 1 declared check ──────────

class TestAC1FixtureExtraction:
    """Each shape must extract exactly 1 check from step 0."""

    def test_c1_colon_extracts_one_check(self) -> None:
        checks = rlc.extract_step_local_checks(C1_FIXTURE, step_n=0)
        assert len(checks) == 1, f"C1 colon: expected 1 check, got {len(checks)}"

    def test_c2_fence_before_yaml_extracts_one_check(self) -> None:
        checks = rlc.extract_step_local_checks(C2_FIXTURE, step_n=0)
        assert len(checks) == 1, f"C2 fence-before-yaml: expected 1 check, got {len(checks)}"

    def test_c3_duplicate_heading_extracts_real_step_check(self) -> None:
        checks = rlc.extract_step_local_checks(C3_FIXTURE, step_n=0)
        assert len(checks) == 1, f"C3: expected 1 check, got {len(checks)}"
        # The locator must find the REAL step's fence, not the quoted example's
        assert checks[0].get("command") == "true", (
            f"C3: expected command 'true' from real step, got {checks[0].get('command')!r}"
        )


# ── AC-2: declared but unextracted refuses (malformed) ─────────────────────

class TestAC2DeclaredButUnextractedRefuses:
    """A step that declares local_checks in a shape the locator cannot resolve
    (e.g. a json fence) must exit 2 with 'malformed' naming the step."""

    def test_json_fence_exits_2_with_malformed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = _make_project(tmp_path, AC2_FIXTURE, "test-ac2-json-fence")
        result = rlc.main([
            "--project", str(proj),
            "--slug", "test-ac2-json-fence",
            "--step", "0",
            "--no-isolate",
        ])
        assert result == 2, f"AC-2 json fence: expected exit 2, got {result}"


# ── AC-3: no gate at all → 0 checks, exit 0 (holds today) ────────────────

class TestAC3NoGateIsUnchanged:
    """A step with no local_checks anywhere must return 0 checks and exit 0."""

    def test_no_gate_returns_empty(self) -> None:
        checks = rlc.extract_step_local_checks(AC3_FIXTURE, step_n=0)
        assert checks == [], "AC-3: no gate → 0 checks"

    def test_no_gate_exits_0(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = _make_project(tmp_path, AC3_FIXTURE, "test-ac3-no-gate")
        result = rlc.main([
            "--project", str(proj),
            "--slug", "test-ac3-no-gate",
            "--step", "0",
            "--no-isolate",
        ])
        assert result == 0, f"AC-3: no gate → exit 0, got {result}"


# ── AC-4: plan_lint reports HARD for AC-2 and duplicate-heading ────────────

class TestAC4PlanLint:
    """plan_lint must report HARD for shapes the runtime cannot extract."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_ac2_json_fence_reports_hard(self) -> None:
        findings = lint_gate_extractable(AC2_FIXTURE, "test-ac2-json-fence")
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert hard_findings, f"AC-4: expected HARD finding for json fence, got {findings}"

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_duplicate_heading_reports_hard(self) -> None:
        findings = lint_gate_extractable(DUPLICATE_HEADING_FIXTURE, "test-duplicate-heading-lint")
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert hard_findings, f"AC-4: expected HARD finding for duplicate heading, got {findings}"

    def test_c1_colon_is_clean_after_fix(self) -> None:
        """C1 must be clean once the locator is fixed — it now extracts."""
        findings = lint_gate_extractable(C1_FIXTURE, "test-c1-colon")
        hard_findings = [f for f in findings if f.startswith("HARD")]
        assert not hard_findings, f"AC-4: C1 must be clean after fix, got {hard_findings}"


# ── AC-5: plan_preflight reports FAIL for AC-2 ─────────────────────────────

class TestAC5PlanPreflight:
    """plan_preflight must report FAIL for a fixture the runtime cannot extract."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_ac2_json_fence_preflight_fails(self, tmp_path: Path) -> None:
        master = tmp_path / "MASTER.md"
        master.write_text(
            "---\nmaster_plan: test\nbatch_date: 2026-09-24\nstatus: active\n---\n\n"
            "# MASTER\n\n## Sub-plan registry\n\n"
            "| # | File |\n|---|---|\n| 1 | test-ac2-json-fence.md |\n",
            encoding="utf-8",
        )
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        (plans_dir / "test-ac2-json-fence.md").write_text(AC2_FIXTURE, encoding="utf-8")
        result = preflight_batch(
            master_text=master.read_text(encoding="utf-8"),
            plans_dir=plans_dir,
            project_root=tmp_path,
            subplan_texts={"test-ac2-json-fence.md": AC2_FIXTURE},
        )
        fail_findings = [f for f in result.findings if "FAIL" in f and "json" in f.lower()]
        assert fail_findings, f"AC-5: expected FAIL for json fence, got {result.findings}"


# ── AC-6: gate-first says why it falls through ─────────────────────────────

class TestAC6GateFirstSaysWhy:
    """gate_first_results_are_green must print the reason on fall-through."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_no_command_prints_reason(self, tmp_path: Path) -> None:
        """A gate result with no `command` key must print the fall-through reason."""
        import subprocess
        # Write a results file with a pass record that has no command
        results_file = tmp_path / "results.jsonl"
        results_file.write_text(
            '{"outcome": "pass", "exit_code": 0}\n',
            encoding="utf-8",
        )
        # Call gate_first_results_are_green via the shell function
        proc = subprocess.run(
            ["bash", "-c",
             f"source '{SCRIPTS_DIR / 'run_ilk_loop_claude.sh'}' 2>/dev/null; "
             f"gate_first_results_are_green '{results_file}'"],
            capture_output=True, text=True, timeout=30,
        )
        # The function must return 1 (not green)
        assert proc.returncode == 1, "AC-6: no command → must return 1"
        # After the fix, stderr must contain the reason
        combined = proc.stdout + proc.stderr
        assert "falling through to the agent" in combined, (
            f"AC-6: expected fall-through reason in output, got: {combined!r}"
        )
        assert "result has no command" in combined, (
            f"AC-6: expected 'result has no command' in output, got: {combined!r}"
        )


# ── AC-7: extraction goes from 0 to declared for C1-C3 ─────────────────────

class TestAC7ExtractionGoesFromZeroToDeclared:
    """Over C1-C3 shapes, step_gate_fence must extract the declared checks.
    Today they extract 0; after the fix they must extract the declared count."""

    def test_c1_declared_count_matches_extracted(self) -> None:
        gate = rlc.step_gate_fence(C1_FIXTURE, step_n=0)
        declared = gate.fence_text.count("command:") if gate.fence_text else 0
        extracted = len(rlc.extract_step_local_checks(C1_FIXTURE, step_n=0))
        assert extracted == declared, (
            f"AC-7 C1: declared {declared} commands, extracted {extracted}"
        )

    def test_c2_declared_count_matches_extracted(self) -> None:
        gate = rlc.step_gate_fence(C2_FIXTURE, step_n=0)
        declared = gate.fence_text.count("command:") if gate.fence_text else 0
        extracted = len(rlc.extract_step_local_checks(C2_FIXTURE, step_n=0))
        assert extracted == declared, (
            f"AC-7 C2: declared {declared} commands, extracted {extracted}"
        )

    def test_c3_extracts_real_step_command(self) -> None:
        # C3 has a spurious heading (quoted prose) before the real step.
        # The locator must use the LAST heading and extract the real step's fence.
        extracted = rlc.extract_step_local_checks(C3_FIXTURE, step_n=0)
        assert len(extracted) == 1, f"AC-7 C3: expected 1 check, got {len(extracted)}"
        assert extracted[0].get("command") == "true", (
            f"AC-7 C3: expected 'true' from real step, got {extracted[0].get('command')!r}"
        )
