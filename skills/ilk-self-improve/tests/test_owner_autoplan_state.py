"""Scope-freeze fixes: honest autoplan authority and locked metadata writes."""
import hashlib
import json
from pathlib import Path
import pytest
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"scripts"),str(Path(__file__).resolve().parents[2]/"ilk-loop/scripts"),str(Path(__file__).resolve().parents[2]/"ilk-watchdog/scripts")]
import autoplan
import autoplan_rails
from plan_state import TransitionError


def master(tmp_path,status='draft',hold=''):
    p=tmp_path/'MASTER-fixture.md'
    p.write_text(f'---\nstatus: {status}\n'+hold+'---\n# Master\n')
    return p


def write(path,status):
    helper=getattr(autoplan,'write_autoplan_status',None)
    if helper:return helper(path,status,run_id='planner-fixture')
    return autoplan.write_status(path,status,allow_from_held=True)


def test_autoplan_never_releases_human_hold(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path,hold='hold: human\n');before=p.read_bytes()
    with pytest.raises(TransitionError,match='hold'):write(p,'queued')
    assert p.read_bytes()==before


def test_generated_draft_release_has_autoplan_actor(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path)
    autoplan_rails.mark_master(p,candidate_id='candidate-fixture',run_id='planner-fixture')
    write(p,'queued')
    rows=[json.loads(line) for line in (p.parent/'state-transitions.jsonl').read_text().splitlines()]
    assert rows[-1]['actor']=='autoplan'
    assert rows[-1]['after_sha256']==hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(row['actor']!='human' for row in rows)


def test_autoplan_metadata_is_journaled_in_same_bytes(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path)
    autoplan_rails.mark_master(p,candidate_id='candidate-fixture',run_id='planner-fixture')
    journal=p.parent/'state-transitions.jsonl'
    assert journal.exists()
    rows=[json.loads(line) for line in journal.read_text().splitlines()]
    assert [r['phase'] for r in rows]==['intent','applied']
    assert rows[-1]['actor']=='autoplan'
    assert rows[-1]['after_sha256']==hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize('mode',['human-hold','parked','blocked','paused','other-planner','worker'])
def test_planner_metadata_never_changes_held_or_foreign_master(tmp_path,monkeypatch,mode):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path,status=mode if mode in {'parked','blocked','paused'} else 'draft',hold='hold: human\n' if mode=='human-hold' else '')
    if mode=='other-planner':p.write_text(p.read_text().replace('status: draft','status: draft\nautoplan_run: other-run'))
    if mode=='worker':monkeypatch.setenv('ILK_WORKER_SESSION','fixture')
    before=p.read_bytes()
    with pytest.raises(TransitionError):autoplan_rails.mark_master(p,candidate_id='candidate-fixture',run_id='planner-fixture')
    assert p.read_bytes()==before


def test_unmarked_human_draft_is_not_auto_queued(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path);before=p.read_bytes()
    with pytest.raises(TransitionError):write(p,'queued')
    assert p.read_bytes()==before


def test_metadata_cas_preserves_concurrent_master_edit(tmp_path,monkeypatch):
    import plan_state
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    p=master(tmp_path);real=plan_state.transition
    def moved(*a,**kw):
        p.write_text(p.read_text()+'concurrent body\n')
        return real(*a,**kw)
    monkeypatch.setattr(plan_state,'transition',moved)
    with pytest.raises(TransitionError,match='bytes changed'):
        autoplan_rails.mark_master(p,candidate_id='candidate-fixture',run_id='planner-fixture')
    assert 'concurrent body' in p.read_text() and 'auto_planned' not in p.read_text()
