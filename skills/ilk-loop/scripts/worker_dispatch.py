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
from run_inputs import bind_context,verify_launch_resources,pin_context_policy,launch_fingerprint
from run_registry import process_identity,register_child,liveness


def prepare(context,context_path,*,inputs,deadline,registry,owner_pid):
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise ContextError('worker cannot prepare dispatch')
    if sys.platform!='darwin':raise ContextError('code-only host adapter unavailable')
    if deadline<=monotonic():raise ContextError('iteration deadline reached before preparation')
    if not context.get('dispatch'):raise ContextError('exact dispatch required')
    context_path=Path(context_path).resolve();inputs=Path(inputs).resolve();registry=Path(registry).resolve()
    if context_path.exists():raise ContextError('context already frozen')
    manifest=verify_launch_resources(inputs)
    source_project=manifest.get('source_project')
    expected_projects={context['exec_tree']}
    if context['mode']=='selfmod':expected_projects.add(context['clone'])
    if source_project is not None and source_project not in expected_projects:
        raise ContextError('prepared project settings/dependencies differ from execution role')
    from run_inputs import verify_code_snapshot
    verify_code_snapshot(inputs,deadline=deadline)
    if Path(context['code_root']).resolve()!=Path(manifest['code_root']).resolve():
        raise ContextError('dispatch differs from pinned toolkit')
    if Path(__file__).resolve()!=Path(context['code_root'])/'skills/ilk-loop/scripts/worker_dispatch.py':
        raise ContextError('dispatch helper is outside pinned toolkit')
    owner=process_identity(owner_pid)
    row=json.loads((registry/'runs'/f"{context['run_id']}.json").read_text())
    if not owner or owner!={key:row[key] for key in ('pid','pgid','lstart')} or liveness(row)!='running':
        raise ContextError('verified runner ownership required')
    if deadline<=monotonic():raise ContextError('iteration deadline reached before preparation')
    from driver_step import validate,gate_contract,admit_retained_red_entry
    context=dict(context,worker_tools=manifest.get('worker_tools','shell'),
                 measured_red_progress_protocol=manifest.get('measured_red_progress_protocol'),
                 private_worker_home=manifest['worker_home'],worker_inputs_dir=str(inputs),
                 toolkit_code_inventory_sha256=manifest.get('code_inventory_sha256'),
                 launch_inputs_sha256=launch_fingerprint(manifest),
                 worker_sandbox_adapter='macos-brokered-shell',run_registry_dir=str(registry),
                 runner_owner_pid=owner_pid,iteration_deadline_monotonic=deadline)
    from instruction_inputs import inventory
    context['instruction_input_inventory_protocol']=2
    context['instruction_input_inventory']=inventory(context)
    if context.get('entry_dirty') is not False:
        import driver_step
        budget=driver_step._deadline.set(deadline);operation=driver_step._operation_context.set(context)
        try:context['retained_red_entry']=admit_retained_red_entry(context)
        finally:
            driver_step._operation_context.reset(operation);driver_step._deadline.reset(budget)
    selected,plan,root,_=validate(context)
    gate_contract(root,plan,selected)
    # /tmp keeps the AF_UNIX pathname below macOS's fixed length limit.
    # Separate IPC and worker scratch prevent the shell from unlinking IPC.
    ipc=Path(tempfile.mkdtemp(prefix='ilk-ipc-')).resolve()
    scratch=Path(tempfile.mkdtemp(prefix='ilk-worker-')).resolve()
    context=dict(context,worker_tools=manifest.get('worker_tools','shell'),
                 measured_red_progress_protocol=manifest.get('measured_red_progress_protocol'),
                 private_worker_home=manifest['worker_home'],worker_inputs_dir=str(inputs),
                 toolkit_code_inventory_sha256=manifest.get('code_inventory_sha256'),
                 worker_tmp_dir=str(scratch),worker_shell_python=sys.executable,
                 worker_sandbox_adapter='macos-brokered-shell',shell_broker_path=str(ipc/'shell.sock'),
                 shell_broker_nonce=uuid.uuid4().hex,run_registry_dir=str(registry),runner_owner_pid=owner_pid,
                 iteration_deadline_monotonic=deadline,scratch_disposable=True)
    from instruction_inputs import inventory
    context['global_instruction_inventory']=manifest.get('global_instruction_inputs',[])
    context['global_instruction_inventory_protocol']=manifest.get('global_instruction_inventory_protocol')
    context['instruction_input_inventory']=inventory(context)
    from instruction_inputs import bootstrap_inventory
    context['root_instruction_inventory']=bootstrap_inventory(context)
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
        pin_context_policy(inputs,context_path,policy_path)
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
    """Close exact broker/groups within the original iteration deadline."""
    context=read_context(Path(context_path));identity=context.get('shell_broker_identity')
    if not identity:raise ContextError('owned broker identity absent')
    from run_registry import require_driver,scan
    require_driver(context)
    observed=process_identity(identity['pid'])
    if observed is not None:
        if observed!=identity:raise ContextError('broker identity changed; refusing signal')
        import select
        queue=select.kqueue()
        try:
            try:
                queue.control([select.kevent(identity['pid'],filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD|select.KQ_EV_ONESHOT,fflags=select.KQ_NOTE_EXIT)],0,0)
            except ProcessLookupError:pass
            else:
                observed=process_identity(identity['pid'])
                if observed is not None:
                    if observed!=identity:raise ContextError('broker identity changed before close')
                    os.kill(identity['pid'],signal.SIGTERM)
                    if not queue.control(None,1,max(context['iteration_deadline_monotonic']-monotonic(),0)):
                        raise ContextError('broker cleanup reached original iteration deadline')
        finally:queue.close()
    # Exit notification precedes init reaping on this host. Bounded 20ms
    # readiness retries are a responsiveness judgment, not a new timeout;
    # sustained scan CPU or slow valid cleanup falsifies this interval.
    while True:
        rows=[row for row in scan(Path(context['run_registry_dir'])) if row['run_id']==context['run_id']]
        if len(rows)!=1:raise ContextError('broker cleanup registry identity unavailable')
        children=rows[0]['children_liveness']
        if any(child['liveness']=='reused-or-moved' for child in children):
            raise ContextError('owned child identity changed during cleanup')
        if all(child['liveness']=='dead' for child in children):
            from run_registry import retire_child
            for child in children:
                if not retire_child(context,child['pid']):
                    raise ContextError('owned child retirement remains unverified')
            # The caller's close receipt can now attest an empty live registry.
            return
        if monotonic()>=context['iteration_deadline_monotonic']:
            raise ContextError('owned group cleanup incomplete at original deadline')
        threading.Event().wait(.02)


def binding(context_path,*,with_manifest=False):
    context_path=Path(context_path);context=read_context(context_path);inputs=Path(context['worker_inputs_dir'])
    manifest=verify_launch_resources(inputs,expected=context.get('launch_inputs_sha256'));fingerprint=hashlib.sha256(context_path.read_bytes()).hexdigest()
    bindings=[row for row in manifest.get('context_bindings',[]) if row['context_sha256']==fingerprint]
    if len(bindings)!=1:raise ContextError('exact settings binding unavailable')
    row=bindings[0];settings=Path(row['settings_path'])
    if hashlib.sha256(settings.read_bytes()).hexdigest()!=row['settings_sha256']:
        raise ContextError('bound settings changed')
    expected=context_path.parent/(context_path.stem+'-sandbox.sb')
    if row.get('sandbox_path')!=str(expected):raise ContextError('bound sandbox policy missing or contradictory')
    result=dict(settings_path=str(settings),worker_tmp_dir=context['worker_tmp_dir'],sandbox_path=str(expected))
    return (result,manifest) if with_manifest else result


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['close','binding'])
    parser.add_argument('--context',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='close':close(args.context)
    else:print(json.dumps(binding(args.context)))


if __name__=='__main__':main()
