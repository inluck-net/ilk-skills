"""Candidate exact close projection: proven progress, explicitly unproven gate.

No runtime/consumer caller enabled. An open row or pending landing is never a
completed boundary, and old green proof readers must keep rejecting this row.
"""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from iteration_context import ContextError,read_dispatch
from plan_state import _lock,_sync_parent
from run_registry import require_driver,project_status
from ship_proof_ledger import append_record,read_raw_records
from progress_projection import project
import progress_transaction as transaction


def finalize(context,log,*,deadline):
    with transaction._authority(context,deadline):
        log=Path(log).resolve(strict=True)
        expected=(Path(context['runtime_dir']).parent/'logs/.ilk-loop.log').resolve()
        if log!=expected:raise ContextError('progress close outside exact summary log')
        closes=[]
        for line in log.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if (isinstance(row,dict) and row.get('record_phase')=='close'
                    and row.get('run_id')==context['run_id'] and row.get('iteration')==context['iteration']):
                closes.append(row)
        if len(closes)!=1:raise ContextError('progress finalization needs one durable exact close')
        close=closes[0]
        if (close.get('context')!=context or close.get('stop_reason')
                or close.get('driver_progress_integrity_passed') is not True
                or close.get('driver_integrity_passed') is not False
                or close.get('state_audit',{}).get('explained') is not True):
            raise ContextError('progress close lacks exact non-green integrity/state observation')
        result=close.get('driver_acceptance') or {}
        projected=project(context,result,deadline=deadline)
        status=project_status(Path(context['run_registry_dir']),project=Path(context['clone']))
        runs=[row for row in status.get('runs',[]) if row.get('run_id')==context['run_id']]
        if status.get('source')!='registry' or len(runs)!=1 or runs[0].get('children_liveness')!=[]:
            raise ContextError('progress close child cleanup is unknown or live')
        landed=context['mode']!='selfmod'
        if context['mode']=='selfmod':
            landing=close.get('selfmod_landing') or {}
            clone=Path(context['clone']);git=transaction.driver.git
            landed=(close.get('merge_outcome')=='merged' and landing.get('outcome')=='merged'
                and landing.get('green_accepted') is False and landing.get('progress_accepted') is True
                and landing.get('head_after')==result['head_commit'] and landing.get('accepted_tree') is None
                and git(clone,'rev-parse','HEAD')==result['head_commit']
                and git(clone,'rev-parse','HEAD^{tree}')==result['tree'] and transaction.driver.working_tree(clone)==result['tree'])
        if not landed:raise ContextError('progress close has pending or changed landing')
        selected=read_dispatch(context)
        close_id=hashlib.sha256(json.dumps(close,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        projected.update(record_phase='close',close_record_id=close_id,proof_id='progress-'+close_id,
            integrity_passed=False,progress_integrity_passed=True,landed=True,gate_outcome='fail',
            step_from=selected['step'],step_to=selected['step']+1,
            before_sha=context['base_commit'],after_sha=result['head_commit'],
            head=result['head_commit'],commits=result['commits'],
            master_path=selected.get('master_path'),batch_base_sha=selected.get('batch_base_sha'),
            driver_progress_accepted=True,owned_children_retired=True,
            context_plan_path=selected['subplan_path'],
            context_plan_fingerprint=context['plans'][Path(selected['subplan_path']).name].get('fingerprint'),
            context_master_fingerprint=(context.get('master_snapshot') or {}).get('fingerprint'),
            driver_gate_checks=result['attempts'][-1]['check_observations'],
            driver_declared_commands=result['attempts'][-1]['declared_contract_commands'])
        ledger=Path(context['ledger_path']);ledger.parent.mkdir(parents=True,exist_ok=True)
        with _lock(ledger.with_name(ledger.name+'.progress.lock'),deadline=transaction.driver._deadline.get(),guard=lambda:require_driver(context)):
            existing=[row for row in read_raw_records(ledger) if row.get('proof_id')==projected['proof_id']]
            if existing:
                if len(existing)!=1 or existing[0]!=projected:raise ContextError('progress close projection changed')
                return 0
            append_record(ledger,projected);_sync_parent(ledger)
        return 1
