#!/usr/bin/env python3
"""Finalize provisional ledger evidence from the existing JSONL close record."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from ship_proof_ledger import append_record,read_raw_records


def project_close_record(close: dict) -> int:
    if close.get('record_phase')!='close':
        raise ValueError('ledger projection requires a close record')
    context=close['context']
    if close['run_id']!=context['run_id'] or close['iteration']!=context['iteration']:
        raise ValueError('close record contradicts frozen context')
    ledger=Path(context['ledger_path'])
    raw=read_raw_records(ledger)
    finalized={r.get('proof_id') for r in raw if r.get('record_phase')=='close'}
    close_id=hashlib.sha256(json.dumps(close,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    count=0
    for row in raw:
        if row.get('record_phase')!='provisional' or not row.get('proof_id'): continue
        if row.get('run_id')!=close['run_id'] or row.get('iteration')!=close['iteration']: continue
        if row['proof_id'] in finalized: continue
        outcome=close.get('merge_outcome')
        # Unknown/new terminal failures cannot become proof by omission from
        # a blacklist. Historical empty-success closes remain readable.
        integrity_ok=not close.get('stop_reason')
        if context.get('worker_sandbox_adapter')=='macos-brokered-shell':
            accepted=close.get('driver_acceptance') or {}
            execution=close.get('execution') or {}
            integrity_ok=(integrity_ok and close.get('driver_integrity_passed') is True
                and close.get('state_audit',{}).get('explained') is True
                and accepted.get('accepted') is True and accepted.get('run_id')==close['run_id']
                and accepted.get('iteration')==close['iteration'] and bool(accepted.get('tree'))
                and accepted['tree']==execution.get('head_tree') and not execution.get('capture_error'))
        landed=context['mode']!='selfmod' or outcome=='merged'
        projected=dict(row,record_phase='close',close_record_id=close_id,
                       integrity_passed=integrity_ok,landed=landed,merge_outcome=outcome,
                       iteration_stop_reason=close.get('stop_reason'))
        if context.get('worker_sandbox_adapter')=='macos-brokered-shell':
            dispatch=context.get('dispatch') or {}
            snapshots=[snapshot for snapshot in context.get('plans',{}).values()
                       if snapshot.get('path')==dispatch.get('subplan_path')]
            attempts=accepted.get('attempts') or []
            matching=(row.get('slug')==dispatch.get('slug') and len(snapshots)==1
                      and isinstance(dispatch.get('step'),int)
                      and row.get('step_from')==dispatch['step'] and row.get('step_to')==dispatch['step']+1)
            projected.update(driver_owned=matching,accepted_tree=accepted.get('tree'),
                accepted_head=accepted.get('head_commit'),
                acceptance_base_commit=context.get('base_commit'),acceptance_base_tree=context.get('base_tree'),
                context_plan_path=dispatch.get('subplan_path'),
                context_plan_fingerprint=snapshots[0].get('fingerprint') if len(snapshots)==1 else None,
                context_master_fingerprint=(context.get('master_snapshot') or {}).get('fingerprint'),
                driver_declared_commands=attempts[-1].get('declared_contract_commands',[]) if attempts else [],
                driver_gate_checks=attempts[-1].get('check_observations',[]) if attempts else [])
            projected['integrity_passed']=bool(integrity_ok and matching)
        append_record(ledger,projected)
        finalized.add(row['proof_id']);count+=1
    return count


def latest_close(log: Path, run_id: str, iteration: int) -> dict:
    found=None
    for line in log.read_text(encoding='utf-8-sig').splitlines():
        try: row=json.loads(line)
        except ValueError: continue
        if isinstance(row,dict) and row.get('run_id')==run_id and row.get('iteration')==iteration and row.get('record_phase')=='close':
            found=row
    if found is None: raise ValueError('no matching close record; provisional evidence retained')
    return found


def replay_closed_records(log: Path, ledger: Path) -> int:
    """Recover only pending proof rows with an actual matching close record.

    The caller holds the runner lock and supplies its resolved ledger path.
    Missing or partial closes remain provisional, never guessed successful.
    """
    raw=read_raw_records(ledger)
    finalized={r.get('proof_id') for r in raw if r.get('record_phase')=='close'}
    pending={(r.get('run_id'),r.get('iteration')) for r in raw
             if r.get('record_phase')=='provisional' and r.get('proof_id')
             and r['proof_id'] not in finalized}
    if not pending or not log.exists(): return 0
    closes={}
    with log.open(encoding='utf-8-sig') as source:
        for line in source:
            try: row=json.loads(line)
            except ValueError: continue
            if not isinstance(row,dict) or row.get('record_phase')!='close': continue
            key=(row.get('run_id'),row.get('iteration'))
            if key in pending: closes[key]=row
    # Validate all selected paths before making any mutation.
    for close in closes.values():
        context=close.get('context') or {}
        if Path(context.get('ledger_path','')).resolve()!=ledger.resolve():
            raise ValueError('recovered close ledger differs from current project ledger')
        if close.get('run_id')!=context.get('run_id') or close.get('iteration')!=context.get('iteration'):
            raise ValueError('recovered close contradicts frozen identity')
    return sum(project_close_record(close) for close in closes.values())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log',type=Path,required=True)
    p.add_argument('--replay-ledger',type=Path)
    p.add_argument('--run-id')
    p.add_argument('--iteration',type=int)
    a=p.parse_args()
    if a.replay_ledger:
        print(replay_closed_records(a.log,a.replay_ledger))
    else:
        if not a.run_id or a.iteration is None: p.error('run-id and iteration required for close projection')
        print(project_close_record(latest_close(a.log,a.run_id,a.iteration)))
if __name__=='__main__':main()
