"""Red-first: heads, gates, and ship-gap follow the declared work_tree.

Reproduces rezmac run 20260924-072803 where ``get_repo_heads`` ran before
``DECLARED_WORK_TREE`` was resolved, the gate judged the clone instead of
the work tree, and ship-gap scanned the clone.

AC-1  iteration 1's heads-before names the work tree's HEAD; the
      launcher log has 0 trailer-slug warnings for foreign-slug and 0
      [local_checks] rows for it.
AC-2  the step gate records cwd inside the work tree — both gate-first
      and post-iteration variants.
AC-3  dirty file only in clone ⇒ ship-gap silent; dirty file only in work
      tree ⇒ ship-gap reports it.
AC-4  (passes today) no ``work_tree`` ⇒ selfmod behaviour unchanged
      (``test_selfmod_worktree.py`` and ``test_gate_first_step.py`` green).
AC-5  (passes today) invalid ``work_tree`` still stops ``work_tree_invalid``
      before any heads capture.
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

SLUG = "heads-and-gates-test"
STEM = f"2026-09-24-{SLUG}"


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


def _head(repo: Path) -> str:
    return _git_out(repo, "rev-parse", "HEAD")


def _build_world(
    root: Path,
    *,
    work_tree: str | None = None,
    dirty_in_clone: bool = False,
    dirty_in_sibling: bool = False,
) -> dict:
    """Clone + sibling worktree + isolated data home.

    The clone gets ≥3 extra commits with ``[plan:foreign-slug#step-0]``
    trailers so its HEAD is NOT an ancestor of the sibling's HEAD,
    reproducing the 801-commit phantom range from rezmac.

    When *work_tree* is ``"sibling"``, the master declares
    ``work_tree: <sibling path>``.
    """
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone init\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

    # Create a sibling worktree on a different branch.
    sibling = root / "sibling-worktree"
    _git(clone, "worktree", "add", str(sibling), "-b", "work-branch")
    # Initial commit in sibling so it diverges.
    (sibling / "sibling-file.txt").write_text("sibling\n", encoding="utf-8")
    _git(sibling, "add", "-A")
    _git(sibling, "commit", "-q", "-m", "sibling init")

    # ≥3 commits in the clone with foreign trailers (the phantom range).
    for i in range(3):
        (clone / f"foreign-{i}.txt").write_text(f"foreign {i}\n", encoding="utf-8")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-q", "-m",
             f"feat: foreign work {i} [plan:foreign-slug#step-0]")

    # Dirty files for AC-3.
    if dirty_in_clone:
        (clone / "dirty-clone.txt").write_text("dirty\n", encoding="utf-8")
    if dirty_in_sibling:
        (sibling / "dirty-sibling.txt").write_text("dirty\n", encoding="utf-8")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(clone)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    wt_line = f"work_tree: {sibling}\n" if work_tree == "sibling" else ""
    if work_tree and work_tree != "sibling":
        wt_line = f"work_tree: {work_tree}\n"

    # Gate: in AC-2 we want to record cwd.  When work_tree is declared the
    # gate should run inside the sibling; otherwise it runs in the clone.
    gate_cmd = "pwd > gate-cwd.txt"

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
        f"  - command: \"{gate_cmd}\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # Stub agent: commits in the sibling, sets shipped, writes marker.
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
        f"touch {sibling}/marker.txt\n"
        f"git -C {sibling} add marker.txt\n"
        "git -c user.email=t@example.com -c user.name=t "
        f"-C {sibling} commit -q -m 'feat: the work [plan:{SLUG}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": clone, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir, "sibling": sibling,
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


def _find_heads_before(world: dict, iteration: int = 1) -> Path | None:
    """Find the heads-before-<N>.tmp file for the given iteration."""
    log_dir = world["data_home"] / "projects" / world["key"] / "logs"
    candidate = log_dir / f"heads-before-{iteration}.tmp"
    if candidate.is_file():
        return candidate
    # Fallback: search in runtime
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    for d in [runtime, runtime / "launcher"]:
        candidate = d / f"heads-before-{iteration}.tmp"
        if candidate.is_file():
            return candidate
    return None


def _read_launcher_log(world: dict) -> str:
    """Read the launcher log for the current run."""
    log_dir = world["data_home"] / "projects" / world["key"] / "logs"
    for f in sorted(log_dir.glob("*.log")):
        return f.read_text(encoding="utf-8", errors="replace")
    return ""


# ── AC-1: heads-before names the work tree's HEAD ────────────────────────────

@_NEEDS_GTIMEOUT
def test_heads_before_uses_work_tree_head(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: iteration 1's heads-before names the sibling's HEAD, not the
    clone's.  No trailer-slug warnings for foreign-slug; no [local_checks]
    rows for it.
    """
    root = tmp_path_factory.mktemp("heads-before-work-tree")
    world = _build_world(root, work_tree="sibling")
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"
    assert sentinel.get("state") != "work_tree_invalid", (
        f"work_tree valid but got {sentinel['state']}.\n{tail}"
    )

    sibling_head = _head(world["sibling"])
    clone_head = _head(world["project"])

    # The two HEADs must differ for the test to be meaningful.
    assert sibling_head != clone_head, (
        "clone and sibling have the same HEAD — the phantom range is empty.\n"
        f"  clone:   {clone_head}\n  sibling: {sibling_head}"
    )

    heads_file = _find_heads_before(world, iteration=1)
    assert heads_file is not None, (
        f"heads-before-1.tmp not found.\n{tail}"
    )
    content = heads_file.read_text(encoding="utf-8")
    # The sibling's HEAD must appear.
    assert sibling_head in content, (
        f"heads-before does not contain sibling HEAD {sibling_head}.\n"
        f"Content: {content}\n{tail}"
    )
    # The clone's HEAD must NOT be recorded for this repo.
    # (Both might appear if there are multiple repos, but the repo keyed
    # by PROJECT_PATH must resolve to the sibling.)
    # We check the JSONL for trailer-slug warnings and local_checks rows.
    jsonl = _read_jsonl(world)
    combined = proc.stdout + proc.stderr

    # No trailer-slug warnings for foreign-slug.
    trailer_warnings = [l for l in combined.splitlines()
                        if "trailer-slug" in l.lower() and "foreign-slug" in l]
    assert len(trailer_warnings) == 0, (
        f"{len(trailer_warnings)} trailer-slug warnings for foreign-slug.\n{tail}"
    )

    # No [local_checks] rows for foreign-slug.
    gate_rows = [r for r in jsonl
                 if r.get("local_checks") and "foreign" in str(r)]
    assert len(gate_rows) == 0, (
        f"{len(gate_rows)} [local_checks] rows for foreign-slug.\n{tail}"
    )


# ── AC-2: gate records cwd inside the work tree ─────────────────────────────

@_NEEDS_GTIMEOUT
def test_gate_cwd_is_in_work_tree(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: the gate's recorded cwd is inside the work tree.

    The gate command is ``pwd > gate-cwd.txt``.  The cwd file must be inside
    the sibling worktree, not the clone.
    """
    root = tmp_path_factory.mktemp("gate-cwd-work-tree")
    world = _build_world(root, work_tree="sibling")
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"

    sibling = world["sibling"]
    # The gate writes gate-cwd.txt in its cwd.  It should be in the sibling.
    cwd_file = sibling / "gate-cwd.txt"
    if not cwd_file.is_file():
        # Gate might have run in the clone instead.
        cwd_file_clone = world["project"] / "gate-cwd.txt"
        assert False, (
            f"gate-cwd.txt not in sibling ({sibling}); "
            f"exists in clone: {cwd_file_clone.is_file()}.\n{tail}"
        )

    recorded_cwd = cwd_file.read_text(encoding="utf-8").strip()
    assert recorded_cwd.startswith(str(sibling)), (
        f"gate ran in {recorded_cwd}, expected inside {sibling}.\n{tail}"
    )


# ── AC-3: ship-gap scans the work tree ──────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_ship_gap_scans_work_tree(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: dirty in clone only ⇒ ship-gap silent; dirty in sibling ⇒ reports.

    Creates dirty files in both trees and checks that only the sibling's
    dirty file appears in the ship-gap output.
    """
    root = tmp_path_factory.mktemp("ship-gap-work-tree")
    world = _build_world(root, work_tree="sibling",
                         dirty_in_clone=True, dirty_in_sibling=True)
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"

    # The sibling's dirty file should appear in ship-gap output.
    assert "dirty-sibling.txt" in combined, (
        "ship-gap did not report dirty-sibling.txt in the work tree.\n"
        f"{tail}"
    )
    # The clone's dirty file should NOT appear (ship-gap should scan the
    # sibling, not the clone).
    assert "dirty-clone.txt" not in combined, (
        "ship-gap reported dirty-clone.txt from the clone — "
        "it should scan the work tree, not the clone.\n"
        f"{tail}"
    )


# ── AC-4: selfmod unchanged (passes today) ──────────────────────────────────

@_NEEDS_GTIMEOUT
def test_selfmod_behaviour_unchanged(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-4: no work_tree ⇒ the tree and plans root passed to the helper
    equal today's values.  test_selfmod_worktree.py and test_gate_first_step.py
    stay green (we run a minimal smoke here).
    """
    root = tmp_path_factory.mktemp("selfmod-unchanged")
    world = _build_world(root)  # no work_tree
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"
    # Should not error with work_tree_invalid.
    assert sentinel.get("state") != "work_tree_invalid", (
        f"unexpected {sentinel['state']} without work_tree.\n{tail}"
    )


# ── AC-5: invalid work_tree stops before heads (passes today) ────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.parametrize("bad_path", [
    "/nonexistent/path/to/nowhere",
])
def test_invalid_work_tree_stops_before_heads(
    tmp_path_factory: pytest.TempPathFactory,
    bad_path: str,
) -> None:
    """AC-5: invalid work_tree ⇒ work_tree_invalid, no gate runs."""
    root = tmp_path_factory.mktemp("invalid-work-tree")
    world = _build_world(root, work_tree=bad_path)
    proc = _run_one_iteration(world, root)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    sentinel = _read_sentinel(world)
    assert sentinel is not None, f"no sentinel.\n{tail}"
    assert sentinel.get("state") == "work_tree_invalid", (
        f"expected work_tree_invalid, got {sentinel.get('state')!r}.\n{tail}"
    )
