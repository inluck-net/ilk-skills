"""Status and proof must refer to the same already-selected master."""
from pathlib import Path
import batch_gate
import loop_status
import ship_audit
import json
import sys


def test_status_does_not_switch_to_higher_priority_queued_proof(tmp_path,monkeypatch):
    plans=tmp_path/'plans';plans.mkdir()
    stem='2026-10-12-alpha-subplan'
    (plans/(stem+'.md')).write_text('---\nplan: alpha-subplan\nstatus: shipped\n'
        'current_step: 1\nestimated_steps: 1\nverification_tier: loop-verified\n---\n'
        '## Steps\n### Step 0: change\n')
    next_stem='2026-10-12-alpha-next'
    (plans/(next_stem+'.md')).write_text('---\nplan: alpha-next\nstatus: pending\n'
        'current_step: 0\nestimated_steps: 1\n---\n## Steps\n### Step 0: next\n')
    for name,status,priority in [('alpha','active',0),('beta','queued',100)]:
        (plans/('MASTER-'+name+'.md')).write_text(
            f'---\nmaster_plan: {name}\nslug: {name}\nstatus: {status}\n'
            f'priority: {priority}\n---\n[plan](./{stem}.md)\n[next](./{next_stem}.md)\n')
    monkeypatch.setattr(loop_status,'_resolve_plans_dir',lambda *a:(plans,'fixture'))
    monkeypatch.setattr(batch_gate,'resolve_runtime_dir',lambda *a:tmp_path/'runtime')
    monkeypatch.setattr(ship_audit,'load_ledger_records',lambda *a:[])
    calls=[]
    def audit(**kwargs):
        calls.append(kwargs)
        return dict(proven=False,reasons=['fixture'])
    monkeypatch.setattr(ship_audit,'audit_ship',audit)
    status=loop_status.resolve_status(tmp_path,json_mode=True)
    assert Path(status['master']).name=='MASTER-alpha.md'
    assert calls and calls[0]['batch']=='batch-alpha'
    assert Path(calls[0]['master_path']).name=='MASTER-alpha.md'


def test_status_uses_frozen_roots_and_master(tmp_path,monkeypatch,capsys):
    plans=tmp_path/'frozen-plans';plans.mkdir()
    master=plans/'MASTER-frozen.md'
    master.write_text('---\nmaster_plan: frozen\nstatus: active\n---\n')
    (plans/'MASTER-other.md').write_text('---\nmaster_plan: other\nstatus: active\npriority: 100\n---\n')
    context=dict(schema_version=1,dispatch={'master_path':str(master)},
        **{key:str(tmp_path/key) for key in ('clone','exec_tree','runtime_dir','data_root','code_root')},
        plans_dir=str(plans))
    path=tmp_path/'context.json';path.write_text(json.dumps(context))
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path))
    monkeypatch.setenv('ILK_MASTER','MASTER-other.md')
    def discovery(*args):raise AssertionError('frozen status rediscovered plans')
    monkeypatch.setattr(loop_status,'_resolve_plans_dir',discovery)
    status=loop_status.resolve_status(tmp_path,json_mode=True)
    assert status['master']==master.name
    assert status['plans_dir']==str(plans)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'argv',['loop_status.py'])
    assert loop_status.main()==2  # Deliberately empty master has no subplans.
    assert 'Master:    MASTER-frozen.md' in capsys.readouterr().out


def test_invalid_frozen_context_does_not_fall_back(tmp_path,monkeypatch):
    path=tmp_path/'context.json';path.write_text('{')
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path))
    def discovery(*args):raise AssertionError('invalid context fell back')
    monkeypatch.setattr(loop_status,'_resolve_plans_dir',discovery)
    status=loop_status.resolve_status(tmp_path,json_mode=True)
    assert status['queue_exit']==2 and 'context' in status['error']
