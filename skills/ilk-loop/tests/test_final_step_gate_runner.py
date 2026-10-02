"""Runner-level: a shipped sub-plan must have a passing final-step gate.

Replays the retro-2026-09-29 F1 shape: a dispatched slug is hand-edited
to ``shipped`` with only a ``fail`` gate row in history.  The runner must
revert it to ``in-progress``.

  AC-1  dispatched slug, status edited to shipped, gate fails ⇒ reverted
  AC-2  green control — gate passes ⇒ stays shipped
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

SLUG = "final-gate-test"
STEM = f"2026-09-29-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(
    root: Path,
    *,
    gate_passes: bool = False,
) -> dict:
    """A project with one dispatched sub-plan (2 steps).

    The stub agent commits with [plan:<slug>#step-1] and sets status to
    shipped.  The gate either passes or fails.
    """
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    # Step 0 is "done" below, so its commit must exist: ship-integrity is
    # told the repo (--repo) and checks every authored step for a commit.
    (project / "step0.txt").write_text("step 0 work\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", f"feat: step 0 [plan:{SLUG}#step-0]")

    data_home = root / ".ilk-data"
    import ilk_paths
    key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Master: active, one sub-plan.
    (plans / "MASTER-2026-09-29.md").write_text(
        "---\n"
        "master_plan: 2026-09-29\n"
        "batch_date: 2026-09-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    # Sub-plan: in-progress, step 1 of 2 (step 0 done, step 1 pending).
    gate_cmd = "true" if gate_passes else "false"
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — first step\n\n"
        "Done.\n\n"
        "### Step 1 — final step\n\n"
        "```yaml\n"
        "local_checks:\n"
        f"  - command: \"{gate_cmd}\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"

    # The stub agent commits with [plan:<slug>#step-1] and ships.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{STEM}.md')!r}\n"
        # Commit with the final-step trailer.
        f"echo 'step 1 work' > {project}/step1.txt\n"
        f"git -C {project} add step1.txt\n"
        "git -c user.email=t@example.com -c user.name=t "
        f'-C {project} commit -q -m "feat: step 1 [plan:{SLUG}#step-1]"\n'
        # Mark as shipped.
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 1', 'current_step: 2', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir,
    }


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


def _read_subplan_status(world: dict) -> str:
    f = world["plans"] / f"{STEM}.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_subplan_step(world: dict) -> str:
    f = world["plans"] / f"{STEM}.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("current_step:"):
            return line.split(":", 1)[1].strip()
    return ""


# ── AC-1: dispatched slug with failing gate ⇒ reverted ──────────────────────

@_NEEDS_GTIMEOUT
def test_dispatched_slug_failing_gate_reverted(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: dispatched slug hand-edited to shipped, gate fails ⇒ reverted
    to in-progress.  The final-step gate invariant catches the missing pass."""
    root = tmp_path_factory.mktemp("final-gate-red")
    world = _build_world(root, gate_passes=False)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"sub-plan status is {status!r}, expected 'in-progress' — "
        f"final-step gate violation was not caught.\n{tail}"
    )

    # Log must mention the final-step gate violation.
    assert "final-step gate" in combined or "ship-integrity VIOLATION" in combined, (
        "log missing final-step gate violation message.\n" + tail
    )


# ── AC-2: green control — gate passes ⇒ stays shipped ───────────────────────

@_NEEDS_GTIMEOUT
def test_green_control_passing_gate_kept(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """GREEN CONTROL: dispatched slug with passing gate ⇒ stays shipped."""
    root = tmp_path_factory.mktemp("final-gate-green")
    world = _build_world(root, gate_passes=True)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "shipped", (
        f"sub-plan status is {status!r}, expected 'shipped' — "
        f"passing gate was rejected.\n{tail}"
    )
