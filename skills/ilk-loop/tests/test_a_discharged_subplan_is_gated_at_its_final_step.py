"""A discharged sub-plan is gated, and recorded, at its final step.

On a shared remote (no step trailers) the driver gates "the active
sub-plan" via ``get_active_subplan_targets``.  It emitted the raw
``current_step``.  A sub-plan whose steps are all discharged has
``current_step == estimated_steps``, one past its last step, so the gate
row was written for a step that does not exist.  ``ship_integrity`` accepts
only a pass row for step ``estimated_steps - 1``, so it reverted the ship on
every launch: rezmac 2026-10-09, issue-8006-work-64502e34 (estimated_steps
4, rows recorded at step 4), 6 of 6 runs, each spending a full convex suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASH_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_LOOP_STATUS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "loop_status.py"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                   check=True)


def _project(tmp: Path, current_step: int, estimated_steps: int) -> Path:
    _git(["init"], tmp)
    _git(["config", "user.email", "t@t"], tmp)
    _git(["config", "user.name", "t"], tmp)
    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-10-09-x.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-09-x
        status: active
        ---
        # batch

        | # | Slug |
        |---|---|
        | 1 | 2026-10-09-work.md |
    """))
    steps = "".join(f"### Step {n}\n" for n in range(estimated_steps))
    (plans / "2026-10-09-work.md").write_text(
        "---\nplan: work\nstatus: in-progress\n"
        f"current_step: {current_step}\nestimated_steps: {estimated_steps}\n"
        f"---\n{steps}")
    _git(["add", "."], tmp)
    _git(["commit", "-m", "plans"], tmp)
    return tmp


def _targets(project: Path) -> str:
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{_BASH_RUNNER}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        PROJECT_PATH='{project}'
        LOOP_STATUS_SCRIPT='{_LOOP_STATUS}'
        export ILK_DATA_HOME='{project}/ilk-data'
        get_active_subplan_targets
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=120)
    return r.stdout.strip()


def test_loop_status_step_fields_parse_as_numbers(tmp_path):
    """loop_status emits the step fields as strings ("4"); the clamp converts
    them with tonumber.  Pin that both parse, whatever their JSON type."""
    project = _project(tmp_path, 4, 4)
    r = subprocess.run([sys.executable, str(_LOOP_STATUS), "--json"],
                       cwd=project, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "ILK_DATA_HOME": str(project / "ilk-data")})
    sub = json.loads(r.stdout)["subplans"][0]
    assert int(sub["current_step"]) == 4 and int(sub["estimated_steps"]) == 4, sub


def test_discharged_subplan_targets_its_final_step(tmp_path):
    project = _project(tmp_path, 4, 4)
    assert _targets(project) == "work 3"


def test_control_mid_plan_step_is_unchanged(tmp_path):
    project = _project(tmp_path, 1, 4)
    assert _targets(project) == "work 1"
