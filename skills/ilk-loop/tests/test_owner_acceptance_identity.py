"""A gate/commit cannot grant state authority after its runner disappears."""
import json
import pytest
import driver_step
import run_registry
from iteration_clock import monotonic
from test_owner_driver_step import fixture


@pytest.mark.parametrize('loss',['before-gate','after-gate','after-commit','recycled'])
def test_owning_runner_loss_never_advances_pointer(tmp_path,monkeypatch,loss):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    # Unit authority-loss seam: fictitious PID identity cannot be inherited by
    # a real owned-command child. Native registration is exercised by the
    # archived caller tests; here keep process execution behind this seam.
    import owned_command
    monkeypatch.setattr(owned_command,'wrap_command',lambda argv,context:argv)
    context,root,plan=fixture(tmp_path);base=driver_step.git(root,'rev-parse','HEAD')
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    registry=tmp_path/'registry';(registry/'runs').mkdir(parents=True)
    identity=dict(pid=123,pgid=123,lstart='fixture owner start')
    row=dict(identity,schema=1,run_id=context['run_id'],project_key='fixture',state='running',
        clone=context['clone'],exec_tree=context['exec_tree'],toolkit_sha='0'*40,children=[])
    (registry/'runs'/('test.json')).write_text(json.dumps(row))
    context.update(run_registry_dir=str(registry),runner_owner_pid=123)
    live=[loss not in {'before-gate','recycled'}]
    monkeypatch.setattr(run_registry,'process_identity',lambda _:dict(identity,lstart='reused') if loss=='recycled' else identity if live[0] else None)
    real_gate=driver_step.gate
    def gate(*args,**kwargs):
        result=real_gate(*args,**kwargs)
        if loss=='after-gate':live[0]=False
        return result
    monkeypatch.setattr(driver_step,'gate',gate)
    real_git=driver_step.git
    def git(root,*args,**kwargs):
        result=real_git(root,*args,**kwargs)
        if args[0]=='commit' and loss=='after-commit':live[0]=False
        return result
    monkeypatch.setattr(driver_step,'git',git)
    with pytest.raises(run_registry.RegistryError,match='no longer live'):
        driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    assert 'current_step: 0' in plan.read_text()
    if loss!='after-commit':assert real_git(root,'rev-parse','HEAD')==base
    receipt=driver_step._acceptance_receipt(context)
    if loss=='after-commit':
        pending=json.loads(receipt.read_text())
        assert pending['phase']=='commit-intent' and pending['result']['accepted'] is False
        with pytest.raises(run_registry.RegistryError,match='no longer live'):
            driver_step.recover_acceptance(context,deadline=monotonic()+10)
    else:assert not receipt.exists()
