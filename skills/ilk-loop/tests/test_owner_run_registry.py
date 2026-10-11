"""Hermetic lifecycle decisions with injected identity/signal boundaries."""
import json
import pytest
from run_registry import reserve,bind_running,scan,signal_run,finish,RegistryError,read


def fixture(tmp_path):
    runtime=tmp_path/'launcher';identity={'pid':271,'pgid':271,'lstart':'fixed start token'}
    reserve(runtime,run_id='fixture',project_key='consumer',clone=tmp_path/'clone',exec_tree=tmp_path/'work',
            toolkit_sha='frozen-code',expires_at=200,now=100)
    return runtime,identity


def test_launch_gap_expiry_and_running_identity(tmp_path):
    runtime,identity=fixture(tmp_path)
    assert scan(runtime,now=150)[0]['liveness']=='launching'
    assert scan(runtime,now=201)[0]['liveness']=='expired'
    row=bind_running(runtime,'fixture',pid=271,probe=lambda pid:identity,now=150)
    assert row['toolkit_sha']=='frozen-code'
    assert scan(runtime,probe=lambda pid:identity)[0]['liveness']=='running'
    assert scan(runtime,probe=lambda pid:dict(identity,lstart='reused PID'))[0]['liveness']=='reused-or-moved'
    assert scan(runtime,probe=lambda pid:dict(identity,pgid=999))[0]['liveness']=='reused-or-moved'
    assert scan(runtime,probe=lambda pid:None)[0]['liveness']=='dead'


def test_expired_or_duplicate_launch_cannot_bind(tmp_path):
    runtime,identity=fixture(tmp_path)
    with pytest.raises(RegistryError,match='expired'):
        bind_running(runtime,'fixture',pid=271,probe=lambda pid:identity,now=201)
    with pytest.raises(RegistryError,match='already reserved'):
        reserve(runtime,run_id='fixture',project_key='consumer',clone=tmp_path,exec_tree=tmp_path,
                toolkit_sha='other',expires_at=300,now=100)


def test_stop_never_signals_a_reused_pid_and_finish_checks_identity(tmp_path):
    runtime,identity=fixture(tmp_path);signals=[]
    bind_running(runtime,'fixture',pid=271,probe=lambda pid:identity,now=150)
    with pytest.raises(RegistryError,match='no longer live'):
        signal_run(runtime,'fixture',probe=lambda pid:dict(identity,lstart='new process'),send=lambda *args:signals.append(args))
    assert signals==[]
    signal_run(runtime,'fixture',probe=lambda pid:identity,send=lambda *args:signals.append(args))
    assert signals[0][0]==271
    with pytest.raises(RegistryError,match='mismatch'):finish(runtime,'fixture',identity=dict(identity,pgid=999))
    finish(runtime,'fixture',identity=identity)
    assert scan(runtime,probe=lambda pid:identity)==[]


def test_unknown_record_refuses_instead_of_looking_idle(tmp_path):
    runtime,_=fixture(tmp_path);path=runtime/'runs/fixture.json';data=json.loads(path.read_text());data['schema']=999;path.write_text(json.dumps(data))
    with pytest.raises(RegistryError,match='unknown'):scan(runtime)


def test_worker_cannot_reserve_or_finish_and_paths_cannot_escape(tmp_path,monkeypatch):
    runtime,identity=fixture(tmp_path)
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    with pytest.raises(RegistryError,match='worker'):finish(runtime,'fixture',identity=identity)
    monkeypatch.delenv('ILK_WORKER_SESSION')
    with pytest.raises(RegistryError,match='invalid run ID'):
        reserve(runtime,run_id='../escape',project_key='consumer',clone=tmp_path,exec_tree=tmp_path,
                toolkit_sha='code',expires_at=200,now=100)


def test_actual_owned_posix_identity_and_stop(tmp_path):
    import os,subprocess,sys,time
    if os.name=='nt':pytest.skip('explicit POSIX owned process fixture')
    from run_registry import process_identity
    process=subprocess.Popen([sys.executable,'-u','-c','import threading;print("ready",flush=True);threading.Event().wait()'],
                             stdout=subprocess.PIPE,text=True,start_new_session=True)
    runtime=tmp_path/'launcher'
    try:
        assert process.stdout.readline().strip()=='ready'
        identity=process_identity(process.pid)
        assert identity['pid']==process.pid and identity['pgid']==process.pid and identity['lstart']
        reserve(runtime,run_id='owned',project_key='fixture',clone=tmp_path,exec_tree=tmp_path,
                toolkit_sha='test',expires_at=time.time()+10)
        bind_running(runtime,'owned',pid=process.pid)
        signal_run(runtime,'owned');process.wait(timeout=10)
        assert process.returncode!=0
        finish(runtime,'owned',identity=identity)
    finally:
        if process.poll() is None:process.kill();process.wait()
