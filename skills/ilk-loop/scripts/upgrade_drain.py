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
        master_path=selected['master_path'],next_toolkit_sha=sha,next_inputs_dir=str(target),
        consumer_worktrees=roots,consumer_protocol=row['consumer_protocol'],
        drained_iteration=context['iteration'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--log',type=Path,required=True)
    args=parser.parse_args()
    result=boundary(args.context,args.log)
    print(json.dumps(result))


if __name__=='__main__':main()
