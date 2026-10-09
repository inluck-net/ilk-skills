"""Pin that a `### Step N` heading under ## Findings is not counted as a step.

gh-resolve 2026-10-09/10: the batch-verify sub-plan green-base-batch-verify
(estimated_steps 2, two real step headings) had a worker Findings note headed
`### Step 1 — 2026-10-09, worker`.  count_step_headings
(run_ilk_loop_claude.sh) counted 3, so the gate-first ship block's
`new_step == total_steps` test (2 == 3) failed silently.  Every iteration
re-ran a green gate, shipped nothing, and three runs ended no-progress
(20261009-224648, 20261009-234717, 20261010-000236), about 1 h.

AC-1: a sub-plan with Step 0/1 headings plus a `### Step 1` heading under
      ## Findings counts 2.
AC-2 (control): with no Findings heading, it counts 2.
AC-3: when the ship block's count disagrees, the runner says so (the
      silent branch now logs).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"

PLAN = """---
plan: verify-x
status: pending
current_step: 2
estimated_steps: 2
---

## Steps

### Step 0: run the suite

### Step 1: re-derive the verdict

## Findings
{findings}
"""


def _count(tmp_path: Path, findings: str) -> str:
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-10-09d-verify-x.md").write_text(PLAN.format(findings=findings))
    script = f"""
set -u
eval "$(sed -n '/^find_subplan_file_by_slug()/,/^}}/p' '{RUNNER}')"
eval "$(sed -n '/^count_step_headings()/,/^}}/p' '{RUNNER}')"
_gate_first_plans_dir() {{ printf '%s\\n' '{plans}'; }}
count_step_headings verify-x
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_ac1_a_findings_step_heading_is_not_counted(tmp_path):
    findings = "\n### Step 1 — 2026-10-09, worker\n\n- gate green, ship pending\n"
    assert _count(tmp_path, findings) == "2"


def test_ac2_control_without_findings_headings(tmp_path):
    assert _count(tmp_path, "\n_(filled by the loop)_\n") == "2"


def test_ac3_a_count_mismatch_in_the_ship_block_is_logged():
    text = RUNNER.read_text(encoding="utf-8")
    start = text.index("attempt_gate_first_fast_path() {")
    body = text[start:text.index("\n}\n", start)]
    assert "headings, so the driver cannot ship" in body
