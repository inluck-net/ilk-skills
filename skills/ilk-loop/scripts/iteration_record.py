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
        integrity_ok=close.get('stop_reason') not in {'ship_integrity_violation','selfmod_state_invalid','selfmod_live_clone_touched'}
        landed=context['mode']!='selfmod' or outcome=='merged'
        projected=dict(row,record_phase='close',close_record_id=close_id,
                       integrity_passed=integrity_ok,landed=landed,merge_outcome=outcome,
                       iteration_stop_reason=close.get('stop_reason'))
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


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log',type=Path,required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--iteration',type=int,required=True)
    a=p.parse_args()
    print(project_close_record(latest_close(a.log,a.run_id,a.iteration)))
if __name__=='__main__':main()
