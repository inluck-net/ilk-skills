"""I1: a red gate blocks a ship.

A stub worker ships a sub-plan whose step gate is red.  After the run
the sub-plan is not ``shipped`` and ``last-exit.json`` ``state`` is
``ship_integrity_violation``.

Rail: ``test_ship_integrity`` in ``run_ilk_loop_claude.sh``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

pytestmark = pytest.mark.timeout(120)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(root: Path) -> dict:
    """Project + isolated data home + a stub ``claude``."""
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

    slug = "a-subplan-with-a-red-gate"
    stem = f"2026-10-04-{slug}"

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
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"python3 -c 'raise SystemExit(1)'\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{stem}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"--allow-empty -m 'feat: the work [plan:{slug}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir, "slug": slug, "stem": stem}


def _run_one_iteration(world: dict, root: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    (root / ".claude").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, cwd=str(root),
    )


@pytest.fixture(scope="module")
def red_gate_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("i1-red-gate")
    world = _build_world(root)
    proc = _run_one_iteration(world, root)
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    sentinel = next((c for c in candidates if c.is_file()), None)
    return {
        "proc": proc,
        "sentinel": json.loads(sentinel.read_text(encoding="utf-8"))
        if sentinel is not None else None,
        "sentinel_path": sentinel or " or ".join(str(c) for c in candidates),
        "subplan": world["plans"] / f"{world['stem']}.md",
    }


def test_red_gate_produces_ship_integrity_violation(red_gate_run: dict) -> None:
    sentinel = red_gate_run["sentinel"]
    tail = "\n".join(
        (red_gate_run["proc"].stdout + red_gate_run["proc"].stderr
         ).splitlines()[-30:]
    )
    assert sentinel is not None, (
        f"the runner wrote no sentinel at {red_gate_run['sentinel_path']}.\n{tail}"
    )
    assert sentinel.get("state") == "ship_integrity_violation", (
        "a sub-plan shipped with its declared gate red and the run ended at "
        f"state={sentinel.get('state')!r} instead of ship_integrity_violation.\n"
        f"last 30 lines:\n{tail}"
    )


def test_red_gate_reverts_ship_to_in_progress(red_gate_run: dict) -> None:
    body = red_gate_run["subplan"].read_text(encoding="utf-8")
    m = re.search(r"^status:\s*(\S+)", body, re.MULTILINE)
    status = m.group(1) if m else "<none>"
    assert status == "in-progress", (
        f"sub-plan is still `{status}` after its gate came back red."
    )