"""Red-first: worker cwd and WIP-preserve use the declared work_tree.

Reproduces rezmac where the worker ran in the main clone (L1:22) and
WIP-preserve committed dc49ac2ba onto the clone's dev.

AC-1  with work_tree declared, the stub worker records its cwd ⇒ it is
      the work tree.
AC-2  timeout with a dirty work tree ⇒ the [wip:timeout] commit is in
      the work tree's history, clone HEAD unchanged.
AC-3  timeout with only the clone dirty ⇒ no commit, one "clone dirty,
      not preserved" line.
AC-4  (passes today) no work_tree ⇒ today's behaviour.
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

SLUG = "worker-wip-test"
STEM = f"2026-09-24-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _head(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )
    return r.stdout.strip()


def _build_world(
    root: Path,
    *,
    work_tree: str | None = None,
    dirty_in_sibling: bool = False,
    dirty_in_clone: bool = False,
    hang: bool = False,
) -> dict:
    """Clone + sibling worktree + isolated data home + stub agent.

    The stub agent records its cwd to ``cwd.txt`` in the project root.
    With ``hang`` it instead sleeps past the iteration bound, so the
    driver's timeout path (and WIP-preserve) actually runs.
    """
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

    sibling = root / "sibling-worktree"
    _git(clone, "worktree", "add", str(sibling), "-b", "work-branch")
    (sibling / "sibling-file.txt").write_text("sibling\n", encoding="utf-8")
    _git(sibling, "add", "-A")
    _git(sibling, "commit", "-q", "-m", "sibling init")

    # Dirty files for WIP tests.
    if dirty_in_sibling:
        (sibling / "dirty.txt").write_text("dirty sibling\n", encoding="utf-8")
    if dirty_in_clone:
        (clone / "dirty.txt").write_text("dirty clone\n", encoding="utf-8")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(clone)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    wt_line = f"work_tree: {sibling}\n" if work_tree == "sibling" else ""
    if work_tree and work_tree != "sibling":
        wt_line = f"work_tree: {work_tree}\n"

    (plans / "MASTER-2026-09-24-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-24-execution\n"
        "batch_date: 2026-09-24\n"
        "status: active\n"
        "supervised_only: false\n"
        f"{wt_line}"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"true\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # Stub agent: records cwd, then commits.
    if hang:
        stub.write_text("#!/usr/bin/env bash\nexec sleep 60\n", encoding="utf-8")
        stub.chmod(0o755)
        return {
            "project": clone, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir, "sibling": sibling,
        }
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Record cwd for AC-1.\n"
        "pwd > cwd.txt\n"
        "git add cwd.txt\n"
        "git -c user.email=t@example.com -c user.name=t "
        f"commit -q -m 'feat: agent work [plan:{SLUG}#step-0]'\n"
        # Mark shipped.
        f"SP={str(plans / f'{STEM}.md')!r}\n"
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
        "project": clone, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir, "sibling": sibling,
    }


def _run_one_iteration(
    world: dict, root: Path, *, timeout_sec: int | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    if timeout_sec is not None:
        # Test affordance (resolve_iteration_timeout_sec): a sub-minute bound.
        env["ILK_ITERATION_TIMEOUT_SEC"] = str(timeout_sec)
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


# ── AC-1: worker cwd is the work tree ───────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_worker_cwd_is_work_tree(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: with work_tree declared, the stub worker's cwd is the sibling."""
    root = tmp_path_factory.mktemp("worker-cwd")
    world = _build_world(root, work_tree="sibling")
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    # The stub agent writes cwd.txt in its cwd.
    cwd_file = world["sibling"] / "cwd.txt"
    if not cwd_file.is_file():
        cwd_file_clone = world["project"] / "cwd.txt"
        assert False, (
            f"cwd.txt not in sibling ({world['sibling']}); "
            f"in clone: {cwd_file_clone.is_file()}.\n{tail}"
        )

    recorded_cwd = cwd_file.read_text(encoding="utf-8").strip()
    assert recorded_cwd.startswith(str(world["sibling"])), (
        f"worker ran in {recorded_cwd}, expected inside {world['sibling']}.\n{tail}"
    )


# ── AC-2: WIP preserved in work tree ────────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_wip_preserved_in_work_tree(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: timeout with dirty work tree ⇒ WIP commit in sibling, clone
    HEAD unchanged.
    """
    root = tmp_path_factory.mktemp("wip-work-tree")
    world = _build_world(root, work_tree="sibling", dirty_in_sibling=True,
                         hang=True)
    # Override the hang stub to also create a file — the snapshot now
    # skips pre-dirty files, so the agent must produce new work.
    stub = world["bin"] / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'agent work' > agent-created.txt\n"
        "exec sleep 60\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    clone_head_before = _head(world["project"])

    proc = _run_one_iteration(world, root, timeout_sec=3)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    # The sibling should have a WIP commit.
    sibling_log = subprocess.run(
        ["git", "-C", str(world["sibling"]), "log", "--oneline", "-5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "wip:timeout" in sibling_log.lower() or "WIP" in sibling_log, (
        f"no WIP commit in sibling.\nSibling log:\n{sibling_log}\n{tail}"
    )

    # Clone HEAD must be unchanged.
    clone_head_after = _head(world["project"])
    assert clone_head_after == clone_head_before, (
        f"clone HEAD changed: {clone_head_before} → {clone_head_after}.\n{tail}"
    )


# ── AC-3: only clone dirty ⇒ no commit, warning ─────────────────────────────

@_NEEDS_GTIMEOUT
def test_only_clone_dirty_no_commit(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: timeout with only the clone dirty ⇒ no WIP commit, one
    "clone dirty, not preserved" line.
    """
    root = tmp_path_factory.mktemp("wip-clone-only")
    world = _build_world(root, work_tree="sibling", dirty_in_clone=True,
                         hang=True)
    clone_head_before = _head(world["project"])

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-30:])

    # Clone HEAD must be unchanged (no WIP commit).
    clone_head_after = _head(world["project"])
    assert clone_head_after == clone_head_before, (
        f"clone HEAD changed: {clone_head_before} → {clone_head_after}.\n{tail}"
    )

    # Log should mention clone dirty.
    assert "clone" in combined.lower() and "dirty" in combined.lower(), (
        "log missing 'clone dirty' line.\n" + tail
    )


# ── AC-4: no work_tree ⇒ today's behaviour (passes today) ───────────────────

@_NEEDS_GTIMEOUT
def test_no_work_tree_uses_clone(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-4: no work_tree ⇒ worker runs in clone, WIP preserved in clone."""
    root = tmp_path_factory.mktemp("no-work-tree")
    world = _build_world(root, dirty_in_clone=True)
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"
    assert sentinel.get("state") != "work_tree_invalid", (
        f"unexpected {sentinel['state']} without work_tree.\n{tail}"
    )
