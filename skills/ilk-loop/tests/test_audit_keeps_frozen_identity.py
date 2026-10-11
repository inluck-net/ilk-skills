"""An available frozen scope never permits a new audit target or master."""
from pathlib import Path
import pytest
import iteration_context
import ship_audit

@pytest.mark.parametrize('case',['unregistered','missing-plan','different-master'])
def test_frozen_audit_refuses_competing_identity(tmp_path,monkeypatch,case):
    plans=tmp_path/'plans';plans.mkdir()
    master=plans/'MASTER-one.md';master.write_text('---\nmaster_plan: one\n---\n')
    plan=plans/'one.md';plan.write_text('---\nplan: one\n---\n')
    context=dict(plans_dir=str(plans),exec_tree=str(tmp_path/'frozen-execution'),mode='none',
        dispatch={'master_path':str(master)},plans={'one.md':{'path':str(plan)}})
    monkeypatch.setattr(iteration_context,'read_context',lambda:context)
    target=plans/'unregistered.md' if case=='unregistered' else None if case=='missing-plan' else plan
    selected=plans/'MASTER-other.md' if case=='different-master' else master
    if case=='different-master':selected.write_text('---\nmaster_plan: other\n---\n')
    with pytest.raises(iteration_context.ContextError):
        ship_audit.audit_execution_root(tmp_path,target,selected)


def test_no_context_preserves_legacy_root(tmp_path,monkeypatch):
    monkeypatch.setattr(iteration_context,'read_context',lambda:None)
    assert ship_audit.audit_execution_root(tmp_path,None,None)==tmp_path
