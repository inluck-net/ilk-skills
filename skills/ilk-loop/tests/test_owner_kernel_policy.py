"""New owner-only bases refuse session kernel edits; older bases stay historical."""
from pathlib import Path
import json
import pytest
import safety_kernel
from test_a_session_batch_may_edit_the_kernel import (
    _init_repo, _copy_kernel_json, _make_session_master, _git, _git_output,
    _trailered_commit,
)


def test_default_lookup_is_toolkit_root():
    assert safety_kernel.load()["loop_kernel_policy"] == "owner-only"


@pytest.mark.parametrize("legacy", [False, True])
def test_session_kernel_policy_is_frozen_at_base(tmp_path: Path, legacy: bool):
    _init_repo(tmp_path)
    path = _copy_kernel_json(tmp_path)
    data = json.loads(path.read_text())
    if legacy:
        data.pop("loop_kernel_policy", None)
    else:
        data["loop_kernel_policy"] = "owner-only"
    path.write_text(json.dumps(data))
    _git(["add", "."], tmp_path)
    _git(["commit", "--allow-empty", "-m", "freeze policy"], tmp_path)
    plans = _make_session_master(tmp_path)
    base = _git_output(["rev-parse", "HEAD"], tmp_path)
    target = tmp_path / "skills/ilk-loop/scripts/batch_gate.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    _trailered_commit(tmp_path, target, "# changed\n")
    # Changing policy at HEAD cannot relax or strengthen the frozen base.
    data["loop_kernel_policy"] = "owner-only" if legacy else "legacy-session"
    path.write_text(json.dumps(data))
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "manual policy change"], tmp_path)
    rows = safety_kernel.check_range(tmp_path, base, "HEAD", plans_dir=plans)
    assert [r["reason"] for r in rows] == ([] if legacy else ["kernel-edit-requires-owner-build"])


@pytest.mark.parametrize("path", [
    "install.sh", "skills/ilk-watchdog/scripts/bounce_daemons.sh",
    "skills/ilk-ship/scripts/host_deploy_status.py",
    "skills/ilk-loop/scripts/iteration_context.py",
    "skills/ilk-loop/scripts/iteration_record.py",
    "skills/ilk-loop/scripts/ship_proof_ledger.py",
    "skills/ilk-loop/scripts/selfmod_state.py",
    "skills/ilk-loop/scripts/dispatch_target.py",
    "skills/ilk-loop/scripts/recovery.py",
    "skills/ilk-loop/scripts/check_verdict_history.py",
    "skills/ilk-loop/scripts/emit_jsonl_record.py",
    "skills/ilk-loop/scripts/launch_inputs_root.py",
    "skills/ilk-loop/scripts/migration_guard.py",
])
def test_new_authority_and_deployment_paths_are_kernel(path):
    assert safety_kernel.touches_kernel(path) == path


def test_unknown_policy_fails_closed(tmp_path):
    _init_repo(tmp_path)
    path = _copy_kernel_json(tmp_path)
    data = json.loads(path.read_text())
    data["loop_kernel_policy"] = "typo"
    path.write_text(json.dumps(data))
    with pytest.raises(safety_kernel.KernelListError):
        safety_kernel.load(tmp_path)


@pytest.mark.parametrize("declaration", [
    "---\nscope_paths:\n  - skills/ilk-loop/scripts/batch_gate.py\n---\n",
    "- **Edit** `skills/ilk-loop/scripts/batch_gate.py` to change proof.\n",
])
def test_admission_refuses_declared_kernel_work(tmp_path, declaration):
    _init_repo(tmp_path)
    path = _copy_kernel_json(tmp_path)
    data = json.loads(path.read_text()); data["loop_kernel_policy"] = "owner-only"
    path.write_text(json.dumps(data))
    assert safety_kernel.check_plan_admission(declaration, tmp_path)


def test_consumer_and_report_work_are_admissible(tmp_path):
    assert safety_kernel.check_plan_admission("- Edit `install.sh`", tmp_path) == []
    assert safety_kernel.check_plan_admission("---\nscope_paths:\n  - tools/loop-performance/report.py\n---\n", Path(__file__).resolve().parents[3]) == []


def test_preflight_reports_kernel_admission(tmp_path):
    from plan_preflight import preflight_batch
    _init_repo(tmp_path)
    path = _copy_kernel_json(tmp_path)
    data=json.loads(path.read_text());data['loop_kernel_policy']='owner-only';path.write_text(json.dumps(data))
    result=preflight_batch("", tmp_path, tmp_path, {'step.md': '- Edit `install.sh` to install.'})
    assert any('supervised owner build' in finding for finding in result.failures)
