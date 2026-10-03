"""I2: a vacuous pin (xfail strict, passes) is a red gate.

``run_local_checks.py --project <tmp> --slug <s> --step 0`` on a step-0
gate whose pin is ``xfail(strict=True)`` and passes reports outcome
``fail`` naming XPASS(strict).

Rail: the check runner's mapping of a non-zero check exit to ``fail``
in ``run_local_checks.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
RUN_CHECKS = _SCRIPTS / "run_local_checks.py"

pytestmark = pytest.mark.timeout(120)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(root: Path) -> dict:
    """Project with a sub-plan whose step-0 gate runs a vacuous pin."""
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    slug = "vacuous-pin-test"
    stem = f"2026-10-04-{slug}"

    # A pin test that passes — xfail(strict=True) makes it an XPASS (red).
    pin_test = project / "test_vacuous_pin.py"
    pin_test.write_text(
        "import pytest\n\n"
        "@pytest.mark.xfail(strict=True, reason='control: must not pass')\n"
        "def test_vacuous_pin():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    (plans / "MASTER-2026-10-04-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-04-execution\n"
        "batch_date: 2026-10-04\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{stem}](./{stem}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {slug}\n\n"
        "### Step 0 — pin check\n\n"
        "```yaml\n"
        "local_checks:\n"
        f"  - command: \"python3 -m pytest {pin_test} -q -p no:cacheprovider\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "add pin test")

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "slug": slug}


def test_vacuous_pin_reports_fail_outcome(tmp_path: Path) -> None:
    world = _build_world(tmp_path)
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(world["data_home"]),
    }
    r = subprocess.run(
        [sys.executable, str(RUN_CHECKS),
         "--project", str(world["project"]),
         "--slug", world["slug"],
         "--step", "0",
         "--no-isolate"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env,
    )
    # The gate should report a failure (XPASS is a non-zero exit → fail).
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"run_local_checks.py did not produce JSON.\n"
            f"exit={r.returncode}\nstdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}"
        )

    # The rollup should be "fail" because the XPASS is a non-zero exit.
    rollup = data.get("rollup", data.get("outcome", ""))
    assert rollup == "fail", (
        f"a vacuous pin (xfail strict, passes) should be a red gate, "
        f"got rollup={rollup!r}.\ndata={json.dumps(data, indent=2)}"
    )