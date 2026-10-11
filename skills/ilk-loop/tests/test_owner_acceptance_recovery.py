"""Fault the actual gated commit/CAS boundary, never reconstruct green."""
import pytest
from iteration_clock import monotonic
import driver_step
from iteration_context import ContextError
from test_owner_driver_step import fixture


@pytest.mark.parametrize('boundary',['staged','committed'])
def test_commit_intent_recovers_before_receipt_without_duplicate_commit(tmp_path,monkeypatch,boundary):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    real=driver_step.git
    def crash(root,*args,**kwargs):
        answer=real(root,*args,**kwargs)
        if (boundary=='staged' and args[:2]==('add','-A') and kwargs.get('env') is None
                or boundary=='committed' and args[0]=='commit'):
            raise RuntimeError('fixture lost caller after '+boundary)
        return answer
    monkeypatch.setattr(driver_step,'git',crash)
    with pytest.raises(RuntimeError,match='lost caller'):
        driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    monkeypatch.setattr(driver_step,'git',real)
    result=driver_step.recover_acceptance(context,deadline=monotonic()+10)
    assert result['accepted'] and len(result['attempts'])==1 and len(result['commits'])==1
    assert 'current_step: 1' in plan.read_text()
    assert len(real(root,'rev-list',context['base_commit']+'..HEAD').splitlines())==1


@pytest.mark.parametrize('hook_green',[False,True])
def test_committed_intent_hook_tree_requires_fresh_gate(tmp_path,monkeypatch,hook_green):
    from pathlib import Path
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    hook=Path(driver_step.git(root,'rev-parse','--git-path','hooks'))/'pre-commit'
    hook.write_text("#!/bin/sh\nprintf '%s\\n' 'def f(value): return value + "+('2 # hook' if hook_green else '3')+"' > counter.py\ngit add counter.py\n")
    hook.chmod(0o700)
    real=driver_step.git
    def crash(root,*args,**kwargs):
        answer=real(root,*args,**kwargs)
        if args[0]=='commit':raise RuntimeError('fixture committed')
        return answer
    monkeypatch.setattr(driver_step,'git',crash)
    with pytest.raises(RuntimeError):driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    monkeypatch.setattr(driver_step,'git',real)
    if hook_green:
        result=driver_step.recover_acceptance(context,deadline=monotonic()+10)
        assert len(result['attempts'])==2 and result['attempts'][-1]['all_passed']
        assert result['attempts'][0]['working_tree']!=result['attempts'][1]['working_tree']
        assert 'current_step: 1' in plan.read_text()
    else:
        with pytest.raises(ContextError,match='fresh gate is red'):
            driver_step.recover_acceptance(context,deadline=monotonic()+10)
        assert 'current_step: 0' in plan.read_text()


@pytest.mark.parametrize('after_advance',[False,True])
def test_same_frozen_transaction_recovers_before_or_after_cas(tmp_path,monkeypatch,after_advance):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    real=driver_step.transition
    def crash(*args,**kwargs):
        if after_advance:real(*args,**kwargs)
        raise RuntimeError('fixture interruption at CAS boundary')
    monkeypatch.setattr(driver_step,'transition',crash)
    with pytest.raises(RuntimeError,match='fixture interruption'):
        driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    head=driver_step.git(root,'rev-parse','HEAD')
    monkeypatch.setattr(driver_step,'transition',real)
    result=driver_step.recover_acceptance(context,deadline=monotonic()+10)
    assert result['accepted'] and result['proof_provisional'] and result['recovered_acceptance']
    assert len(result['attempts'])==1 and len(result['commits'])==1
    assert driver_step.git(root,'rev-parse','HEAD')==head
    assert 'current_step: 1' in plan.read_text()
    again=driver_step.recover_acceptance(context,deadline=monotonic()+10)
    assert again['head_commit']==head and 'current_step: 1' in plan.read_text()


@pytest.mark.parametrize('changed',['tree','capture','contract','hold','unexplained-pointer'])
def test_recovery_refuses_changed_acceptance_inputs(tmp_path,monkeypatch,changed):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    real=driver_step.transition
    def crash(*args,**kwargs):raise RuntimeError('interrupted')
    monkeypatch.setattr(driver_step,'transition',crash)
    with pytest.raises(RuntimeError):
        driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    monkeypatch.setattr(driver_step,'transition',real)
    if changed=='tree':(root/'counter.py').write_text('def f(value): return value + 3\n')
    elif changed=='capture':
        import json
        receipt=json.loads(driver_step._acceptance_receipt(context).read_text())
        from pathlib import Path
        Path(receipt['captures'][0]['path']).write_text('altered')
    elif changed=='contract':plan.write_text(plan.read_text().replace('assert f(4)==6','assert True'))
    elif changed=='hold':plan.write_text(plan.read_text().replace('status: pending','status: blocked\nhold: human'))
    else:plan.write_text(plan.read_text().replace('current_step: 0','current_step: 1').replace('status: pending','status: in-progress'))
    with pytest.raises(ContextError):
        driver_step.recover_acceptance(context,deadline=monotonic()+10)
