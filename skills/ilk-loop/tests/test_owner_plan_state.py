from pathlib import Path
import json
import pytest
from plan_state import transition,TransitionError,_digest


def plan(tmp_path,status='pending'):
    path=tmp_path/'step.md'
    path.write_text(f'---\nstatus: {status}\ncurrent_step: 0\n---\n# Exact body\n')
    return path


def test_cas_advances_once_and_preserves_body(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path)
    assert transition(path,{'current_step':'0'},{'current_step':1},actor='driver',reason='gate pass')
    assert path.read_text()=='---\nstatus: pending\ncurrent_step: 1\n---\n# Exact body\n'
    with pytest.raises(TransitionError,match='compare-and-set'):
        transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='stale gate')
    rows=[json.loads(line) for line in (tmp_path/'state-transitions.jsonl').read_text().splitlines()]
    assert [row['phase'] for row in rows]==['intent','applied']
    assert rows[0]['id']==rows[1]['id']


@pytest.mark.parametrize('status',['draft','parked','blocked'])
def test_driver_cannot_lift_or_progress_hold(tmp_path,monkeypatch,status):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path,status)
    with pytest.raises(TransitionError,match='human hold'):
        transition(path,{'status':status},{'current_step':1},actor='driver',reason='gate pass')
    assert transition(path,{'status':status},{'status':'active'},actor='human',reason='operator released')


def test_worker_cannot_spoof_human_actor(tmp_path,monkeypatch):
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    path=plan(tmp_path)
    with pytest.raises(TransitionError,match='workers'):
        transition(path,{'current_step':0},{'current_step':1},actor='human',reason='spoof')


def test_guarded_master_hold_and_changed_status_refuse_step_advance(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path);master=tmp_path/'MASTER-fixture.md'
    master.write_text('---\nstatus: active\n---\n')
    expected={'status':'active','hold':None}
    master.write_text('---\nstatus: blocked\nhold: human\n---\n')
    with pytest.raises(TransitionError,match='guard state changed'):
        transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='gate green',guards={master:expected})
    with pytest.raises(TransitionError,match='human hold on guard'):
        transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='gate green',guards={master:{'status':'blocked','hold':'human'}})
    assert 'current_step: 0' in path.read_text()


@pytest.mark.parametrize('applied',[False,True])
def test_interrupted_intent_is_explained_without_fabrication(tmp_path,monkeypatch,applied):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path);before=path.read_bytes();after=before.replace(b'current_step: 0',b'current_step: 1')
    row={'id':'interrupted','path':str(path.resolve()),'phase':'intent','before_sha256':_digest(before),'after_sha256':_digest(after)}
    log=tmp_path/'state-transitions.jsonl';log.write_text(json.dumps(row)+'\n')
    if applied:path.write_bytes(after)
    assert transition(path,{'current_step':1 if applied else 0},{'last_updated':'today'},actor='driver',reason='next operation')
    rows=[json.loads(line) for line in log.read_text().splitlines()]
    assert rows[1]['phase']==('recovered-applied' if applied else 'recovered-not-applied')


def test_unexplained_interrupted_mutation_is_refused(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path);original=path.read_bytes()
    row={'id':'interrupted','path':str(path.resolve()),'phase':'intent','before_sha256':_digest(original),'after_sha256':'unknown'}
    (tmp_path/'state-transitions.jsonl').write_text(json.dumps(row)+'\n');path.write_bytes(original+b'foreign edit')
    with pytest.raises(TransitionError,match='unexplained'):
        transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='pass')


def test_frozen_registry_prevents_foreign_state_write(tmp_path,monkeypatch):
    import importlib.util
    from iteration_context import resolve_context,write_context
    spec=importlib.util.spec_from_file_location('contextfixture',Path(__file__).with_name('test_iteration_context.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    context=resolve_context(**fixture.fixture(tmp_path))
    contextpath=tmp_path/'context.json';write_context(contextpath,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(contextpath))
    outsider=tmp_path/'foreign.md';outsider.write_text('---\ncurrent_step: 0\nstatus: pending\n---\n')
    with pytest.raises(TransitionError,match='outside frozen registry'):
        transition(outsider,{'current_step':0},{'current_step':1},actor='driver',reason='spoof')


def test_concurrent_driver_calls_cannot_both_advance(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path);barrier=Barrier(2)
    def attempt():
        barrier.wait()
        try:
            return transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='same dispatched gate')
        except TransitionError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(results)==[False,True]
    assert 'current_step: 1' in path.read_text()


def test_human_hold_field_survives_runnable_status(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path,'active');path.write_text(path.read_text().replace('status: active','status: active\nhold: human'))
    with pytest.raises(TransitionError,match='human hold'):
        transition(path,{'current_step':0},{'current_step':1},actor='driver',reason='pass')


def test_held_ship_refuses_before_creating_marker(tmp_path,monkeypatch):
    import test_ship_transition as fixtures
    from ship_transition import ship,ShipTransitionError
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    monkeypatch.delenv('ILK_ITERATION_SUBPLAN',raising=False)
    repo=fixtures._make_repo(tmp_path)
    plans=fixtures._make_plans_dir(tmp_path,status='blocked')
    before=fixtures._git(repo,'rev-parse','HEAD')
    with pytest.raises(ShipTransitionError,match='human hold'):
        ship(plans_dir=plans,slug='conflict-batch-verify',repo=repo)
    assert fixtures._git(repo,'rev-parse','HEAD')==before
    assert not (plans/'.ship-intent.json').exists()


def test_notes_and_rollback_keep_hold_and_complete_journal(tmp_path,monkeypatch):
    from plan_state import append_findings,rollback_ship
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path,'shipped')
    raw=path.read_bytes().replace(b'current_step: 0',b'current_step: 2')
    path.write_bytes(b'\xef\xbb\xbf'+raw.replace(b'\n',b'\r\n'))
    assert append_findings(path,'a refusal',reason='diagnostic')
    assert not append_findings(path,'a refusal',reason='diagnostic')
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in path.read_bytes() and b'\n' not in path.read_bytes().replace(b'\r\n',b'')
    rollback_ship(path,step=0,reason='foreign ship')
    assert 'current_step: 0' in path.read_text(encoding='utf-8-sig')
    rows=[json.loads(line) for line in (tmp_path/'state-transitions.jsonl').read_text().splitlines()]
    assert [row['phase'] for row in rows]==['intent','applied','intent','applied']
    path.write_text('---\nstatus: shipped\nhold: human\ncurrent_step: 2\n---\n')
    with pytest.raises(TransitionError,match='human hold'):
        rollback_ship(path,step=0,reason='foreign ship')


def test_rollback_refuses_corrupt_history_and_stale_bytes(tmp_path,monkeypatch):
    from plan_state import rollback_ship,append_findings
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=plan(tmp_path,'shipped');before=path.read_bytes()
    history=tmp_path/'history.jsonl';history.write_text('{broken')
    with pytest.raises(TransitionError,match='corrupt'):
        rollback_ship(path,history=history,slug='alpha',reason='red')
    assert path.read_bytes()==before
    original=__import__('plan_state').transition
    def race(*a,**k):
        path.write_bytes(path.read_bytes()+b'\nchanged by human\n')
        return original(*a,**k)
    monkeypatch.setattr('plan_state.transition',race)
    with pytest.raises(TransitionError,match='bytes changed'):
        append_findings(path,'diagnostic',reason='test race')
    assert 'diagnostic' not in path.read_text()
