"""Pin that the foreign-plan hook guards the worker's project as installed.

Regression for gh-resolve run 20261009-212756, iteration 2 (21:41): a worker
pinned to MASTER-2026-10-09b edited another master's sub-plan to `shipped`,
plus that master and its verify sub-plan, and the hook allowed every edit.

The hook resolved the plans dir from its own file.  Installed, that file is
under ``~/.ilk/releases/<tag>/`` (not a git repo), so ``_plans_dir()``
returned None and the hook allowed everything.  The older tests set
``ILK_PLANS_DIR``, which the runner never exports, so they never ran the
production branch.

Each test here runs a copy of the hook from a non-git directory, with no
``ILK_PLANS_DIR``, the way the worker home runs it.

AC-1: a foreign sub-plan in the worker's project (cwd) is denied.
AC-2: with ILK_MASTER set, another MASTER file is denied.
AC-3 (controls): the iteration's own sub-plan and own master are allowed,
      and nothing is denied with ILK_ITERATION_SUBPLAN unset.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "hooks" / "no-foreign-plan-edit.py"
SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"


@pytest.fixture()
def world(tmp_path: Path):
    # A release-shaped install: hooks/ + skills/ilk-loop/scripts/, no .git.
    release = tmp_path / "release"
    (release / "hooks").mkdir(parents=True)
    shutil.copy2(HOOK, release / "hooks" / HOOK.name)
    shutil.copytree(SCRIPTS, release / "skills" / "ilk-loop" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))

    # The worker's project: a git repo whose plans live externally.
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    data = tmp_path / "ilk-data"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "ILK_DATA_HOME": str(data),
        "ILK_DATA_DIR": str(data),
    }
    plans = Path(subprocess.run(
        [sys.executable, "-c",
         "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
         "import ilk_paths as p; r, _ = p.find_project_root(Path(sys.argv[2]));"
         "print(p.external_plans_dir(p.project_key(r)))",
         str(SCRIPTS), str(project)],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip())
    plans.mkdir(parents=True)
    for name in ("MASTER-2026-10-09b-execution-plan.md",
                 "MASTER-2026-10-09d-execution-plan.md"):
        (plans / name).write_text("---\nstatus: active\n---\n")
    for slug in ("own-step", "other-step"):
        (plans / f"2026-10-09b-{slug}.md").write_text(
            f"---\nplan: {slug}\nstatus: pending\n---\n")
    return {"hook": release / "hooks" / HOOK.name, "project": project,
            "plans": plans, "env": env}


def _run(world, target: Path, **extra_env: str) -> bool:
    """True if the hook allows an Edit of *target*."""
    env = dict(world["env"])
    env.update(extra_env)
    event = json.dumps({"tool_name": "Edit", "cwd": str(world["project"]),
                        "tool_input": {"file_path": str(target),
                                       "old_string": "a", "new_string": "b"}})
    r = subprocess.run([sys.executable, str(world["hook"])], input=event,
                       capture_output=True, text=True, env=env,
                       cwd=str(world["project"]), timeout=30)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return True
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] != "deny"


WORKER = {"ILK_ITERATION_SUBPLAN": "own-step",
          "ILK_MASTER": "MASTER-2026-10-09b-execution-plan.md"}


def test_ac1_foreign_subplan_in_the_workers_project_is_denied(world):
    assert not _run(world, world["plans"] / "2026-10-09b-other-step.md",
                    **WORKER)


def test_ac2_another_master_is_denied(world):
    assert not _run(world, world["plans"] / "MASTER-2026-10-09d-execution-plan.md",
                    **WORKER)


def test_ac3_own_subplan_and_own_master_are_allowed(world):
    assert _run(world, world["plans"] / "2026-10-09b-own-step.md", **WORKER)
    assert _run(world, world["plans"] / "MASTER-2026-10-09b-execution-plan.md",
                **WORKER)


def test_ac3_interactive_session_is_never_denied(world):
    assert _run(world, world["plans"] / "2026-10-09b-other-step.md")
    assert _run(world, world["plans"] / "MASTER-2026-10-09d-execution-plan.md")
