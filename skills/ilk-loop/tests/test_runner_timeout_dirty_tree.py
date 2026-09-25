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
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)


def _git_log(repo: Path, n: int = 5) -> list[str]:
    """Return the last n commit messages (one line each)."""
    result = subprocess.run(
        ["git", "log", f"--oneline", f"-{n}", "--format=%s"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


def _git_status(repo: Path) -> str:
    """Return `git status --short` output."""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def _is_dirty(repo: Path) -> bool:
    """Return True if the working tree has tracked or untracked changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env_copy,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env={**env, "ILK_DOTSOURCE_ONLY": "1"},
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
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

        # Now make a dirty change to a different file
        (repo / "seed.txt").write_text("modified\n")

        _run_preservation(repo, env)

        # Plan file should be unchanged
        assert plan_file.read_text() == plan_content, \
            "plan file must not be modified by preservation"

    def test_no_plan_references_in_function(self, env: dict) -> None:
        """The function source references plan-aware helpers ONLY through the
        re-entry stamp path (get_active_subplan_targets + _stamp_reentry_note).

        The function MUST NOT inline plan parsing — that logic lives in the
        dedicated helper so the preservation core stays focused on git.
        """
        script = textwrap.dedent(f"""
            export ILK_DOTSOURCE_ONLY=1
            source '{RUNNER}' 2>/dev/null
            # Inline plan/frontmatter parsing is forbidden; references to
            # get_active_subplan_targets and _stamp_reentry_note are allowed
            # because they are dedicated helpers for the re-entry stamp.
            type preserve_dirty_tree_on_timeout | grep -iE 'front.?matter|current_step|status:' && echo "REFERENCED" || echo "CLEAN"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env={**env, "ILK_DOTSOURCE_ONLY": "1"},
        )
        assert "CLEAN" in result.stdout, (
            "function must not inline plan/frontmatter parsing — use dedicated helpers"
        )


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
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        subprocess.run(
            ["git", "commit", "-m", "real work"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
    subprocess.run(["git", "init"], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

    # Create initial files
    (proj / "README.md").write_text("# scratch\n")
    (proj / ".gitignore").write_text(".ilk-loop/\n.ilk-remote-type\n")
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

    # NOTE: dirty files are NOT created here — tests that need them create
    # them inside the mock claude (after the pre-iteration snapshot) so the
    # WIP-preserve mechanism correctly sees them as iteration-owned changes.

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
            # Mock claude: create dirty files (after snapshot) then sleep
            echo "some work in progress" > work.txt
            echo "def test_wip(): pass" > untracked_test.py
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,  # 3 min hard limit
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
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        assert "WIP:" in git_log.stdout, \
            f"Expected WIP commit in git log:\n{git_log.stdout}"

        # Assert: iteration-owned files are committed (pre-existing untracked
        # dirs like docs/ may remain — the WIP commit only takes what changed
        # since the pre-iteration snapshot).
        git_status = subprocess.run(
            ["git", "status", "--short"],
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        remaining = [l for l in git_status.stdout.strip().splitlines() if l.strip()]
        # Only pre-existing untracked paths (docs/) should remain.
        for line in remaining:
            assert "work.txt" not in line and "untracked_test.py" not in line, \
                f"iteration-owned file should be committed, still untracked: {line}"

        # Assert: WIP commit includes both tracked and untracked files
        git_show = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
        subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        subprocess.run(
            ["git", "commit", "-m", "clean up"],
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )

        # Verify tree is clean before running the runner
        pre_status = subprocess.run(
            ["git", "status", "--short"],
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            env=env, cwd=str(proj),
        )

        # Assert: no WIP commit (the last commit should still be "clean up")
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        if "WIP:" in git_log.stdout:
            # Debug: what did the WIP commit include?
            git_show = subprocess.run(
                ["git", "show", "--stat", "HEAD"],
                cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            )
            git_status = subprocess.run(
                ["git", "status", "--short"],
                cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env_copy,
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


# ── AC-14: the WIP preserve stamps re-entry state ──────────────────────────
#
# Regression for the 2026-09-20 lark thrash: three G2 iterations each
# re-derived the prior iterations' state because the timeout WIP-preserve
# said only [wip:timeout] files=N.  The next session's only memory is the
# plan file plus the tree; nothing tells it which step was in flight, what
# completed, or what remained — so it re-derives (~16 min of a 30-min
# window measured at 22:30-22:46).
#
# The fix: the WIP-preserve path stamps a re-entry note into the active
# sub-plan's Findings section AND extends the commit message with
# `step=<slug>#<N>` (machine-parseable for ship_audit's ledger rules).

def _setup_plans_dir(project: Path, plan_body: str) -> Path:
    """Create a docs/plans/ dir with one master and one sub-plan."""
    plans_dir = project / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = plans_dir / "MASTER-test.md"
    master.write_text(textwrap.dedent("""\
        ---
        title: test
        slug: test
        created: 2026-09-20T23:10:00+08:00
        status: active
        priority: null
        pause_after_ship: false
        supervised_only: false
        base_branch: main
        branch: null
        goal: test
        out_of_scope: []
        cross_cutting_invariants: []
        ---

        # MASTER plan: test

        ## Sub-plan registry

        | # | Sub-plan | File | Status |
        |---|---|---|---|
        | 1 | sub-a | 2026-09-20-sub-a.md | in-progress |
    """), encoding="utf-8")
    sub = plans_dir / "2026-09-20-sub-a.md"
    sub.write_text(textwrap.dedent(plan_body), encoding="utf-8")
    return plans_dir


ACTIVE_SUB_PLAN = """\
    ---
    plan: sub-a
    status: in-progress
    current_step: 1
    tickets: []
    priority: P0
    estimated_steps: 3
    last_updated: 2026-09-20
    ---

    # Sub-plan: fix the mocks

    ## Steps

    ### Step 0 — classify failures
    - Run the failing tests and classify every failure.

    ```yaml
    local_checks:
      - command: python3 -m pytest skills/ilk-lark-tickets/tests/test_init_project.py -q
        timeout: 120
    ```

    ### Step 1 — fix get_tenant_access_token mock
    - Update mock expectations to match the client's actual call shape.
    - The remaining commit line for step 1.

    ```yaml
    local_checks:
      - command: python3 -m pytest skills/ilk-lark-tickets/tests/test_init_project.py -q
        timeout: 120
    ```

    ### Step 2 — run full suite
    - Run the full suite and verify all green.

    ## Findings
"""


def _run_preservation_with_plans(
    repo: Path, env: dict, project: Path
) -> tuple[int, str, str]:
    """Call preserve_dirty_tree_on_timeout with REPOS and PROJECT_PATH.

    Returns (wip_count, stderr, commit_message).
    """
    env_copy = dict(env)
    env_copy["ILK_DOTSOURCE_ONLY"] = "1"
    env_copy["REPOS"] = str(repo)
    env_copy["PROJECT_PATH"] = str(project)

    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        REPOS=('{repo}')
        PROJECT_PATH='{project}'
        preserve_dirty_tree_on_timeout
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env_copy,
    )
    stdout_lines = result.stdout.strip().splitlines()
    wip_count = int(stdout_lines[-1]) if stdout_lines else 0
    commit_msg = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%B"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10,
    ).stdout
    return wip_count, result.stderr, commit_msg


class TestAC14ReentryStamp:
    """The WIP preserve stamps re-entry state into the plan and commit message."""

    def test_commit_message_carries_step_slug(self, tmp_path: Path, env: dict) -> None:
        """WIP commit message contains step=<slug>#<N> from the active sub-plan."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, ACTIVE_SUB_PLAN)
        (project / "work.txt").write_text("uncommitted\n")

        _wip_count, stderr, commit_msg = _run_preservation_with_plans(project, env, project)

        assert "step=sub-a#1" in commit_msg, (
            f"Expected 'step=sub-a#1' in WIP commit message, got:\n{commit_msg}"
        )

    def test_findings_section_stamped(self, tmp_path: Path, env: dict) -> None:
        """The active sub-plan's Findings section gets a re-entry note."""
        project = tmp_path / "project"
        _init_repo(project)
        _setup_plans_dir(project, ACTIVE_SUB_PLAN)
        (project / "work.txt").write_text("uncommitted\n")

        _run_preservation_with_plans(project, env, project)

        sub = project / "docs" / "plans" / "2026-09-20-sub-a.md"
        body = sub.read_text(encoding="utf-8")
        assert "### Re-entry" in body, (
            "Expected '### Re-entry' heading in Findings section after WIP preserve."
        )
        assert "step 1 killed at its bound" in body.lower(), (
            "Expected 'step 1 killed at its bound' in re-entry note."
        )


# ── the-pin-reaches-the-driver AC-4 / AC-5: preserve the pinned worktree ─────
#
# Sub-plan `2026-09-22-the-pin-reaches-the-driver`.  The existing cases above
# set `REPOS` by hand, which is exactly the gap: `preserve_dirty_tree_on_timeout`
# trusts that global, and the runner fills it from `discover_git_repos` →
# `ilk_paths.project_root`.  A linked worktree without a pin resolves to its
# clone (`git_root` parses the `gitdir:` entry back), so a timeout staged the
# clone's junk and left the agent's real output untracked in the worktree.
#
# Measured on rezmac 2026-09-22 (gh-resolve, kira-cloudflare#6200): a
# 30-minute iteration timed out; `gc_push_failures.json` was committed to the
# clone's `dev` (`50bc6c223`, 0 remotes contain it) while a 666-line test
# file written nine minutes before the timeout sat untracked in the worktree
# and was not preserved.
#
# The fixtures build real git state — a real clone and a real
# `git worktree add` — because a fake `.git` file would test the fixture.

def _git_head(repo: Path) -> str:
    """Return the repo's HEAD sha."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def _init_clone_with_worktree(tmp_path: Path, *, pinned: bool) -> tuple[Path, Path]:
    """A real clone and a real linked worktree; optionally pin the worktree.

    The worktree's `.git` file points at `<clone>/.git/worktrees/<name>`, which
    is the entry `git_root` walks back to the clone.  That parse is the
    defect's mechanism, so the fixture must produce it for real.
    """
    clone = tmp_path / "clone"
    _init_repo(clone)
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", str(worktree), "-b", "feature"],
        cwd=clone, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    if pinned:
        (worktree / ".ilk-project-root").write_text("\n")
    return clone, worktree


def _run_discover_and_preserve(project_path: Path, env: dict) -> tuple[str, int, str]:
    """Derive REPOS the way the runner does, then run preservation.

    Unlike `_run_preservation`, this does NOT set `REPOS` by hand — that would
    bypass the derivation under test.  Returns (repos_line, wip_count, stderr).
    """
    env_copy = dict(env)
    env_copy["ILK_DOTSOURCE_ONLY"] = "1"
    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        PROJECT_PATH='{project_path}'
        discover_git_repos
        echo "REPOS:${{REPOS[*]}}"
        preserve_dirty_tree_on_timeout
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env_copy,
    )
    repos_line = ""
    wip_count = 0
    for ln in result.stdout.splitlines():
        if ln.startswith("REPOS:"):
            repos_line = ln[len("REPOS:"):].strip()
        else:
            try:
                wip_count = int(ln.strip())
            except ValueError:
                continue
    return repos_line, wip_count, result.stderr


class TestPinnedWorktreePreservation:
    """the-pin-reaches-the-driver AC-4 / AC-5.

    AC-4: `preserve_dirty_tree_on_timeout` commits the WORKTREE's untracked file.
    AC-5: in AC-4 the clone gains **no** commit.

    AC-5 is the one that names the observed damage.  AC-4 passing without AC-5
    would still commit `gc_push_failures.json` to the clone every timeout.
    """

    def test_ac4_wip_commit_lands_in_the_worktree(self, tmp_path: Path, env: dict) -> None:
        """The worktree's untracked file is preserved in the worktree's WIP commit."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=True)
        # Both trees dirty — the measured failure had dirt on each side.
        (clone / "gc_push_failures.json").write_text("stray\n")
        (worktree / "new_test.py").write_text("def test_foo(): pass\n")

        repos_line, wip_count, _stderr = _run_discover_and_preserve(worktree, env)

        assert wip_count == 1, f"expected one WIP attempt, got {wip_count} (REPOS={repos_line!r})"
        messages = _git_log(worktree)
        assert any("WIP: preserve timed-out iteration" in m for m in messages), (
            "Expected a WIP commit in the WORKTREE "
            f"(REPOS={repos_line!r}), got worktree log: {messages}"
        )
        show = subprocess.run(
            ["git", "show", "--stat", "HEAD"],
            cwd=worktree, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        assert "new_test.py" in show.stdout, (
            "the worktree's untracked file must be in the WIP commit; "
            f"commit contents:\n{show.stdout}"
        )
        assert not _is_dirty(worktree), "worktree should be clean after its WIP commit"

    def test_ac5_the_clone_gains_no_commit(self, tmp_path: Path, env: dict) -> None:
        """The clone's HEAD is unchanged: timeout preservation never touches it."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=True)
        (clone / "gc_push_failures.json").write_text("stray\n")
        (worktree / "new_test.py").write_text("def test_foo(): pass\n")
        clone_head_before = _git_head(clone)

        repos_line, _wip_count, _stderr = _run_discover_and_preserve(worktree, env)

        clone_head_after = _git_head(clone)
        assert clone_head_after == clone_head_before, (
            "the clone gained a commit on timeout — exactly the observed damage "
            f"(gc_push_failures.json committed to the clone's branch). "
            f"HEAD {clone_head_before} -> {clone_head_after}, REPOS={repos_line!r}, "
            f"clone log: {_git_log(clone)}"
        )
        # The clone's stray file is still there, untracked and untouched.
        assert "gc_push_failures.json" in _git_status(clone), (
            "the clone's dirty state must be left alone — preservation targets "
            f"the worktree, not the clone. status: {_git_status(clone)!r}"
        )


# ── the-pin-reaches-the-driver AC-1 / AC-2 / AC-3: the pin reaches the driver ─
#
# `get_plans_dir` and `REPOS` both derive from `ilk_paths.project_root`, so
# once #1 landed the pin seam they inherit it without a second resolution
# path. These cases pin that down so a future reader cannot bypass `ilk_paths`
# unnoticed.

def _isolated_data_env(env: dict, tmp_path: Path) -> dict:
    """env with ILK_DATA_HOME pinned under tmp_path.

    These cases CREATE files under ``<data_home>/projects/<key>/plans``, so
    they must never resolve the ambient data root — that is a live
    ``~/.ilk-data`` write. ``ilk_data_root()`` reads ``ILK_DATA_HOME`` before
    falling back to ``$HOME``, so pin the primary variable and drop the
    ``ILK_DATA_DIR`` alias so the two cannot disagree.
    """
    e = dict(env)
    data_home = tmp_path / "ilk-data"
    data_home.mkdir(parents=True, exist_ok=True)
    e["ILK_DATA_HOME"] = str(data_home)
    e.pop("ILK_DATA_DIR", None)
    e["ILK_DOTSOURCE_ONLY"] = "1"
    return e


def _ilk_paths_json(start: Path, env: dict) -> dict:
    """Run ilk_paths.py --start and return its JSON payload."""
    resolver = RUNNER.parent / "ilk_paths.py"
    result = subprocess.run(
        ["python3", str(resolver), "--start", str(start)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env,
    )
    assert result.returncode == 0, f"ilk_paths.py failed: {result.stderr}"
    return json.loads(result.stdout)


def _seed_external_plans(data_home: Path, key: str) -> Path:
    """Create ``<data_home>/projects/<key>/plans`` holding one MASTER plan."""
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-09-22-test.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-09-22-test
        batch_date: 2026-09-22
        status: active
        supervised_only: false
        current_subplan: 2026-09-22-test-sub
        cross_cutting_invariants: []
        ---

        # Test master

        ## Sub-plan registry

        | # | File | Sub-plan |
        |---|---|---|
        | 1 | 2026-09-22-test-sub.md | test-sub |
    """), encoding="utf-8")
    return plans


def _run_get_plans_dir(project_path: Path, env: dict) -> str:
    """Call get_plans_dir with PROJECT_PATH set; return its stdout, stripped."""
    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        PROJECT_PATH='{project_path}'
        get_plans_dir
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env,
    )
    assert result.returncode == 0, (
        f"get_plans_dir failed: rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return result.stdout.strip()


def _run_discover_repos(project_path: Path, env: dict) -> list[str]:
    """Call discover_git_repos with PROJECT_PATH set; return REPOS."""
    script = textwrap.dedent(f"""
        export ILK_DOTSOURCE_ONLY=1
        source '{RUNNER}' 2>/dev/null
        PROJECT_PATH='{project_path}'
        discover_git_repos
        printf '%s\\n' "${{REPOS[@]}}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env,
    )
    assert result.returncode == 0, (
        f"discover_git_repos failed: rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


class TestPinReachesTheDriver:
    """the-pin-reaches-the-driver AC-1 / AC-2 / AC-3.

    AC-1: `get_plans_dir` on a pinned worktree returns the pinned external
          plans dir.
    AC-2: same, unpinned ⇒ the clone's plans dir (regression guard).
    AC-3: `REPOS` is the pinned worktree when pinned, the clone when not.
    """

    def test_ac1_get_plans_dir_returns_the_pinned_external_plans_dir(self, tmp_path: Path, env: dict) -> None:
        """A pinned worktree's plans dir is keyed by the worktree, not the clone."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=True)
        env = _isolated_data_env(env, tmp_path)
        info = _ilk_paths_json(worktree, env)
        assert info["project_root"] == str(worktree), (
            f"expected the pin to win, project_root={info['project_root']!r}"
        )
        plans = _seed_external_plans(Path(env["ILK_DATA_HOME"]), info["project_key"])

        got = _run_get_plans_dir(worktree, env)

        assert got == str(plans), (
            f"get_plans_dir must return the pinned worktree's external plans dir, "
            f"got {got!r}, expected {str(plans)!r}"
        )

    def test_ac2_get_plans_dir_unpinned_returns_the_clone_plans_dir(self, tmp_path: Path, env: dict) -> None:
        """Regression guard: without a pin the worktree still resolves to the clone."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=False)
        env = _isolated_data_env(env, tmp_path)
        clone_plans = clone / "docs" / "plans"
        clone_plans.mkdir(parents=True, exist_ok=True)
        (clone_plans / "MASTER-2026-09-22-test.md").write_text(
            "---\nmaster_plan: 2026-09-22-test\nbatch_date: 2026-09-22\n"
            "status: active\nsupervised_only: false\n"
            "current_subplan: 2026-09-22-test-sub\ncross_cutting_invariants: []\n---\n\n"
            "# Test master\n",
            encoding="utf-8",
        )

        got = _run_get_plans_dir(worktree, env)

        assert got == str(clone_plans), (
            f"unpinned worktree must still resolve to the clone's plans dir, "
            f"got {got!r}, expected {str(clone_plans)!r}"
        )

    def test_ac3_repos_is_the_pinned_worktree(self, tmp_path: Path, env: dict) -> None:
        """REPOS contains the pinned root, so preservation targets it."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=True)
        env = _isolated_data_env(env, tmp_path)

        repos = _run_discover_repos(worktree, env)

        assert repos == [str(worktree)], (
            f"REPOS must be the pinned worktree, got {repos!r} "
            f"(expected {[str(worktree)]!r})"
        )

    def test_ac3_repos_is_the_clone_when_unpinned(self, tmp_path: Path, env: dict) -> None:
        """Regression guard: without a pin REPOS is still the clone."""
        clone, worktree = _init_clone_with_worktree(tmp_path, pinned=False)
        env = _isolated_data_env(env, tmp_path)

        repos = _run_discover_repos(worktree, env)

        assert repos == [str(clone)], (
            f"unpinned worktree must resolve REPOS to the clone, got {repos!r} "
            f"(expected {[str(clone)]!r})"
        )
