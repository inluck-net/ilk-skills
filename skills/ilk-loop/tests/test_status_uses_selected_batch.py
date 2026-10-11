"""Status and proof must refer to the same already-selected master."""
from pathlib import Path
import batch_gate
import loop_status
import ship_audit


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
