"""Red-first pins: a release host reads its head from the release manifest.

Part of sub-plan a-release-host-knows-its-head.  These tests pin the four
acceptance criteria *before* the resolver exists:

  AC-1  Bouncer --check from a release dir prints the RELEASE's sha as HEAD
        and ``tree_state: clean``.  (xfail — no release-aware resolver yet.)
  AC-2  Scheduler state writer from a release dir writes
        ``toolkit_head`` = the manifest's sha.  (xfail — same cause.)
  AC-3  Neither git nor manifest, cwd in a git repo → HEAD empty/unknown
        and ``tree_state: unknown``.  The cwd's repo is NEVER used.
        (xfail — the fallback today is ``git -C .``.)
  AC-4  Inside a git work tree the behaviour is unchanged
        (head = rev-parse, tree state clean/dirty).  Unmarked — should pass.

All tests use tmp dirs; each builds a fake release dir (read-only, with a
manifest) and a tmp git repo.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"
_SCHEDULER_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release_dir(root: Path, sha: str = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2") -> Path:
    """Create a fake release dir with a read-only .ilk-release.json manifest."""
    release_dir = root / "releases" / "v0.9.140"
    release_dir.mkdir(parents=True)
    manifest = {
        "tag": "v0.9.140",
        "sha": sha,
        "source_repo": "https://github.com/inluck-net/ilk-skills",
        "extracted_at": "2026-10-03T12:50:00+00:00",
    }
    manifest_path = release_dir / ".ilk-release.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # Make read-only (matches ilk_release.py behaviour)
    for dirpath, dirnames, filenames in os.walk(release_dir):
        for fn in filenames:
            fp = Path(dirpath) / fn
            fp.chmod(fp.stat().st_mode & ~0o222)
        Path(dirpath).chmod(Path(dirpath).stat().st_mode & ~0o222)
    return release_dir


def _make_git_repo(root: Path) -> Path:
    """Create a minimal git repo with one commit, return its root."""
    repo_dir = root / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    (repo_dir / "README.md").write_text("stub\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"},
    )
    return repo_dir


def _write_state_file(data_home: Path, toolkit_head: str) -> Path:
    """Write a scheduler.state.json with the given toolkit_head."""
    state_file = data_home / "scheduler.state.json"
    state_file.write_text(
        json.dumps({"pid": 12345, "started_at": "2026-10-03T12:00:00Z", "toolkit_head": toolkit_head}),
        encoding="utf-8",
    )
    return state_file


def _write_fake_launchctl(bin_dir: Path) -> Path:
    """Create a minimal fake launchctl that logs argv and exits 0."""
    fake = bin_dir / "launchctl"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _run_bounce_check(env: dict[str, str], cwd: Path, bin_dir: Path | None = None) -> subprocess.CompletedProcess:
    """Run bounce_daemons.sh --check, return the result.

    If *bin_dir* is given, it is prepended to PATH so the fake launchctl
    is found before the real one.
    """
    run_env = dict(env)
    if bin_dir is not None:
        run_env["PATH"] = f"{bin_dir}:{run_env.get('PATH', '')}"
    return subprocess.run(
        [str(_BOUNCE_SH), "--check"],
        cwd=cwd,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# AC-1: Bouncer --check from a release dir reads the RELEASE's sha
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="release-aware resolver not yet implemented")
def test_ac1_bouncer_check_from_release_dir_reads_release_sha(tmp_path):
    """Bouncer --check started from a release dir must print the manifest's sha
    as HEAD and tree_state: clean — regardless of cwd."""
    release_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    release_dir = _make_release_dir(tmp_path, sha=release_sha)
    git_repo = _make_git_repo(tmp_path)  # unrelated git repo

    # Write state file pointing to the release sha.
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir()
    _write_state_file(data_home, release_sha)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_launchctl(bin_dir)

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO_ROOT / "skills"),
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }

    # Run from the unrelated git repo — must NOT use that repo's HEAD.
    result = _run_bounce_check(env, cwd=git_repo, bin_dir=bin_dir)

    assert result.returncode == 0, f"bouncer failed: {result.stderr}"
    assert f"recorded_sha: {release_sha}" in result.stdout, (
        f"expected release sha {release_sha[:12]}… in output, got:\n{result.stdout}"
    )
    assert "tree_state: clean" in result.stdout, (
        f"expected tree_state: clean, got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# AC-2: Scheduler state writer from a release dir writes manifest sha
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="release-aware resolver not yet implemented")
def test_ac2_scheduler_state_writer_from_release_dir(tmp_path):
    """write_scheduler_state from a release dir must write toolkit_head
    = the manifest's sha."""
    release_sha = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
    release_dir = _make_release_dir(tmp_path, sha=release_sha)
    git_repo = _make_git_repo(tmp_path)  # unrelated git repo

    data_home = tmp_path / ".ilk-data"
    data_home.mkdir()
    state_file = data_home / "scheduler.state.json"

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO_ROOT / "skills"),
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }

    # Source write_scheduler_state and run it from the release dir.
    script = f'source "{_SCHEDULER_SH}"; write_scheduler_state'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=release_dir,  # CWD doesn't matter — resolver uses script location
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"scheduler failed: {result.stderr}"
    assert state_file.exists(), "state file was not written"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["toolkit_head"] == release_sha, (
        f"expected toolkit_head={release_sha[:12]}…, got {state['toolkit_head']}"
    )


# ---------------------------------------------------------------------------
# AC-3: Neither git nor manifest → HEAD unknown, tree_state unknown
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="release-aware resolver not yet implemented")
def test_ac3_no_git_no_manifest_cwd_repo_never_used(tmp_path):
    """With no .git and no .ilk-release.json above the script, HEAD must be
    empty/unknown and tree_state: unknown — even when cwd IS a git repo."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    git_repo = _make_git_repo(tmp_path)

    data_home = tmp_path / ".ilk-data"
    data_home.mkdir()
    # Write a state file with a known head so the bouncer reaches the HEAD check.
    _write_state_file(data_home, "abc123")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_launchctl(bin_dir)

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO_ROOT / "skills"),
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }

    result = _run_bounce_check(env, cwd=git_repo, bin_dir=bin_dir)

    assert result.returncode == 0, f"bouncer failed: {result.stderr}"
    assert "recorded_sha: abc123" in result.stdout, "recorded_sha should still appear"
    # The current_head must NOT be the cwd repo's HEAD.
    assert "HEAD unknown" in result.stdout or "current_head" not in result.stdout.lower(), (
        f"HEAD should be unknown when no .git/.ilk-release.json found:\n{result.stdout}"
    )
    assert "tree_state: unknown" in result.stdout, (
        f"tree_state should be unknown, got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# AC-4 (control): Inside a git work tree the behaviour is unchanged
# ---------------------------------------------------------------------------


def test_ac4_git_worktree_behaviour_unchanged(tmp_path):
    """Inside a git work tree, bouncer --check uses rev-parse HEAD and reports
    clean/dirty tree_state as before.  This is the control — must pass today.

    Exit code 2 (unreachable daemon) is acceptable — we're testing HEAD
    resolution, not daemon reachability.
    """
    git_repo = _make_git_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    ).stdout.strip()

    data_home = tmp_path / ".ilk-data"
    data_home.mkdir()
    _write_state_file(data_home, head)

    # Fake plist so the daemon appears "loaded" (avoids exit 2 from unreachable).
    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    (plist_dir / "net.inluck.ilk.scheduler.plist").write_text(
        "<plist><!-- stub --></plist>", encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_launchctl(bin_dir)

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO_ROOT / "skills"),
        "ILK_BOUNCE_ALLOW_FOREIGN_HOME": "1",
    }

    result = _run_bounce_check(env, cwd=git_repo, bin_dir=bin_dir)

    # Exit 0 (fresh) or 1 (stale) are both valid; exit 2 means unreachable.
    assert result.returncode in (0, 1), f"bouncer failed unexpectedly: {result.stderr}"
    assert f"recorded_sha: {head}" in result.stdout, (
        f"expected recorded_sha {head[:12]}… in output:\n{result.stdout}"
    )
    assert "tree_state: clean" in result.stdout or "tree_state: dirty" in result.stdout, (
        f"expected clean or dirty tree_state, got:\n{result.stdout}"
    )