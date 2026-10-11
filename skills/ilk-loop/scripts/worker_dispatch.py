#!/usr/bin/env python3
"""Prepare an owned code-only dispatch from the already resolved context.

No target/root discovery, state mutation, or worker API call occurs here. The
runner supplies its existing deadline and registry; missing ownership fails.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from iteration_clock import monotonic
import uuid
from iteration_context import ContextError,read_context,write_context
from run_inputs import bind_context,verify_launch_resources
from run_registry import process_identity,register_child,liveness


def prepare(context,context_path,*,inputs,deadline,registry,owner_pid):
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise ContextError('worker cannot prepare dispatch')
    if sys.platform!='darwin':raise ContextError('code-only host adapter unavailable')
    if context.get('entry_dirty') is not False:raise ContextError('clean frozen entry required')
    if not context.get('dispatch'):raise ContextError('exact dispatch required')
    context_path=Path(context_path).resolve();inputs=Path(inputs).resolve();registry=Path(registry).resolve()
    if context_path.exists():raise ContextError('context already frozen')
    manifest=verify_launch_resources(inputs)
    if Path(context['code_root']).resolve()!=Path(manifest['code_root']).resolve():
        raise ContextError('dispatch differs from pinned toolkit')
    if Path(__file__).resolve()!=Path(context['code_root'])/'skills/ilk-loop/scripts/worker_dispatch.py':
        raise ContextError('dispatch helper is outside pinned toolkit')
    owner=process_identity(owner_pid)
    row=json.loads((registry/'runs'/f"{context['run_id']}.json").read_text())
    if not owner or owner!={key:row[key] for key in ('pid','pgid','lstart')} or liveness(row)!='running':
        raise ContextError('verified runner ownership required')
    if deadline<=monotonic():raise ContextError('iteration deadline reached before preparation')
    from driver_step import validate,gate_contract
    selected,plan,root,_=validate(dict(context,worker_sandbox_adapter='macos-brokered-shell'))
    gate_contract(root,plan,selected)
    # /tmp keeps the AF_UNIX pathname below macOS's fixed length limit.
    # Separate IPC and worker scratch prevent the shell from unlinking IPC.
    ipc=Path(tempfile.mkdtemp(prefix='ilk-ipc-')).resolve()
    scratch=Path(tempfile.mkdtemp(prefix='ilk-worker-')).resolve()
    context=dict(context,private_worker_home=manifest['worker_home'],worker_inputs_dir=str(inputs),
                 worker_tmp_dir=str(scratch),worker_shell_python=sys.executable,
                 worker_sandbox_adapter='macos-brokered-shell',shell_broker_path=str(ipc/'shell.sock'),
                 shell_broker_nonce=uuid.uuid4().hex,run_registry_dir=str(registry),runner_owner_pid=owner_pid,
                 iteration_deadline_monotonic=deadline,scratch_disposable=True)
    log=context_path.parent/(context_path.stem+'-broker.log');log.parent.mkdir(parents=True,exist_ok=True)
    process=None
    try:
        with log.open('xb') as output:
            log.chmod(0o600)
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('worker_broker.py')),
                '--context',str(context_path),'--deadline',str(deadline),'--owner',json.dumps(owner)],
                stdout=output,stderr=output,start_new_session=True)
        context['shell_broker_owner_pid']=process.pid
        child=register_child(registry,context['run_id'],pid=process.pid,owner_pid=owner_pid)
        context['shell_broker_identity']=child
        write_context(context_path,context)
        waiter=threading.Event()
        while not Path(context['shell_broker_path']).is_socket():
            if process.poll() is not None:raise ContextError('broker startup failed; see owned broker log')
            if monotonic()>=deadline:raise ContextError('broker startup reached iteration deadline')
            if process_identity(owner_pid)!=owner:raise ContextError('runner ownership changed')
            waiter.wait(.02)
        settings=bind_context(inputs,context_path)
        from worker_sandbox import profile
        diagnostic=context_path.parent/f"write-scope-{context['iteration']}.jsonl"
        # The hook runs inside this CLI boundary. Permit only its diagnostic
        # append path, never the surrounding frozen context/proof directory.
        policy=profile(context,temporary=scratch,private_home=Path(manifest['worker_home']),
                       diagnostic_log=diagnostic)
        policy_path=context_path.parent/(context_path.stem+'-sandbox.sb')
        with policy_path.open('x') as stream:
            stream.write(policy);stream.flush();os.fsync(stream.fileno())
        policy_path.chmod(0o400)
        return dict(settings_path=str(settings),sandbox_path=str(policy_path),
                    worker_tmp_dir=str(scratch),broker_pid=process.pid,
                    context_sha256=hashlib.sha256(context_path.read_bytes()).hexdigest())
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=max(deadline-monotonic(),.02))
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
        import shutil
        shutil.rmtree(ipc,ignore_errors=True);shutil.rmtree(scratch,ignore_errors=True)
        raise


def close(context_path):
    """Signal only the freshly verified broker created for this context."""
    context=read_context(Path(context_path));identity=context.get('shell_broker_identity')
    if not identity:raise ContextError('owned broker identity absent')
    observed=process_identity(identity['pid'])
    if observed is None:return
    if observed!=identity:raise ContextError('broker identity changed; refusing signal')
    os.kill(identity['pid'],signal.SIGTERM)


def binding(context_path):
    context_path=Path(context_path);context=read_context(context_path);inputs=Path(context['worker_inputs_dir'])
    manifest=verify_launch_resources(inputs);fingerprint=hashlib.sha256(context_path.read_bytes()).hexdigest()
    bindings=[row for row in manifest.get('context_bindings',[]) if row['context_sha256']==fingerprint]
    if len(bindings)!=1:raise ContextError('exact settings binding unavailable')
    row=bindings[0];settings=Path(row['settings_path'])
    safe={key:value for key,value in json.loads(settings.read_text()).items() if key!='env'}
    if hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest()!=row['settings_non_env_sha256']:
        raise ContextError('bound settings changed')
    return dict(settings_path=str(settings),worker_tmp_dir=context['worker_tmp_dir'],
                sandbox_path=str(context_path.parent/(context_path.stem+'-sandbox.sb')))


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['close','binding'])
    parser.add_argument('--context',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='close':close(args.context)
    else:print(json.dumps(binding(args.context)))


if __name__=='__main__':main()
