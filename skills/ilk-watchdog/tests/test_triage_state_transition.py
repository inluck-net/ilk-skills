"""Triage state mutations use the shared journal and preserve human holds."""
import json
from pathlib import Path
import sys
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).resolve().parents[2]/'ilk-loop/scripts')]
from triage_apply import _set_plan_status
from plan_state import TransitionError


def fixture(tmp_path,extra=''):
    data=tmp_path/'data';plans=data/'plans';plans.mkdir(parents=True)
    path=plans/'2026-10-12-triage.md'
    path.write_text('---\nstatus: in-progress\ncurrent_step: 2\nlast_updated: 2026-10-11\n'+extra+'---\n# Plan\n')
    return data,path


def test_triage_reopen_writes_durable_state_transition(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,path=fixture(tmp_path)
    _set_plan_status(path,data,status='in-progress',current_step=1)
    journal=path.parent/'state-transitions.jsonl'
    assert journal.exists()
    rows=[json.loads(line) for line in journal.read_text().splitlines()]
    assert [row['phase'] for row in rows]==['intent','applied']
    assert rows[-1]['actor']=='triage'
    assert rows[-1]['to']['current_step']==1


def test_triage_cannot_reopen_human_held_plan(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,path=fixture(tmp_path,'hold: human\n');before=path.read_bytes()
    with pytest.raises(TransitionError,match='human hold'):
        _set_plan_status(path,data,status='in-progress',current_step=1)
    assert path.read_bytes()==before


@pytest.mark.parametrize("action",["reopen","amend","ack-and-relaunch"])
def test_public_apply_records_hold_refusal_without_ack(tmp_path,monkeypatch,action):
    import triage_apply
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    monkeypatch.setattr(triage_apply,'ilk_notify',lambda **_: None)
    data,path=fixture(tmp_path,'hold: human\n');before=path.read_bytes()
    decision={'action':action,'slug':'triage','step':1,'finding':'diagnosis',
              'basis':'stopped run','falsifier':'repair passes'}
    result=triage_apply.apply(decision,data,run_id='held-fixture')
    assert result['action']=='refused' and result['audit_kind']=='triage-refused'
    assert path.read_bytes()==before
    assert not (data/'runtime/launcher/blacklist-cleared.json').exists()


def test_triage_byte_cas_preserves_concurrent_edit(tmp_path,monkeypatch):
    import plan_state
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,path=fixture(tmp_path);real=plan_state.transition
    def move(*args,**kwargs):
        path.write_text(path.read_text().replace('current_step: 2','current_step: 3'))
        return real(*args,**kwargs)
    monkeypatch.setattr(plan_state,'transition',move)
    with pytest.raises(TransitionError):
        _set_plan_status(path,data,status='in-progress',current_step=1)
    assert 'current_step: 3' in path.read_text()
    assert not (path.parent/'state-transitions.jsonl').exists()


def test_findings_update_uses_journal_without_changing_step(tmp_path,monkeypatch):
    from triage_apply import _append_finding
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,path=fixture(tmp_path)
    _append_finding(path,data,run_id='finding-fixture',finding='diagnosis',basis='output',falsifier='repaired')
    assert 'current_step: 2' in path.read_text()
    rows=[json.loads(line) for line in (path.parent/'state-transitions.jsonl').read_text().splitlines()]
    assert rows[-1]['phase']=='applied' and rows[-1]['from_']==rows[-1]['to']


def test_worker_cannot_write_triage_or_audit_state(tmp_path,monkeypatch):
    import triage_apply
    data,path=fixture(tmp_path)
    before={p:p.read_bytes() for p in data.rglob('*') if p.is_file()}
    monkeypatch.setenv('ILK_WORKER_SESSION','fixture-worker')
    with pytest.raises(TransitionError,match='worker cannot'):
        triage_apply.apply({'action':'ack-and-relaunch'},data,run_id='worker-fixture')
    assert {p:p.read_bytes() for p in data.rglob('*') if p.is_file()}==before


def test_triage_target_is_exact_not_a_slug_prefix(tmp_path,monkeypatch):
    from triage_apply import _resolve_target_slug
    data,path=fixture(tmp_path)
    sibling=path.parent/'2026-10-12-foo-verify.md'
    sibling.write_text('---\nplan: foo-verify\nstatus: in-progress\n---\n')
    target=path.parent/'2026-10-12-foo.md'
    target.write_text('---\nplan: foo\nstatus: in-progress\n---\n')
    real=Path.glob
    def sibling_first(root,pattern):
        if root==path.parent and pattern=='*foo*.md':return iter([sibling,target])
        return real(root,pattern)
    monkeypatch.setattr(Path,'glob',sibling_first)
    selected,slug=_resolve_target_slug({'slug':'foo'},data,path.parent)
    assert selected==target and slug=='foo'


def test_triage_refuses_duplicate_exact_identity_before_plan_change(tmp_path,monkeypatch):
    import triage_apply
    monkeypatch.setattr(triage_apply,'ilk_notify',lambda **_: None)
    data,path=fixture(tmp_path)
    for name in ('first','second'):
        (path.parent/(name+'-duplicate.md')).write_text('---\nplan: duplicate\nstatus: in-progress\ncurrent_step: 1\n---\n')
    before={p:p.read_bytes() for p in path.parent.glob('*.md')}
    result=triage_apply.apply({'action':'reopen','slug':'duplicate','step':0,
                              'finding':'diagnosis','basis':'stopped run','falsifier':'repair'},
                             data,run_id='duplicate-fixture')
    assert result['action']=='refused'
    assert {p:p.read_bytes() for p in path.parent.glob('*.md')}==before
    assert not (data/'runtime/launcher/blacklist-cleared.json').exists()


def test_triage_uses_frozen_record_path_instead_of_duplicate_aliases(tmp_path,monkeypatch):
    import triage_apply
    from iteration_context import plan_snapshot
    monkeypatch.setattr(triage_apply,'ilk_notify',lambda **_: None)
    data,path=fixture(tmp_path)
    path.write_text(path.read_text().replace('status: in-progress','plan: duplicate\nstatus: in-progress'))
    other=path.parent/'2026-10-12-other-duplicate.md'
    other.write_text(path.read_text())
    context={'schema_version':1,'run_id':'frozen-fixture','iteration':1,
             'plans_dir':str(path.parent),'exec_tree':str(tmp_path),'code_root':str(tmp_path),
             'plans':{path.name:plan_snapshot(path)},
             'dispatch':{'slug':'duplicate','step':2,'subplan_path':str(path)}}
    logs=data/'logs';logs.mkdir()
    (logs/'.ilk-loop.log').write_text(json.dumps({'run_id':'frozen-fixture','iteration':1,
                                                'record_phase':'close','context':context})+'\n')
    before=other.read_bytes()
    result=triage_apply.apply({'action':'reopen','slug':'duplicate','step':1,
                              'finding':'diagnosis','basis':'recorded run','falsifier':'repair'},
                             data,run_id='frozen-fixture')
    assert result['action']=='reopen'
    assert 'current_step: 1' in path.read_text() and other.read_bytes()==before


@pytest.mark.parametrize('mutation',['changed-plan','wrong-decision','missing-context','bool-iteration','contradictory-close','held-master','bool-schema','bad-master-snapshot'])
def test_frozen_triage_refuses_changed_or_unknown_authority(tmp_path,monkeypatch,mutation):
    import triage_apply
    from iteration_context import plan_snapshot
    monkeypatch.setattr(triage_apply,'ilk_notify',lambda **_: None)
    data,path=fixture(tmp_path)
    context={'schema_version':1,'run_id':'record-refusal','iteration':1,'plans_dir':str(path.parent),
             'exec_tree':str(tmp_path),'code_root':str(tmp_path),'plans':{path.name:plan_snapshot(path)},
             'dispatch':{'slug':'triage','step':2,'subplan_path':str(path)}}
    master=path.parent/'MASTER-fixture.md';master.write_text('---\nstatus: active\n---\n# Master\n')
    context['dispatch']['master_path']=str(master);context['master_snapshot']=plan_snapshot(master)
    row={'record_phase':'close','run_id':'record-refusal','iteration':1,'context':context}
    decision={'action':'reopen','slug':'triage','step':1,'finding':'diagnosis','basis':'record','falsifier':'repair'}
    if mutation=='changed-plan':path.write_text(path.read_text()+'\nChanged contract\n')
    elif mutation=='wrong-decision':decision['slug']='unrelated'
    elif mutation=='missing-context':row['context']=None
    elif mutation=='bool-iteration':row['iteration']=True
    elif mutation=='bool-schema':context['schema_version']=True
    elif mutation=='bad-master-snapshot':context['master_snapshot']='unknown'
    elif mutation=='held-master':master.write_text(master.read_text().replace('status: active','status: parked\nhold: human'))
    logs=data/'logs';logs.mkdir();body=json.dumps(row)+'\n'
    if mutation=='contradictory-close':
        import copy
        other=copy.deepcopy(row);other['context']['dispatch']['step']=1;body+=json.dumps(other)+'\n'
    (logs/'.ilk-loop.log').write_text(body)
    before={p:p.read_bytes() for p in path.parent.glob('*.md')}
    result=triage_apply.apply(decision,data,run_id='record-refusal')
    assert result['action']=='refused'
    assert {p:p.read_bytes() for p in path.parent.glob('*.md')}==before
    assert not (data/'runtime/launcher/blacklist-cleared.json').exists()


def test_triage_refuses_when_another_run_is_launching(tmp_path,monkeypatch):
    import triage_apply,time
    from run_registry import reserve
    monkeypatch.setattr(triage_apply,'ilk_notify',lambda **_: None)
    data,path=fixture(tmp_path);before=path.read_bytes()
    # Fixture judgment: expiry remains future-dated for this synchronous call;
    # a valid setup exceeding 60s falsifies the fixture, not a runtime default.
    reserve(data/'runtime/launcher',run_id='new-launch',project_key='fixture',clone=tmp_path,
            exec_tree=tmp_path,toolkit_sha='a'*40,expires_at=time.time()+60)
    result=triage_apply.apply({'action':'reopen','slug':'triage','step':1,
                              'finding':'old failure','basis':'old run','falsifier':'repair'},
                             data,run_id='old-run')
    assert result['action']=='refused' and path.read_bytes()==before
    assert not (data/'runtime/launcher/blacklist-cleared.json').exists()
