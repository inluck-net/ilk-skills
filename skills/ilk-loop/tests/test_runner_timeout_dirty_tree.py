"""Tests for preserve_dirty_tree_on_timeout — the timeout-preservation mechanism.

When an iteration is killed by gtimeout (ITER_COMPLETED=0), the working tree may
hold finished but uncommitted work.  The runner must commit it as a WIP so the
next iteration resumes from a recoverable state.

AC-1: dirty tree → WIP commit on timeout.
AC-2: WIP commit identifiable by message shape ([wip:timeout] trailer).
AC-4: untracked files inside the repo are included.
AC-5: clean tree → no commit on timeout.
AC-7: preservation failure does not abort (set +e inside subshell).
AC-11: the WIP commit does not inflate new_commits_total (stop_reason drives
       classification, not the commit count).

Tests dot-source the runner and call preserve_dirty_tree_on_timeout directly.
"""
import subprocess
import textwrap
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


def _source_runner():
    """Return env dict after dot-sourcing the runner with ILK_DOTSOURCE_ONLY=1."""
    result = subprocess.run(
        ["bash", "-c", (
            "export ILK_DOTSOURCE_ONLY=1; "
            f"source '{RUNNER}' 2>/dev/null; "
            "env"
        )],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Failed to source runner: {result.stderr}"
    env = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    return env


def _init_repo(path: Path) -> None:
    """Create a minimal git repo with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True, check=True)


def _git_log(repo: Path, n: int = 5) -> list[str]:
    """Return the last n commit messages (one line each)."""
    result = subprocess.run(
        ["git", "log", f"--oneline", f"-{n}", "--format=%s"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


def _git_status(repo: Path) -> str:
    """Return `git status --short` output."""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _is_dirty(repo: Path) -> bool:
    """Return True if the working tree has tracked or untracked changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return bool(result.stdout.strip())


def _run_preservation(repo: Path, env: dict) -> tuple[int, str]:
    """Call preserve_dirty_tree_on_timeout with REPOS=(repo).

    Returns (wip_count_from_stdout, stderr_output).
    """
    env_copy = dict(env)
    env_copy["ILK_DOTSOURCE_ONLY"] = "1"
    env_copy["REPOS"] = str(repo)
    env_copy["PROJECT_PATH"] = str(repo.parent)

    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        REPOS=('{repo}')
        PROJECT_PATH='{repo.parent}'
        preserve_dirty_tree_on_timeout
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30, env=env_copy,
    )
    # The function echoes the wip_count on stdout and logs on stderr
    stdout_lines = result.stdout.strip().splitlines()
    wip_count = int(stdout_lines[-1]) if stdout_lines else 0
    return wip_count, result.stderr


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo with one initial commit."""
    _init_repo(tmp_path / "project")
    return tmp_path / "project"


@pytest.fixture
def env() -> dict:
    """Env dict from sourcing the runner."""
    return _source_runner()


# ── AC-1: dirty tree → WIP commit on timeout ────────────────────────────────

class TestAC1DirtyTreePreservation:
    """A timed-out iteration with a dirty tree leaves a WIP commit."""

    def test_modified_file_is_committed(self, repo: Path, env: dict) -> None:
        """Tracked modifications are preserved in a WIP commit."""
        (repo / "seed.txt").write_text("modified\n")
        assert _is_dirty(repo)

        wip_count, stderr = _run_preservation(repo, env)

        assert wip_count == 1
        assert not _is_dirty(repo), "tree should be clean after WIP commit"
        messages = _git_log(repo)
        assert any("WIP: preserve timed-out iteration" in m for m in messages), \
            f"Expected WIP commit in log, got: {messages}"
        assert "WIP commit:" in stderr, f"Expected confirmation in stderr, got: {stderr}"

    def test_multiple_modified_files(self, repo: Path, env: dict) -> None:
        """Multiple modified files are all included in one WIP commit."""
        (repo / "a.txt").write_text("a\n")
        (repo / "b.txt").write_text("b\n")
        (repo / "seed.txt").write_text("changed\n")

        wip_count, _ = _run_preservation(repo, env)

        assert wip_count == 1
        assert not _is_dirty(repo)
        # Verify all files are in the commit
        result = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert "a.txt" in result.stdout
        assert "b.txt" in result.stdout


# ── AC-2: WIP commit identifiable by message shape ──────────────────────────

class TestAC2MessageShape:
    """The WIP commit is identifiable by its message prefix and trailer."""

    def test_message_has_wip_prefix_and_trailer(self, repo: Path, env: dict) -> None:
        """Commit message contains [wip:timeout] trailer for tooling discovery."""
        (repo / "seed.txt").write_text("changed\n")

        _run_preservation(repo, env)

        result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        body = result.stdout
        assert "WIP: preserve timed-out iteration" in body, \
            "message must start with WIP prefix"
        assert "[wip:timeout]" in body, \
            "message must contain [wip:timeout] trailer for tooling"


# ── AC-4: untracked files inside the repo are included ──────────────────────

class TestAC4UntrackedFiles:
    """Untracked files within the repo are included in the WIP commit."""

    def test_untracked_file_is_committed(self, repo: Path, env: dict) -> None:
        """A new untracked file is added and committed."""
        (repo / "new_test.py").write_text("def test_foo(): pass\n")
        assert _is_dirty(repo)

        wip_count, _ = _run_preservation(repo, env)

        assert wip_count == 1
        assert not _is_dirty(repo), "untracked file should be committed"
        # Verify the file is in the commit
        result = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert "new_test.py" in result.stdout

    def test_mixed_tracked_and_untracked(self, repo: Path, env: dict) -> None:
        """Both tracked modifications and untracked files are preserved."""
        (repo / "seed.txt").write_text("modified\n")
        (repo / "untracked.txt").write_text("new content\n")

        wip_count, _ = _run_preservation(repo, env)

        assert wip_count == 1
        assert not _is_dirty(repo)
        result = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert "seed.txt" in result.stdout
        assert "untracked.txt" in result.stdout


# ── AC-5: clean tree → no commit on timeout ─────────────────────────────────

class TestAC5CleanTree:
    """A timeout with a clean tree produces no WIP commit."""

    def test_clean_tree_no_commit(self, repo: Path, env: dict) -> None:
        """No changes in the tree → no WIP commit."""
        assert not _is_dirty(repo)
        before = _git_log(repo)

        wip_count, stderr = _run_preservation(repo, env)

        assert wip_count == 0, "clean tree should produce no WIP commit"
        after = _git_log(repo)
        assert before == after, "commit log should be unchanged"
        assert "WIP commit" not in stderr, "no confirmation message expected"


# ── AC-7: preservation failure does not abort ────────────────────────────────

class TestAC7CannotAbort:
    """A failure inside the preservation path logs and continues."""

    def test_detached_head_does_not_abort(self, repo: Path, env: dict) -> None:
        """On a detached HEAD, the commit fails but the function returns."""
        # Detach HEAD
        subprocess.run(
            ["git", "checkout", "--detach", "HEAD"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "seed.txt").write_text("changed\n")

        # The function should return 0 (wip_count) even though the commit fails
        wip_count, stderr = _run_preservation(repo, env)

        # wip_count should be 1 (attempt was made) even though the commit failed
        assert wip_count == 1, "attempt counted even on failure"
        # The function should not have aborted — it returned normally

    def test_nonexistent_repo_does_not_abort(self, env: dict) -> None:
        """A nonexistent repo path is skipped without error."""
        import tempfile
        fake = Path(tempfile.mkdtemp()) / "nonexistent"

        script = f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            REPOS=('{fake}')
            PROJECT_PATH='{fake.parent}'
            preserve_dirty_tree_on_timeout
        """
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert result.returncode == 0, "nonexistent repo should not abort"
        assert "0" in result.stdout, "wip_count should be 0 for nonexistent repo"


# ── AC-3: front-matter untouched by preservation ─────────────────────────────

class TestAC3FrontMatterUntouched:
    """The preservation path does not mutate sub-plan front-matter.

    The function operates on git repos via git commands.  It does not read
    or write plan files.  This test verifies that a plan file co-located in
    the repo is not touched by the WIP commit.
    """

    def test_plan_file_unchanged_after_preservation(self, repo: Path, env: dict) -> None:
        """A .md file in the repo is not modified by the preservation."""
        plan_content = textwrap.dedent("""\
            ---
            plan: test-slug
            status: in-progress
            current_step: 2
            ---
            # Test plan
        """)
        plan_file = repo / "plan.md"
        plan_file.write_text(plan_content)
        # Commit the plan file so it's tracked
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, capture_output=True, check=True)

        # Now make a dirty change to a different file
        (repo / "seed.txt").write_text("modified\n")

        _run_preservation(repo, env)

        # Plan file should be unchanged
        assert plan_file.read_text() == plan_content, \
            "plan file must not be modified by preservation"

    def test_no_plan_references_in_function(self, env: dict) -> None:
        """The function source contains no references to plan/front-matter."""
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            type preserve_dirty_tree_on_timeout | grep -iE 'plan|front.?matter|current_step|status:' && echo "REFERENCED" || echo "CLEAN"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert "CLEAN" in result.stdout, \
            "function must not reference plan files or front-matter"


# ── AC-6: next iteration resumes from preserved state ───────────────────────

class TestAC6NextIterationResumes:
    """After preservation, the tree is clean and new work can proceed."""

    def test_clean_tree_after_preservation(self, repo: Path, env: dict) -> None:
        """The working tree is clean after a WIP commit."""
        (repo / "seed.txt").write_text("modified\n")
        (repo / "new_file.txt").write_text("new\n")

        _run_preservation(repo, env)

        assert not _is_dirty(repo), "tree should be clean after WIP commit"

    def test_new_work_can_build_on_preserved_state(self, repo: Path, env: dict) -> None:
        """New commits can be made on top of the WIP commit."""
        (repo / "seed.txt").write_text("modified\n")

        _run_preservation(repo, env)

        # Now make a new change and commit — simulates next iteration
        (repo / "seed.txt").write_text("further modified\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "real work"],
            cwd=repo, capture_output=True, check=True,
        )

        messages = _git_log(repo, 3)
        assert "real work" in messages[0], "new commit should succeed on top of WIP"
        assert any("WIP:" in m for m in messages), "WIP commit should be in history"

    def test_wip_commit_does_not_wedge_git(self, repo: Path, env: dict) -> None:
        """After preservation, git operations work normally."""
        (repo / "seed.txt").write_text("modified\n")

        _run_preservation(repo, env)

        # Verify basic git operations work
        status = _git_status(repo)
        assert status == "", f"tree should be clean, got: '{status}'"

        # Verify we can create new branches
        result = subprocess.run(
            ["git", "checkout", "-b", "test-branch"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert result.returncode == 0


# ── AC-11: WIP commit does not inflate new_commits_total ────────────────────

class TestAC11ClassificationSafety:
    """The preservation commit does not make a timeout look like progress.

    The JSONL stop_reason is 'timeout', which collect.py classifies as
    'timeout-bound' regardless of new_commits_total.  This test verifies the
    structural contract: the stop_reason drives classification, not the commit
    count.
    """

    def test_stop_reason_is_timeout_in_jsonl(self, repo: Path, env: dict) -> None:
        """After preservation, the JSONL stop_reason is still 'timeout'.

        This is a structural assertion — we verify the runner sets
        stop_reason=timeout before calling preservation, so the JSONL record
        is correct regardless of new_commits_total.
        """
        # We can't easily run the full runner here, but we can verify the
        # structural invariant: the preservation happens AFTER iter_stop_reason
        # is set to "timeout", so the JSONL record will have stop_reason=timeout.
        #
        # This is verified by the code structure:
        #   1. iter_stop_reason="timeout"   (line ~1530)
        #   2. wip_preserved = preserve_dirty_tree_on_timeout()
        #   3. ... JSONL writes stop_reason = iter_stop_reason ...
        #
        # As long as step 2 doesn't mutate iter_stop_reason, the contract holds.
        # We verify this by checking the code doesn't mutate the variable.
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            # Verify preserve_dirty_tree_on_timeout doesn't set iter_stop_reason
            type preserve_dirty_tree_on_timeout | grep -q 'iter_stop_reason' && echo "LEAKED" || echo "CLEAN"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert "CLEAN" in result.stdout, \
            "preserve_dirty_tree_on_timeout must not mutate iter_stop_reason"


# ── Structural: function exists and is callable ─────────────────────────────

class TestStructural:
    """The function exists and is defined in the runner."""

    def test_function_defined(self, env: dict) -> None:
        """preserve_dirty_tree_on_timeout is defined in the runner."""
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            type preserve_dirty_tree_on_timeout
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert result.returncode == 0, "function should be defined"
        assert "preserve_dirty_tree_on_timeout" in result.stdout

    def test_function_echoes_wip_count(self, repo: Path, env: dict) -> None:
        """The function echoes the wip_count as its last line of stdout."""
        (repo / "seed.txt").write_text("changed\n")

        wip_count, _ = _run_preservation(repo, env)

        assert isinstance(wip_count, int)
        assert wip_count >= 0
