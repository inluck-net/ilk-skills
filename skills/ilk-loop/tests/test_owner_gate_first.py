"""Gate-first uses the same acceptance transaction, not a legacy bypass."""
from pathlib import Path
import pytest
import worker_iteration
from iteration_clock import monotonic
from iteration_context import write_context,plan_snapshot
from test_owner_driver_step import fixture


@pytest.mark.parametrize('already_green',[False,True])
def test_gate_first_accepts_head_or_repairs_with_one_worker(tmp_path,monkeypatch,already_green):
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    context,root,plan=fixture(tmp_path)
    if already_green:
        # Green behavior belongs to the frozen base; it is not a dirty worker
        # diff and does not manufacture an empty accepted code commit.
        from driver_step import git
        (root/'counter.py').write_text('def f(value): return value + 2\n')
        git(root,'add','.');git(root,'commit','-qm','existing behavior')
        context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    plan.write_text(plan.read_text().replace('local_checks:','gate_first: true\nlocal_checks:'))
    context['plans'][plan.name]=plan_snapshot(plan)
    context['iteration_deadline_monotonic']=monotonic()+10
    path=tmp_path/'context.json';write_context(path,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path));monkeypatch.setattr(worker_iteration,'phase',lambda *args:None)
    turns=[]
    def turn(context,path,prompt,**kwargs):
        turns.append(prompt)
        assert not already_green
        (root/'counter.py').write_text('def f(value): return value + 2\n')
        return dict(completed=True,session_id='fixture',exit_code=0)
    result=worker_iteration.run(context,path,'implement',claude='unused',model='',budget=0,mcp=None,
        gate_timeout=10,turn=turn)
    assert result['accepted'] and len(turns)==(0 if already_green else 1)
    assert len(result['attempts'])==(1 if already_green else 2)
    assert len(result['commits'])==(0 if already_green else 1)
    assert 'current_step: 1' in plan.read_text()


def test_same_tree_flaky_gate_stops_without_retry_strike_or_state_change(tmp_path,monkeypatch):
    import shlex
    from driver_step import git
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    signal=tmp_path/'external-environment-signal'
    context,root,plan=fixture(tmp_path,'test -f '+shlex.quote(str(signal)))
    plan.write_text(plan.read_text().replace('local_checks:','gate_first: true\nlocal_checks:'))
    context['plans'][plan.name]=plan_snapshot(plan)
    context['iteration_deadline_monotonic']=monotonic()+10
    path=tmp_path/'context.json';write_context(path,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path));monkeypatch.setattr(worker_iteration,'phase',lambda *args:None)
    calls=[]
    def turn(context,path,prompt,**kwargs):
        calls.append(prompt)
        signal.write_text('environment transition; no worker code change')
        return dict(completed=True,session_id='fixture',exit_code=0)
    result=worker_iteration.run(context,path,'implement',claude='unused',model='',budget=0,mcp=None,
        gate_timeout=10,turn=turn)
    assert not result['accepted'] and result['stop_reason']=='local_checks_inconclusive'
    assert len(calls)==1 and len(result['attempts'])==2
    assert result['attempts'][-1]['verdict']=='flaky'
    assert 'current_step: 0' in plan.read_text() and 'status: pending' in plan.read_text()
    assert git(root,'rev-parse','HEAD')==context['base_commit']
    assert not (Path(context['recovery_dir'])/'gate-red-counts.json').exists()
