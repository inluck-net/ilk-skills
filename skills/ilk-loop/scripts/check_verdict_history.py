#!/usr/bin/env python3
"""Diagnostic rolling flaky counts; never supplies a passing gate measurement.

The 2-in-10 policy is Chad's existing 2026-09-28 judgment call: tolerate an
isolated transient failure, block recurrence. Frequent healthy noise or a
persistent defect escaping this window would falsify its suitability. A single flaky result remains the historical legacy policy;
owned acceptance continues to refuse any mixed same-input result.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from iteration_clock import monotonic
from plan_state import _lock, _sync_parent
from run_registry import require_driver

WINDOW = 10
FLAKY_LIMIT = 2
VERDICTS = {'green', 'red', 'flaky', 'inconclusive'}


def observations(row):
    attempts = row.get('attempt_records') or row.get('driver_attempts') or [row]
    groups = {}
    for attempt in attempts:
        checks = attempt.get('check_observations') or (attempt.get('raw') or {}).get('results')
        if not checks:
            checks = [dict(command=attempt.get('command'), outcome=value)
                      for value in attempt.get('attempts', [attempt.get('outcome')])]
        for check in checks:
            if not check.get('command'): continue
            identity = dict(slug=row.get('slug'), step=row.get('step'),
                            command=check['command'], scope=check.get('scope'),
                            files=check.get('files'), partition_index=check.get('partition_index'),
                            partition_count=check.get('partition_count'))
            key=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            group=groups.setdefault(key, dict(identity=identity, outcomes=[]))
            group['outcomes'].append(check.get('outcome'))
    for group in groups.values():
        values=group['outcomes']
        if (row.get('outcome') in {'inconclusive', 'unknown', None} and not row.get('flaky_blocking')) or any(v not in {'pass','fail','error'} for v in values):
            verdict='inconclusive'
        elif 'pass' in values and any(v in {'fail','error'} for v in values):verdict='flaky'
        elif all(v=='pass' for v in values):verdict='green'
        else:verdict='red'
        group['verdict']=verdict
    return groups


def read_history(path):
    if not path.exists():return {}
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('diagnostic_only') is not True:
        raise ValueError('invalid check verdict history')
    rows=data.get('checks')
    if not isinstance(rows,dict):raise ValueError('invalid check verdict history')
    for key, entries in rows.items():
        if not isinstance(key,str) or len(key)!=64 or not isinstance(entries,list) or len(entries)>WINDOW:
            raise ValueError('invalid check verdict history identity')
        seen=set()
        for entry in entries:
            if (not isinstance(entry,dict) or entry.get('verdict') not in VERDICTS or
                not isinstance(entry.get('run_id'),str) or not isinstance(entry.get('iteration'),int)):
                raise ValueError('invalid check verdict history entry')
            identity=(entry['run_id'],entry['iteration'])
            if identity in seen:raise ValueError('duplicate check verdict history entry')
            seen.add(identity)
    return rows


def record_history(context, records):
    """Current gates always run. Old results only count diagnostic flakiness."""
    require_driver(context)
    path=Path(context['runtime_dir'])/'launcher/local-check-verdict-history.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    deadline=context.get('iteration_deadline_monotonic')
    def guard():
        require_driver(context)
        if deadline is not None and monotonic()>=deadline:raise ValueError('check history reached original deadline')
    guard()
    # Legacy adapters without a transferred deadline must never wait forever
    # for an optional diagnostic lock. Contention is unknown and blocks proof.
    with _lock(path.with_suffix('.lock'),deadline=deadline,guard=guard,non_blocking=deadline is None):
        history=read_history(path); blocked=[]
        for row in records:
            if not row.get('slug'):continue
            row['flaky_checks']=[]
            for key, group in observations(row).items():
                entry=dict(run_id=context['run_id'],iteration=context['iteration'],
                           identity=group['identity'],verdict=group['verdict'],outcomes=group['outcomes'])
                entries=history.setdefault(key,[])
                existing=[old for old in entries if (old['run_id'],old['iteration'])==(entry['run_id'],entry['iteration'])]
                if existing and existing!=[entry]:raise ValueError('current check history identity changed')
                if not existing:entries.append(entry)
                history[key]=entries[-WINDOW:]
                count=sum(old['verdict']=='flaky' for old in history[key])
                if group['verdict']=='flaky':
                    row.setdefault('flaky_checks',[]).append(dict(check_id=key,count=count,window=WINDOW))
                    if count>=FLAKY_LIMIT:
                        # Remains flaky, never a confirmed red or permission to strike.
                        row.update(outcome='inconclusive',verdict='flaky',flaky_blocking=True)
                        blocked.append(dict(slug=row['slug'],step=row.get('step'),check_id=key,count=count))
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name)
            try:
                json.dump(dict(schema_version=1,diagnostic_only=True,checks=history),stream)
                stream.flush();os.fsync(stream.fileno())
            except BaseException:temporary.unlink(missing_ok=True);raise
        try:
            temporary.chmod(0o600);guard();os.replace(temporary,path);_sync_parent(path)
        finally:temporary.unlink(missing_ok=True)
    return dict(blocked=bool(blocked),inconclusive=bool(blocked),flaky_blocking=blocked)


def main():
    import argparse
    from iteration_context import read_context
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path)
    args=parser.parse_args();context=read_context()
    if not context:parser.error('frozen context required')
    try:
        records=[json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
        if not records or any(not isinstance(row,dict) for row in records):
            raise ValueError('current gate observations unavailable')
        result=record_history(context,records)
        with tempfile.NamedTemporaryFile(mode='w',dir=args.results.parent,delete=False) as stream:
            temporary=Path(stream.name)
            try:
                stream.write(''.join(json.dumps(row)+'\n' for row in records))
                stream.flush();os.fsync(stream.fileno())
            except BaseException:temporary.unlink(missing_ok=True);raise
        try:
            temporary.chmod(0o600);require_driver(context)
            os.replace(temporary,args.results);_sync_parent(args.results)
        finally:temporary.unlink(missing_ok=True)
    except (OSError,ValueError,RuntimeError) as error:
        result=dict(blocked=True,inconclusive=True,error=type(error).__name__+': '+str(error))
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
