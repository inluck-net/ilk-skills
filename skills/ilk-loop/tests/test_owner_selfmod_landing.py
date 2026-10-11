"""Exact fresh-base selfmod crash recovery; no live toolkit merge or daemon bounce."""
from pathlib import Path
import json
import pytest
import driver_step
from iteration_clock import monotonic
from iteration_context import write_context,ContextError
from acceptance_recovery import recover_pending
from selfmod_landing import land
from ship_proof_ledger import read_records
from test_owner_driver_step import fixture as driver_fixture


def fixture(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    context,root,plan=driver_fixture(tmp_path)
    clone=Path(context['clone'])
    # Fixture setup puts its initial counter on the source base too. It is
    # baseline setup, not permission to land arbitrary historical WIP.
    driver_step.git(clone,'merge','--ff-only',context['base_commit'])
    context.update(clone_base_commit=driver_step.git(clone,'rev-parse','HEAD'),
                   clone_ref=driver_step.git(clone,'symbolic-ref','HEAD'),clone_entry_dirty=False)
    frozen=tmp_path/'context.json';write_context(frozen,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(frozen))
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    accepted=driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    monkeypatch.delenv('ILK_ITER_CONTEXT')
    return context,root,clone,plan,accepted


def test_pending_selfmod_acceptance_lands_and_closes_without_new_gate(tmp_path,monkeypatch):
    context,root,clone,plan,accepted=fixture(tmp_path,monkeypatch)
    def forbidden(*args,**kwargs):raise AssertionError('accepted transaction must not dispatch or gate another tree')
    monkeypatch.setattr(driver_step,'gate',forbidden)
    log=tmp_path/'summary.jsonl'
    result=recover_pending(runtime=Path(context['runtime_dir']),project=clone,log=log,deadline=monotonic()+10)
    assert len(result)==1 and root.exists()
    assert 'status: shipped' in plan.read_text()
    close=json.loads(log.read_text());assert close['merge_outcome']=='merged'
    assert close['driver_integrity_passed'] and close['state_audit']['explained']
    assert close['selfmod_landing']['execution_worktree_retained']
    assert driver_step.git(clone,'rev-parse','HEAD')==driver_step.git(root,'rev-parse','HEAD')
    assert driver_step.git(clone,'rev-parse','HEAD^{tree}')==accepted['tree']
    assert read_records(Path(context['ledger_path']))[0]['landed']
    from selfmod_state import reconcile
    state=Path(context['runtime_dir'])/'launcher/selfmod-state.json'
    assert reconcile(clone,root,state)['state']=='MERGED'

    assert recover_pending(runtime=Path(context['runtime_dir']),project=clone,log=log,deadline=monotonic()+10)==[]
    (root/'counter.py').write_text('uncommitted next work\n')
    assert reconcile(clone,root,state)['state']=='ISOLATED'


@pytest.mark.parametrize('changed',['dirty-clone','moved-clone','branch','historical-base','receipt','tree'])
def test_changed_selfmod_landing_inputs_never_merge(tmp_path,monkeypatch,changed):
    context,root,clone,plan,accepted=fixture(tmp_path,monkeypatch)
    before=driver_step.git(clone,'rev-parse','HEAD')
    if changed=='dirty-clone':(clone/'counter.py').write_text('human change\n')
    elif changed=='moved-clone':driver_step.git(clone,'commit','--allow-empty','-qm','human moved source');before=driver_step.git(clone,'rev-parse','HEAD')
    elif changed=='branch':driver_step.git(clone,'checkout','-qb','human-branch')
    elif changed=='historical-base':context=dict(context,clone_base_commit='unverified-old-base')
    elif changed=='receipt':accepted=dict(accepted,head_commit='forged')
    else:(root/'counter.py').write_text('different accepted tree\n')
    with pytest.raises(ContextError):land(context,accepted,deadline=monotonic()+10)
    assert driver_step.git(clone,'rev-parse','HEAD')==before
    assert 'status: shipped' not in plan.read_text()


def test_selfmod_state_lock_obeys_existing_deadline_and_keeps_prior_bytes(tmp_path,monkeypatch):
    import fcntl,threading
    from selfmod_state import reconcile
    from plan_state import TransitionError
    context,root,clone,plan,accepted=fixture(tmp_path,monkeypatch)
    state=Path(context['runtime_dir'])/'launcher/selfmod-state.json'
    reconcile(clone,root,state);before=state.read_bytes()
    ready=threading.Event();release=threading.Event()
    def holder():
        with state.with_name(state.name+'.lock').open('a+b') as stream:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX);ready.set();release.wait()
            fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    thread=threading.Thread(target=holder);thread.start()
    # 2s startup and 0.1s lock cap are fixture judgments for local readiness,
    # not production timeout defaults. A slow valid host falsifies these.
    token=driver_step._deadline.set(monotonic()+.1)
    try:
        assert ready.wait(2)
        with pytest.raises(TransitionError,match='existing iteration deadline'):
            reconcile(clone,root,state)
        assert state.read_bytes()==before
    finally:
        driver_step._deadline.reset(token);release.set();thread.join(2)
    assert not thread.is_alive()
