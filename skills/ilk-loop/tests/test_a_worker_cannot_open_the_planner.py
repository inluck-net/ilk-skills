"""Pin that a loop worker cannot open a planning skill mid-iteration.

Backlog a134fac5.  The runner's prompt is "/ilk please continue the active
plan" (run_ilk_loop_claude.sh:46).  Some workers open Skill(ilk-plan)
instead: 5 ilk-skills runs (*202610* logs), and gh-resolve runs
20261009-133350 and 20261009-212756.  ilk-plan has no one-step contract and
works on whatever master is newest; in run 212756 that ended with another
master's quarantined sub-plan set to `shipped`.

Rule (hooks/no-foreign-plan-edit.py): with ILK_ITERATION_SUBPLAN set, an
ilk-* skill other than ilk, ilk-loop and ilk-status is denied, and the deny
names Skill(ilk).  Other skills, and every skill outside an iteration, are
allowed.

AC-1: ilk-plan, ilk-spec and a plugin-prefixed ilk-self-improve are denied in
      an iteration; the reason names the iteration's sub-plan and Skill(ilk).
AC-2 (controls): ilk, ilk-loop, ilk-status and a non-ilk skill are allowed
      in an iteration; ilk-plan is allowed with no iteration.
AC-3: install.sh registers the hook for the Skill matcher on worker homes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "hooks" / "no-foreign-plan-edit.py"


def _decision(skill: str, tmp_path: Path, subplan: str | None) -> dict | None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "ILK_DATA_HOME": str(tmp_path / "ilk-data")}
    if subplan:
        env["ILK_ITERATION_SUBPLAN"] = subplan
    event = json.dumps({"tool_name": "Skill", "cwd": str(tmp_path),
                        "tool_input": {"skill": skill, "args": "continue"}})
    r = subprocess.run([sys.executable, str(HOOK)], input=event,
                       capture_output=True, text=True, env=env,
                       cwd=str(tmp_path), timeout=30)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]


@pytest.mark.parametrize("skill", ["ilk-plan", "ilk-spec",
                                   "ilk-skills:ilk-self-improve"])
def test_ac1_a_planning_skill_is_denied_in_an_iteration(skill, tmp_path):
    out = _decision(skill, tmp_path, "own-step")
    assert out is not None and out["permissionDecision"] == "deny"
    assert "own-step" in out["permissionDecisionReason"]
    assert "Skill(ilk)" in out["permissionDecisionReason"]


@pytest.mark.parametrize("skill", ["ilk", "ilk-loop", "ilk-status", "simplify"])
def test_ac2_the_loop_skill_is_allowed_in_an_iteration(skill, tmp_path):
    assert _decision(skill, tmp_path, "own-step") is None


def test_ac2_no_iteration_allows_the_planner(tmp_path):
    assert _decision("ilk-plan", tmp_path, None) is None


def test_ac3_install_registers_the_skill_matcher_for_workers():
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert '"no-foreign-plan-edit.py:Skill:worker"' in text
