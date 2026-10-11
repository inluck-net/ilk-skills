"""Two driver processes' durable interface: pending acceptance then close."""
from pathlib import Path
import json
import pytest
import driver_step
from acceptance_recovery import recover_pending
from iteration_clock import monotonic
from iteration_context import write_context,ContextError
from ship_proof_ledger import read_records
from ship_audit import ledger_record_is_reachable
from test_owner_driver_step import fixture as driver_fixture


def fixture(tmp_path):
    context,root,plan=driver_fixture(tmp_path)
    # This adapter closes an ordinary consumer checkout; the shared driver
    # fixture otherwise describes toolkit selfmod, whose landing is separate.
    context.update(clone=str(root),mode='none',observe_tree={str(root):str(root)})
    return context,root,plan


@pytest.mark.parametrize('after_advance',[False,True])
def test_pending_acceptance_closes_before_new_dispatch(tmp_path,monkeypatch,after_advance):
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    context,root,plan=fixture(tmp_path)
    frozen=tmp_path/'ctx.json';write_context(frozen,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(frozen))
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    real=driver_step.transition
    def crash(*args,**kwargs):
        if after_advance:real(*args,**kwargs)
        raise RuntimeError('interrupted')
    monkeypatch.setattr(driver_step,'transition',crash)
    with pytest.raises(RuntimeError):
        driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    monkeypatch.setattr(driver_step,'transition',real)
    monkeypatch.delenv('ILK_ITER_CONTEXT')
    log=tmp_path/'summary.jsonl'
    completed=recover_pending(runtime=Path(context['runtime_dir']),project=root,log=log,deadline=monotonic()+10)
    assert len(completed)==1
    assert 'status: shipped' in plan.read_text() and 'current_step: 1' in plan.read_text()
    closed=json.loads(log.read_text())
    assert closed['acceptance_recovered_by_driver'] and closed['driver_integrity_passed']
    proofs=read_records(Path(context['ledger_path']))
    assert len(proofs)==1 and ledger_record_is_reachable(proofs[0],root)
    assert recover_pending(runtime=Path(context['runtime_dir']),project=root,log=log,deadline=monotonic()+10)==[]


def test_hold_after_acceptance_refuses_closure_without_erasing_hold(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    context,root,plan=fixture(tmp_path);frozen=tmp_path/'ctx.json';write_context(frozen,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(frozen))
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
    plan.write_text(plan.read_text().replace('status: in-progress','status: blocked\nhold: human'))
    monkeypatch.delenv('ILK_ITER_CONTEXT')
    log=tmp_path/'summary.jsonl'
    with pytest.raises(ContextError,match='contract changed|hold changed'):
        recover_pending(runtime=Path(context['runtime_dir']),project=root,log=log,deadline=monotonic()+10)
    assert 'hold: human' in plan.read_text() and not log.exists()
    assert read_records(Path(context['ledger_path']))==[]
