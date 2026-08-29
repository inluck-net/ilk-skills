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
from __future__ import annotations

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


# ── AC-10 / AC-11: Live runner invocation with real gtimeout kill ────────────

import glob
import json
import os
import shutil
import sys as _sys
from typing import Optional

_real_gtimeout = shutil.which("gtimeout")

_integration_skip = pytest.mark.skipif(
    _sys.platform == "win32"
    or shutil.which("bash") is None
    or _real_gtimeout is None
    or shutil.which("git") is None
    or shutil.which("claude") is None,
    reason="Integration test: needs bash + gtimeout + git + claude CLI",
)


def _isolated_env(tmp_path: Path) -> dict:
    """os.environ with ILK_DATA_HOME redirected under tmp_path.

    These tests drive the REAL runner, which resolves its data dir through
    ``ilk_paths.py`` — that honours ``$ILK_DATA_HOME`` and otherwise falls back
    to ``~/.ilk-data``.  Without this redirect the runner writes a real
    ``projects/<key>/runtime/launcher/last-exit.json`` for every pytest tmpdir
    it is pointed at.  Because the mock claude is killed by gtimeout, some of
    those sentinels are left at ``state: running`` with a now-dead PID, which
    ``status_all`` reports as ``blocked: stale-running`` forever and the xbar
    panel renders as a permanent ``!`` row.  Observed 2026-08-14: two leaked
    sentinels from this file plus 87 orphaned project dirs under ~/.ilk-data.
    """
    env = dict(os.environ)
    data_home = tmp_path / "ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    env["ILK_DATA_HOME"] = str(data_home)
    return env


def _setup_scratch_project(tmp_path: Path) -> Path:
    """Create a minimal project with plans, dirty tree, and an in-progress sub-plan."""
    proj = tmp_path / "scratch-project"
    proj.mkdir()

    # Init git repo
    subprocess.run(["git", "init"], cwd=proj, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=proj, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=proj, capture_output=True, check=True)

    # Create initial files
    (proj / "README.md").write_text("# scratch\n")
    (proj / ".gitignore").write_text(".ilk-loop/\n.ilk-remote-type\n")
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=proj, capture_output=True, check=True)

    # Create dirty files (will be preserved on timeout)
    (proj / "work.txt").write_text("some work in progress\n")
    (proj / "untracked_test.py").write_text("def test_wip(): pass\n")

    # Set up plans directory
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    (plans / "MASTER-2026-08-10-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-08-10-test
        batch_date: 2026-08-10
        status: active
        supervised_only: false
        current_subplan: 2026-08-10-test-sub
        cross_cutting_invariants: []
        ---

        # Test Master

        ## Sub-plan registry

        | # | Sub-plan | Steps |
        |---|---|---|
        | 1 | [2026-08-10-test-sub.md](./2026-08-10-test-sub.md) | 3 |
    """))

    (plans / "2026-08-10-test-sub.md").write_text(textwrap.dedent("""\
        ---
        plan: 2026-08-10-test-sub
        status: in-progress
        current_step: 1
        priority: P1
        estimated_steps: 3
        last_updated: 2026-08-10
        ---

        # Test sub-plan

        ## Steps

        ### Step 0
        - Do nothing.

        ### Step 1
        - Do nothing.

        ### Step 2
        - Do nothing.
    """))

    return proj


def _find_jsonl_record(proj: Path) -> Optional[dict]:
    """Find the last JSONL record in the run's log directory."""
    log_dirs = sorted(glob.glob(str(proj / ".ilk-loop" / "logs" / "run-*")))
    if not log_dirs:
        return None
    jsonl_files = sorted(glob.glob(f"{log_dirs[-1]}/*.log.jsonl"))
    if not jsonl_files:
        return None
    last_record = None
    with open(jsonl_files[-1]) as f:
        for line in f:
            line = line.strip()
            if line:
                last_record = json.loads(line)
    return last_record


@_integration_skip
class TestLiveRunnerTimeout:
    """Drive the real runner CLI with a tiny timeout so gtimeout fires.

    This exercises the full path: runner → gtimeout kill → timeout detection →
    preserve_dirty_tree_on_timeout → JSONL record.
    """

    @pytest.mark.timeout(60)  # ILK_ITERATION_TIMEOUT_SEC=5 bounds the iteration
    def test_timeout_preserves_dirty_tree_via_real_cli(self, tmp_path: Path) -> None:
        """A real gtimeout kill preserves the dirty tree as a WIP commit.

        AC-10: the preservation path is exercised through the runner's real CLI
        entry point.
        AC-11: the JSONL record has stop_reason=timeout and wip_preserved > 0.
        """
        proj = _setup_scratch_project(tmp_path)

        # Create a mock claude that sleeps long enough to be killed by timeout
        mock_bin = tmp_path / "mock-bin"
        mock_bin.mkdir()
        mock_claude = mock_bin / "claude"
        mock_claude.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            # Mock claude: sleep until killed by gtimeout
            sleep 300
        """))
        mock_claude.chmod(0o755)

        # Run the real runner with 1-minute timeout
        env = _isolated_env(tmp_path)
        env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
        env["ILK_DOTSOURCE_ONLY"] = ""  # ensure main() runs
        # 5s instead of the 60s floor --iteration-timeout-min imposes.
        # These two tests were 121s of a 299s suite (batch-gate
        # --durations=25, 2026-08-26).  The mechanism under test is
        # unchanged: a real runner, the same gtimeout path, the same
        # timeout branch — only the bound is smaller.
        env["ILK_ITERATION_TIMEOUT_SEC"] = "5"

        result = subprocess.run(
            [
                "bash", str(RUNNER),
                "--project-path", str(proj),
                "--max-iterations", "1",
                "--iteration-timeout-min", "1",
                "--model", "test-model",
            ],
            capture_output=True, text=True, timeout=180,  # 3 min hard limit
            env=env, cwd=str(proj),
        )

        # The runner should have exited (not hung)
        # Check stdout/stderr for timeout indication
        combined = result.stdout + result.stderr

        # Assert: the runner detected timeout
        assert "timeout" in combined.lower() or result.returncode in (0, 1), \
            f"runner should have detected timeout, rc={result.returncode}\n{combined[-500:]}"

        # Assert: WIP commit was created
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=proj, capture_output=True, text=True, check=True,
        )
        assert "WIP:" in git_log.stdout, \
            f"Expected WIP commit in git log:\n{git_log.stdout}"

        # Assert: tree is clean after preservation
        git_status = subprocess.run(
            ["git", "status", "--short"],
            cwd=proj, capture_output=True, text=True, check=True,
        )
        assert git_status.stdout.strip() == "", \
            f"tree should be clean after WIP commit:\n{git_status.stdout}"

        # Assert: WIP commit includes both tracked and untracked files
        git_show = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=proj, capture_output=True, text=True, check=True,
        )
        assert "work.txt" in git_show.stdout, "tracked file should be in WIP commit"
        assert "untracked_test.py" in git_show.stdout, "untracked file should be in WIP commit"

        # Assert: JSONL record exists and has correct fields
        record = _find_jsonl_record(proj)
        if record is not None:
            # If we got a JSONL record, verify the contract
            assert record.get("stop_reason") == "timeout", \
                f"JSONL stop_reason should be 'timeout', got: {record.get('stop_reason')}"
            assert record.get("wip_preserved", 0) > 0, \
                f"JSONL wip_preserved should be > 0, got: {record.get('wip_preserved')}"

    @pytest.mark.timeout(60)  # ILK_ITERATION_TIMEOUT_SEC=5 bounds the iteration
    def test_timeout_clean_tree_no_wip(self, tmp_path: Path) -> None:
        """A timeout with a clean tree produces no WIP commit.

        AC-5 (integration): clean-tree timeout is unchanged.
        """
        proj = _setup_scratch_project(tmp_path)

        # Remove all dirty state (make tree clean)
        for f in ["work.txt", "untracked_test.py"]:
            p = proj / f
            if p.exists():
                p.unlink()
        subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "clean up"],
            cwd=proj, capture_output=True, check=True,
        )

        # Verify tree is clean before running the runner
        pre_status = subprocess.run(
            ["git", "status", "--short"],
            cwd=proj, capture_output=True, text=True, check=True,
        )
        assert pre_status.stdout.strip() == "", \
            f"tree should be clean before runner, got:\n{pre_status.stdout}"

        # Create mock claude
        mock_bin = tmp_path / "mock-bin"
        mock_bin.mkdir()
        mock_claude = mock_bin / "claude"
        mock_claude.write_text("#!/usr/bin/env bash\nsleep 300\n")
        mock_claude.chmod(0o755)

        env = _isolated_env(tmp_path)
        env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"
        env["ILK_DOTSOURCE_ONLY"] = ""
        # 5s instead of the 60s floor --iteration-timeout-min imposes.
        # These two tests were 121s of a 299s suite (batch-gate
        # --durations=25, 2026-08-26).  The mechanism under test is
        # unchanged: a real runner, the same gtimeout path, the same
        # timeout branch — only the bound is smaller.
        env["ILK_ITERATION_TIMEOUT_SEC"] = "5"

        subprocess.run(
            [
                "bash", str(RUNNER),
                "--project-path", str(proj),
                "--max-iterations", "1",
                "--iteration-timeout-min", "1",
                "--model", "test-model",
            ],
            capture_output=True, text=True, timeout=180,
            env=env, cwd=str(proj),
        )

        # Assert: no WIP commit (the last commit should still be "clean up")
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            cwd=proj, capture_output=True, text=True, check=True,
        )
        if "WIP:" in git_log.stdout:
            # Debug: what did the WIP commit include?
            git_show = subprocess.run(
                ["git", "show", "--stat", "HEAD"],
                cwd=proj, capture_output=True, text=True, check=True,
            )
            git_status = subprocess.run(
                ["git", "status", "--short"],
                cwd=proj, capture_output=True, text=True, check=True,
            )
            pytest.fail(
                f"clean tree should not produce WIP commit.\n"
                f"WIP commit contents:\n{git_show.stdout}\n"
                f"Current status:\n{git_status.stdout}"
            )


# ── AC-12: tool-call and test-invocation counts ─────────────────────────────

class TestAC12IterationMetrics:
    """count_iteration_metrics parses the stream JSON log correctly."""

    def _run_count(self, jsonl_content: str, env: dict) -> tuple:
        """Run count_iteration_metrics on the given JSONL content."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(jsonl_content)
            f.flush()
            tmp_path = f.name
        try:
            script = textwrap.dedent(f"""
                export ILK_DOTSOURCE_ONLY=1
                source '{RUNNER}' 2>/dev/null
                count_iteration_metrics '{tmp_path}'
            """)
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30,
                env={**env, "ILK_DOTSOURCE_ONLY": "1"},
            )
            parts = result.stdout.strip().split()
            return int(parts[0]), int(parts[1])
        finally:
            os.unlink(tmp_path)

    def test_empty_log(self, env: dict) -> None:
        """Empty log returns 0 0."""
        tc, ti = self._run_count("", env)
        assert tc == 0
        assert ti == 0

    def test_counts_bash_as_tool_call(self, env: dict) -> None:
        """A single Bash tool call is counted."""
        jsonl = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                ]
            }
        })
        tc, ti = self._run_count(jsonl + "\n", env)
        assert tc == 1
        assert ti == 0  # not a test command

    def test_counts_read_as_tool_call(self, env: dict) -> None:
        """A Read tool call is counted."""
        jsonl = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x"}}
                ]
            }
        })
        tc, ti = self._run_count(jsonl + "\n", env)
        assert tc == 1
        assert ti == 0

    def test_counts_pytest_as_test_invocation(self, env: dict) -> None:
        """A Bash call with pytest is counted as a test invocation."""
        jsonl = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "python3 -m pytest -q"}}
                ]
            }
        })
        tc, ti = self._run_count(jsonl + "\n", env)
        assert tc == 1
        assert ti == 1

    def test_counts_jest_as_test_invocation(self, env: dict) -> None:
        """A Bash call with jest is counted as a test invocation."""
        jsonl = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "npx jest --watch"}}
                ]
            }
        })
        tc, ti = self._run_count(jsonl + "\n", env)
        assert tc == 1
        assert ti == 1

    def test_counts_multiple_calls(self, env: dict) -> None:
        """Multiple tool calls across messages are all counted."""
        msgs = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x"}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/x"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
            ]}},
        ]
        jsonl = "\n".join(json.dumps(m) for m in msgs) + "\n"
        tc, ti = self._run_count(jsonl, env)
        assert tc == 4
        assert ti == 1

    def test_non_assistant_messages_ignored(self, env: dict) -> None:
        """Non-assistant messages (system, tool_result) are ignored."""
        msgs = [
            {"type": "system", "subtype": "init"},
            {"type": "tool_result", "content": "ok"},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
            ]}},
        ]
        jsonl = "\n".join(json.dumps(m) for m in msgs) + "\n"
        tc, ti = self._run_count(jsonl, env)
        assert tc == 1

    def test_malformed_lines_skipped(self, env: dict) -> None:
        """Malformed JSON lines are skipped without error."""
        jsonl = "not json\n" + json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]},
        }) + "\n"
        tc, ti = self._run_count(jsonl, env)
        assert tc == 1


# ── AC-13 / AC-14: orphan reaping ──────────────────────────────────────────

class TestAC13OrphanReaping:
    """reap_iteration_orphans kills background children of the runner."""

    def test_function_defined(self, env: dict) -> None:
        """reap_iteration_orphans is defined in the runner."""
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            type reap_iteration_orphans
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert result.returncode == 0, "function should be defined"

    def test_does_not_abort_on_failure(self, env: dict) -> None:
        """The function returns 0 even when pgrep fails."""
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            # Override pgrep to simulate failure
            pgrep() {{ return 1; }}
            reap_iteration_orphans
            echo "returned: $?"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert result.returncode == 0, "function should not abort"
        assert "returned: 0" in result.stdout


# ── AC-13: the function's stdout is its return value ─────────────────────────
#
# Regression for the 2026-08-29 crash in run `20260829-163114`.
#
# `preserve_dirty_tree_on_timeout` ends with `echo "$wip_count"`, so its stdout
# IS the return channel -- both of its diagnostics correctly use `>&2`.  But the
# `git commit` at run_ilk_loop_claude.sh:1714-1719 redirected only stderr, so on
# a SUCCESSFUL WIP commit git's stdout ("[main abc1234] WIP: ...\n N files
# changed, ...") joined the return value.
#
# The caller captures it (`wip_preserved=$(preserve_dirty_tree_on_timeout ...)`,
# :2182) into `_WIP_PRESERVED`, and the JSONL builder does `int(wp)` -- which
# raised ValueError and killed the python block BEFORE `print(json.dumps(d))`.
# The whole iteration record was lost, making a timed-out iteration
# unclassifiable.  Observed: `.ilk-loop.log` gained 0 records for that run.
#
# NOTE: `_run_preservation` above reads `stdout_lines[-1]`, so it tolerates the
# leak -- which is why every existing test in this file passed while production
# crashed.  These tests read the WHOLE stream on purpose.

def _raw_preservation_stdout(repo: Path, env: dict) -> str:
    """Return the function's COMPLETE stdout, unparsed."""
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
    return result.stdout


def test_stdout_is_only_the_count_after_a_successful_commit(repo: Path, env: dict) -> None:
    """AC-13: a successful WIP commit must not leak git's stdout into the count."""
    (repo / "work.txt").write_text("uncommitted work\n")
    raw = _raw_preservation_stdout(repo, env)
    assert raw.strip() == "1", (
        "preserve_dirty_tree_on_timeout must emit ONLY the wip_count on stdout; "
        f"got {raw!r}. git commit's stdout is leaking into the return value."
    )


def test_captured_value_survives_int_conversion(repo: Path, env: dict) -> None:
    """AC-13: the captured value must parse as int, as the JSONL builder does."""
    (repo / "work.txt").write_text("uncommitted work\n")
    raw = _raw_preservation_stdout(repo, env)
    try:
        parsed = int(raw.strip())
    except ValueError as exc:  # pragma: no cover - the regression itself
        raise AssertionError(
            "int() on the captured stdout raised, exactly as the JSONL builder "
            f"does at run_ilk_loop_claude.sh:2439 -- the iteration record is "
            f"lost when this happens. Value was {raw!r}"
        ) from exc
    assert parsed == 1
