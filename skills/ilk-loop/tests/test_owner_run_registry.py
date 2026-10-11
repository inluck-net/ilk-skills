"""Hermetic lifecycle decisions with injected identity/signal boundaries."""
import json
import pytest
from run_registry import reserve,bind_running,scan,signal_run,finish,RegistryError,read


def test_owner_exit_cleanup_refuses_reuse_and_never_signals_its_observer(tmp_path,monkeypatch):
    import run_registry as rr
    runtime,owner=fixture(tmp_path)
    rr.bind_running(runtime,'fixture',pid=owner['pid'],probe=lambda _:owner,now=150)
    path=runtime/'runs/fixture.json';row=read(path)
    row['children']=[dict(pid=501,pgid=501,lstart='worker'),dict(pid=502,pgid=502,lstart='gate'),
                     dict(pid=503,pgid=503,lstart='broker')]
    path.write_text(json.dumps(row));sent=[]
    monkeypatch.setattr(rr.os,'getpgrp',lambda:503)
    monkeypatch.setattr(rr,'group_exists',lambda _:False)
    observed={501:row['children'][0],502:dict(row['children'][1],lstart='reused')}
    refused=rr.cleanup_children_after_owner_exit(runtime,'fixture',owner=owner,
        probe=lambda pid:observed.get(pid),send=lambda *args:sent.append(args))
    assert refused==[502] and sent==[(501,rr.signal.SIGKILL)]
    with pytest.raises(RegistryError,match='vanished'):
        rr.cleanup_children_after_owner_exit(runtime,'fixture',owner=owner,probe=lambda _:owner)


def fixture(tmp_path):
    runtime=tmp_path/'launcher';identity={'pid':271,'pgid':271,'lstart':'fixed start token'}
    reserve(runtime,run_id='fixture',project_key='consumer',clone=tmp_path/'clone',exec_tree=tmp_path/'work',
            toolkit_sha='frozen-code',expires_at=200,now=100)
    return runtime,identity


@pytest.mark.parametrize('state,alive',[('S',True),('R+',True),('Z',False),('Z+',False),('X',False)])
def test_zombie_or_exited_owner_is_not_a_live_identity(monkeypatch,state,alive):
    import run_registry as rr
    from types import SimpleNamespace
    monkeypatch.setattr(rr.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,
        stdout='271 271 '+state+' Sun Oct 11 13:02:46 2026\n'))
    identity=rr.process_identity(271)
    assert bool(identity)==alive
    if alive:assert identity=={'pid':271,'pgid':271,'lstart':'Sun Oct 11 13:02:46 2026'}


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


def test_busy_decision_is_registry_or_unowned_legacy_never_argv(tmp_path):
    from run_registry import project_status
    runtime,identity=fixture(tmp_path)
    assert project_status(runtime,now=150,locked=False)['source']=='registry'
    assert project_status(runtime,now=201,locked=True)['source']=='unowned-lock'
    assert not project_status(runtime,now=201,locked=False,probe=lambda pid:None)['busy']
    (runtime/'running.pid').write_text('271')
    assert project_status(runtime,now=201,locked=False,probe=lambda pid:identity)['source']=='unowned-legacy-pid'
    assert not project_status(runtime,now=201,locked=False,probe=lambda pid:None)['busy']
    with pytest.raises(RegistryError,match='another project'):
        project_status(runtime,project=tmp_path/'other',now=150,locked=False)


def test_second_project_reservation_refuses_even_with_different_run_id(tmp_path):
    runtime,identity=fixture(tmp_path)
    with pytest.raises(RegistryError,match='active registry'):
        reserve(runtime,run_id='other',project_key='consumer',clone=tmp_path/'clone',exec_tree=tmp_path/'work',
                toolkit_sha='same',expires_at=300,now=100)


def test_direct_attach_requires_lock_identity(tmp_path,monkeypatch):
    from run_registry import attach
    runtime=tmp_path/'launcher';runtime.mkdir()
    with pytest.raises(RegistryError,match='lock identity absent'):
        attach(runtime,'direct',pid=271,project=tmp_path,key='consumer',toolkit_sha='test',ttl=120,probe=lambda pid:{'pid':271,'pgid':271,'lstart':'own'})
    (runtime/'run.lock').write_text(json.dumps({'pid':271,'pgid':271,'lstart':'other'}))
    with pytest.raises(RegistryError,match='does not own'):
        attach(runtime,'direct',pid=271,project=tmp_path,key='consumer',toolkit_sha='test',ttl=120,probe=lambda pid:{'pid':271,'pgid':271,'lstart':'own'})


def test_cli_stop_refuses_live_unowned_legacy_process(tmp_path):
    import subprocess,sys
    from pathlib import Path
    script=Path(__file__).parents[1]/'scripts/run_registry.py'
    runtime=tmp_path/'launcher';runtime.mkdir()
    # This PID is real but no registry record proves ownership.
    (runtime/'running.pid').write_text(str(__import__('os').getpid()))
    cp=subprocess.run([sys.executable,str(script),'stop','--runtime',str(runtime),'--run-id','absent'],capture_output=True,text=True)
    assert cp.returncode==2 and 'unreadable' in cp.stderr


def test_child_groups_are_verified_before_parent_stop_and_block_finish(tmp_path,monkeypatch):
    import run_registry as rr
    runtime,identity=fixture(tmp_path);child={'pid':272,'pgid':272,'lstart':'child start'}
    probe=lambda pid:identity if pid==271 else child if pid==272 else None
    bind_running(runtime,'fixture',pid=271,probe=probe,now=150)
    rr.register_child(runtime,'fixture',pid=272,owner_pid=271,probe=probe)
    sent=[]
    monkeypatch.setattr(rr,'group_exists',lambda group:False)
    signal_run(runtime,'fixture',probe=probe,send=lambda group,sig:sent.append(group))
    assert sent==[272,271]
    monkeypatch.setattr(rr,'process_identity',probe)
    with pytest.raises(RegistryError,match='child'):finish(runtime,'fixture',identity=identity)
    assert project_busy(rr,runtime,lambda pid:child if pid==272 else None)
    sent.clear()
    signal_run(runtime,'fixture',probe=lambda pid:child if pid==272 else None,send=lambda group,sig:sent.append(group))
    assert sent==[272] # orphan child is still owned; no signal to dead/reused parent


def project_busy(rr,runtime,probe):
    return rr.project_status(runtime,probe=probe,locked=False)['busy']


def test_unknown_child_group_blocks_without_unverified_signal(tmp_path,monkeypatch):
    import run_registry as rr
    runtime,identity=fixture(tmp_path);child={'pid':272,'pgid':272,'lstart':'child start'}
    bind_running(runtime,'fixture',pid=271,probe=lambda pid:identity,now=150)
    rr.register_child(runtime,'fixture',pid=272,owner_pid=271,probe=lambda pid:identity if pid==271 else child)
    monkeypatch.setattr(rr,'group_exists',lambda group:True)
    sent=[]
    with pytest.raises(RegistryError,match='ownership unavailable'):
        signal_run(runtime,'fixture',probe=lambda pid:identity if pid==271 else None,send=lambda *a:sent.append(a))
    assert not sent
    assert project_busy(rr,runtime,lambda pid:None)


def test_shell_registry_readers_keep_launch_gap_busy(tmp_path,monkeypatch):
    import os,subprocess,time
    from pathlib import Path
    from ilk_paths import project_key,external_launcher_dir
    repo=tmp_path/'consumer';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
    monkeypatch.setenv('ILK_DATA_HOME',str(tmp_path/'data'));monkeypatch.setenv('ILK_DATA_DIR',str(tmp_path/'data'))
    runtime=external_launcher_dir(project_key(repo));scripts=Path(__file__).parents[2]
    reserve(runtime,run_id='reader',project_key='consumer',clone=repo,exec_tree=repo,toolkit_sha='test',expires_at=time.time()+120)
    env=dict(os.environ,ILK_SKIP_MAIN='1',ILK_DOTSOURCE_ONLY='1',ILK_SKILL_HOME=str(scripts))
    cases=[('ilk-launcher/scripts/launch.sh','test_running_pid "$1"','registry-busy'),
           ('ilk-watchdog/scripts/watchdog.sh','_SKILL_ROOT="$2"; read_ilk_pid "$1"','registry-busy'),
           ('ilk-watchdog/scripts/scheduler.sh','test_running_pid "$3" "$1"','')]
    for file,call,want in cases:
        cp=subprocess.run(['bash','-c',f'source "{scripts/file}"; {call}', 'fixture',str(repo),str(scripts),str(runtime.parent.parent)],env=env,capture_output=True,text=True)
        assert cp.returncode==0,(file,cp.stderr)
        assert cp.stdout.strip()==want,(file,cp.stdout,cp.stderr)
    (runtime/'runs/reader.json').write_text('{broken')
    cp=subprocess.run(['bash','-c',f'source "{scripts}/ilk-watchdog/scripts/scheduler.sh"; test_running_pid "$1" "$2"','fixture',str(runtime.parent.parent),str(repo)],env=env,capture_output=True,text=True)
    assert cp.returncode==0 # corrupt ownership is busy, never dispatch permission


def test_actual_stop_caller_only_signals_registered_fixture(tmp_path,monkeypatch):
    import os,subprocess,sys,time,threading
    from pathlib import Path
    from ilk_paths import project_key,external_launcher_dir
    repo=tmp_path/'consumer';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
    monkeypatch.setenv('ILK_DATA_HOME',str(tmp_path/'data'));monkeypatch.setenv('ILK_DATA_DIR',str(tmp_path/'data'))
    runtime=external_launcher_dir(project_key(repo));scripts=Path(__file__).parents[2]
    process=subprocess.Popen([sys.executable,'-u','-c','import threading;print("ready",flush=True);threading.Event().wait()'],stdout=subprocess.PIPE,text=True,start_new_session=True)
    try:
        assert process.stdout.readline().strip()=='ready'
        reserve(runtime,run_id='stop-fixture',project_key=project_key(repo),clone=repo,exec_tree=repo,toolkit_sha='test',expires_at=time.time()+120)
        row=bind_running(runtime,'stop-fixture',pid=process.pid)
        (runtime/'running.pid').write_text(str(process.pid))
        sentinel=runtime/'last-exit.json';sentinel.write_text(json.dumps(dict(state='running',run_id=row['run_id'],pid=process.pid,toolkit_sha='test')))
        # Reap owned child concurrently, as a real detached parent's init does.
        reaper=threading.Thread(target=lambda:process.wait(timeout=10));reaper.start()
        env=dict(os.environ,ILK_SKILL_HOME=str(scripts))
        cp=subprocess.run(['bash',str(scripts/'ilk-launcher/scripts/stop.sh'),'--project-path',str(repo)],env=env,capture_output=True,text=True,timeout=10)
        reaper.join()
        assert cp.returncode==0,(cp.stdout,cp.stderr)
        assert process.returncode!=0
        terminal=json.loads(sentinel.read_text())
        assert terminal['state']=='interrupted' and terminal['toolkit_sha']=='test'
        assert not (runtime/'running.pid').exists()
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_merge_and_migration_share_registry_without_argv(tmp_path,monkeypatch):
    import os,time
    import run_registry as rr
    import selfmod_worktree as sw
    data=tmp_path/'data';runtime=data/'projects/consumer/runtime/launcher';clone=tmp_path/'consumer'
    identity={'pid':271,'pgid':271,'lstart':'start'}
    reserve(runtime,run_id='merge',project_key='consumer',clone=clone,exec_tree=clone,toolkit_sha='test',expires_at=time.time()+120)
    bind_running(runtime,'merge',pid=271,probe=lambda pid:identity)
    monkeypatch.setenv('ILK_DATA_HOME',str(data));monkeypatch.setenv('ILK_DATA_DIR',str(data))
    original=rr.project_status
    monkeypatch.setattr(rr,'project_status',lambda runtime:original(runtime,probe=lambda pid:identity,locked=False))
    monkeypatch.setattr(sw,'_pgrep',lambda *a:(_ for _ in ()).throw(AssertionError('argv probe forbidden')))
    assert sw._find_any_live_ilk_pids()==[271]
    assert sw._find_live_ilk_pids(clone)==[271]
    assert sw._find_live_ilk_pids(tmp_path/'other')==[]
    assert sw._find_live_ilk_pids_hostwide(tmp_path/'toolkit')==[271]
    assert rr.host_blockers(data_root=data,exclude_group=271)==[]
    row=read(runtime/'runs/merge.json');row.update(inputs_pinned=True,code_root=str(tmp_path/'immutable-code'))
    rr._write(runtime/'runs/merge.json',row)
    assert rr.host_blockers(data_root=data,clone=tmp_path/'toolkit')==[]


def test_shared_caller_group_cannot_be_registered_as_run(tmp_path):
    runtime,identity=fixture(tmp_path)
    with pytest.raises(RegistryError,match='own its process group'):
        bind_running(runtime,'fixture',pid=271,probe=lambda pid:dict(identity,pgid=270),now=150)


def test_relaunch_and_stale_readers_respect_reservation_and_unknown(tmp_path,monkeypatch):
    import importlib.util,time
    import pid_health
    from pathlib import Path
    runtime=tmp_path/'launcher';project=tmp_path/'consumer';project.mkdir()
    reserve(runtime,run_id='reader',project_key='consumer',clone=project,exec_tree=project,toolkit_sha='test',expires_at=time.time()+120)
    script=Path(__file__).parents[2]/'ilk-watchdog/scripts/relaunch_guard.py'
    spec=importlib.util.spec_from_file_location('owner_relaunch',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.decide(project,runtime)[0]==10
    assert pid_health.detect_stale_running('running',999999,runtime=runtime)==(False,'running',None)
    (runtime/'runs/reader.json').write_text('{broken')
    assert pid_health.detect_stale_running('running',999999,runtime=runtime)==(False,'running',None)
    with pytest.raises(RegistryError):module.decide(project,runtime)
