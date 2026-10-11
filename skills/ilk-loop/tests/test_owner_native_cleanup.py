"""Actual native owner exit and pre-exec registration; isolated processes only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import pytest
from iteration_clock import monotonic
from iteration_context import write_context
from owned_command import wrap_command
from run_registry import reserve,bind_running,register_child,process_identity,project_status,cleanup_children
from test_owner_worker_sandbox import fixture

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Mac native owner-exit adapter')


def test_exited_unreaped_group_leader_retains_exact_cleanup_identity(tmp_path):
    owner=subprocess.Popen([sys.executable,'-u','-c','import threading;print("ready");threading.Event().wait()'],
        stdout=subprocess.PIPE,text=True,start_new_session=True)
    leader=None
    try:
        assert owner.stdout.readline().strip()=='ready'
        identity=process_identity(owner.pid);registry=tmp_path/'launcher'
        reserve(registry,run_id='exited-leader',project_key='fixture',clone=tmp_path,exec_tree=tmp_path,
            toolkit_sha='fixture',expires_at=time.time()+30)
        bind_running(registry,'exited-leader',pid=owner.pid)
        context=dict(run_registry_dir=str(registry),runner_owner_pid=owner.pid,run_id='exited-leader')
        source='import subprocess,sys;subprocess.Popen([sys.executable,"-c","import threading;threading.Event().wait()"]);print("ready",flush=True)'
        leader=subprocess.Popen(wrap_command([sys.executable,'-u','-c',source],context),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        assert leader.stdout.readline().strip()=='ready'
        # Three seconds is fixture-only startup/exit responsiveness judgment.
        # Do not poll/wait the leader: its unreaped PID still owns the group.
        deadline=monotonic()+3
        while process_identity(leader.pid) is not None:
            assert monotonic()<deadline
            threading.Event().wait(.02)
        assert cleanup_children(registry,'exited-leader',owner=identity)==[]
        leader.communicate(timeout=3)
    finally:
        # Before reaping on failure, this exact Popen PID remains reserved;
        # its group cannot have been reassigned to another process.
        for process in (leader,owner):
            if process is not None and process.returncode is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()


def test_native_owner_exit_kills_registered_worker_and_gate_groups(tmp_path):
    context,temporary,home=fixture(tmp_path)
    scripts=Path(__file__).parents[1]/'scripts';children=[];broker=None
    owner=subprocess.Popen([sys.executable,'-u','-c','import threading;print("ready");threading.Event().wait()'],
        stdout=subprocess.PIPE,text=True,start_new_session=True)
    try:
        assert owner.stdout.readline().strip()=='ready'
        identity=process_identity(owner.pid);registry=Path(context['runtime_dir'])/'launcher'
        reserve(registry,run_id='native-exit',project_key='fixture',clone=Path(context['clone']),
            exec_tree=Path(context['exec_tree']),toolkit_sha='fixture',expires_at=time.time()+60)
        bind_running(registry,'native-exit',pid=owner.pid)
        context.update(schema_version=1,run_id='native-exit',iteration=1,run_registry_dir=str(registry),
            runner_owner_pid=owner.pid,worker_tmp_dir=str(temporary),private_worker_home=str(home))
        frozen=tmp_path/'context.json'
        with tempfile.TemporaryDirectory(prefix='ilk-native-ipc-') as ipc:
            context.update(shell_broker_path=str(Path(ipc)/'shell.sock'),shell_broker_nonce='fixture')
            broker=subprocess.Popen([sys.executable,str(scripts/'worker_broker.py'),'--context',str(frozen),
                '--deadline',str(monotonic()+15),'--owner',json.dumps(identity)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            register_child(registry,context['run_id'],pid=broker.pid,owner_pid=owner.pid)
            context['shell_broker_owner_pid']=broker.pid;write_context(frozen,context)
            # Five seconds is a fixture startup judgment over sampled sub-second
            # socket readiness, not a production stall/activation threshold.
            deadline=monotonic()+5
            while not Path(context['shell_broker_path']).is_socket():
                assert broker.poll() is None and monotonic()<deadline
                threading.Event().wait(.02)
            for worker in (False,True):
                command=[sys.executable,'-u','-c',
                    'import os,threading;assert bool(os.environ.get("ILK_WORKER_SESSION"))=='+repr(worker)+
                    ';print("ready");threading.Event().wait()']
                child=subprocess.Popen(wrap_command(command,context,worker=worker),
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                children.append(child)
                assert child.stdout.readline().strip()=='ready'
            owner.kill();owner.wait()
            # Existing 15s test deadline bounds the broker. Three seconds is
            # the fixture cleanup responsiveness judgment; slower native
            # notification on a valid host falsifies this check's bound.
            _,error=broker.communicate(timeout=3)
            assert broker.returncode==0,error
            for child in children:
                child.communicate(timeout=3)
                assert child.returncode==-signal.SIGKILL
            assert not Path(context['shell_broker_path']).exists()
            assert not project_status(registry)['busy']
    finally:
        for child in [*children,broker,owner]:
            if child is not None and child.poll() is None:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait()
