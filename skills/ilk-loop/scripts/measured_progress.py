"""Assess measured pre-existing-red progress separately from green acceptance.

New candidate decision API only; transaction/CAS/terminal wiring is pending.
"""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
from iteration_context import ContextError,read_dispatch
from run_registry import require_driver
from driver_step import working_tree,gate_input_fingerprint
from red_owner import _parse_pytest_nodes,_budget_scope
from base_samples import measure_nodes,load_measurement


def assess(context,attempt,*,budget_s):
    require_driver(context);selected=read_dispatch(context)
    rejected=dict(protocol='measured-red-progress-v1',progress_eligible=False,accepted=False,green_accepted=False)
    identity=dict(run_id=context['run_id'],iteration=context['iteration'],slug=selected.get('slug'),step=selected['step'],base_commit=context['base_commit'])
    if any(attempt.get(key)!=value for key,value in identity.items()):
        raise ContextError('progress assessment attempt belongs to another dispatch')
    identifier=attempt.get('attempt_id')
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):
        raise ContextError('durable head attempt identity unavailable')
    directory=Path(context['runtime_dir'])/'launcher/driver-step'/identifier
    if json.loads((directory/'attempt.json').read_text())!=attempt:
        raise ContextError('durable head attempt changed')
    root=Path(context['exec_tree']);fingerprint=gate_input_fingerprint(context,selected)
    if (attempt.get('all_passed') is not False or attempt.get('raw_all_passed') is not False
            or attempt.get('verdict')!='red' or attempt.get('gate_tree_unchanged') is not True
            or attempt.get('input_fingerprint')!=fingerprint or attempt.get('working_tree')!=working_tree(root)):
        return dict(rejected,reason='head attempt is not exact measured red')
    checks=attempt.get('check_observations') or [];verdicts=attempt.get('check_verdicts') or []
    keys=[(check.get('scope'),check.get('command')) for check in checks]
    if (not checks or len(set(keys))!=len(keys)
            or {(row.get('scope'),row.get('command')) for row in verdicts}!=set(keys)
            or any(row.get('verdict') not in {'green','red'} for row in verdicts)):
        return dict(rejected,reason='all-attempt gate inventory is unknown or flaky')
    failed=[];head_captures=[]
    for check in checks:
        if check.get('output_complete') is not True or check.get('error') or check.get('outcome') not in {'pass','fail'}:
            return dict(rejected,reason='head gate output is incomplete')
        output={}
        for channel in ('stdout','stderr'):
            raw=check.get(channel+'_path')
            if not isinstance(raw,str):return dict(rejected,reason='head output artifact missing')
            path=Path(raw).resolve(strict=True)
            if path.parent!=directory.resolve():raise ContextError('head output outside exact attempt')
            data=path.read_bytes();output[channel]=data.decode('utf-8')
            head_captures.append(dict(path=str(path),sha256=hashlib.sha256(data).hexdigest()))
        if check['outcome']=='pass':
            if check.get('exit_code')!=0:return dict(rejected,reason='head pass lacks actual zero exit')
            continue
        if check.get('exit_code')!=1:return dict(rejected,reason='head failure is not a pytest assertion result')
        blob=re.sub(r'\x1b\[[0-9;]*m','',output['stdout']+output['stderr'])
        nodes=_parse_pytest_nodes(blob)
        failed_lines='\n'.join(line for line in blob.splitlines() if line.startswith('FAILED '))
        if not nodes or nodes!=_parse_pytest_nodes(failed_lines):
            return dict(rejected,reason='head failed selectors are unknown or include fixture errors')
        failed.append((check,nodes))
    if not failed:return dict(rejected,reason='no complete failed node inventory')
    observations=[]
    with _budget_scope(budget_s):
        for check,nodes in failed:
            receipt=measure_nodes(context,check['command'],nodes,budget_s=budget_s)
            load_measurement(context,receipt['receipt'])
            bases=set(receipt['bases'].values())
            if (receipt.get('capture_error') or set(receipt['measurements'])!=bases
                    or any(results!={node:'failed' for node in nodes} for results in receipt['measurements'].values())):
                return dict(rejected,reason='exact nodes were not measured red at both frozen bases',observations=observations+[receipt])
            captures=receipt['captures']
            if ({(row.get('base_commit'),row.get('node')) for row in captures}!={(base,node) for base in bases for node in nodes}
                    or any(row.get('sample_head')!=row.get('base_commit') or row.get('sample_unchanged') is not True
                           or row.get('exit_code')!=1 or row.get('timed_out') or row.get('outcome')!='failed' for row in captures)):
                return dict(rejected,reason='base sample observation is incomplete',observations=observations+[receipt])
            path=Path(receipt['receipt'])
            observations.append(dict(scope=check['scope'],command=check['command'],node_ids=nodes,
                receipt=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    require_driver(context)
    if working_tree(root)!=attempt['working_tree'] or gate_input_fingerprint(context,selected)!=fingerprint:
        raise ContextError('progress inputs changed during base measurements')
    if any(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256'] for row in head_captures):
        raise ContextError('head output changed during base measurements')
    return dict(rejected,progress_eligible=True,reason='measured pre-existing red; green proof remains absent',
        **identity,tree=attempt['working_tree'],attempt_id=identifier,input_fingerprint=fingerprint,
        head_captures=head_captures,observations=observations)
