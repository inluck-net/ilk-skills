"""Red-first: pre-loop exits write a terminal sentinel.

Regression: chad-mbp 2026-09-23 — 6 of 11 sentinels read ``state: running``
for a dead pid; all 6 are iteration-0 exits (5 ``shipped-unproven``,
1 ``already-shipped``).

AC-1  (xfail) all shipped, no proof ⇒ ``state == "shipped-unproven"``
AC-2  (xfail) all shipped with proof ⇒ ``state == "already-shipped"``;
      all blocked ⇒ ``state == "blocked-no-runnable"``
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS))

SLUG = "pre-loop-sentinel-test"
STEM = f"2026-09-23-{SLUG}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _build_world_shipped_unproven(root: Path) -> dict:
    """World where all sub-plans are shipped but no ship-proof exists."""
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    sys.path.insert(0, str(RUNNER.parent))
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-08-29-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-execution\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )
    # Sub-plan already shipped, no proof
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        "verification_tier: loop-verified\n"
        "---\n\n"
        f"# {SLUG}\n\nBody.\n",
        encoding="utf-8",
    )

    # Stub claude: does nothing (iteration-0 will see all shipped)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


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


def _read_sentinel(world: dict) -> dict:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = list(runtime.glob("run-*/last-exit.json"))
    assert candidates, f"no sentinel found in {runtime}"
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


@pytest.mark.xfail(strict=True, reason="red-first: pre-loop exit leaves running")
def test_shipped_unproven_writes_terminal_sentinel(tmp_path: Path) -> None:
    """AC-1: all shipped, no proof ⇒ state == shipped-unproven."""
    root = tmp_path / "harness"
    root.mkdir()
    world = _build_world_shipped_unproven(root)
    result = _run_one_iteration(world, root)
    sentinel = _read_sentinel(world)
    assert sentinel["state"] == "shipped-unproven", (
        f"expected shipped-unproven, got {sentinel['state']}; "
        f"stdout={result.stdout[-500:]}; stderr={result.stderr[-500:]}"
    )
    assert "ended_at" in sentinel, f"missing ended_at: {sentinel}"
    assert sentinel.get("pid") != os.getpid(), "pid should not be the runner's live pid"


@pytest.mark.xfail(strict=True, reason="red-first: pre-loop exit leaves running")
def test_already_shipped_writes_terminal_sentinel(tmp_path: Path) -> None:
    """AC-2a: all shipped with proof ⇒ state == already-shipped."""
    root = tmp_path / "harness"
    root.mkdir()
    world = _build_world_shipped_unproven(root)
    # Add ship-proof ledger entry so it becomes "already-shipped"
    ledger = world["data_home"] / "projects" / world["key"] / "ship-proof-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"slug": SLUG, "head_sha": "abc123", "verdict": "pass"}) + "\n",
        encoding="utf-8",
    )
    result = _run_one_iteration(world, root)
    sentinel = _read_sentinel(world)
    assert sentinel["state"] == "already-shipped", (
        f"expected already-shipped, got {sentinel['state']}; "
        f"stdout={result.stdout[-500:]}; stderr={result.stderr[-500:]}"
    )
