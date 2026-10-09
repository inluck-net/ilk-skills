"""Pin that a red step gate pulls current_step back to the red step.

Backlog aba1019c (driver half).  gh-resolve run 20261009-170157, iteration 2:
the worker committed "bump the-base-is-the-newest-green-commit current_step
to 2" (f41a5df0), the driver gated step 1 -> FAIL twice, and the run ended
local_checks_failed with the pointer still at 2.  Run 174717 started at
step 2, gated only step 2, and shipped the sub-plan with its step-1 pin
check still red.

rollback_pointer_past_red <results-file> (run_ilk_loop_claude.sh): for every
attributable blocking record (slug, step), if the sub-plan's current_step is
past that step, write it back to that step and say so.

AC-1: pointer 2, red record at step 1 => pointer 1, and the log names both.
AC-2 (control): pointer 1, red record at step 1 => unchanged, silent.
AC-3 (control): pointer 2, a passing record at step 1 => unchanged.
AC-4: the runner calls it on the confirmed-red path, before the
      stop-or-retry decision.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
SKILLS = REPO_ROOT / "skills"

PLAN = """---
plan: the-step
status: pending
current_step: {cur}
estimated_steps: 3
---

### Step 0: a

### Step 1: b

### Step 2: c
"""

FUNCS = ("find_subplan_file_by_slug", "_read_subplan_current_step",
         "_write_subplan_current_step", "rollback_pointer_past_red")


def _run(tmp_path: Path, cur: int, outcome: str) -> tuple[str, str]:
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "2026-10-09d-the-step.md"
    plan.write_text(PLAN.format(cur=cur))
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"slug": "the-step", "step": 1,
                                   "outcome": outcome,
                                   "command": "pytest t.py"}) + "\n")
    extract = "\n".join(
        f"eval \"$(sed -n '/^{f}()/,/^}}/p' '{RUNNER}')\"" for f in FUNCS)
    script = f"""
set -u
_SKILL_ROOT='{SKILLS}'
{extract}
_gate_first_plans_dir() {{ printf '%s\\n' '{plans}'; }}
rollback_pointer_past_red '{results}'
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    cs = next(l for l in plan.read_text().splitlines()
              if l.startswith("current_step:"))
    return cs, r.stdout + r.stderr


def test_ac1_a_red_step_pulls_the_pointer_back(tmp_path):
    cs, out = _run(tmp_path, 2, "fail")
    assert cs == "current_step: 1"
    assert "the-step" in out and "2 -> 1" in out


def test_ac2_pointer_at_the_red_step_is_left_alone(tmp_path):
    cs, out = _run(tmp_path, 1, "fail")
    assert cs == "current_step: 1"
    assert out.strip() == ""


def test_ac3_a_green_step_never_moves_the_pointer(tmp_path):
    cs, _ = _run(tmp_path, 2, "pass")
    assert cs == "current_step: 2"


def test_ac4_the_confirmed_red_path_calls_it_before_stop_or_retry():
    text = RUNNER.read_text(encoding="utf-8")
    call = text.index('rollback_pointer_past_red "$local_checks_results"')
    decision = text.index('if [[ "$_should_stop" == "true" ]]; then', call - 4000)
    assert call < decision
