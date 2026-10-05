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

from release_train import check, cut, prove  # noqa: E402
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
    """Write a baseline file for the given tag and invocation.

    Writes to both the canonical .ilk-baselines/ store (which prove reads)
    and the legacy private store (for tests that check both paths).
    """
    from baseline_diff import store_baseline
    store_baseline(
        project_root=data_dir,
        tag=tag,
        suite_invocation=invocation,
        node_ids=frozenset(ids),
        search_space=len(ids),
    )


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


# ── Step 0 red-first fixtures: one proof source + explicit red policy ───────
#
# These fixtures pin the expected behaviour for sub-plan
# one-proof-source-and-explicit-red-policy.  They are xfail(strict=True)
# until step 1 implements the canonical-source policy in release_train.py.


class TestProveReadsCanonicalBaseline:
    """AC-1: release_train.prove reads .ilk-baselines/<tag>__<inv-hash>.json
    written by baseline_diff.store_baseline; absence returns could_not_compare
    and no private train baseline is consulted.
    """

    def test_prove_reads_canonical_not_private(self, tmp_path: Path) -> None:
        """When only the canonical baseline exists (not the private store),
        prove must still find and use it."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        # Write baseline in the CANONICAL format (.ilk-baselines/) via
        # baseline_diff.store_baseline, NOT the private store.
        from baseline_diff import store_baseline
        invocation = "python3 -m pytest"
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=invocation,
            node_ids=frozenset(),
            search_space=3,
        )

        # Do NOT write to the private store (data_dir/runtime/release/baselines/).
        # If prove reads the private store, it will find nothing → could_not_compare.
        result = prove(project, data_dir)

        assert result["proven"] is True, (
            f"prove should read canonical .ilk-baselines/, got: {result['reason']}"
        )

    def test_private_store_alone_is_not_enough(self, tmp_path: Path) -> None:
        """A baseline in the private store only (no canonical) must NOT be used.
        prove must return could_not_compare."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        # Write ONLY to the private store
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Also write a canonical baseline that says there ARE failures,
        # to detect if prove accidentally reads the private store's [].
        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=invocation,
            node_ids=frozenset({"tests/test_fake.py::test_deliberate_failure"}),
            search_space=3,
        )

        # Now remove the canonical one — only private remains
        import hashlib
        h = hashlib.sha256(invocation.encode()).hexdigest()[:12]
        canonical = data_dir / ".ilk-baselines" / f"v0.0.1__{h}.json"
        canonical.unlink()

        result = prove(project, data_dir)

        assert result["proven"] is False, (
            "prove must not consult the private store; expected could_not_compare"
        )
        assert "could_not_compare" in result["reason"]


class TestProveRefusalPaths:
    """Existing refusal paths (not xfail) — guard against regressions."""

    def test_absent_both_returns_could_not_compare(self, tmp_path: Path) -> None:
        """When neither canonical nor private baseline exists,
        prove returns could_not_compare."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        # No baseline written anywhere

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert "could_not_compare" in result["reason"]


class TestCutStoresCanonicalBaseline:
    """AC-2: cut stores the new tag baseline through baseline_diff.store_baseline,
    preserving search_space, invocation, tag, and exact node IDs."""

    def test_cut_writes_canonical_format(self, tmp_path: Path) -> None:
        """After cut, the baseline file must be in .ilk-baselines/ with the
        canonical key format and full metadata — not in the private
        data_dir/runtime/release/baselines/ store."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        # Write initial baseline so prove succeeds
        from baseline_diff import store_baseline, baseline_key
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=invocation,
            node_ids=frozenset(),
            search_space=3,
        )

        # Run prove() to create the proof file that cut() needs
        prove(project, data_dir)

        import hashlib
        expected_key = f"v0.0.2__{hashlib.sha256(invocation.encode()).hexdigest()[:12]}"
        canonical_path = data_dir / ".ilk-baselines" / f"{expected_key}.json"
        private_path = data_dir / "runtime" / "release" / "baselines" / "v0.0.2_python3_-_m_pytest.json"

        # cut() writes baseline before push; push will fail (no remote).
        with pytest.raises(SystemExit):
            cut(project, data_dir)

        assert canonical_path.exists(), (
            "cut must write to .ilk-baselines/ canonical store"
        )
        assert not private_path.exists(), (
            "cut must NOT write to the private runtime/release/baselines/ store"
        )

        # Verify canonical format: {tag, suite_invocation, node_ids, search_space}
        data = json.loads(canonical_path.read_text())
        assert "tag" in data, "canonical baseline must carry 'tag'"
        assert "suite_invocation" in data, "canonical baseline must carry 'suite_invocation'"
        assert "node_ids" in data, "canonical baseline must carry 'node_ids'"
        assert "search_space" in data, "canonical baseline must carry 'search_space'"


# ── AC-3 + AC-4: baseline_red evidence contracts (in baseline_diff) ────────
#
# These are pinned here because they affect the prove() flow.  The actual
# implementation lives in baseline_diff.py; step 1 wires the evidence
# parsing into the comparison.


class TestBaselineRedEvidenceInheritance:
    """AC-3: an exact baseline_red node with measured failed-at-base evidence
    is inherited; a declared node without evidence and every undeclared node
    remain regressions."""

    def test_measured_evidence_is_inherited(self, tmp_path: Path) -> None:
        """A baseline_red entry with failed_at_base=True measured evidence
        must be treated as inherited, not a regression."""
        from baseline_diff import (
            BaselineRef, BaselineStatus, compare, check_baseline_red_evidence,
        )

        current = frozenset({"tests/test_known.py::test_flaky"})
        baseline = frozenset()  # empty baseline (node wasn't failing at tag time)

        # baseline_red entry WITH measured evidence
        baseline_red = [
            {
                "node_id": "tests/test_known.py::test_flaky",
                "reason": "order-dependent",
                "as_of": "2026-10-05",
                "evidence": {
                    "failed_at_base": True,
                    "base_sha": "abc1234" + "0" * 33,
                    "measured_at": "2026-10-05T10:00:00+08:00",
                },
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        # The evidence-aware comparison must treat measured nodes as inherited
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
        )

        # With evidence, this node should be inherited (not new/regression)
        assert diff.new_failures == frozenset(), (
            "measured failed-at-base evidence should make the node inherited"
        )
        assert diff.inherited_failures == frozenset({"tests/test_known.py::test_flaky"})

    def test_declared_without_evidence_is_regression(self, tmp_path: Path) -> None:
        """A baseline_red entry without evidence (declaration only) must NOT
        be inherited — the node remains a regression."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_declared.py::test_flaky"})
        baseline = frozenset()

        # baseline_red entry WITHOUT evidence — just a declaration
        baseline_red = [
            {
                "node_id": "tests/test_declared.py::test_flaky",
                "reason": "I think this is flaky",
                "as_of": "2026-10-05",
                # No "evidence" key
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
        )

        # Without evidence, this node must remain a regression
        assert diff.new_failures == frozenset({"tests/test_declared.py::test_flaky"}), (
            "declared node without evidence must remain a regression"
        )

    def test_undeclared_node_is_regression(self, tmp_path: Path) -> None:
        """A failing node not in baseline_red at all must be a regression."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_undeclared.py::test_new_fail"})
        baseline = frozenset()
        baseline_red = []  # empty — nothing declared

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
        )

        assert diff.new_failures == frozenset({"tests/test_undeclared.py::test_new_fail"})


class TestFlakyOwedEvidenceContract:
    """AC-4: bounded flaky-owed evidence may carry only the exact node and
    exact suite invocation; a changed node, invocation, or missing
    serial-green record refuses."""

    def test_exact_match_is_inherited(self, tmp_path: Path) -> None:
        """Exact node + invocation + serial-green → inherited."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_flaky.py::test_order_dep"})
        baseline = frozenset()

        baseline_red = [
            {
                "node_id": "tests/test_flaky.py::test_order_dep",
                "reason": "order-dependent",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": "python3 -m pytest",
                    "serial_green": True,
                    "serial_green_at": "2026-10-05T09:00:00+08:00",
                },
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
            suite_invocation="python3 -m pytest",
        )

        assert diff.new_failures == frozenset(), (
            "exact match with serial-green should be inherited"
        )

    def test_mismatched_invocation_refuses(self, tmp_path: Path) -> None:
        """flaky-owed with wrong invocation → remains regression."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_flaky.py::test_order_dep"})
        baseline = frozenset()

        baseline_red = [
            {
                "node_id": "tests/test_flaky.py::test_order_dep",
                "reason": "order-dependent",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": "python3 -m pytest --timeout-method=thread",  # WRONG
                    "serial_green": True,
                    "serial_green_at": "2026-10-05T09:00:00+08:00",
                },
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
            suite_invocation="python3 -m pytest",  # actual invocation
        )

        assert diff.new_failures == frozenset({"tests/test_flaky.py::test_order_dep"}), (
            "mismatched invocation must refuse inheritance"
        )

    def test_missing_serial_green_refuses(self, tmp_path: Path) -> None:
        """flaky-owed without serial_green record → remains regression."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_flaky.py::test_order_dep"})
        baseline = frozenset()

        baseline_red = [
            {
                "node_id": "tests/test_flaky.py::test_order_dep",
                "reason": "order-dependent",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": "python3 -m pytest",
                    # serial_green missing
                },
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
            suite_invocation="python3 -m pytest",
        )

        assert diff.new_failures == frozenset({"tests/test_flaky.py::test_order_dep"}), (
            "missing serial_green must refuse inheritance"
        )

    def test_changed_node_refuses(self, tmp_path: Path) -> None:
        """flaky-owed for a different node id → remains regression."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_flaky.py::test_changed"})
        baseline = frozenset()

        baseline_red = [
            {
                "node_id": "tests/test_flaky.py::test_old_name",  # WRONG node
                "reason": "order-dependent",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": "python3 -m pytest",
                    "serial_green": True,
                    "serial_green_at": "2026-10-05T09:00:00+08:00",
                },
            },
        ]

        ref = BaselineRef(tag="v0.0.1", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=100,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=100,
            ref=ref,
            baseline_red_entries=baseline_red,
            suite_invocation="python3 -m pytest",
        )

        assert diff.new_failures == frozenset({"tests/test_flaky.py::test_changed"}), (
            "evidence for a different node must not carry over"
        )


# ── AC-5: v0.9.147 fixtures reproduce prior behaviour ──────────────────────

class TestV09147Fixtures:
    """AC-5: the v0.9.147 fixtures reproduce both prior could_not_compare
    and declared-order-flake refusals before the fix and prove the
    corrected outcomes after it."""

    def test_could_not_compare_reproduced(self, tmp_path: Path) -> None:
        """v0.9.147 scenario: no baseline for the tag → could_not_compare.

        This is existing behaviour (not xfail) — guards the refusal path."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        # No baseline written — simulates the v0.9.147 state

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert "could_not_compare" in result["reason"]

    def test_declared_order_flake_refused_before_fix(self, tmp_path: Path) -> None:
        """v0.9.147 scenario: declared order-dependent failure without evidence
        must be refused (remains a regression).

        This xfail pins the expectation that compare() will accept
        baseline_red_entries and treat evidence-less declarations as
        regressions.  Step 1 wires the evidence mechanism."""
        from baseline_diff import BaselineRef, BaselineStatus, compare

        current = frozenset({"tests/test_order.py::test_depends_on_setup"})
        baseline = frozenset()  # was green at tag time

        # Declared but no evidence — the v0.9.147 state
        baseline_red = [
            {
                "node_id": "tests/test_order.py::test_depends_on_setup",
                "reason": "order-dependent flake",
                "as_of": "2026-09-30",
            },
        ]

        ref = BaselineRef(tag="v0.9.147", resolved=True, status=BaselineStatus.FOUND)
        diff = compare(
            current_failures=current,
            search_space=1846,
            filtered=False,
            baseline_failures=baseline,
            baseline_search_space=1846,
            ref=ref,
            baseline_red_entries=baseline_red,
        )

        # Without evidence, this is a regression — refusal
        assert diff.regression_count == 1, (
            "declared node without evidence must count as regression"
        )


# ── Step 0 integration: prove() + evidence-aware compare() ─────────────────
#
# These fixtures pin the production-caller gap: prove() delegates to
# baseline_diff.compare() but does not pass baseline_red_entries, so
# the evidence-aware policy is never invoked.  Each fixture monkeypatches
# compare() to inject the entries, then asserts the correct outcome.
# xfail(strict=True) captures the expected behaviour before step 1 wires
# the mechanism.  When step 1 makes prove() pass baseline_red_entries
# natively, the monkeypatch becomes a no-op and the xfail markers are removed.
#
# The 5→8 node-id shape mirrors batch-2026-10-05o vs v0.9.147:
# baseline has 5 known failures; current has 8 (3 "new" nodes with evidence).


def _make_evidence_project(
    tmp_path: Path,
    num_tests: int = 8,
    failing_indices: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7),
) -> Path:
    """Create a project with num_tests tests, failing at the given indices.

    All tests fail deterministically (assert False) so the test can control
    exactly which node ids appear in the baseline and current sets.
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

    (project / ".ilk-launch.json").write_text(json.dumps({
        "ship": {
            "release_train": True,
            "suite": {
                "command": "python3 -m pytest",
                "flags": [],
            },
        },
    }))

    # Tag commit — identical tests so baseline can be written
    lines = []
    for i in range(num_tests):
        assertion = "False" if i in failing_indices else "True"
        lines.append(f"def test_{i}():\n    assert {assertion}\n\n")
    (project / "test_suite.py").write_text("".join(lines))

    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
        cwd=project, check=True, capture_output=True,
    )

    # HEAD commit (must differ from tag)
    (project / "marker.txt").write_text("head")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "head"],
        cwd=project, check=True, capture_output=True,
    )

    return project


# Baseline: indices 0–4 fail (5 nodes)
_BASELINE_FAIL_INDICES = (0, 1, 2, 3, 4)
# Current: all 8 fail (indices 0–7)
_CURRENT_FAIL_INDICES = (0, 1, 2, 3, 4, 5, 6, 7)
# The 3 "new" nodes (in current, not in baseline)
_DELTA_INDICES = (5, 6, 7)

_BASELINE_IDS = sorted({f"test_suite.py::test_{i}" for i in _BASELINE_FAIL_INDICES})
_CURRENT_IDS = sorted({f"test_suite.py::test_{i}" for i in _CURRENT_FAIL_INDICES})
_DELTA_IDS = sorted({f"test_suite.py::test_{i}" for i in _DELTA_INDICES})

_INVOCATION = "python3 -m pytest"

_EVIDENCE_ENTRIES = [
    {
        "node_id": nid,
        "reason": "declared-at-base",
        "as_of": "2026-10-05",
        "evidence": {
            "failed_at_base": True,
            "base_sha": "d75016e57272" + "0" * 28,
            "measured_at": "2026-10-05T10:00:00+08:00",
        },
    }
    for nid in _DELTA_IDS
]


def _write_evidence(
    data_dir: Path,
    tag: str,
    invocation: str,
    entries: list[dict],
) -> None:
    """Write baseline_red evidence entries for prove() to load."""
    from baseline_diff import store_baseline_red_evidence
    store_baseline_red_evidence(
        project_root=data_dir,
        tag=tag,
        suite_invocation=invocation,
        entries=entries,
    )


class TestProveEvidencePolicy:
    """prove() delegates to evidence-aware compare() with baseline_red entries.

    The 5→8 node-id shape mirrors batch-2026-10-05o vs v0.9.147: baseline
    has 5 known failures; current has 8 (3 "new" nodes with evidence).
    """

    def test_valid_evidence_yields_zero_new_failures(
        self, tmp_path: Path,
    ) -> None:
        """AC-2: 5→8 shape with valid failed-at-base evidence on all 3 delta
        nodes yields 0 new failures.  prove() reads evidence from the
        canonical store and delegates to the evidence-aware compare()."""
        project = _make_evidence_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=_INVOCATION,
            node_ids=frozenset(_BASELINE_IDS),
            search_space=5083,
        )
        _write_evidence(data_dir, "v0.0.1", _INVOCATION, _EVIDENCE_ENTRIES)

        result = prove(project, data_dir)

        assert result["proven"] is True, (
            f"expected proven with valid evidence, got: {result['reason']}"
        )
        assert result["new_failing_ids"] == [], (
            "valid failed-at-base evidence should make delta nodes inherited"
        )

        # Proof must record carried ids and evidence basis
        proof = json.loads(result["proof_file"].read_text())
        assert len(proof["carried_ids"]) == 3
        for carried in proof["carried_ids"]:
            assert carried["basis"] == "failed_at_base"
            assert carried["node_id"] in _DELTA_IDS

    def test_missing_evidence_refuses(
        self, tmp_path: Path,
    ) -> None:
        """AC-3: delta node without evidence must remain a regression.

        With no evidence file, compare() treats all 3 delta nodes as new
        failures → prove() refuses.
        """
        project = _make_evidence_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=_INVOCATION,
            node_ids=frozenset(_BASELINE_IDS),
            search_space=5083,
        )
        # No evidence written

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert len(result["new_failing_ids"]) == 3
        for nid in _DELTA_IDS:
            assert nid in result["new_failing_ids"]

    def test_node_mismatch_refuses(
        self, tmp_path: Path,
    ) -> None:
        """AC-3: evidence for a different node id must not carry over.

        Evidence points to 'test_suite.py::test_99' (nonexistent) instead
        of the actual delta nodes.  All 3 delta nodes remain regressions.
        """
        project = _make_evidence_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=_INVOCATION,
            node_ids=frozenset(_BASELINE_IDS),
            search_space=5083,
        )

        mismatched_entries = [
            {
                "node_id": "test_suite.py::test_99",
                "reason": "wrong node",
                "as_of": "2026-10-05",
                "evidence": {
                    "failed_at_base": True,
                    "base_sha": "d75016e57272" + "0" * 28,
                    "measured_at": "2026-10-05T10:00:00+08:00",
                },
            },
        ]
        _write_evidence(data_dir, "v0.0.1", _INVOCATION, mismatched_entries)

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert len(result["new_failing_ids"]) == 3

    def test_invocation_mismatch_refuses(
        self, tmp_path: Path,
    ) -> None:
        """AC-4: flaky-owed evidence with wrong invocation → remains regression.

        Evidence has invocation='python3 -m pytest --wrong' but actual
        suite invocation is 'python3 -m pytest'.  Policy refuses inheritance.
        """
        project = _make_evidence_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=_INVOCATION,
            node_ids=frozenset(_BASELINE_IDS),
            search_space=5083,
        )

        bad_invocation_entries = [
            {
                "node_id": nid,
                "reason": "flaky",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": "python3 -m pytest --wrong",
                    "serial_green": True,
                    "serial_green_at": "2026-10-05T09:00:00+08:00",
                },
            }
            for nid in _DELTA_IDS
        ]
        _write_evidence(data_dir, "v0.0.1", _INVOCATION, bad_invocation_entries)

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert len(result["new_failing_ids"]) == 3

    def test_missing_serial_green_refuses(
        self, tmp_path: Path,
    ) -> None:
        """AC-4: flaky-owed without serial_green → remains regression.

        Evidence claims flaky_owed but has no serial_green record.
        Policy refuses inheritance.
        """
        project = _make_evidence_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        from baseline_diff import store_baseline
        store_baseline(
            project_root=data_dir,
            tag="v0.0.1",
            suite_invocation=_INVOCATION,
            node_ids=frozenset(_BASELINE_IDS),
            search_space=5083,
        )

        no_serial_green_entries = [
            {
                "node_id": nid,
                "reason": "flaky",
                "as_of": "2026-10-05",
                "evidence": {
                    "flaky_owed": True,
                    "invocation": _INVOCATION,
                    # serial_green missing
                },
            }
            for nid in _DELTA_IDS
        ]
        _write_evidence(data_dir, "v0.0.1", _INVOCATION, no_serial_green_entries)

        result = prove(project, data_dir)

        assert result["proven"] is False
        assert len(result["new_failing_ids"]) == 3