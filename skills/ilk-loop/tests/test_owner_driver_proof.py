"""Scoped closed proof is usable; changed trees/contracts and missing steps fail."""
from pathlib import Path
import pytest
from driver_step import accept_step,project_result,git
from iteration_clock import monotonic
from iteration_context import write_context,capture_execution
from iteration_record import project_close_record
from ship_proof_ledger import append_record,read_records
from ship_audit import audit_ship,read_subplan_for_audit
import test_owner_driver_step as fixtures


def closed_fixture(tmp_path,monkeypatch,*,two_plans=False):
    context,root,plan=fixtures.fixture(tmp_path)
    master=Path(context['plans_dir'])/'MASTER-proof.md'
    master.write_text('---\nstatus: active\n---\n# Master\n## Sub-plan registry\n| File |\n|---|\n| step.md |\n')
    if two_plans:
        later=plan.with_name('2026-10-11-later.md')
        later.write_text('---\nplan: later\nstatus: pending\ncurrent_step: 0\n---\n# Later\n## Steps\n'
            '### Step 0: add independent file\n```yaml\nlocal_checks:\n'
            '  - command: python3 -I -c "from pathlib import Path;assert Path(\'later.txt\').read_text()==\'accepted\'"\n```\n')
        master.write_text(master.read_text()+'| '+later.name+' |\n')
    # Freeze actual master identity in a new canonical context.
    from iteration_context import plan_snapshot
    context['dispatch']['master_path']=str(master);context['master_snapshot']=plan_snapshot(master)
    path=tmp_path/'ctx.json';write_context(path,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path));monkeypatch.setenv('ILK_ITER_RECORD_STAGE','driver-check')
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    result=accept_step(context,default_timeout=10,deadline=monotonic()+10)
    result.update(run_id=context['run_id'],iteration=context['iteration'])
    projected=project_result(result,context)
    ledger=Path(context['ledger_path'])
    append_record(ledger,{'run_id':'test','iteration':1,'slug':'counter','step_from':0,'step_to':1,
                         'commits':result['commits'],'gate_outcome':'pass','head':result['head_commit']})
    close={'run_id':'test','iteration':1,'record_phase':'close','context':context,'merge_outcome':'merged',
           'driver_acceptance':result,'driver_integrity_passed':True,'state_audit':{'explained':True},
           'execution':capture_execution(context)}
    project_close_record(close)
    monkeypatch.delenv('ILK_ITER_RECORD_STAGE')
    return root,plan,master,ledger


def audit(root,plan,master,ledger):
    info=read_subplan_for_audit(plan)
    return audit_ship(status='shipped',body=info['body'],declared_checks=info['declared_checks'],
        gate_passed='unknown',slug='counter',cwd=root,ledger_records=read_records(ledger),
        subplan_path=plan,master_path=master)


def test_scoped_proof_survives_empty_marker_without_repeating_gate(tmp_path,monkeypatch):
    root,plan,master,ledger=closed_fixture(tmp_path,monkeypatch)
    git(root,'commit','--allow-empty','-qm','marker [plan:counter#ship]')
    verdict=audit(root,plan,master,ledger)
    assert verdict['proven'],verdict


@pytest.mark.parametrize('tamper',[None,'unclosed','red','base','tree','contract','unknown-commit'])
def test_later_independent_accepted_step_preserves_prior_closed_ship(tmp_path,monkeypatch,tamper):
    """An actual second acceptance, not a fabricated cached gate result."""
    from iteration_context import resolve_context
    root,plan,master,ledger=closed_fixture(tmp_path,monkeypatch,two_plans=True)
    later=plan.with_name('2026-10-11-later.md')
    context=resolve_context(clone=root,exec_tree=root,observe_tree={str(root):str(root)},code_root=root,
        plans_dir=plan.parent,runtime_dir=ledger.parent.parent,data_root=tmp_path/'data',
        run_id='later-run',iteration=2,mode='none',dispatch={'slug':'later','step':0,
        'subplan_path':str(later),'master_path':str(master)})
    context['worker_sandbox_adapter']='macos-brokered-shell'
    path=tmp_path/'later-context.json';write_context(path,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path));monkeypatch.setenv('ILK_ITER_RECORD_STAGE','driver-check')
    (root/'later.txt').write_text('accepted')
    result=accept_step(context,default_timeout=10,deadline=monotonic()+10)
    result.update(run_id=context['run_id'],iteration=2)
    project_result(result,context)
    append_record(ledger,{'run_id':context['run_id'],'iteration':2,'slug':'later','step_from':0,
        'step_to':1,'commits':result['commits'],'gate_outcome':'pass','head':result['head_commit']})
    project_close_record({'run_id':context['run_id'],'iteration':2,'record_phase':'close','context':context,
        'merge_outcome':'not-applicable','driver_acceptance':result,'driver_integrity_passed':True,
        'state_audit':{'explained':True},'execution':capture_execution(context)})
    monkeypatch.delenv('ILK_ITER_RECORD_STAGE')
    if tamper in {'unclosed','red','base','tree'}:
        import json
        rows=[json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            if row.get('record_phase')=='close' and row.get('slug')=='later':
                if tamper=='unclosed':row['record_phase']='provisional'
                elif tamper=='red':row['gate_outcome']='fail'
                elif tamper=='base':row.pop('acceptance_base_commit',None)
                else:row['accepted_tree']='0'*40
        ledger.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    elif tamper=='contract':later.write_text(later.read_text()+'\n## Contract\nchanged\n')
    elif tamper=='unknown-commit':
        (root/'unknown.txt').write_text('not accepted');git(root,'add','.');git(root,'commit','-qm','unknown change')
    verdict=audit(root,plan,master,ledger)
    assert verdict['proven'] is (tamper is None),verdict


@pytest.mark.parametrize('changed',['tree','plan','master','missing-check'])
def test_scoped_proof_never_covers_changed_code_or_contract(tmp_path,monkeypatch,changed):
    root,plan,master,ledger=closed_fixture(tmp_path,monkeypatch)
    if changed=='tree':
        (root/'counter.py').write_text('def f(value): return value + 9\n');git(root,'add','.');git(root,'commit','-qm','untested change')
    elif changed=='plan':plan.write_text(plan.read_text()+'\n## New contract\nnew requirement\n')
    elif changed=='master':master.write_text(master.read_text()+'\n## New contract\nnew requirement\n')
    else:
        import json
        rows=[json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            if row.get('record_phase')=='close':row['driver_gate_checks']=[]
        ledger.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    assert not audit(root,plan,master,ledger)['proven']
