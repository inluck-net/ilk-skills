"""Tests for release_train check + prove.

Sub-plan: a-release-is-proven-before-it-is-cut, step 0 (pins) + step 1 (impl).

Each test builds a throwaway git repo (with tags) under ``tmp_path``,
a fake data root (``ILK_DATA_HOME``), and a tiny fake "suite" (a test
directory of 3 trivial tests whose pass/fail the test controls).  They
never run this repo's real suite, never push, never touch the real
``~/.ilk-data``.

The six acceptance criteria:
  AC-1  check is eligible when preconditions hold; each precondition
        flip produces the named reason.
  AC-2  prove with all-green suite and baseline of ``[]``: proven, 0 new ids.
  AC-3  prove with one red test: refused, naming the node id.
  AC-4  prove with missing baseline: refused ``could_not_compare``.
  AC-5  Phase 1 runs in a clone (not a worktree).
  AC-6  nothing outside ``<data_dir>/runtime/release/`` and tmp clone.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train import check, prove  # noqa: E402
import safety_case  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _stub_safety_case_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub safety_case.run to always return pass.

    prove() now runs the safety case after Phase 1.  The old tests' fake
    projects don't have the golden-batch / teeth fixtures, so we stub the
    call to always pass.  The new tests in
    test_a_release_runs_the_safety_case.py exercise the real integration.
    """
    def _pass(*a, **kw):
        return {
            "verdict": "pass",
            "tree": "stub",
            "head": "stub",
            "components": [
                {"name": "invariants", "ok": True, "seconds": 1, "budget_seconds": 120, "exit": 0, "tail": ""},
                {"name": "golden", "ok": True, "seconds": 1, "budget_seconds": 120, "exit": 0, "tail": ""},
                {"name": "teeth", "ok": True, "seconds": 1, "budget_seconds": 900, "exit": 0, "tail": ""},
            ],
            "writer": "driver",
        }
    monkeypatch.setattr(safety_case, "run", _pass)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_project(
    tmp_path: Path,
    release_train: bool = True,
    failing: bool = False,
) -> Path:
    """Create a minimal git repo with one commit, a tag, and a fake suite.

    The project includes ``test_trivial.py`` (3 tests; if *failing* is True
    the second one asserts False) and a ``.ilk-launch.json`` with a suite
    config so ``prove`` can discover the invocation.
    """
    project = tmp_path / "project"
    project.mkdir()

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, check=True, capture_output=True,
    )

    # .ilk-launch.json — includes suite config for prove
    (project / ".ilk-launch.json").write_text(json.dumps({
        "ship": {
            "release_train": release_train,
            "suite": {
                "command": "python3 -m pytest",
                "flags": [],
            },
        },
    }))

    # Initial commit + tag
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
        cwd=project, check=True, capture_output=True,
    )

    # Fake test suite (in HEAD, not in the tagged commit)
    assertion = "False" if failing else "True"
    (project / "test_trivial.py").write_text(textwrap.dedent(f"""\
        def test_one():
            assert True

        def test_two():
            assert {assertion}

        def test_three():
            assert True
    """))

    # Second commit (HEAD != tag)
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add tests"],
        cwd=project, check=True, capture_output=True,
    )

    return project


def _make_data_dir(tmp_path: Path) -> Path:
    """Create a minimal data directory structure."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime = data_dir / "runtime" / "release"
    runtime.mkdir(parents=True)
    return data_dir


def _write_baseline(data_dir: Path, tag: str, invocation: str, ids: list) -> None:
    """Write a baseline file for the given tag and invocation."""
    baselines_dir = data_dir / "runtime" / "release" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    key = f"{tag}_{invocation.replace(' ', '_')}"
    (baselines_dir / f"{key}.json").write_text(json.dumps(ids))


# ── AC-1: check eligibility ─────────────────────────────────────────────────

class TestCheckEligible:
    """check is eligible when all preconditions hold."""

    def test_eligible_when_all_preconditions_hold(self, tmp_path: Path) -> None:
        """tmp repo with tag v0.0.1, release_train=true, clean tree, no runner."""
        project = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        result = check(project, data_dir)

        assert result["eligible"] is True, f"expected eligible, got {result}"
        assert result["reason"] == ""
        assert result["last_tag"] == "v0.0.1"
        assert len(result["head"]) == 40  # full sha


class TestCheckRefusesWhenFlagFalse:
    """check refuses when ship.release_train is not true."""

    def test_refuses_when_flag_false(self, tmp_path: Path) -> None:
        """release_train=false → not eligible, reason names the flag."""
        project = _make_fake_project(tmp_path, release_train=False)
        data_dir = _make_data_dir(tmp_path)

        result = check(project, data_dir)

        assert result["eligible"] is False
        assert "release_train" in result["reason"].lower() or "flag" in result["reason"].lower()


class TestCheckRefusesWhenKillSwitchPresent:
    """check refuses when the kill switch file exists."""

    def test_refuses_when_kill_switch(self, tmp_path: Path) -> None:
        """release-train.disabled present → not eligible."""
        project = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)
        # Create kill switch in the data root
        data_dir.parent.joinpath("release-train.disabled").touch()

        result = check(project, data_dir)

        assert result["eligible"] is False
        assert "kill" in result["reason"].lower() or "disabled" in result["reason"].lower()


class TestCheckRefusesWhenHeadEqualsTag:
    """check refuses when HEAD equals the latest tag."""

    def test_refuses_when_head_equals_tag(self, tmp_path: Path) -> None:
        """HEAD == tag → not eligible, nothing to release."""
        project = _make_fake_project(tmp_path)
        # Reset HEAD to the tag
        subprocess.run(
            ["git", "reset", "--hard", "v0.0.1"],
            cwd=project, check=True, capture_output=True,
        )
        data_dir = _make_data_dir(tmp_path)

        result = check(project, data_dir)

        assert result["eligible"] is False
        assert "tag" in result["reason"].lower() or "head" in result["reason"].lower()


class TestCheckRefusesWhenDirtyTree:
    """check refuses when the working tree is dirty."""

    def test_refuses_when_dirty_tree(self, tmp_path: Path) -> None:
        """Uncommitted changes → not eligible."""
        project = _make_fake_project(tmp_path)
        (project / "dirty.txt").write_text("uncommitted")
        data_dir = _make_data_dir(tmp_path)

        result = check(project, data_dir)

        assert result["eligible"] is False
        assert "dirty" in result["reason"].lower() or "clean" in result["reason"].lower()


class TestCheckRefusesWhenRunnerLive:
    """check refuses when a runner is live for this project."""

    def test_refuses_when_runner_live(self, tmp_path: Path) -> None:
        """A live running.pid → not eligible."""
        project = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)
        # Create a running.pid with a live process (sleep 30)
        launcher_dir = data_dir / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(["sleep", "30"])
        (launcher_dir / "running.pid").write_text(str(proc.pid))

        try:
            result = check(project, data_dir)
            assert result["eligible"] is False
            assert "runner" in result["reason"].lower() or "running" in result["reason"].lower()
        finally:
            proc.terminate()
            proc.wait()


# ── AC-2: prove with all-green suite ────────────────────────────────────────

class TestProveAllGreen:
    """prove with all-green suite and baseline of []: proven, 0 new ids."""

    def test_proven_when_all_green(self, tmp_path: Path) -> None:
        """Fake suite all green, baseline []: proven, proof file has 0 new ids."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        # Write baseline of [] for v0.0.1
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        result = prove(project, data_dir)

        assert result["proven"] is True
        assert result["new_failing_ids"] == []
        assert result["proof_file"] is not None
        assert result["proof_file"].exists()

        # Verify proof file content
        proof = json.loads(result["proof_file"].read_text())
        assert proof["head"] == subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project, capture_output=True, text=True,
        ).stdout.strip()
        assert proof["last_tag"] == "v0.0.1"
        assert proof["verdict"] == "proven"


# ── AC-3: prove with one red test ───────────────────────────────────────────

class TestProveOneRed:
    """prove with one fake test turned red: refused, naming the node id."""

    def test_refused_when_one_red(self, tmp_path: Path) -> None:
        """One test red → refused, proof file says refused, names the node id."""
        project = _make_fake_project(tmp_path, failing=True)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert result["proof_file"] is not None
        proof = json.loads(result["proof_file"].read_text())
        assert proof["verdict"] == "refused"
        assert len(result["new_failing_ids"]) > 0
        # The failing test should be in the new_failing_ids
        assert any("test_two" in id for id in result["new_failing_ids"])


# ── AC-4: prove with missing baseline ───────────────────────────────────────

class TestProveMissingBaseline:
    """prove with no baseline for v0.0.1: refused could_not_compare."""

    def test_refused_when_no_baseline(self, tmp_path: Path) -> None:
        """No baseline → refused with could_not_compare."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        # No baseline written

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert "could_not_compare" in result["reason"] or "baseline" in result["reason"].lower()
        assert result["proof_file"] is not None
        proof = json.loads(result["proof_file"].read_text())
        assert proof["verdict"] == "refused"


# ── AC-5: Phase 1 runs in a clone ───────────────────────────────────────────

class TestProveRunsInClone:
    """Phase 1 runs in a clone: cwd is not the project, git-dir is .git."""

    def test_prove_uses_clone_not_worktree(self, tmp_path: Path) -> None:
        """The suite subprocess cwd must not be the project; git-dir must be .git."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        result = prove(project, data_dir)

        assert result["proven"] is True
        # The proof file should record the suite cwd
        assert result["proof_file"] is not None
        proof = json.loads(result["proof_file"].read_text())
        assert "suite_cwd" in proof, "proof file must record suite_cwd"

        suite_cwd = Path(proof["suite_cwd"])
        # Must not be the project itself
        assert suite_cwd != project, "suite must run in a clone, not the project"
        # Must be a real .git dir (not a worktree gitdir file)
        git_dir_result = subprocess.run(
            ["git", "-C", str(suite_cwd), "rev-parse", "--git-dir"],
            capture_output=True, text=True,
        )
        assert git_dir_result.stdout.strip() == ".git", (
            f"expected .git (real dir), got {git_dir_result.stdout.strip()!r}"
        )


# ── AC-6: nothing outside data_dir/runtime/release and tmp clone ────────────

class TestProveWritesOnlyToReleaseDir:
    """nothing outside <data_dir>/runtime/release/ and the tmp clone is written."""

    def test_no_writes_outside_release_dir(self, tmp_path: Path) -> None:
        """Snapshot project tree and data root before/after prove; diff must be empty
        outside data_dir/runtime/release/ and the tmp clone."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Snapshot before
        before_project = set(p.relative_to(project) for p in project.rglob("*"))
        before_data = set(p.relative_to(data_dir) for p in data_dir.rglob("*"))

        result = prove(project, data_dir)

        # Snapshot after
        after_project = set(p.relative_to(project) for p in project.rglob("*"))
        after_data = set(p.relative_to(data_dir) for p in data_dir.rglob("*"))

        # Project tree must be unchanged
        new_project_files = after_project - before_project
        assert new_project_files == set(), (
            f"prove wrote to project tree: {new_project_files}"
        )

        # Data dir: only runtime/release/ should have new files
        new_data_files = after_data - before_data
        for f in new_data_files:
            assert str(f).startswith("runtime/release/"), (
                f"prove wrote outside runtime/release/: {f}"
            )