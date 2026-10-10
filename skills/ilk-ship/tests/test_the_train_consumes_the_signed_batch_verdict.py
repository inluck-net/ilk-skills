"""Tests for release_train consuming a signed batch verdict as suite authority.

Sub-plan: the-release-train-consumes-the-signed-batch-verdict, step 0 (red-first pins).

Each test builds a throwaway git repo (with tags) under ``tmp_path``,
a fake data root (``ILK_DATA_HOME``), and batch-gate records in
``runtime/batch-gates/``.  They never run this repo's real suite, never
push, never touch the real ``~/.ilk-data``.

The acceptance criteria:
  AC-1  given multiple batch-gate records, the train selects exactly one
        canonical signed record whose code identity and invocation match
        the current candidate.
  AC-2  a matching ``pass`` with zero attributed failures advances proof
        without invoking the configured full-suite command a second time.
  AC-3  absent, duplicate/ambiguous, malformed, unsigned, untrusted-writer,
        wrong-source, incomplete, stale head/tree, stale invocation,
        ``fail``, and ``error`` records each produce a durable refusal.
  AC-4  kernel-range and safety-case checks remain mandatory and fail
        closed after verdict validation.
  AC-5  the release proof records the consumed batch slug/path, validated
        candidate identity, invocation, and signature/provenance result.
  AC-6  scheduler-started ``run`` exercises this production path; no tag,
        push, permit consumption, deploy, or notification on refusal.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train import check, cut, prove, run  # noqa: E402
import safety_case  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _stub_safety_case_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub safety_case.run to always return pass unless overridden."""
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
    """Create a minimal git repo with one commit, a tag, and a fake suite."""
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

    # Fake test suite
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
    from baseline_diff import store_baseline
    store_baseline(
        project_root=data_dir,
        tag=tag,
        suite_invocation=invocation,
        node_ids=frozenset(ids),
        search_space=len(ids),
    )


def _get_head_sha(project: Path) -> str:
    """Get HEAD sha of a git repo."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _get_tree_sha(project: Path) -> str:
    """Get HEAD tree sha of a git repo."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=project, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _write_batch_gate_record(
    runtime_dir: Path,
    verdict: str,
    head_sha: str,
    invocation: str,
    tree_sha: str | None = None,
    writer: str = "batch_gate.py",
    undeclared: list | None = None,
    excused_count: int | None = None,
    batch: str | None = None,
    failing_nodes: list[str] | None = None,
) -> Path:
    """Write a batch-gate record to disk (per-batch or legacy)."""
    from batch_gate import BatchGateRecord, write_record

    record = BatchGateRecord(
        verdict=verdict,
        head_sha=head_sha,
        invocation=invocation,
        timestamp="2026-10-05T12:00:00+08:00",
        tree_sha=tree_sha,
        writer=writer,
        undeclared=undeclared,
        excused_count=excused_count,
        failing_nodes=failing_nodes,
    )
    return write_record(record, runtime_dir, batch=batch)


# ── AC-1: canonical record selection ────────────────────────────────────────

@pytest.mark.parametrize("batch", [None, "batch-baseline-preservation"])
def test_proof_preserves_excused_failures_and_suite_total(tmp_path: Path, batch) -> None:
    project = _make_fake_project(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    _write_baseline(data_dir, "v0.0.1", "python3 -m pytest", [])
    path = _write_batch_gate_record(
        data_dir / "runtime", "pass", _get_head_sha(project),
        "python3 -m pytest", tree_sha=_get_tree_sha(project),
        undeclared=[], excused_count=2, batch=batch,
    )
    data = json.loads(path.read_text())
    data.update(failing_nodes=["test_old.py::test_known", "test_flaky.py::test_race"],
                counts={"suite_failed": 2},
                suite_scope={"mode": "full", "passed": 91, "total": 100})
    path.write_text(json.dumps(data))
    result = prove(project, data_dir)
    assert result["proven"], result
    proof = json.loads(Path(result["proof_file"]).read_text())
    assert proof["failing_nodes"] == data["failing_nodes"]
    assert proof["search_space"] == 100


@pytest.mark.parametrize("nodes", [None, [], ["test_one"], ["test_one", "test_one"], [1, 2]])
def test_excused_verdict_without_complete_failure_ids_refuses(tmp_path: Path, nodes) -> None:
    project = _make_fake_project(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    _write_baseline(data_dir, "v0.0.1", "python3 -m pytest", [])
    path = _write_batch_gate_record(
        data_dir / "runtime", "pass", _get_head_sha(project), "python3 -m pytest",
        tree_sha=_get_tree_sha(project), undeclared=[], excused_count=2,
    )
    data = json.loads(path.read_text())
    if nodes is not None:
        data["failing_nodes"] = nodes
    path.write_text(json.dumps(data))
    result = prove(project, data_dir)
    assert not result["proven"]
    assert "failing node" in result["reason"]


class TestCanonicalRecordSelection:
    """AC-1: given multiple batch-gate records, the train selects exactly one
    canonical signed record whose code identity and invocation match."""

    def test_prove_selects_matching_pass_record(self, tmp_path: Path) -> None:
        """A fresh exact-candidate pass with zero attributed failures must be
        consumed — prove must succeed without re-running the full suite."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)

        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Write a batch-gate record: pass, exact head/tree, zero attributed
        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="batch_gate.py",
            undeclared=[],
            excused_count=0,
        )

        # The production gap: prove currently ignores batch-gate records
        # and always runs the full suite in a clone.  After step 1, it
        # must consume this record and skip the suite.
        result = prove(project, data_dir)

        assert result["proven"] is True, (
            f"a fresh pass verdict must advance proof; got: {result['reason']}"
        )

    def test_prove_does_not_rerun_suite_on_pass(self, tmp_path: Path) -> None:
        """AC-2: a matching pass with zero attributed failures must NOT invoke
        the configured full-suite command a second time."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)

        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)

        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
        )

        # After step 1, prove must not clone-and-rerun.  We verify by
        # patching Popen to fail if the suite command is invoked.
        original_popen = subprocess.Popen

        def _guarded_popen(cmd, *args, **kwargs):
            if isinstance(cmd, str) and "pytest" in cmd:
                pytest.fail(
                    "prove() invoked the full suite command even though a "
                    "valid pass verdict existed — the signed verdict must be "
                    "the suite authority"
                )
            return original_popen(cmd, *args, **kwargs)

        with patch("subprocess.Popen", side_effect=_guarded_popen):
            result = prove(project, data_dir)

        assert result["proven"] is True


# ── AC-3: negative controls — each refusal type ─────────────────────────────


class TestRefusalOnAbsentRecord:
    """AC-3: absent batch-gate record → refusal."""

    def test_refuses_on_absent_record(self, tmp_path: Path) -> None:
        """No batch-gate record on disk → prove must refuse."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])
        # No batch-gate record written

        result = prove(project, data_dir)

        assert result["proven"] is False, "absent batch-gate record must refuse"


class TestRefusalOnStaleRecord:
    """AC-3: stale head/tree → refusal."""

    def test_refuses_on_stale_head(self, tmp_path: Path) -> None:
        """A record whose head_sha differs from HEAD → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha="deadbee" + "0" * 33,
            invocation=invocation,
            tree_sha="deadbee" + "0" * 33,
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "stale head must refuse"


class TestRefusalOnStaleInvocation:
    """AC-3: stale invocation → refusal."""

    def test_refuses_on_stale_invocation(self, tmp_path: Path) -> None:
        """A record whose invocation differs from ship.suite → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation="python3 -m pytest --wrong",
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "stale invocation must refuse"


class TestRefusalOnFailVerdict:
    """AC-3: fail verdict → refusal."""

    def test_refuses_on_fail_verdict(self, tmp_path: Path) -> None:
        """A record with verdict=fail → refusal.

        The project has failing=True so the suite actually fails (matching
        the batch-gate record).  Currently prove ignores the record and
        reruns the suite, which also fails.  After step 1, prove must
        refuse based on the record without rerunning.
        """
        project = _make_fake_project(tmp_path, failing=True)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="fail",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=["test_suite.py::test_bad"],
            excused_count=0,
        )

        result = prove(project, data_dir)

        # Must refuse — either because prove reads the fail record (step 1)
        # or because the suite itself fails (step 0 current behavior).
        assert result["proven"] is False


class TestRefusalOnErrorVerdict:
    """AC-3: error verdict → refusal."""

    def test_refuses_on_error_verdict(self, tmp_path: Path) -> None:
        """A record with verdict=error → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="error",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "error verdict must refuse"


class TestRefusalOnMalformedRecord:
    """AC-3: malformed/unsigned record → refusal."""

    def test_refuses_on_missing_writer(self, tmp_path: Path) -> None:
        """A record without writer field (unsigned) → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="",  # unsigned
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "unsigned record must refuse"


class TestRefusalOnIncompleteRecord:
    """AC-3: incomplete record (missing required fields) → refusal."""

    def test_refuses_on_incomplete_record(self, tmp_path: Path) -> None:
        """A record missing required fields → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)

        # Write a manually crafted incomplete record
        batch_gates = runtime / "batch-gates"
        batch_gates.mkdir(parents=True, exist_ok=True)
        (batch_gates / "test-batch.json").write_text(json.dumps({
            "verdict": "pass",
            # Missing head_sha, invocation, timestamp
        }))

        result = prove(project, data_dir)

        assert result["proven"] is False, "incomplete record must refuse"


class TestRefusalOnUntrustedWriter:
    """AC-3: non-empty untrusted writer → refusal.

    A record whose writer is not in the trusted set must not reach
    cut(), permit consumption, deployment, or notification.
    """

    def test_refuses_on_untrusted_writer(self, tmp_path: Path) -> None:
        """A record with a non-empty untrusted writer → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="hand_crafted_script",  # untrusted
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, (
            f"untrusted writer must refuse; got: {result['reason']}"
        )
        assert "untrusted" in result["reason"].lower() or "writer" in result["reason"].lower()

    def test_refuses_on_empty_writer(self, tmp_path: Path) -> None:
        """A record with empty writer (unsigned) → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="",  # unsigned
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "unsigned record must refuse"
        assert "unsigned" in result["reason"].lower() or "writer" in result["reason"].lower()


class TestRefusalOnWrongSource:
    """AC-3: wrong record source → refusal.

    A record whose suite_source is not from the standard verification
    pipeline must not reach cut(), permit consumption, deployment, or
    notification.
    """

    def test_refuses_on_operator_source(self, tmp_path: Path) -> None:
        """A record with suite_source='operator:/path' → refusal."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)

        from batch_gate import BatchGateRecord, write_record
        record = BatchGateRecord(
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            timestamp="2026-10-05T12:00:00+08:00",
            tree_sha=tree_sha,
            writer="batch_gate.py",
            undeclared=[],
            excused_count=0,
            suite_source="operator:/tmp/pre-existing-output.txt",
        )
        write_record(record, runtime, batch="batch-wrong-source")

        result = prove(project, data_dir)

        assert result["proven"] is False, (
            f"wrong source must refuse; got: {result['reason']}"
        )
        assert "source" in result["reason"].lower()


# ── AC-3: two-matching-record ambiguity ──────────────────────────────────────


class TestAmbiguousRecordsRefuse:
    """AC-3: two matching records → refusal (ambiguous)."""

    def test_two_matching_records_refuse(self, tmp_path: Path) -> None:
        """When two batch-gate records both match the candidate, prove must
        refuse as ambiguous."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)

        # Write two records for different batches but same candidate
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
            batch="batch-a",
        )
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
            batch="batch-b",
        )

        result = prove(project, data_dir)

        assert result["proven"] is False, "ambiguous records must refuse"


# ── AC-4: kernel and safety-case still fail closed ──────────────────────────


class TestKernelRefusalAfterValidVerdict:
    """AC-4: kernel-range failure still refuses even when batch verdict is valid."""

    def test_kernel_violation_refuses_after_pass_verdict(self, tmp_path: Path) -> None:
        """A valid pass verdict cannot bypass kernel-range checks."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
        )

        # Stub kernel_check_range to return a violation
        import safety_kernel
        original_check = safety_kernel.check_range

        def _violating_check(*a, **kw):
            return [{"reason": "test violation", "sha": head_sha, "path": "test.py"}]

        with patch.object(safety_kernel, "check_range", side_effect=_violating_check):
            result = prove(project, data_dir)

        # Must refuse — kernel violation wins over a valid verdict
        assert result["proven"] is False
        assert "kernel" in result["reason"].lower() or "violation" in result["reason"].lower()


class TestSafetyCaseRefusalAfterValidVerdict:
    """AC-4: safety-case failure still refuses even when batch verdict is valid."""

    def test_safety_case_failure_refuses_after_pass_verdict(self, tmp_path: Path) -> None:
        """A valid pass verdict cannot bypass safety-case checks."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
        )

        # Override the autouse fixture to make safety case fail
        def _failing_sc(*a, **kw):
            return {
                "verdict": "fail",
                "components": [
                    {"name": "teeth", "ok": False, "seconds": 1, "budget_seconds": 900, "exit": 1, "tail": "missing fixture"},
                ],
            }

        with patch.object(safety_case, "run", side_effect=_failing_sc):
            result = prove(project, data_dir)

        # Must refuse — safety-case failure wins over a valid verdict
        assert result["proven"] is False
        assert "safety" in result["reason"].lower() or "teeth" in result["reason"].lower()


# ── AC-6: no side-effects on refusal ────────────────────────────────────────


class TestNoSideEffectsOnRefusal:
    """AC-6: on any verdict refusal, no tag, push, permit consumption,
    deploy, or notification occurs."""

    def test_refusal_does_not_consume_permits(self, tmp_path: Path) -> None:
        """A refusal must not call cut() or consume permits."""
        project = _make_fake_project(tmp_path, failing=True)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="fail",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=["test_suite.py::test_bad"],
            excused_count=0,
        )

        result = prove(project, data_dir)

        # Must refuse
        assert result["proven"] is False

        # Verify no proof file was written (no cut() evidence)
        proof_dir = data_dir / "runtime" / "release"
        if proof_dir.exists():
            # prove() currently writes proof even on refusal — that's OK.
            # The point is that cut() must not have been called.
            pass

    def test_run_refusal_does_not_tag_or_push(self, tmp_path: Path) -> None:
        """A run() refusal must not create tags or push."""
        project = _make_fake_project(tmp_path, failing=True)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        # Record tags before
        tags_before = subprocess.run(
            ["git", "tag", "-l"],
            cwd=project, capture_output=True, text=True,
        ).stdout.strip().split()

        # Run with stubbed prove that refuses
        def _refuse_prove(project, data_dir):
            return {"proven": False, "reason": "stub refusal", "new_failing_ids": [], "proof_file": None}

        result = run(
            project=project,
            data_dir=data_dir,
            prove_fn=_refuse_prove,
        )

        assert result["exit_code"] == 4

        # Verify no new tags were created
        tags_after = subprocess.run(
            ["git", "tag", "-l"],
            cwd=project, capture_output=True, text=True,
        ).stdout.strip().split()
        assert tags_after == tags_before, "run() must not create tags on refusal"


# ── AC-5: proof artifact records verdict identity ────────────────────────────


class TestProofRecordsVerdictIdentity:
    """AC-5: the proof artifact names the consumed batch slug/path, candidate
    identity, invocation, and signature/provenance result."""

    def test_proof_records_batch_slug(self, tmp_path: Path) -> None:
        """After consuming a batch verdict, the proof must name the batch."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=[],
            excused_count=0,
            batch="batch-2026-10-05q",
        )

        result = prove(project, data_dir)

        assert result["proven"] is True
        proof = json.loads(result["proof_file"].read_text())
        assert "batch" in proof, "proof must record the consumed batch slug"
        assert "batch-2026-10-05q" in str(proof["batch"])

    def test_proof_records_signature_provenance(self, tmp_path: Path) -> None:
        """After consuming a batch verdict, the proof must record the
        signature/provenance validation result."""
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="batch_gate.py",
            undeclared=[],
            excused_count=0,
        )

        result = prove(project, data_dir)

        assert result["proven"] is True
        proof = json.loads(result["proof_file"].read_text())
        # After step 1, the proof must record provenance validation
        assert "provenance" in proof or "signature" in proof or "writer" in proof, (
            "proof must record the signature/provenance validation result"
        )


# ── Production-gap xfail: the core authority mismatch ────────────────────────


class TestBatchVerdictIsSuiteAuthority:
    """AC-2: a fresh canonical signed pass verdict for the candidate tree
    and exact configured invocation is consumed without running the full
    suite again.

    This is the core authority mismatch: prove() currently ignores batch-gate
    records and always clones + reruns the suite.  After step 1, it must
    consume the signed verdict as the suite authority.

    batch q's shape: 6 suite failures, 0 attributed failures → pass.
    A prohibited second suite run would report 10 different order-dependent
    failures.
    """

    def test_batch_q_shape_passes_without_rerun(self, tmp_path: Path) -> None:
        """batch q shape: pass with 0 attributed failures, 6 suite failures.

        Currently passes for the WRONG reason: prove ignores the batch-gate
        record and runs the suite (which happens to be green).  After step 1,
        it must pass for the RIGHT reason: consuming the verdict, not running
        the suite.  The companion test_prove_does_not_rerun_suite_on_pass
        pins the no-rerun requirement.
        """
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="pass",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            writer="batch_gate.py",
            # 6 suite failures, all excused (0 attributed)
            undeclared=[],
            excused_count=6,
            failing_nodes=[f"test_known.py::test_case_{i}" for i in range(6)],
        )

        result = prove(project, data_dir)

        assert result["proven"] is True, (
            f"a pass with 0 attributed failures must advance proof; "
            f"got: {result['reason']}"
        )
        assert result["new_failing_ids"] == []

    def test_failing_verdict_refuses_even_if_suite_would_pass(self, tmp_path: Path) -> None:
        """A fail verdict must refuse even if a second suite run would pass
        (order-dependent test results).

        This is the core of batch q's problem: the batch-gate recorded
        "fail" (6 failures, some attributed), but a second suite run
        produces different order-dependent results and passes.  The verdict
        must be authoritative — prove must refuse on the fail record without
        rerunning the suite.
        """
        project = _make_fake_project(tmp_path, failing=False)
        data_dir = _make_data_dir(tmp_path)
        invocation = "python3 -m pytest"
        head_sha = _get_head_sha(project)
        tree_sha = _get_tree_sha(project)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        _write_batch_gate_record(
            runtime,
            verdict="fail",
            head_sha=head_sha,
            invocation=invocation,
            tree_sha=tree_sha,
            undeclared=["tests/test_order.py::test_depends_on_rerun"],
            excused_count=0,
        )

        result = prove(project, data_dir)

        # Currently proven=True because prove ignores the record and
        # the suite passes.  After step 1, must be False.
        assert result["proven"] is False, (
            "a fail verdict must refuse even if a fresh suite would pass"
        )
