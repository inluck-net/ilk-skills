#!/usr/bin/env python3
"""Driver-owned shell execution outside the CLI sandbox; no state/proof operations.

The frozen descriptor is the only worker entry. Every command gets a separate
OS write/network boundary and the existing iteration deadline. This candidate
still needs lifecycle-registry integration before production activation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from worker_sandbox import profile


class ShellBroker:
    def __init__(self,context: dict,*,deadline: float):
        self.context=dict(context);self.deadline=deadline
        self._directory=tempfile.TemporaryDirectory(prefix='ilk-ipc-')
        self.path=Path(self._directory.name).resolve()/'shell.sock'
        self.nonce=uuid.uuid4().hex
        self._listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self._listener.bind(str(self.path));self.path.chmod(0o600);self._listener.listen()
        self._closed=threading.Event();self._active=None;self._guard=threading.Lock()
        self.error=None;self.command_started=threading.Event()
        self._thread=threading.Thread(target=self._serve,daemon=True)

    def descriptor(self):
        return {'shell_broker_path':str(self.path),'shell_broker_nonce':self.nonce,
                'worker_sandbox_adapter':'macos-brokered-shell','shell_broker_owner_pid':os.getpid()}

    def start(self):
        self._thread.start();return self

    def _serve(self):
        try:
            while not self._closed.is_set():
                connection,_=self._listener.accept()
                with connection:
                    connection.settimeout(max(self.deadline-time.monotonic(),0))
                    with connection.makefile('rwb') as stream:
                        request=json.loads(stream.readline())
                        if request.get('nonce')!=self.nonce: response={'error':'broker identity mismatch','exit_code':1}
                        elif request.get('op')=='close':break
                        elif request.get('op')!='exec' or not isinstance(request.get('command'),str):
                            response={'error':'unsupported broker operation','exit_code':1}
                        else:response=self._execute(request['command'])
                        stream.write(json.dumps(response).encode()+b'\n');stream.flush()
        except Exception as exc:
            if not self._closed.is_set():self.error=type(exc).__name__
        finally:self._listener.close()

    def _execute(self,command):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:return {'exit_code':124,'error':'iteration deadline reached'}
        context=self.context;attempt=uuid.uuid4().hex
        key=hashlib.sha256(json.dumps([context['run_id'],context['iteration']]).encode()).hexdigest()
        directory=Path(context['runtime_dir'])/'launcher/worker-shell'/key;directory.mkdir(parents=True,exist_ok=True)
        stdout_path=directory/(attempt+'.stdout');stderr_path=directory/(attempt+'.stderr')
        policy=profile(context,temporary=Path(context['worker_tmp_dir']),shell=True)
        env={key:value for key,value in os.environ.items() if not key.startswith('ANTHROPIC_')}
        env.update(ILK_WORKER_SESSION='1',ILK_ITER_RECORD_STAGE='',ILK_DATA_HOME=context['data_root'],
                   ILK_DATA_DIR=context['data_root'],TMPDIR=context['worker_tmp_dir'])
        if context.get('private_worker_home'):env['HOME']=context['private_worker_home']
        env.update(GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_NOSYSTEM='1')
        started=time.monotonic();timed_out=False
        with stdout_path.open('wb') as stdout,stderr_path.open('wb') as stderr:
            stdout_path.chmod(0o600);stderr_path.chmod(0o600)
            process=subprocess.Popen(['/usr/bin/sandbox-exec','-p',policy,'/bin/bash','-c',command],
                cwd=context['exec_tree'],env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            with self._guard:self._active=process
            self.command_started.set()
            try:code=process.wait(timeout=max(self.deadline-time.monotonic(),0))
            except subprocess.TimeoutExpired:
                timed_out=True
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait();code=124
            finally:
                with self._guard:self._active=None
        result={'exit_code':code,'stdout':stdout_path.read_text(errors='replace'),
                'stderr':stderr_path.read_text(errors='replace'),'stdout_path':str(stdout_path),'stderr_path':str(stderr_path)}
        record={key:value for key,value in result.items() if key not in {'stdout','stderr'}}
        record.update(attempt_id=attempt,run_id=context['run_id'],iteration=context['iteration'],
            command_sha256=hashlib.sha256(command.encode()).hexdigest(),started_monotonic=started,
            ended_monotonic=time.monotonic(),diagnostic_only=True,iteration_deadline_reached=timed_out)
        with (directory/'attempts.jsonl').open('a') as stream:stream.write(json.dumps(record)+'\n')
        return result

    def close(self):
        self._closed.set()
        with self._guard:
            if self._active is not None and self._active.poll() is None:
                try:os.killpg(self._active.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                connection.connect(str(self.path));connection.sendall(json.dumps({'op':'close','nonce':self.nonce}).encode()+b'\n')
        except OSError:pass
        if self._thread.is_alive():self._thread.join()
        self._listener.close()
        self._directory.cleanup()

    def __enter__(self):return self.start()
    def __exit__(self,*_):self.close()
