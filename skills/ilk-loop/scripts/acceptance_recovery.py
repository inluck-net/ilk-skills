#!/usr/bin/env python3
"""Close interrupted accepted transactions before selecting another worker.

Only the clean single-repo non-selfmod adapter is available here. No provider
is called and no gate is run/reused for a different transaction. Unexplained
state or unsupported landing remains an explicit refusal, never inferred proof.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
import sys
from pathlib import Path
from iteration_clock import monotonic
from iteration_context import read_context,read_dispatch,capture_execution,ContextError
from driver_step import recover_acceptance,project_result,git,working_tree
from plan_status import parse_frontmatter
from plan_state import audit_context,_sync_parent
from ship_proof_ledger import append_record,read_raw_records
from iteration_record import project_close_record,replay_closed_records


@contextmanager
def original_context(path):
    keys=('ILK_ITER_CONTEXT','ILK_ITER_RECORD_STAGE','ILK_WORKER_SESSION','ILK_ITERATION_SUBPLAN')
    saved={key:os.environ.get(key) for key in keys}
    os.environ.update(ILK_ITER_CONTEXT=str(path),ILK_ITER_RECORD_STAGE='driver-check')
    os.environ.pop('ILK_WORKER_SESSION',None);os.environ.pop('ILK_ITERATION_SUBPLAN',None)
    try:yield
    finally:
        for key,value in saved.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def recover_pending(*,runtime,project,log,deadline,owner_context=None):
    from run_registry import as_driver
    with as_driver(owner_context):
        return _recover_pending(runtime=runtime,project=project,log=log,deadline=deadline)


def _recover_pending(*,runtime,project,log,deadline):
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise ContextError('worker cannot recover pending acceptance')
    runtime=Path(runtime).resolve();project=Path(project).resolve();log=Path(log)
    ledger=runtime/'launcher/ship-proof.jsonl'
    replay_closed_records(log,ledger)
    closes={}
    if log.exists():
        for line in log.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if isinstance(row,dict) and row.get('record_phase')=='close':
                closes[(row.get('run_id'),row.get('iteration'))]=row
    pending=[]
    for receipt in sorted((runtime/'launcher/driver-step').glob('acceptance-*.json')):
        row=json.loads(receipt.read_text());context=row.get('context')
        if not isinstance(context,dict):raise ContextError('pending acceptance lacks original context adapter')
        if Path(context['runtime_dir']).resolve()!=runtime or Path(context['clone']).resolve()!=project:
            raise ContextError('pending acceptance belongs to another project')
        key=(context['run_id'],context['iteration'])
        if key in closes:
            close=closes[key]
            if close.get('stop_reason') or close.get('driver_integrity_passed') is not True:
                raise ContextError('prior accepted transaction closed unsuccessfully; recovery needs attribution')
            continue
        if context.get('mode')!='none' or Path(context['exec_tree']).resolve()!=project:
            raise ContextError('pending acceptance landing adapter unavailable')
        path=Path(row.get('context_path') or '')
        if not path.is_file() or read_context(path)!=context:
            raise ContextError('pending acceptance original frozen context unavailable')
        pending.append((context,path))
    if len(pending)>1:raise ContextError('multiple pending accepted transactions; refusing ambiguous recovery')
    completed=[]
    for context,path in pending:
        with original_context(path):
            accepted=recover_acceptance(context,deadline=deadline)
            if not accepted:raise ContextError('pending acceptance disappeared')
            accepted.update(run_id=context['run_id'],iteration=context['iteration'],worker_turns=[])
            projected=project_result(accepted,context)
            selected=read_dispatch(context);plan=Path(selected['subplan_path']);root=Path(context['exec_tree'])
            state=parse_frontmatter(plan.read_text())
            # Final authored step may ship; intermediate steps close only.
            from ship_audit import count_authored_steps
            steps=count_authored_steps(plan.read_text())
            if steps and selected['step']==max(steps):
                from ship_transition import ship
                ship(Path(context['plans_dir']),root,selected['slug'])
            if monotonic()>=deadline:raise ContextError('acceptance close recovery reached supplied deadline')
            if working_tree(root)!=accepted['tree'] or git(root,'rev-parse','HEAD^{tree}')!=accepted['tree']:
                raise ContextError('acceptance tree changed during recovered ship')
            from ship_integrity import _missing_step_reason
            if _missing_step_reason(plan,repo=root) is not None:
                raise ContextError('recovered acceptance lacks step-commit integrity')
            audit=audit_context(context)
            if not audit['explained']:raise ContextError('recovered acceptance state is unexplained')
            proof=dict(run_id=context['run_id'],iteration=context['iteration'],slug=selected['slug'],
                repo=str(root),step_from=selected['step'],step_to=selected['step']+1,
                commits=accepted['commits'],provenance='loop-executed',gate_outcome='pass')
            if not accepted['commits']:
                proof.update(proof='gate_pass_at_head',head=accepted['head_commit'])
            existing=read_raw_records(ledger)
            if not any(r.get('record_phase')=='provisional' and r.get('run_id')==context['run_id']
                       and r.get('iteration')==context['iteration'] and r.get('slug')==selected['slug'] for r in existing):
                append_record(ledger,proof)
            close=dict(record_phase='close',run_id=context['run_id'],iteration=context['iteration'],
                context=context,execution=capture_execution(context),driver_acceptance=accepted,
                driver_integrity_passed=True,state_audit=audit,merge_outcome='not-applicable',
                stop_reason=None,local_checks=projected,acceptance_recovered_by_driver=True,
                recovery_scope='finish original accepted transaction; no worker dispatch or new gate')
            from run_registry import require_driver
            require_driver(context)
            log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a') as stream:
                stream.write(json.dumps(close,separators=(',',':'))+'\n');stream.flush();os.fsync(stream.fileno())
            _sync_parent(log)
            project_close_record(close)
            completed.append(dict(run_id=context['run_id'],iteration=context['iteration'],slug=selected['slug']))
    return completed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--timeout',type=int,required=True)
    parser.add_argument('--registry-run-id',required=True)
    parser.add_argument('--owner-pid',type=int,required=True)
    args=parser.parse_args()
    if args.timeout<=0:parser.error('existing caller timeout must be positive')
    try:
        from run_registry import register_child
        # gtimeout owns the new group; register its exact identity before any
        # recovery write, rather than racing the parent's post-spawn probe.
        register_child(args.runtime/'launcher',args.registry_run_id,
                       pid=os.getpgrp(),owner_pid=args.owner_pid)
        result=recover_pending(runtime=args.runtime,project=args.project,log=args.log,deadline=monotonic()+args.timeout,
            owner_context=dict(run_registry_dir=str(args.runtime/'launcher'),run_id=args.registry_run_id,
                               runner_owner_pid=args.owner_pid))
    except Exception as exc:
        print('Pending acceptance recovery refused: '+type(exc).__name__+': '+str(exc),file=sys.stderr)
        return 1
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
