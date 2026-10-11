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
