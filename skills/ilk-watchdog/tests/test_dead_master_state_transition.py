"""Dead-tree parking has one authoritative state/body transaction."""
import hashlib
import json
from pathlib import Path
import sys
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).resolve().parents[2]/'ilk-loop/scripts')]
import park_dead_master


def fixture(tmp_path):
    data=tmp_path/'data';plans=data/'plans';plans.mkdir(parents=True)
    master=plans/'MASTER-fixture.md'
    master.write_text('---\nstatus: active\n---\n# Master\n\n## Progress log\n\n| Date | Event |\n|---|---|\n\n## Next\nuntouched\n')
    launcher=data/'runtime/launcher';launcher.mkdir(parents=True)
    (launcher/'last-exit.json').write_text(json.dumps({'state':'work_tree_invalid','run_id':'dead-fixture'}))
    return data,master


def test_parking_status_and_note_share_journal_bytes(tmp_path,monkeypatch,capsys):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,master=fixture(tmp_path)
    park_dead_master.main([str(data)])
    assert json.loads(capsys.readouterr().out)['parked']==master.name
    rows=[json.loads(line) for line in (master.parent/'state-transitions.jsonl').read_text().splitlines()]
    assert [r['phase'] for r in rows]==['intent','applied']
    assert rows[-1]['after_sha256']==hashlib.sha256(master.read_bytes()).hexdigest()
    assert master.read_text().count('work_tree_invalid')==1
    assert master.read_text().index('work_tree_invalid')<master.read_text().index('## Next')


def test_parking_refuses_live_project(tmp_path,monkeypatch,capsys):
    import run_registry
    data,master=fixture(tmp_path);before=master.read_bytes()
    monkeypatch.setattr(run_registry,'project_status',lambda *_a,**_k:{'busy':True})
    park_dead_master.main([str(data)])
    assert json.loads(capsys.readouterr().out)['parked'] is None
    assert master.read_bytes()==before
    assert not (data/'runtime/launcher/parked-dead-fixture.json').exists()


@pytest.mark.parametrize('mode',['hold','worker','changed-bytes','wrong-master','invalid-run-id'])
def test_parking_refusal_preserves_master_and_marker(tmp_path,monkeypatch,capsys,mode):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    data,master=fixture(tmp_path)
    if mode=='hold':master.write_text(master.read_text().replace('status: active','status: active\nhold: human'))
    if mode=='worker':monkeypatch.setenv('ILK_WORKER_SESSION','worker-fixture')
    if mode=='changed-bytes':
        real=park_dead_master.transition
        def change(*args,**kwargs):
            master.write_text(master.read_text().replace('untouched','concurrent change'))
            return real(*args,**kwargs)
        monkeypatch.setattr(park_dead_master,'transition',change)
    sentinel=data/'runtime/launcher/last-exit.json'
    row=json.loads(sentinel.read_text())
    if mode=='wrong-master':row['master_path']=str(master.with_name('MASTER-other.md'))
    if mode=='invalid-run-id':row['run_id']='../escape'
    sentinel.write_text(json.dumps(row));before=master.read_bytes()
    park_dead_master.main([str(data)])
    assert json.loads(capsys.readouterr().out)['parked'] is None
    if mode=='changed-bytes':assert 'concurrent change' in master.read_text() and 'status: active' in master.read_text()
    else:assert master.read_bytes()==before
    assert not list((data/'runtime/launcher').glob('parked-*.json'))
    assert not (master.parent/'state-transitions.jsonl').exists()


def test_parking_refuses_unknown_registry(tmp_path,monkeypatch,capsys):
    import run_registry
    data,master=fixture(tmp_path);before=master.read_bytes()
    def unknown(*_a,**_k):raise run_registry.RegistryError('corrupt registry')
    monkeypatch.setattr(run_registry,'project_status',unknown)
    park_dead_master.main([str(data)])
    assert json.loads(capsys.readouterr().out)['parked'] is None
    assert master.read_bytes()==before
