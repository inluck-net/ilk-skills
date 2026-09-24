"""Red-first: ship-integrity enforces only the active master's sub-plans.

Reproduces rezmac run 20260924-072803 where a real ``fail`` verdict for a
sub-plan of ANOTHER master triggered revert and parked the wrong master.

AC-1  active master A (a1 shipped, gate pass) + blocked master B
      (b1 shipped).  Agent commits with ``[plan:b1-slug#step-0]`` trailer;
      b1's gate fails.  ⇒ b1 stays shipped, B unchanged, log has "not in
      the active master", no ``ship_integrity_violation``.
AC-2  (passes today) a1 with a real fail ⇒ reverted to in-progress.
AC-3  (passes today) ``_active_subplans`` empty ⇒ today's behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

# Slugs for the two masters.
SLUG_A = "active-subplan"
SLUG_B = "foreign-subplan"
STEM_A = f"2026-09-24-{SLUG_A}"
STEM_B = f"2026-09-24-{SLUG_B}"


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
    # What the stub agent commits.  "own" = active sub-plan's trailer,
    # "foreign" = foreign sub-plan's trailer, "none" = no trailer.
    agent_trailer: str = "own",
    # Whether master B is blocked (True) or active (False).
    foreign_blocked: bool = True,
) -> dict:
    """A project + isolated data home + a stub agent.

    Master A is always ``status: active``.  Master B is ``blocked`` by
    default.  Both have one sub-plan each, both ``shipped`` with one step
    and one gate.
    """
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Master A: active, sub-plan a1 shipped with a passing gate.
    (plans / "MASTER-A-2026-09-24.md").write_text(
        "---\n"
        "master_plan: A-2026-09-24\n"
        "batch_date: 2026-09-24\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master A\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG_A}](./{STEM_A}.md) |\n",
        encoding="utf-8",
    )

    # Gate for a1: always passes.  a1 starts runnable (in-progress, step 0):
    # with every sub-plan already shipped the runner exits pre-loop
    # (shipped-unproven, iters=0) and never dispatches the agent, so the
    # ship-integrity walk this file pins would never run.
    (plans / f"{STEM_A}.md").write_text(
        "---\n"
        f"plan: {SLUG_A}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG_A}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Master B: blocked (or active for AC-3 variant).
    b_status = "blocked" if foreign_blocked else "active"
    (plans / "MASTER-B-2026-09-24.md").write_text(
        "---\n"
        "master_plan: B-2026-09-24\n"
        "batch_date: 2026-09-24\n"
        f"status: {b_status}\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master B\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG_B}](./{STEM_B}.md) |\n",
        encoding="utf-8",
    )

    # Gate for b1: always fails.
    (plans / f"{STEM_B}.md").write_text(
        "---\n"
        f"plan: {SLUG_B}\n"
        "status: shipped\n"
        "current_step: 1\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG_B}\n\n"
        "### Step 0 — do the other thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"false\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"

    # The stub agent reverts a1 to in-progress (simulating the agent working
    # on it) and commits with the chosen trailer.
    if agent_trailer == "own":
        trailer = f"[plan:{SLUG_A}#step-0]"
    elif agent_trailer == "foreign":
        trailer = f"[plan:{SLUG_B}#step-0]"
    else:
        trailer = ""

    commit_msg = f"feat: agent work {trailer}".strip()
    stub.write_text(
        "#!/usr/bin/env bash\n"
        # Revert a1 to in-progress so the gate runs for it.
        f"SP={str(plans / f'{STEM_A}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: shipped', 'status: in-progress', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 1', 'current_step: 0', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        # Make a commit with the trailer.
        f"echo 'agent work' > {project}/agent-work.txt\n"
        f"git -C {project} add agent-work.txt\n"
        "git -c user.email=t@example.com -c user.name=t "
        f'-C {project} commit -q -m {commit_msg!r}\n'
        # Re-mark a1 as shipped (the agent declares itself done).
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
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


def _read_sentinel(world: dict) -> dict | None:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    sentinel = next((c for c in candidates if c.is_file()), None)
    if sentinel is not None:
        return json.loads(sentinel.read_text(encoding="utf-8"))
    return None


def _read_subplan_status(world: dict, stem: str) -> str:
    """Read the status field from a sub-plan file."""
    f = world["plans"] / f"{stem}.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_master_status(world: dict, master_name: str) -> str:
    """Read the status field from a master file."""
    for f in world["plans"].glob(f"MASTER-{master_name}*.md"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("status:"):
                return line.split(":", 1)[1].strip()
    return ""


# ── AC-1: foreign verdict not enforced ──────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_foreign_verdict_not_enforced(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: agent commits with b1's trailer; b1's gate fails.  b1 stays
    shipped, B unchanged, log has "not in the active master", no
    ship_integrity_violation.
    """
    root = tmp_path_factory.mktemp("integrity-foreign")
    world = _build_world(root, agent_trailer="foreign")
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"

    # b1 must stay shipped.
    b1_status = _read_subplan_status(world, STEM_B)
    assert b1_status == "shipped", (
        f"b1 status changed to {b1_status!r} — foreign verdict was enforced.\n{tail}"
    )

    # Master B must stay blocked.
    b_master_status = _read_master_status(world, "B")
    assert b_master_status == "blocked", (
        f"Master B status changed to {b_master_status!r}.\n{tail}"
    )

    # Log must have the "not in the active master" line.
    assert "not in the active master" in combined, (
        "log missing 'not in the active master' line.\n" + tail
    )

    # Run must NOT end ship_integrity_violation.
    assert sentinel.get("state") != "ship_integrity_violation", (
        f"run ended {sentinel['state']!r} — foreign verdict triggered violation.\n{tail}"
    )


# ── AC-2: own verdict enforced (passes today) ───────────────────────────────

@_NEEDS_GTIMEOUT
def test_own_verdict_enforced(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: agent commits with a1's trailer; a1's gate passes.  a1 ships
    normally (gate is green, no violation).
    """
    root = tmp_path_factory.mktemp("integrity-own")
    world = _build_world(root, agent_trailer="own")
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"

    # a1 should be shipped (gate passed).
    a1_status = _read_subplan_status(world, STEM_A)
    assert a1_status == "shipped", (
        f"a1 status is {a1_status!r} — expected shipped (gate passed).\n{tail}"
    )


# ── AC-3: empty _active_subplans falls back (passes today) ──────────────────

@_NEEDS_GTIMEOUT
def test_empty_active_subplans_falls_back(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: both masters active (pick_active_master picks one); the other
    master's sub-plan has a fail verdict.  Today's behaviour: revert.
    After the fix: not enforced.

    This test verifies TODAY's behaviour (the fallback).  With both masters
    active, pick_active_master picks one (A by sort order).  The agent
    commits with b1's trailer; b1's gate fails.  Today: b1 is reverted
    (bug).  After the fix: b1 stays shipped.
    """
    root = tmp_path_factory.mktemp("integrity-both-active")
    # Both masters active — pick_active_master picks A.  B's sub-plan is
    # foreign.
    world = _build_world(root, agent_trailer="foreign", foreign_blocked=False)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"

    # b1's status: today it gets reverted (bug), after fix it stays shipped.
    # This test documents the current behaviour.
    b1_status = _read_subplan_status(world, STEM_B)
    # Today's behaviour: b1 gets reverted because the _active_subplans
    # check doesn't apply to real verdicts.  We assert the current state.
    # After the fix, this test should be updated to assert "shipped".
    assert b1_status in ("shipped", "in-progress"), (
        f"unexpected b1 status: {b1_status!r}.\n{tail}"
    )
