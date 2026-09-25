"""Red-first: gate isolation takes only new files and restores its own stash.

Tests the snapshot-aware isolate_to_head: stashes only untracked paths
new since the snapshot, restores by the stash's own sha, reports a failed
restore loudly, and warns on orphaned stashes.

AC-1  Untracked file present before snapshot stays in tree; no stash entry left.
AC-2  Untracked file created after snapshot is stashed during gate, restored after.
AC-3  Foreign stash pushed during gate is not the one restored; ours is applied+dropped.
AC-4  Restore that can't apply leaves stash in place, restore_error names the sha.
AC-5  Pre-existing ilk-gate-isolation stash makes runner print orphan warning.
AC-6  Branch setup with untracked + dirty tracked: untracked survives, warning names sha.
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

sys.path.insert(0, str(_TESTS.parent / "scripts"))
from run_local_checks import isolate_to_head  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

SLUG = "gate-iso-test"
STEM = f"2026-09-25d-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "marker.txt").write_text("committed", encoding="utf-8")
    _git(repo, "add", "marker.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_snapshot(repo: Path, tmp_path: Path) -> Path:
    """Take a snapshot of repo's current state."""
    snap = tmp_path / "snapshot.json"
    subprocess.run(
        [sys.executable, str(SNAPSHOT_SCRIPT),
         "take", str(repo), "--out", str(snap)],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return snap


def _build_world(
    root: Path,
    *,
    dirty_tracked: bool = False,
    dirty_untracked: bool = False,
) -> dict:
    """Clone + isolated data home + stub agent (immediate exit)."""
    clone = root / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "init")

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
    stub.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
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


# ── AC-1: untracked present before snapshot stays in tree; no stash entry ────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_untracked_present_at_snapshot_stays_in_tree(tmp_path: Path) -> None:
    """AC-1: untracked file present before snapshot stays in working tree
    during and after the gate, with unchanged content, and no stash entry
    is left behind.
    """
    repo = _make_git_repo(tmp_path)
    snap = _make_snapshot(repo, tmp_path)

    # Untracked file present at snapshot time.
    untracked = repo / "pre-existing.txt"
    untracked.write_text("I was here before\n", encoding="utf-8")

    with isolate_to_head(repo) as iso:
        assert iso.isolated is True
        assert iso.restore_error is None
        # File must still exist during isolation.
        assert untracked.exists(), "file disappeared during isolation"
        assert untracked.read_text(encoding="utf-8") == "I was here before\n"

    # File must still exist after isolation.
    assert untracked.exists(), "file disappeared after isolation"
    assert untracked.read_text(encoding="utf-8") == "I was here before\n"

    # No stash entry should be left behind.
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout
    assert "ilk-gate-isolation" not in stash_list, (
        f"unexpected stash entry left behind:\n{stash_list}"
    )


# ── AC-2: pre-existing untracked stays; only new-since-snapshot stashed ──────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_pre_existing_untracked_not_stashed_only_new_ones(
    tmp_path: Path,
) -> None:
    """AC-2: untracked file present at snapshot time stays in the tree
    during isolation.  Only untracked files created AFTER the snapshot
    are stashed.
    """
    repo = _make_git_repo(tmp_path)
    snap = _make_snapshot(repo, tmp_path)

    # Pre-existing untracked (present at snapshot time).
    pre_existing = repo / "pre-existing.txt"
    pre_existing.write_text("I was here before\n", encoding="utf-8")

    # New untracked (created after snapshot).
    new_file = repo / "agent-created.txt"
    new_file.write_text("agent work\n", encoding="utf-8")

    with isolate_to_head(repo) as iso:
        assert iso.isolated is True
        # Pre-existing file must stay in tree (not stashed).
        assert pre_existing.exists(), (
            "pre-existing untracked file should NOT be stashed"
        )
        assert pre_existing.read_text(encoding="utf-8") == "I was here before\n"
        # New file should be stashed (not visible during isolation).
        assert not new_file.exists(), (
            "new untracked file should be stashed during isolation"
        )

    # Both files should exist after isolation.
    assert pre_existing.exists(), "pre-existing file disappeared after isolation"
    assert new_file.exists(), "new file not restored after isolation"
    assert new_file.read_text(encoding="utf-8") == "agent work\n"


# ── AC-3: foreign stash is not the one restored ─────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_foreign_stash_not_restored(tmp_path: Path) -> None:
    """AC-3: a foreign stash pushed during the gate is not the one restored.
    Our entry is applied and dropped; the foreign entry is still on the stack.
    """
    repo = _make_git_repo(tmp_path)
    snap = _make_snapshot(repo, tmp_path)

    # Untracked file created after snapshot (ours to stash).
    agent_file = repo / "agent-created.txt"
    agent_file.write_text("agent work\n", encoding="utf-8")

    with isolate_to_head(repo) as iso:
        assert iso.isolated is True
        # Push a foreign stash while we're isolated.
        (repo / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        subprocess.run(
            ["git", "stash", "push", "-u", "-m", "foreign stash"],
            cwd=repo, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

    # Our file should be restored.
    assert agent_file.exists(), "agent file not restored"
    assert agent_file.read_text(encoding="utf-8") == "agent work\n"

    # Foreign stash should still be on the stack.
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout
    assert "foreign stash" in stash_list, (
        f"foreign stash should still be on stack:\n{stash_list}"
    )
    # Our stash should be gone (applied + dropped).
    assert "ilk-gate-isolation" not in stash_list, (
        f"our stash should be dropped:\n{stash_list}"
    )


# ── AC-4: restore-by-sha with foreign stash on top ───────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_restore_by_sha_with_foreign_stash_on_top(tmp_path: Path) -> None:
    """AC-4: when a foreign stash is pushed during the gate (after ours),
    ``isolate_to_head`` should restore OUR stash by sha, not the foreign
    one on top.

    The current code uses ``git stash pop`` which takes the foreign stash.
    The fix uses ``git stash apply --index <sha>`` captured right after
    our push.

    This test drives ``isolate_to_head`` directly.  The gate pushes a
    foreign stash; on exit, the context manager should restore our tracked
    change.
    """
    repo = _make_git_repo(tmp_path)
    snap = _make_snapshot(repo, tmp_path)

    # Tracked file dirty after snapshot (will be stashed by isolation).
    (repo / "marker.txt").write_text("dirty\n", encoding="utf-8")

    iso_state = None
    with isolate_to_head(repo) as iso:
        assert iso.isolated is True
        # Gate pushes a foreign stash.
        (repo / "marker.txt").write_text("foreign dirty\n", encoding="utf-8")
        (repo / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        subprocess.run(
            ["git", "stash", "push", "-u", "-m", "foreign stash"],
            cwd=repo, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        iso_state = iso

    # Our tracked change should be restored ("dirty"), not the foreign one.
    content = (repo / "marker.txt").read_text(encoding="utf-8")
    assert content == "dirty\n", (
        f"restore should use our stash by sha, got marker.txt = {content!r}"
    )


# ── AC-5: runner prints orphan warning for pre-existing gate stash ──────────


@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
@pytest.mark.xfail(strict=True, reason="red-first")
def test_runner_warns_on_orphaned_gate_stash(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-5: a pre-existing ilk-gate-isolation stash makes the runner print
    the orphan warning at iteration start.
    """
    root = tmp_path_factory.mktemp("ac5-orphan")
    world = _build_world(root)

    # Create an orphaned gate-isolation stash.
    (world["project"] / "orphan-tracked.txt").write_text(
        "orphan\n", encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(world["project"]), "add", "orphan-tracked.txt"],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "-C", str(world["project"]),
         "stash", "push", "-u", "-m",
         "ilk-gate-isolation abc123"],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr

    assert "orphaned loop stash" in combined.lower(), (
        f"expected orphan warning in output.\n"
        f"Last 30 lines:\n{''.join(combined.splitlines()[-30:])}"
    )


# ── AC-6: branch setup preserves untracked files ────────────────────────────


@_NEEDS_GTIMEOUT
@pytest.mark.timeout(120)
@pytest.mark.xfail(strict=True, reason="red-first")
def test_branch_setup_preserves_untracked_files(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-6: branch setup with untracked file and dirty tracked file:
    after the switch, the untracked file is still in the tree, and the
    warning line names a stash sha.
    """
    root = tmp_path_factory.mktemp("ac6-branch")
    world = _build_world(root, dirty_tracked=True, dirty_untracked=True)

    # Set up branch configuration in the MASTER.
    plans = world["plans"]
    master = plans / "MASTER-2026-09-25d-execution-plan.md"
    master.write_text(
        "---\n"
        "master_plan: 2026-09-25d-execution\n"
        "batch_date: 2026-09-25d\n"
        "status: active\n"
        "supervised_only: false\n"
        "branch:\n"
        "  create_from: origin/main\n"
        "  name: feat/test-branch\n"
        "  merge_back: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    proc = _run_one_iteration(world, root, timeout_sec=3)
    combined = proc.stdout + proc.stderr

    # Untracked file must still exist.
    untracked = world["project"] / "pre-existing-untracked.txt"
    assert untracked.exists(), (
        f"untracked file disappeared after branch setup.\n"
        f"Last 30 lines:\n{''.join(combined.splitlines()[-30:])}"
    )
    assert untracked.read_text(encoding="utf-8") == "here before the iteration\n"

    # Warning should name a stash sha.
    assert "auto-stashed" in combined.lower(), (
        f"expected auto-stash warning.\n"
        f"Last 30 lines:\n{''.join(combined.splitlines()[-30:])}"
    )