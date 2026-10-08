"""A scoped verify cannot prove a release that changes the release path.

Backlog 93d82cbae7b970e3.  On 2026-10-08 the 07h batch verified 8 tests
(``suite_scope: scoped``, 1 file) while its diff changed scheduler.sh, and
batch-gate.json carried no scope, so the train read it like a full-suite
pass.  Now the record carries the scope, and ``prove`` refuses a verify not
recorded as ``full`` when ``<last_tag>..HEAD`` touches the release path
(the safety-kernel list plus scheduler.sh).

Each test builds a throwaway git repo under ``tmp_path``; none runs this
repo's suite or touches ``~/.ilk-data``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"
sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

import release_train  # noqa: E402
import safety_case  # noqa: E402
import safety_kernel  # noqa: E402

INVOCATION = "python3 -m pytest"


@pytest.fixture(autouse=True)
def _stub_safety_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety_case, "run", lambda *a, **kw: {
        "verdict": "pass", "components": [], "writer": "driver"})


def _git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, check=True,
                          capture_output=True, text=True).stdout.strip()


def _project(tmp_path: Path, changed: str) -> Path:
    """A repo tagged v0.0.1, then one commit that adds *changed*."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "T")
    (project / ".ilk-launch.json").write_text(json.dumps({"ship": {
        "release_train": True,
        "suite": {"command": INVOCATION, "flags": []}}}))
    _git(project, "add", ".")
    _git(project, "commit", "-m", "initial")
    _git(project, "tag", "-a", "v0.0.1", "-m", "v0.0.1")
    path = project / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# changed\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", f"change {changed}")
    return project


def _data_dir(tmp_path: Path) -> Path:
    from baseline_diff import store_baseline
    data_dir = tmp_path / "data"
    (data_dir / "runtime" / "release").mkdir(parents=True)
    store_baseline(project_root=data_dir, tag="v0.0.1",
                   suite_invocation=INVOCATION, node_ids=frozenset(),
                   search_space=0)
    return data_dir


def _write_pass(project: Path, data_dir: Path, suite_scope: dict | None) -> None:
    from batch_gate import BatchGateRecord, write_record
    write_record(BatchGateRecord(
        verdict="pass",
        head_sha=_git(project, "rev-parse", "HEAD"),
        invocation=INVOCATION,
        timestamp="2026-10-08T21:00:00+08:00",
        tree_sha=_git(project, "rev-parse", "HEAD^{tree}"),
        writer="verify_attribution",
        undeclared=[],
        excused_count=0,
        suite_scope=suite_scope,
    ), data_dir / "runtime")


def _prove(project: Path, data_dir: Path) -> tuple[dict, dict]:
    with patch.object(safety_kernel, "check_range", return_value=[]):
        result = release_train.prove(project, data_dir)
    proof = json.loads(Path(result["proof_file"]).read_text())
    return result, proof


SCOPED_07H = {"mode": "scoped", "selection_size": 1, "passed": 8, "total": 8,
              "reason": "1 test file(s) selected"}
FULL = {"mode": "full", "selection_size": 0, "passed": 3233, "total": 3283}


class TestScopedVerifyOverReleasePath:

    @pytest.mark.parametrize("changed", [
        "skills/ilk-watchdog/scripts/scheduler.sh",
        "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
        "skills/ilk-ship/scripts/release_train.py",
    ])
    def test_scoped_verify_is_refused(self, tmp_path, changed):
        project = _project(tmp_path, changed)
        data_dir = _data_dir(tmp_path)
        _write_pass(project, data_dir, SCOPED_07H)

        result, proof = _prove(project, data_dir)

        assert result["proven"] is False
        assert result["reason"].startswith("scoped-verify-over-release-path"), result
        assert changed in result["reason"]
        assert "8 of 8 tests passed" in result["reason"]
        assert proof["verdict"] == "refused"
        assert proof["suite_scope"]["mode"] == "scoped"

    def test_unrecorded_scope_is_refused(self, tmp_path):
        """A record without suite_scope never reads as full."""
        project = _project(tmp_path, "skills/ilk-watchdog/scripts/scheduler.sh")
        data_dir = _data_dir(tmp_path)
        _write_pass(project, data_dir, None)

        result, proof = _prove(project, data_dir)

        assert result["proven"] is False
        assert "unrecorded" in result["reason"]
        assert proof["suite_scope"] == {"mode": "unrecorded"}

    def test_full_verify_is_proven(self, tmp_path):
        project = _project(tmp_path, "skills/ilk-watchdog/scripts/scheduler.sh")
        data_dir = _data_dir(tmp_path)
        _write_pass(project, data_dir, FULL)

        result, proof = _prove(project, data_dir)

        assert result["proven"] is True, result["reason"]
        assert proof["suite_scope"]["mode"] == "full"


class TestScopedVerifyOffReleasePath:

    def test_scoped_verify_is_proven_and_says_so(self, tmp_path):
        project = _project(tmp_path, "docs/notes.md")
        data_dir = _data_dir(tmp_path)
        _write_pass(project, data_dir, SCOPED_07H)

        result, proof = _prove(project, data_dir)

        assert result["proven"] is True, result["reason"]
        assert proof["suite_scope"] == SCOPED_07H
        assert release_train._scope_phrase(proof["suite_scope"]) == (
            "8 of 8 tests passed, 1 file(s) selected")


class TestTheRecordCarriesTheScope:

    def test_suite_scope_round_trips_through_batch_gate_json(self, tmp_path):
        from batch_gate import BatchGateRecord, read_record, write_record
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        write_record(BatchGateRecord(
            verdict="pass", head_sha="a" * 40, invocation=INVOCATION,
            timestamp="2026-10-08T21:00:00+08:00", writer="verify_attribution",
            suite_scope=SCOPED_07H), runtime, batch="b1")
        assert read_record(runtime, batch="b1").suite_scope == SCOPED_07H
        assert read_record(runtime).suite_scope == SCOPED_07H

    def test_verify_attribution_reads_the_verification_record(self):
        import verify_attribution
        text = (
            "# Batch verification record — b1\n\n"
            "suite_scope: scoped\n"
            "suite_scope_reason: 1 test file(s) selected\n"
            "selection_size: 1\n"
            "suite_total: 8\n"
            "suite_passed: 8\n"
            "suite_failed: 0\n"
        )
        assert verify_attribution.read_suite_scope(text) == SCOPED_07H
        assert verify_attribution.read_suite_scope(
            "**suite_scope:** `full` (0 files)\n") == {"mode": "full"}
        assert verify_attribution.read_suite_scope("suite_failed: 0\n") is None
