from iteration_clock import monotonic
"""Actual disposable code/gate/commit/CAS checks; no live repositories."""
from pathlib import Path
import time
import pytest
from driver_step import accept_step as _accept_step,git
from iteration_context import resolve_context,ContextError
import test_iteration_context as fixtures


def accept_step(context,*,default_timeout):
    return _accept_step(context,default_timeout=default_timeout,deadline=monotonic()+10)


def fixture(tmp_path,command='python3 -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==6"'):
    args=fixtures.fixture(tmp_path);root=Path(args['exec_tree'])
    git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
    (root/'counter.py').write_text('def f(value): return value + 1\n')
    git(root,'add','counter.py');git(root,'commit','-qm','initial counter')
    plan=Path(args['dispatch']['subplan_path'])
    plan.write_text('---\nplan: counter\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\n---\n# Counter\n'
                    '## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: '+command+'\n```\n')
    args['dispatch'].update(step=0,slug='counter')
    context=resolve_context(**args);context['worker_sandbox_adapter']='macos-brokered-shell'
    return context,root,plan


def test_red_never_commits_or_advances_and_retains_full_output(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path);base=git(root,'rev-parse','HEAD')
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and git(root,'rev-parse','HEAD')==base
    assert 'current_step: 0' in plan.read_text()
    check=result['attempts'][0]['check_observations'][0]
    assert Path(check['stderr_path']).exists() and 'AssertionError' in Path(check['stderr_path']).read_text()


def test_green_driver_commits_and_advances_exactly_once_without_second_gate(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    result=accept_step(context,default_timeout=10)
    assert result['accepted'] and len(result['commits'])==1 and len(result['attempts'])==1
    assert result['tree']==result['attempts'][0]['working_tree']
    assert 'current_step: 1' in plan.read_text() and result['proof_provisional']
    assert '[plan:counter#step-0]' in git(root,'log','-1','--format=%B')
    with pytest.raises(ContextError,match='step changed'):
        accept_step(context,default_timeout=10)


def test_mutating_gate_cannot_cover_its_new_tree(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path,'printf mutated > counter.py')
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and not result['attempts'][0]['gate_tree_unchanged']
    assert 'current_step: 0' in plan.read_text()


def test_commit_hook_changed_shipping_tree_is_regated_and_red_not_advanced(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    common=Path(git(root,'rev-parse','--git-common-dir'))
    if not common.is_absolute():common=(root/common).resolve()
    hook=common/'hooks/pre-commit';hook.write_text('#!/bin/sh\nprintf "def f(value): return value + 1\\n" > counter.py\ngit add counter.py\n');hook.chmod(0o700)
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and len(result['attempts'])==2 and result['commits']
    assert 'current_step: 0' in plan.read_text()


def test_held_pointer_and_dirty_entry_refuse_before_gate(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    plan.write_text(plan.read_text().replace('status: pending','status: blocked'))
    with pytest.raises(ContextError,match='held'):accept_step(context,default_timeout=10)
    plan.write_text(plan.read_text().replace('status: blocked','status: pending'))
    with pytest.raises(ContextError,match='clean frozen entry'):
        accept_step(dict(context,entry_dirty=True),default_timeout=10)


def test_owned_commit_hook_is_bounded_without_advancing(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path);base=git(root,'rev-parse','HEAD')
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    common=Path(git(root,'rev-parse','--git-common-dir'))
    if not common.is_absolute():common=(root/common).resolve()
    import shlex
    marker=tmp_path/'hook-started';hook=common/'hooks/pre-commit'
    hook.write_text('#!/bin/sh\ntouch '+shlex.quote(str(marker))+"\nexec /usr/bin/python3 -c 'import threading;threading.Event().wait()'\n")
    hook.chmod(0o700)
    # Two seconds is a test judgment for the existing whole-second gate
    # parser plus sampled subsecond gate/index startup.
    # A slow valid host falsifies it; no production timeout default changes.
    with pytest.raises(ContextError,match='iteration deadline'):
        _accept_step(context,default_timeout=10,deadline=monotonic()+2)
    assert marker.exists() and git(root,'rev-parse','HEAD')==base
    assert 'current_step: 0' in plan.read_text()


@pytest.mark.parametrize('green',[False,True])
def test_declared_baseline_reds_never_waive_driver_gate(tmp_path,monkeypatch,green):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'skills/ilk-ship/scripts'))
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'.ilk-launch.json').write_text(json.dumps({'ship':{'suite':{'command':'false'},'baseline_red':[
        {'node_id':'counter.py','reason':'fixture declared red'}]}}))
    git(root,'add','.ilk-launch.json');git(root,'commit','-qm','baseline declarations')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    from run_local_checks import _read_declared_reds
    assert _read_declared_reds(root)
    if green:(root/'counter.py').write_text('def f(value): return value + 2\n')
    result=accept_step(context,default_timeout=10)
    assert result['accepted'] is green
    assert result['attempts'][0]['all_passed'] is green
    assert result['attempts'][0]['baseline_red_policy']=='strict-raw-exit-no-waivers'
    assert result['attempts'][0]['diagnostic_baseline_red_declarations']
    assert ('current_step: 1' if green else 'current_step: 0') in plan.read_text()


def test_over_cap_mention_inventory_is_inconclusive_not_green(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    context['mention_gate_policy']='complete' # Explicit stricter contract, not the routine bounded policy.
    from run_local_checks import _MENTION_GATE_CAP
    (root/'fixture_mentions.py').write_text('value = 1\n')
    for index in range(_MENTION_GATE_CAP+1):
        target=root/'tests'/('test_counter_'+str(index)+'.py');target.parent.mkdir(exist_ok=True)
        target.write_text('# fixture_mentions.py:1\ndef test_related(): assert True\n')
    git(root,'add','.');git(root,'commit','-qm','fixture related tests')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    (root/'fixture_mentions.py').write_text('value = 2\n')
    with pytest.raises(ContextError,match='mention inventory exceeds'):
        accept_step(context,default_timeout=10)
    assert 'current_step: 0' in plan.read_text()
    assert git(root,'rev-parse','HEAD')==context['base_commit']
    from run_local_checks import build_mention_gate_inventory
    # Inventory parsing only: this command is never executed. Already declared
    # full-file coverage is complete without adding another mention gate.
    assert build_mention_gate_inventory(root,['fixture_mentions.py'],
        {'python -m pytest tests'},require_complete=True)==[]


def test_bounded_mention_policy_keeps_declared_gate_and_records_omissions(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    from run_local_checks import _MENTION_GATE_CAP
    (root/'fixture_mentions.py').write_text('value = 1\n')
    for index in range(_MENTION_GATE_CAP+1):
        target=root/'tests'/('test_related_'+str(index)+'.py');target.parent.mkdir(exist_ok=True)
        target.write_text('# fixture_mentions.py:1\ndef test_related(): assert True\n')
    git(root,'add','.');git(root,'commit','-qm','bounded mention fixture')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    assert context['mention_gate_policy']=='bounded-existing-cap'
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    (root/'fixture_mentions.py').write_text('value = 2\n')
    result=accept_step(context,default_timeout=10)
    assert result['accepted']
    attempt=result['attempts'][0]
    assert attempt['mention_gate_policy']=='bounded-existing-cap'
    assert len(attempt['mention_inventory']['omitted_files'])==_MENTION_GATE_CAP+1
    assert len(attempt['check_observations'])==1 and attempt['check_observations'][0]['passed']
    assert 'current_step: 1' in plan.read_text()


def test_misplaced_gate_first_refuses_before_worker_instead_of_ignoring_option(tmp_path):
    from driver_step import gate_contract
    context,root,plan=fixture(tmp_path)
    plan.write_text(plan.read_text().replace('status: pending','status: pending\ngate_first: true'))
    with pytest.raises(ContextError,match='step YAML'):
        gate_contract(root,plan,context['dispatch'])


def test_same_tree_red_then_green_is_flaky_without_commit_or_step_advance(tmp_path,monkeypatch):
    import shlex
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    signal=tmp_path/'external-gate-signal'
    context,root,plan=fixture(tmp_path,'test -f '+shlex.quote(str(signal)))
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    first=_accept_step(context,default_timeout=10,deadline=monotonic()+10)
    assert not first['accepted']
    signal.write_text('gate environment changed; shipping tree unchanged')
    second=_accept_step(context,default_timeout=10,deadline=monotonic()+10,prior_attempts=first['attempts'])
    assert not second['accepted']
    assert second['attempts'][-1]['verdict']=='flaky'
    assert second['attempts'][-1]['raw_all_passed'] is True
    assert 'current_step: 0' in plan.read_text()
    assert git(root,'rev-parse','HEAD')==context['base_commit']


def test_repaired_tree_green_retains_prior_red_without_inheriting_its_verdict(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    first=_accept_step(context,default_timeout=10,deadline=monotonic()+10)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    second=_accept_step(context,default_timeout=10,deadline=monotonic()+10,prior_attempts=first['attempts'])
    assert second['accepted'] and len(second['attempts'])==2
    assert second['attempts'][0]['verdict']=='red'
    assert second['attempts'][1]['verdict']=='green'
    assert second['attempts'][0]['working_tree']!=second['attempts'][1]['working_tree']


def test_restart_retains_same_input_flaky_history_without_prior_argument(tmp_path,monkeypatch):
    import shlex
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    signal=tmp_path/'external-restart-signal'
    context,root,plan=fixture(tmp_path,'test -f '+shlex.quote(str(signal)))
    first=_accept_step(context,default_timeout=10,deadline=monotonic()+10)
    assert not first['accepted']
    signal.write_text('changed external condition only')
    restarted=dict(context,run_id='next-launch',iteration=1)
    second=_accept_step(restarted,default_timeout=10,deadline=monotonic()+10)
    assert not second['accepted']
    assert second['attempts'][-1]['verdict']=='flaky'
    assert 'current_step: 0' in plan.read_text()
    assert git(root,'rev-parse','HEAD')==context['base_commit']


def test_changed_frozen_inputs_get_fresh_gate_without_reusing_old_pass(tmp_path,monkeypatch):
    import shlex
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    signal=tmp_path/'external-changed-input-signal'
    context,root,plan=fixture(tmp_path,'test -f '+shlex.quote(str(signal)))
    first=_accept_step(context,default_timeout=10,deadline=monotonic()+10)
    signal.write_text('new configured measurement input')
    changed=dict(context,run_id='changed-input-launch',toolkit_sha='a'*40)
    second=_accept_step(changed,default_timeout=10,deadline=monotonic()+10)
    assert second['accepted'] and second['attempts'][-1]['verdict']=='green'
    assert first['attempts'][0]['input_fingerprint']!=second['attempts'][0]['input_fingerprint']
    assert second['attempts'][0]['check_observations'][0]['attempt_id']!=first['attempts'][0]['check_observations'][0]['attempt_id']


def test_corrupt_durable_gate_history_refuses_before_gate_or_commit(tmp_path,monkeypatch):
    import driver_step
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    history=Path(context['recovery_dir'])/'driver-gate-history.json'
    history.parent.mkdir(parents=True,exist_ok=True);history.write_text('{"schema_version":1,"attempts":[{}]}')
    original=history.read_bytes()
    monkeypatch.setattr(driver_step,'gate',lambda *args,**kwargs:pytest.fail('gate must not run on corrupt history'))
    with pytest.raises(ContextError,match='invalid durable gate attempt'):
        _accept_step(context,default_timeout=10,deadline=monotonic()+10)
    assert history.read_bytes()==original and 'current_step: 0' in plan.read_text()
    assert git(root,'rev-parse','HEAD')==context['base_commit']
