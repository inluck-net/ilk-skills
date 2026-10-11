"""Explicit owned Mac broker/CLI subprocess fixtures, never live repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from worker_broker import ShellBroker
from worker_sandbox import profile
from worker_shell import canonical_command
from iteration_context import write_context
import test_owner_worker_sandbox as fixtures
pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='explicit Mac broker integration')
ROOT=Path(__file__).resolve().parents[3]


def test_parent_sandbox_uses_owned_broker_without_nested_apply(tmp_path):
    ctx,temporary,home=fixtures.fixture(tmp_path)
    ctx.update(schema_version=1,run_id='test',iteration=1,code_root=str(ROOT),
               worker_tmp_dir=str(temporary),worker_shell_python=sys.executable,private_worker_home=str(home))
    with ShellBroker(ctx,deadline=time.monotonic()+10) as broker:
        frozen=dict(ctx,**broker.descriptor());path=tmp_path/'ctx.json';write_context(path,frozen)
        parent=profile(frozen,temporary=temporary,private_home=home)
        command=canonical_command(frozen,path,'printf fixture > accepted.py')
        cp=fixtures.run(parent,'/bin/bash','-c',command)
        assert cp.returncode==0,cp.stderr
        assert (Path(ctx['exec_tree'])/'accepted.py').read_text()=='fixture'
        target=Path(ctx['plans_dir'])/'denied.md'
        cp=fixtures.run(parent,'/bin/bash','-c',canonical_command(frozen,path,f'printf bad > {target}'))
        assert cp.returncode!=0 and not target.exists()
        cp=fixtures.run(parent,'/bin/bash','-c',canonical_command(frozen,path,'/usr/bin/python3 -c \'import socket;socket.create_connection(("127.0.0.1",9))\''))
        assert cp.returncode!=0 and 'PermissionError' in cp.stderr
        assert broker.error is None
    assert not Path(frozen['shell_broker_path']).exists()
    records=list(Path(ctx['runtime_dir']).rglob('attempts.jsonl'))
    assert len(records)==1
    rows=[json.loads(line) for line in records[0].read_text().splitlines()]
    assert len(rows)==3 and all(row['diagnostic_only'] for row in rows)
    assert all(Path(row['stdout_path']).exists() for row in rows)


def test_broker_cleanup_owns_and_ends_its_active_child(tmp_path):
    import socket,threading
    ctx,temporary,home=fixtures.fixture(tmp_path)
    ctx.update(run_id='cleanup',iteration=1,worker_tmp_dir=str(temporary),private_worker_home=str(home))
    broker=ShellBroker(ctx,deadline=time.monotonic()+10).start();result=[]
    def request():
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.connect(str(broker.path));connection.sendall(json.dumps({'op':'exec','nonce':broker.nonce,
                'command':"/usr/bin/python3 -c 'import threading;threading.Event().wait()'"}).encode()+b'\n')
            with connection.makefile('rb') as stream:result.append(json.loads(stream.readline()))
    client=threading.Thread(target=request);client.start()
    assert broker.command_started.wait(10)
    with broker._guard:owned=broker._active
    assert owned is not None
    broker.close();client.join()
    assert owned.poll() is not None
    assert result[0]['exit_code']!=0
    assert not broker.path.exists()


def test_shell_cannot_signal_another_owned_fixture_process(tmp_path):
    ctx,temporary,_=fixtures.fixture(tmp_path)
    process=subprocess.Popen(['/usr/bin/python3','-c','import threading;threading.Event().wait()'])
    try:
        cp=fixtures.run(profile(ctx,temporary=temporary,shell=True),'/usr/bin/python3','-c',f'import os,signal;os.kill({process.pid},signal.SIGTERM)')
        assert cp.returncode!=0 and 'PermissionError' in cp.stderr
        assert process.poll() is None
    finally:process.terminate();process.wait()


def test_idle_owned_client_does_not_hold_broker_cleanup(tmp_path):
    import socket,threading
    ctx,temporary,_=fixtures.fixture(tmp_path)
    ctx.update(run_id='idle-client',iteration=1,worker_tmp_dir=str(temporary))
    broker=ShellBroker(ctx,deadline=time.monotonic()+10).start()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.connect(str(broker.path))
        # Synchronize acceptance without sleeping or touching foreign processes.
        accepted=threading.Event()
        def observe():
            while broker._connection is None and broker._thread.is_alive():
                accepted.wait(0.001)
            accepted.set()
        watcher=threading.Thread(target=observe);watcher.start()
        assert accepted.wait(10)
        broker.close();watcher.join()
    assert not broker._thread.is_alive()


def test_shell_completion_kills_its_background_descendants(tmp_path):
    ctx,temporary,_=fixtures.fixture(tmp_path)
    ctx.update(run_id='background',iteration=1,worker_tmp_dir=str(temporary))
    marker=Path(ctx['exec_tree'])/'late-write'
    import shlex
    child="import time;from pathlib import Path;time.sleep(0.2);Path('late-write').write_text('bad')"
    with ShellBroker(ctx,deadline=time.monotonic()+10) as broker:
        result=broker._execute('/usr/bin/python3 -c '+shlex.quote(child)+' &')
        assert result['exit_code']==0
    # 0.4 seconds is a test judgment, twice the intentional fixture delay;
    # a valid host that cannot schedule within that interval falsifies it.
    import threading
    threading.Event().wait(0.4)
    assert not marker.exists()
