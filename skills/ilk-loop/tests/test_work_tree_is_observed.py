"""Red-first: a master's declared work_tree is observed, gated, and ledgered.

On rezmac 20260923-150625 gh-resolve launched the loop with
``--project-path`` = the resolver clone, while the worker committed in a
sibling ``git worktree``.  The driver looked only at ``--project-path``, so
it saw ``new commits: 0``, ran the gate in the wrong tree, and reported
``gate record unreadable``.

AC-1  (xfail) e2e: clone + sibling worktree; master declares
      ``work_tree: <sibling>``; fake worker commits in the sibling.
      ⇒ ``new_commits_total >= 1``, a ``ship-proof.jsonl`` row, gate pass.
AC-2  (unmarked, passes today) ``work_tree`` absent ⇒ today's behaviour
      (``new_commits_total: 0``, no ledger row).
AC-3  (xfail) ``work_tree`` pointing at non-existent path or unrelated repo
      ⇒ stop reason ``work_tree_invalid``, terminal sentinel, stderr names
      the path; no gate runs.
"""
from __future__ import annotations

import json
import os
import re
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

SLUG = "a-subplan-with-work-tree"
STEM = f"2026-09-23-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _git_out(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.stdout.strip()


def _build_world(root: Path, *, work_tree: str | None = None) -> dict:
    """A clone repo + optional sibling worktree + isolated data home.

    When *work_tree* is given, it is written into the master frontmatter as
    ``work_tree: <value>``.
    """
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

    # Create a sibling worktree on a different branch so commits there
    # are distinguishable from the clone's HEAD.
    sibling = root / "sibling-worktree"
    _git(clone, "worktree", "add", str(sibling), "-b", "work-branch")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(clone)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    wt_line = f"work_tree: {sibling}\n" if work_tree == "sibling" else ""
    if work_tree and work_tree != "sibling":
        wt_line = f"work_tree: {work_tree}\n"

    # The gate checks the sibling when work_tree is declared (the worker
    # commits there).  When work_tree is absent the gate is a trivial pass
    # (the worker commits in the sibling but the driver observes the clone;
    # the gate must not fail or ship_integrity fires and muddles the test).
    gate_target = sibling if work_tree else None

    (plans / "MASTER-2026-09-23-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-23-execution\n"
        "batch_date: 2026-09-23\n"
        "status: active\n"
        "supervised_only: false\n"
        f"{wt_line}"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )
    gate_cmd = (
        f"test -f {gate_target}/marker.txt"
        if gate_target is not None
        else "true"
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
        f"  - command: \"{gate_cmd}\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # The stub agent always commits in the sibling worktree (simulating
    # gh-resolve's per-issue worktree).  When work_tree is declared the
    # driver observes the sibling; when absent it observes the clone and
    # misses the commit — that is the back-compat AC-2 tests.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{STEM}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        # Create the marker file in the sibling.
        f"touch {sibling}/marker.txt\n"
        f"git -C {sibling} add marker.txt\n"
        "git -c user.email=t@example.com -c user.name=t "
        f"-C {sibling} commit -q -m 'feat: the work [plan:{SLUG}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {"project": clone, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir, "sibling": sibling}


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


def _read_jsonl(world: dict) -> list[dict]:
    """Read the per-iteration JSONL log."""
    log_dir = world["data_home"] / "projects" / world["key"] / "logs"
    log_file = log_dir / ".ilk-loop.log"
    rows = []
    if log_file.is_file():
        for line in log_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def _read_ship_proof(world: dict) -> list[dict]:
    """Read ship-proof.jsonl if present."""
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    proof = runtime / "launcher" / "ship-proof.jsonl"
    if not proof.is_file():
        return []
    rows = []
    for line in proof.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


# ── AC-1: declared work_tree is observed and gated (xfail) ──────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first: work_tree not read")
def test_declared_work_tree_is_observed_and_gated(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: clone + sibling worktree; master declares work_tree; fake worker
    commits in the sibling.  ⇒ new_commits_total >= 1, ship-proof row, gate pass.
    """
    root = tmp_path_factory.mktemp("work-tree-observed")
    world = _build_world(root, work_tree="sibling")
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel written.\n{tail}"
    assert sentinel.get("state") != "work_tree_invalid", (
        f"work_tree was declared valid but got {sentinel['state']}.\n{tail}"
    )

    jsonl = _read_jsonl(world)
    iter_rows = [r for r in jsonl if r.get("iteration") is not None]
    assert iter_rows, f"no iteration rows in JSONL.\n{tail}"
    last = iter_rows[-1]
    assert last.get("new_commits_total", 0) >= 1, (
        f"new_commits_total={last.get('new_commits_total')}; "
        f"the worker committed in the sibling but the driver looked at the clone.\n{tail}"
    )

    proof = _read_ship_proof(world)
    assert proof, f"no ship-proof.jsonl rows.\n{tail}"
    slugs_in_proof = [r.get("slug") for r in proof]
    assert SLUG in slugs_in_proof, (
        f"slug {SLUG!r} not in ship-proof: {slugs_in_proof}.\n{tail}"
    )


# ── AC-2: work_tree absent = today's behaviour (passes today) ───────────────

@_NEEDS_GTIMEOUT
def test_absent_work_tree_uses_clone(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: work_tree absent ⇒ new_commits_total: 0, no ledger row."""
    root = tmp_path_factory.mktemp("work-tree-absent")
    world = _build_world(root)  # no work_tree
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel written.\n{tail}"

    jsonl = _read_jsonl(world)
    iter_rows = [r for r in jsonl if r.get("iteration") is not None]
    assert iter_rows, f"no iteration rows in JSONL.\n{tail}"
    last = iter_rows[-1]
    # Without work_tree, the driver looks at the clone where the worker
    # did NOT commit, so new_commits_total should be 0.
    assert last.get("new_commits_total", 0) == 0, (
        f"new_commits_total={last.get('new_commits_total')}; "
        f"expected 0 when work_tree is absent.\n{tail}"
    )

    proof = _read_ship_proof(world)
    # No ledger row expected when new_commits_total is 0.
    slugs_in_proof = [r.get("slug") for r in proof]
    assert SLUG not in slugs_in_proof, (
        f"unexpected ship-proof row for {SLUG!r} when work_tree is absent.\n{tail}"
    )


# ── AC-3: invalid work_tree ⇒ work_tree_invalid (xfail) ────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first: work_tree not read")
@pytest.mark.parametrize("bad_path", [
    "/nonexistent/path/to/nowhere",
    pytest.param("relative/path", id="relative"),
])
def test_invalid_work_tree_stops_the_run(
    tmp_path_factory: pytest.TempPathFactory,
    bad_path: str,
) -> None:
    """AC-3: work_tree pointing at non-existent or unrelated repo ⇒
    stop reason work_tree_invalid, terminal sentinel, stderr names the path.
    """
    root = tmp_path_factory.mktemp("work-tree-invalid")
    world = _build_world(root, work_tree=bad_path)
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel written.\n{tail}"
    assert sentinel.get("state") == "work_tree_invalid", (
        f"expected work_tree_invalid, got {sentinel.get('state')!r}.\n{tail}"
    )

    # stderr should name the bad path
    combined = proc.stdout + proc.stderr
    assert bad_path in combined or "work_tree" in combined.lower(), (
        f"stderr does not name the bad path {bad_path!r}.\n{tail}"
    )

    # No gate should have run.
    jsonl = _read_jsonl(world)
    gate_rows = [r for r in jsonl if r.get("local_checks")]
    assert not gate_rows, (
        f"gate ran despite invalid work_tree: {gate_rows}.\n{tail}"
    )
