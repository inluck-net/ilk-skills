"""Red-first pins for ilk_release.py (AC-1..AC-7).

Fixture: a tmp_path git repo with a small tree and tags v1, v2 on
different commits.  Every subprocess gets HOME=<tmp>/home,
ILK_RELEASES_ROOT=<tmp>/home/.ilk/releases.

AC-1..AC-6 are xfail(strict=True) because the script does not exist yet.
AC-7 is a control — the fixture itself passes today.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ilk_release.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def git_repo(tmp_path: Path):
    """Create a minimal git repo with two commits and tags v1, v2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo,
                   check=True, capture_output=True)

    # First commit (v1)
    skill_dir = repo / "skills" / "ilk-loop" / "scripts"
    skill_dir.mkdir(parents=True)
    (skill_dir / "x.sh").write_text("#!/bin/bash\necho v1\n")
    cmd_dir = repo / "commands"
    cmd_dir.mkdir()
    (cmd_dir / "ilk.md").write_text("# ilk\nv1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "tag", "v1"], cwd=repo, check=True,
                   capture_output=True)

    # Second commit (v2) — modify one file, add another
    (skill_dir / "x.sh").write_text("#!/bin/bash\necho v2\n")
    (skill_dir / "y.sh").write_text("#!/bin/bash\necho new\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "tag", "v2"], cwd=repo, check=True,
                   capture_output=True)

    return repo


@pytest.fixture()
def env(tmp_path: Path):
    """Return a dict of env vars pinning HOME and ILK_RELEASES_ROOT."""
    home = tmp_path / "home"
    home.mkdir()
    releases_root = home / ".ilk" / "releases"
    releases_root.mkdir(parents=True)
    return {
        "HOME": str(home),
        "ILK_RELEASES_ROOT": str(releases_root),
        "PATH": os.environ["PATH"],
    }


def _run(env: dict, *args: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run ilk_release.py as a subprocess."""
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _tag_sha(repo: Path, tag: str) -> str:
    """Return the commit sha for a tag."""
    r = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def _releases_root(env: dict) -> Path:
    return Path(env["ILK_RELEASES_ROOT"])


def _parent(env: dict) -> Path:
    """The parent of the releases root (where current/previous live)."""
    return _releases_root(env).parent


# ---------------------------------------------------------------------------
# AC-1: extract + manifest + read-only + current symlink
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac1_extract_manifest_readonly_current(git_repo, env):
    """AC-1: extract v1, verify manifest, read-only bits, and current symlink."""
    sha = _tag_sha(git_repo, "v1")
    r = _run(env, "v1", "--repo", str(git_repo))
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"

    root = _releases_root(env)
    release_dir = root / "v1"
    assert release_dir.is_dir()

    # Manifest
    manifest = release_dir / ".ilk-release.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text())
    assert data["tag"] == "v1"
    assert data["sha"] == sha
    assert data["source_repo"] == str(git_repo.resolve())
    assert "extracted_at" in data

    # File list matches git archive (at least the known files)
    assert (release_dir / "skills" / "ilk-loop" / "scripts" / "x.sh").is_file()
    assert (release_dir / "commands" / "ilk.md").is_file()

    # Read-only
    sample = release_dir / "skills" / "ilk-loop" / "scripts" / "x.sh"
    assert not os.access(sample, os.W_OK)
    with pytest.raises(PermissionError):
        sample.open("w")

    # Current symlink
    current = _parent(env) / "current"
    assert current.is_symlink()
    assert os.readlink(current) == str(release_dir)


# ---------------------------------------------------------------------------
# AC-2: missing tag → exit non-zero, nothing created, current unchanged
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac2_missing_tag(git_repo, env):
    """AC-2: missing tag exits non-zero, stderr names it, no release dir."""
    # First install v1 so current exists
    _run(env, "v1", "--repo", str(git_repo))
    current_before = os.readlink(_parent(env) / "current")

    r = _run(env, "v9", "--repo", str(git_repo))
    assert r.returncode != 0
    assert "v9" in r.stderr

    assert not (_releases_root(env) / "v9").exists()
    assert os.readlink(_parent(env) / "current") == current_before


# ---------------------------------------------------------------------------
# AC-3: install v1 then v2 → current=v2, previous=v1; rollback swaps
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac3_current_previous_and_rollback(git_repo, env):
    """AC-3: install v1, v2; verify pointers; rollback; rollback with no previous."""
    root = _releases_root(env)
    parent = _parent(env)

    _run(env, "v1", "--repo", str(git_repo))
    _run(env, "v2", "--repo", str(git_repo))

    current_target = os.readlink(parent / "current")
    previous_target = os.readlink(parent / "previous")
    assert current_target == str(root / "v2")
    assert previous_target == str(root / "v1")

    # Rollback
    r = _run(env, "--rollback")
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    assert os.readlink(parent / "current") == str(root / "v1")
    assert os.readlink(parent / "previous") == str(root / "v2")

    # Rollback again (now previous=v2, so it swaps back)
    r = _run(env, "--rollback")
    assert r.returncode == 0
    assert os.readlink(parent / "current") == str(root / "v2")

    # Fresh env with no previous → rollback fails
    env2 = {**env, "ILK_RELEASES_ROOT": str(Path(env["HOME"]) / ".ilk2" / "releases")}
    Path(env2["ILK_RELEASES_ROOT"]).mkdir(parents=True, exist_ok=True)
    _run(env2, "v1", "--repo", str(git_repo))
    r2 = _run(env2, "--rollback")
    assert r2.returncode != 0


# ---------------------------------------------------------------------------
# AC-4: atomic flip — concurrent readers never see FileNotFoundError
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac4_atomic_flip(git_repo, env):
    """AC-4: 20 alternating flips, 2000 reads, no FileNotFoundError."""
    _run(env, "v1", "--repo", str(git_repo))
    _run(env, "v2", "--repo", str(git_repo))

    parent = _parent(env)
    current = parent / "current"
    errors: list[str] = []

    def reader():
        for _ in range(2000):
            try:
                os.readlink(current)
            except FileNotFoundError:
                errors.append("FileNotFoundError in reader")

    t = threading.Thread(target=reader)
    t.start()

    for i in range(20):
        tag = "v1" if i % 2 == 0 else "v2"
        _run(env, tag, "--repo", str(git_repo), "--no-flip")
        # Manually flip to test atomicity of the flip mechanism
        # (the script's flip is what we're testing, but --no-flip skips it)
        # We need to call the script normally to exercise the flip path
        _run(env, tag, "--repo", str(git_repo))

    t.join(timeout=30)
    assert not errors, f"Reader saw: {errors}"


# ---------------------------------------------------------------------------
# AC-5: prune keeps last 5 release dirs
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac5_prune_keeps_five(git_repo, env):
    """AC-5: installing 7 tags leaves exactly 5 release dirs."""
    # Create tags t1..t7 by making commits
    for i in range(1, 8):
        tag = f"t{i}"
        # Create a new commit for each tag
        file = git_repo / "skills" / "ilk-loop" / "scripts" / f"f{i}.sh"
        file.write_text(f"echo t{i}\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-m", tag, "--allow-empty"],
                       cwd=git_repo, check=True, capture_output=True)
        subprocess.run(["git", "tag", tag], cwd=git_repo, check=True,
                       capture_output=True)
        _run(env, tag, "--repo", str(git_repo))

    root = _releases_root(env)
    release_dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    assert len(release_dirs) == 5, f"Expected 5 dirs, got {len(release_dirs)}: {sorted(d.name for d in release_dirs)}"


# ---------------------------------------------------------------------------
# AC-6: re-install is idempotent; re-tagged tag is refused
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="red-first: script does not exist yet")
def test_ac6_idempotent_and_retag_refused(git_repo, env):
    """AC-6: re-install doesn't rewrite manifest; re-tagged tag is refused."""
    _run(env, "v1", "--repo", str(git_repo))

    root = _releases_root(env)
    manifest = root / "v1" / ".ilk-release.json"
    mtime_before = manifest.stat().st_mtime

    # Re-install v1 — should be a no-op
    time.sleep(0.05)  # ensure mtime would differ if rewritten
    r = _run(env, "v1", "--repo", str(git_repo))
    assert r.returncode == 0
    assert manifest.stat().st_mtime == mtime_before, "manifest was rewritten on re-install"

    # Re-tag v1 onto a different commit
    subprocess.run(["git", "tag", "-d", "v1"], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "tag", "v1", "v2"], cwd=git_repo, check=True,
                   capture_output=True)

    r2 = _run(env, "v1", "--repo", str(git_repo))
    assert r2.returncode != 0
    assert "moved" in r2.stderr.lower() or "moved" in r2.stdout.lower()


# ---------------------------------------------------------------------------
# AC-7: control — fixture repo has tags on different shas
# ---------------------------------------------------------------------------

def test_ac7_fixture_control(git_repo):
    """AC-7: control — the fixture itself has v1 and v2 on different shas."""
    sha1 = _tag_sha(git_repo, "v1")
    sha2 = _tag_sha(git_repo, "v2")
    assert sha1 != sha2, "fixture tags point to the same commit"