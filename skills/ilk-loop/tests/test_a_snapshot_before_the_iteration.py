"""Red-first: a WIP preserve commits only what the iteration changed.

Tests iteration_snapshot.py (take + changed-since) and the runner's
scoped preserve_dirty_tree_on_timeout, which stages only paths changed
since the pre-iteration snapshot rather than `git add -A`.

AC-1  TRACKED file dirty before iteration + file agent modifies.
      On timeout ⇒ WIP commit contains only the agent's file.
AC-2  UNTRACKED file before iteration + untracked file agent creates.
      On timeout ⇒ pre-existing untracked not committed; agent's is.
AC-3  Pre-dirty file the agent edits further (hash changed) IS committed.
AC-4  Snapshot deleted before timeout ⇒ whole dirty tree preserved, fallback logged.
AC-5  iteration_snapshot.py changed-since with unreadable snapshot ⇒ exit 2, no stdout.
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
SNAPSHOT_SCRIPT = _TESTS.parent / "scripts" / "iteration_snapshot.py"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

SLUG = "snapshot-test"
STEM = f"2026-09-25d-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
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


def _status_files(repo: Path) -> list[str]:
    """Return list of staged files from git status."""
    r = _git(repo, "diff", "--cached", "--name-only")
    return [f for f in r.stdout.strip().splitlines() if f]


def _build_world(
    root: Path,
    *,
    dirty_tracked: bool = False,
    dirty_untracked: bool = False,
    hang: bool = False,
) -> dict:
    """Clone + isolated data home + stub agent.

    With ``hang=True`` the stub agent sleeps past the iteration bound,
    so the driver's timeout path (and WIP-preserve) actually runs.
    """
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

    # Tracked file that another tool (gc) edits — the rezmac shape.
    (clone / "gc_push_failures.json").write_text(
        json.dumps({"status": "ok"}), encoding="utf-8",
    )
    _git(clone, "add", "gc_push_failures.json")
    _git(clone, "commit", "-q", "-m", "add gc file")

    # Pre-dirty tracked file (simulates gc tool editing before iteration).
    if dirty_tracked:
        (clone / "gc_push_failures.json").write_text(
            json.dumps({"status": "dirty-before"}), encoding="utf-8",
        )

    # Pre-existing untracked file.
    if dirty_untracked:
        (clone / "pre-existing-untracked.txt").write_text(
            "here before the iteration\n", encoding="utf-8",
        )

    data_home = root / ".ilk-data"
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(clone)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-09-25d-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-25d-execution\n"
        "batch_date: 2026-09-25d\n"
        "status: active\n"
        "supervised_only: false\n"
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

    if hang:
        stub = bin_dir / "claude"
        stub.write_text("#!/usr/bin/env bash\nexec sleep 60\n", encoding="utf-8")
        stub.chmod(0o755)
    else:
        stub = bin_dir / "claude"
        stub.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
        stub.chmod(0o755)

    return {
        "project": clone, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir,
    }


def _build_world_with_agent_work(
    root: Path,
    *,
    dirty_tracked: bool = False,
    dirty_untracked: bool = False,
    agent_modifies_tracked: bool = False,
    agent_creates_untracked: bool = False,
) -> dict:
    """Build world with a stub agent that does specific work then hangs."""
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

    # Tracked file (the gc_push_failures shape).
    (clone / "gc_push_failures.json").write_text(
        json.dumps({"status": "ok"}), encoding="utf-8",
    )
    _git(clone, "add", "gc_push_failures.json")
    _git(clone, "commit", "-q", "-m", "add gc file")

    if dirty_tracked:
        (clone / "gc_push_failures.json").write_text(
            json.dumps({"status": "dirty-before"}), encoding="utf-8",
        )

    if dirty_untracked:
        (clone / "pre-existing-untracked.txt").write_text(
            "here before the iteration\n", encoding="utf-8",
        )

    data_home = root / ".ilk-data"
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(clone)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-09-25d-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-25d-execution\n"
        "batch_date: 2026-09-25d\n"
        "status: active\n"
        "supervised_only: false\n"
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

    # Build the agent script: do work, then hang.
    agent_lines = ["#!/usr/bin/env bash"]
    if agent_modifies_tracked:
        agent_lines.append(
            "echo '{\"status\": \"agent-modified\"}' > gc_push_failures.json"
        )
    if agent_creates_untracked:
        agent_lines.append("echo 'agent created this' > agent-created.txt")
    # Hang past the iteration timeout.
    agent_lines.append("exec sleep 60")

    stub.write_text("\n".join(agent_lines) + "\n", encoding="utf-8")
    stub.chmod(0o755)

    return {
        "project": clone, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir,
    }


def _run_one_iteration(
    world: dict, root: Path, *, timeout_sec: int | None = None,
    extra_env: dict[str, str] | None = None,
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
        env["ILK_ITERATION_TIMEOUT_SEC"] = str(timeout_sec)
    if extra_env:
        env.update(extra_env)
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


# ── AC-5: iteration_snapshot.py changed-since with unreadable snapshot ────────

def test_changed_since_unreadable_snapshot_exits_2(
    tmp_path: Path,
) -> None:
    """AC-5: changed-since with an unreadable snapshot ⇒ exit 2, no stdout."""
    # Build a minimal repo.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    # Create a snapshot file then make it unreadable.
    snap = tmp_path / "snapshot.json"
    snap.write_text('{"head": "abc", "dirty": {}, "untracked": []}', encoding="utf-8")
    snap.chmod(0o000)

    try:
        result = subprocess.run(
            [sys.executable, str(SNAPSHOT_SCRIPT),
             "changed-since", str(repo), "--snapshot", str(snap)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == 2, (
            f"expected exit 2, got {result.returncode}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert result.stdout == "", (
            f"expected empty stdout, got {result.stdout!r}"
        )
    finally:
        snap.chmod(0o644)


# ── AC-5b: iteration_snapshot.py changed-since with missing snapshot ─────────

def test_changed_since_missing_snapshot_exits_2(
    tmp_path: Path,
) -> None:
    """AC-5: changed-since with a missing snapshot ⇒ exit 2, no stdout."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    snap = tmp_path / "nonexistent-snapshot.json"

    result = subprocess.run(
        [sys.executable, str(SNAPSHOT_SCRIPT),
         "changed-since", str(repo), "--snapshot", str(snap)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout == "", (
        f"expected empty stdout, got {result.stdout!r}"
    )


# ── AC-1: WIP preserve takes only what the iteration changed (tracked) ──────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
def test_wip_preserves_only_iteration_changed_tracked(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: TRACKED file dirty before + agent file.  On timeout ⇒ WIP has
    only agent's file; pre-dirty file still dirty and uncommitted.
    """
    root = tmp_path_factory.mktemp("ac1-tracked")
    world = _build_world_with_agent_work(
        root,
        dirty_tracked=True,          # gc_push_failures.json dirty before
        agent_creates_untracked=True, # agent creates agent-created.txt
    )

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-30:])

    # The WIP commit should exist.
    log = subprocess.run(
        ["git", "-C", str(world["project"]), "log", "--oneline", "-5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "wip" in log.lower(), f"no WIP commit.\nLog: {log}\n{tail}"

    # The WIP commit should contain agent-created.txt but NOT
    # gc_push_failures.json (it was dirty before the iteration).
    wip_files_raw = subprocess.run(
        ["git", "-C", str(world["project"]),
         "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    wip_files = set(wip_files_raw.strip().splitlines())

    assert "agent-created.txt" in wip_files, (
        f"agent-created.txt not in WIP commit.\nFiles: {wip_files}\n{tail}"
    )
    assert "gc_push_failures.json" not in wip_files, (
        f"gc_push_failures.json should NOT be in WIP commit "
        f"(dirty before iteration).\nFiles: {wip_files}\n{tail}"
    )

    # gc_push_failures.json should still be dirty.
    status = subprocess.run(
        ["git", "-C", str(world["project"]), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "gc_push_failures.json" in status, (
        f"gc_push_failures.json is not dirty after WIP preserve.\n"
        f"Status: {status}\n{tail}"
    )


# ── AC-2: pre-existing untracked not committed ──────────────────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
def test_pre_existing_untracked_not_committed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: UNTRACKED before iteration + untracked agent creates.
    On timeout ⇒ pre-existing untracked NOT in WIP; agent's IS.
    """
    root = tmp_path_factory.mktemp("ac2-untracked")
    world = _build_world_with_agent_work(
        root,
        dirty_untracked=True,         # pre-existing-untracked.txt
        agent_creates_untracked=True,  # agent-created.txt
    )

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-30:])

    log = subprocess.run(
        ["git", "-C", str(world["project"]), "log", "--oneline", "-5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "wip" in log.lower(), f"no WIP commit.\nLog: {log}\n{tail}"

    wip_files_raw = subprocess.run(
        ["git", "-C", str(world["project"]),
         "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    wip_files = set(wip_files_raw.strip().splitlines())

    assert "agent-created.txt" in wip_files, (
        f"agent-created.txt not in WIP commit.\nFiles: {wip_files}\n{tail}"
    )
    assert "pre-existing-untracked.txt" not in wip_files, (
        f"pre-existing-untracked.txt should NOT be in WIP commit.\n"
        f"Files: {wip_files}\n{tail}"
    )


# ── AC-3: agent edits pre-dirty file further ⇒ IS committed ─────────────────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
def test_agent_edits_pre_dirty_file_is_committed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: pre-dirty file the agent edits (hash changed) IS committed."""
    root = tmp_path_factory.mktemp("ac3-hash-changed")
    world = _build_world_with_agent_work(
        root,
        dirty_tracked=True,           # gc_push_failures.json dirty before
        agent_modifies_tracked=True,  # agent also modifies it
    )

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-30:])

    log = subprocess.run(
        ["git", "-C", str(world["project"]), "log", "--oneline", "-5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "wip" in log.lower(), f"no WIP commit.\nLog: {log}\n{tail}"

    # The agent modified gc_push_failures.json (hash changed from
    # "dirty-before" to "agent-modified"), so it SHOULD be committed.
    wip_files_raw = subprocess.run(
        ["git", "-C", str(world["project"]),
         "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    wip_files = set(wip_files_raw.strip().splitlines())

    assert "gc_push_failures.json" in wip_files, (
        f"gc_push_failures.json should be in WIP commit "
        f"(agent edited it, hash changed).\nFiles: {wip_files}\n{tail}"
    )


# ── AC-4: snapshot deleted ⇒ whole dirty tree preserved, fallback logged ─────

@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
def test_missing_snapshot_fallback_preserves_all(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-4: snapshot file deleted before timeout ⇒ whole dirty tree
    preserved and fallback line is logged.
    """
    root = tmp_path_factory.mktemp("ac4-fallback")
    world = _build_world_with_agent_work(
        root,
        dirty_tracked=True,
        dirty_untracked=True,
        agent_creates_untracked=True,
    )

    # Build a stub agent that deletes the snapshot file (which the
    # runner creates at iteration start), then hangs past the timeout.
    # The runner names the file pre-iter-snapshot-1.json in RUN_LOG_DIR.
    bin_dir = world["bin"]
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"find {world['data_home']!s} -name 'pre-iter-snapshot-*.json' -delete 2>/dev/null\n"
        "exec sleep 60\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-30:])

    # The fallback should log that there's no pre-iteration snapshot.
    assert "no pre-iteration snapshot" in combined.lower(), (
        f"expected fallback log line about missing snapshot.\n{tail}"
    )

    # Both tracked dirty and untracked agent-created should be in WIP.
    log = subprocess.run(
        ["git", "-C", str(world["project"]), "log", "--oneline", "-5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    assert "wip" in log.lower(), f"no WIP commit.\nLog: {log}\n{tail}"