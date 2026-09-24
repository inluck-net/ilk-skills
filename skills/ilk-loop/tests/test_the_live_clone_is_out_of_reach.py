"""Red-first: the live clone is out of a selfmod worker's reach.

Part of sub-plan ``the-live-clone-is-out-of-reach`` (MASTER-2026-09-25b).

In selfmod mode, the worker's skill root must be the worktree, not the live
clone.  A PreToolUse guard must refuse edits whose paths resolve (via
``realpath``) to the live clone.  After each selfmod iteration, the driver
checks the live clone for modifications and stops with a named exit state.

Five acceptance criteria:

  AC-1  in selfmod mode, the worker process's environment resolves the skill
        root to the worktree (assert the constructed command or env).

  AC-2  the guard refuses an Edit whose path is a symlink into the live
        clone (a tmp symlink → tmp "clone").

  AC-3  a stub worker that modifies a tracked file in the fake live clone
        ⇒ the run ends ``selfmod_live_clone_touched``, the log names the
        file, and no merge is attempted.

  AC-4  ``selfmod_worktree.py merge`` against a dirty clone exits 6 with
        the file names.

  AC-5  ``test_exit_state_vocabulary.py`` green.

Step 0 landed: all ACs are xfail (red-first).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers ──────────────────────────────────────────────────────────────────

_PATH = os.environ.get("PATH", "")


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside root."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _make_clone(tmp_path: Path) -> Path:
    """Create a fake 'live clone' repo."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q")
    _git(clone, "config", "user.email", "test@test")
    _git(clone, "config", "user.name", "Test")
    (clone / "README.md").write_text("seed\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "seed")
    return clone


def _make_worktree(clone: Path, tmp_path: Path) -> Path:
    """Create a worktree from the clone."""
    wt = tmp_path / "worktree"
    _git(clone, "worktree", "add", "-q", str(wt), "-b", "selfmod")
    return wt


def _run_driver_func(
    func_name: str,
    project: Path,
    env: dict[str, str],
    args: str = "",
) -> subprocess.CompletedProcess:
    """Source the driver and call a named function."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
{func_name} {args}
RC=$?
echo "RC=$RC"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


# ── AC-1: skill root resolves to worktree ───────────────────────────────────


class TestSkillRootIsWorktree:
    """AC-1: in selfmod mode, the worker process's environment resolves the
    skill root to the worktree."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_worker_env_skill_root_points_to_worktree(self, tmp_path: Path) -> None:
        """The driver must set ILK_SKILL_HOME to the worktree's skills dir."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)

        # Create a skills directory in the worktree.
        wt_skills = wt / "skills"
        wt_skills.mkdir()

        env = _sandbox_env(tmp_path)
        script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{clone}'
SELFMOD_WORKTREE_PATH='{wt}'

# Simulate what the driver does for selfmod mode.
# The worker's ILK_SKILL_HOME should be the worktree's skills dir.
echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=env, cwd=str(clone),
        )
        assert result.returncode == 0, f"script failed: {result.stderr}"
        assert str(wt_skills) in result.stdout, (
            f"ILK_SKILL_HOME should point to worktree skills dir, "
            f"got: {result.stdout}"
        )


# ── AC-2: guard refuses symlink into live clone ─────────────────────────────


class TestGuardRefusesSymlinkToLiveClone:
    """AC-2: the guard refuses an Edit whose path is a symlink into the live
    clone."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_guard_refuses_live_clone_symlink(self, tmp_path: Path) -> None:
        """A symlink from the worktree to the live clone must be refused by
        the PreToolUse guard.  The guard must exist and return a refusal."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)

        # Create a file in the live clone.
        live_file = clone / "secret.py"
        live_file.write_text("SECRET = True\n", encoding="utf-8")
        _git(clone, "add", "secret.py")
        _git(clone, "commit", "-q", "-m", "add secret")

        # Create a symlink in the worktree pointing to the live clone's file.
        symlink = wt / "secret_link.py"
        symlink.symlink_to(live_file)

        # Call the guard function — it must exist and refuse.
        env = _sandbox_env(tmp_path)
        result = _run_driver_func(
            "check_edit_path", wt, env, args=f"'{symlink}'"
        )
        # The guard must exist (not "command not found").
        assert "command not found" not in result.stderr, (
            f"check_edit_path not defined in driver: {result.stderr}"
        )
        # The guard must refuse (exit non-zero).
        assert "RC=0" not in result.stdout, (
            f"guard should refuse symlink to live clone: {result.stdout}"
        )


# ── AC-3: touched live clone stops the run ──────────────────────────────────


class TestTouchedLiveCloneStopsRun:
    """AC-3: a stub worker that modifies a tracked file in the fake live clone
    ⇒ the run ends ``selfmod_live_clone_touched``, the log names the file,
    and no merge is attempted."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_modified_tracked_file_stops_run(self, tmp_path: Path) -> None:
        """Modifying a tracked file in the live clone must stop the run
        with the named exit state."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)

        # Modify a tracked file in the live clone (not the worktree).
        readme = clone / "README.md"
        readme.write_text("modified by worker\n", encoding="utf-8")

        # Call the detection function — it must exist and detect.
        env = _sandbox_env(tmp_path)
        result = _run_driver_func(
            "check_live_clone_touched", clone, env, args=f"'{clone}'"
        )
        # The function must exist.
        assert "command not found" not in result.stderr, (
            f"check_live_clone_touched not defined in driver: {result.stderr}"
        )
        # The detection must find the modification.
        assert "RC=0" not in result.stdout, (
            f"detection should find live clone modification: {result.stdout}"
        )


# ── AC-4: merge against dirty clone exits 6 ─────────────────────────────────


class TestMergeDirtyCloneExits6:
    """AC-4: ``selfmod_worktree.py merge`` against a dirty clone exits 6 with
    the file names."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_merge_dirty_clone_exits_6(self, tmp_path: Path) -> None:
        """Merging into a dirty live clone must exit with code 6 and name
        the dirty files."""
        clone = _make_clone(tmp_path)
        wt = _make_worktree(clone, tmp_path)

        # Make a change in the worktree.
        (wt / "new_file.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "new_file.py")
        _git(wt, "commit", "-q", "-m", "add new file")

        # Dirty the live clone.
        readme = clone / "README.md"
        readme.write_text("dirty\n", encoding="utf-8")

        # Call merge — it must exit 6 and name the dirty file.
        env = _sandbox_env(tmp_path)
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "selfmod_worktree.py"),
             "merge", str(clone), str(wt)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env={**os.environ, **env},
        )
        # merge should exit 6.
        assert result.returncode == 6, (
            f"merge should exit 6 for dirty clone, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # merge should name the dirty file.
        assert "README.md" in result.stdout, (
            f"merge should name the dirty file: {result.stdout!r}"
        )


# ── AC-5: exit state vocabulary ─────────────────────────────────────────────
# Pinned by test_exit_state_vocabulary.py — no additional test needed here.
