#!/usr/bin/env python3
"""Owner-requested upgrade boundary after one positively closed transaction.

This does not install, launch, renew a budget, or mark pending work shipped.
Consumer readiness is an explicit owner attestation in the protected request;
it must follow installation of the coordinated consumer readers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from iteration_context import read_context, read_dispatch, ContextError
from run_inputs import verify_launch_resources
from run_registry import require_driver, code_sha


def boundary(context_path, log_path):
    context=read_context(context_path)
    if not context.get('run_registry_dir') or not context.get('runner_owner_pid'):
        raise ContextError('upgrade boundary requires exact registered runner authority')
    require_driver(context)
    request=Path(context['run_registry_dir'])/'upgrade-drain-request.json'
    if not request.exists():
        return None
    raw=request.read_bytes();row=json.loads(raw)
    if not isinstance(row,dict) or row.get('schema_version')!=1:
        raise ContextError('unsupported upgrade drain request')
    selected=read_dispatch(context)
    snapshot=context.get('master_snapshot')
    if not snapshot or snapshot.get('path')!=selected.get('master_path'):
        raise ContextError('upgrade boundary requires frozen exact master identity')
    expected=dict(run_id=context['run_id'],from_toolkit_sha=context['toolkit_sha'],
                  master_path=selected.get('master_path'))
    if any(row.get(key)!=value for key,value in expected.items()):
        raise ContextError('upgrade request contradicts frozen run/master')
    if row.get('consumer_protocol')!='upgrade-drain-v1-owner-confirmed':
        raise ContextError('coordinated consumer readiness is not attested')
    if context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        raise ContextError('upgrade boundary requires enforced code-only dispatch')
    target=Path(row['next_inputs_dir']).resolve(strict=True)
    manifest=verify_launch_resources(target)
    if manifest.get('upgrade_drain_protocol')!=1:
        raise ContextError('prepared target lacks upgrade activation protocol')
    sha=manifest['toolkit_sha']
    if sha==context['toolkit_sha'] or row.get('next_toolkit_sha')!=sha or code_sha(Path(manifest['code_root']))!=sha:
        raise ContextError('upgrade target code identity is missing or contradictory')
    if manifest.get('context_bindings'):
        raise ContextError('upgrade target must have unused private worker inputs')
    closes=[]
    for line in Path(log_path).read_text().splitlines():
        record=json.loads(line)
        if (record.get('record_phase')=='close' and record.get('run_id')==context['run_id']
                and record.get('iteration')==context['iteration']):
            closes.append(record)
    if len(closes)!=1:
        raise ContextError('upgrade boundary requires exactly one durable close')
    close=closes[0]
    if (close.get('context')!=context or close.get('stop_reason')
            or close.get('driver_acceptance',{}).get('accepted') is not True
            or close.get('driver_integrity_passed') is not True
            or close.get('state_audit',{}).get('explained') is not True):
        raise ContextError('upgrade boundary requires positive accepted integrity/state close')
    cleanup=[item for item in close.get('driver_phase_observation',{}).get('intervals',[])
             if item.get('name')=='owned_group_cleanup']
    if len(cleanup)!=1 or cleanup[0].get('observation_complete') is not True:
        raise ContextError('upgrade boundary lacks completed owned-child cleanup')
    if context['mode']=='selfmod' and close.get('merge_outcome')!='merged':
        raise ContextError('pending selfmod landing is not a completed drain')
    if context['mode'] not in {'none','selfmod'}:
        raise ContextError('declared-worktree upgrade landing adapter unavailable')
    from plan_state import audit_context
    if audit_context(context).get('explained') is not True:
        raise ContextError('state changed since accepted close')
    from plan_status import parse_frontmatter
    state=parse_frontmatter(Path(selected['master_path']).read_text())
    if state.get('status') in {'draft','blocked','parked','shipped'} or state.get('hold')=='human':
        raise ContextError('held or completed master cannot activate an upgrade')
    require_driver(context)
    if request.read_bytes()!=raw:
        raise ContextError('upgrade request changed during boundary validation')
    roots=sorted(set(context.get('observe_tree',{}).values())|{context['exec_tree']})
    return dict(schema_version=1,request_sha256=hashlib.sha256(raw).hexdigest(),
        drained_context_path=str(Path(context_path).resolve()),
        drained_context_sha256=hashlib.sha256(Path(context_path).read_bytes()).hexdigest(),
        master_path=selected['master_path'],next_toolkit_sha=sha,next_inputs_dir=str(target),
        consumer_worktrees=roots,consumer_protocol=row['consumer_protocol'],
        launch_contract=context.get('launch_contract'),
        drained_iteration=context['iteration'])


def activation(runtime,project):
    """Read-only launcher selection; reserve/lock acquisition still arbitrate."""
    from run_registry import _owner,project_status,process_identity
    _owner();runtime=Path(runtime).resolve();project=Path(project).resolve()
    sentinel=runtime/'last-exit.json'
    if not sentinel.exists():return None
    raw=sentinel.read_bytes();record=json.loads(raw)
    if record.get('state')=='upgrade_request_invalid':
        raise ContextError('invalid upgrade request requires owner recovery; old pin must not restart')
    if record.get('state')!='upgrade-drained':return None
    if project_status(runtime,project=project)['busy'] or process_identity(record['pid']) is not None:
        raise ContextError('old upgrade runner/children/lock have not exited')
    request=runtime/'upgrade-drain-request.json';request_raw=request.read_bytes()
    row=json.loads(request_raw)
    if (hashlib.sha256(request_raw).hexdigest()!=record['request_sha256']
            or row.get('run_id')!=record['run_id'] or row.get('from_toolkit_sha')!=record['toolkit_sha']
            or row.get('consumer_protocol')!='upgrade-drain-v1-owner-confirmed'):
        raise ContextError('upgrade activation request changed or has no consumer agreement')
    target=Path(record['next_inputs_dir']);manifest=verify_launch_resources(target)
    if manifest.get('upgrade_drain_protocol')!=1:
        raise ContextError('prepared target lacks upgrade activation protocol')
    if (manifest['toolkit_sha']!=record['next_toolkit_sha'] or manifest.get('context_bindings')
            or code_sha(Path(manifest['code_root']))!=record['next_toolkit_sha']):
        raise ContextError('prepared upgrade pin changed or was already used')
    master=Path(record['master_path']).resolve(strict=True)
    from plan_status import project_held_by,parse_frontmatter
    from ilk_paths import find_plans_dir
    plans,_=find_plans_dir(project)
    if not plans or master.parent!=Path(plans).resolve():
        raise ContextError('upgrade master belongs to another project')
    state=parse_frontmatter(master.read_text())
    if project_held_by(master.parent) or state.get('status') not in {'active','queued','pending'}:
        raise ContextError('upgrade master is held, completed or no longer runnable')
    launch=record.get('launch_contract')
    if (not isinstance(launch,dict) or launch.get('run_local_checks') is not True
            or any(isinstance(launch.get(key),bool) or not isinstance(launch.get(key),int)
                   or launch[key]<=0 for key in ('max_iterations','iteration_timeout_min'))):
        raise ContextError('frozen launch limits unavailable; refuse inferred upgrade limits')
    import math
    budget=launch.get('max_budget_usd')
    if isinstance(budget,bool) or not isinstance(budget,(int,float)) or not math.isfinite(budget) or budget<0:
        raise ContextError('frozen upgrade budget missing or invalid')
    old_context=Path(record['drained_context_path'])
    if hashlib.sha256(old_context.read_bytes()).hexdigest()!=record['drained_context_sha256']:
        raise ContextError('drained frozen context changed')
    frozen=read_context(old_context)
    from iteration_context import plan_snapshot
    for snapshot in list(frozen['plans'].values())+[frozen['master_snapshot']]:
        if plan_snapshot(Path(snapshot['path']))['fingerprint']!=snapshot['fingerprint']:
            raise ContextError('upgrade plan contract changed; prepare a new owner request')
    if sentinel.read_bytes()!=raw or request.read_bytes()!=request_raw:
        raise ContextError('upgrade state changed during activation selection')
    return dict(record,code_root=manifest['code_root'],worker_home=manifest['worker_home'],
        claude_path=next(row['path'] for row in manifest['executables'] if row['name']=='claude'))


def consume(runtime,run_id,owner_pid,from_run,toolkit_sha):
    """New registered owner archives the exact old request after binding."""
    import os
    from plan_state import _sync_parent
    from run_registry import _path
    _path(runtime,from_run) # validate identity before using it as a filename
    authority=dict(run_registry_dir=str(runtime),run_id=run_id,runner_owner_pid=owner_pid)
    require_driver(authority)
    request=Path(runtime)/'upgrade-drain-request.json';row=json.loads(request.read_text())
    if row.get('run_id')!=from_run or row.get('next_toolkit_sha')!=toolkit_sha:
        raise ContextError('new owner cannot consume another upgrade request')
    archived=Path(runtime)/('upgrade-drain-consumed-'+from_run+'.json')
    if archived.exists():raise ContextError('upgrade request already consumed')
    require_driver(authority);os.replace(request,archived);_sync_parent(archived)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--log',type=Path,required=True)
    args=parser.parse_args()
    result=boundary(args.context,args.log)
    print(json.dumps(result))


if __name__=='__main__':main()
