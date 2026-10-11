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
