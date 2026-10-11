"""Assess measured pre-existing-red progress separately from green acceptance.

New candidate decision API only; transaction/CAS/terminal wiring is pending.
"""
from __future__ import annotations
import hashlib,json,re,math
from pathlib import Path
from iteration_context import ContextError,read_dispatch
from run_registry import require_driver,driver_authority
from iteration_clock import monotonic
from driver_step import working_tree,gate_input_fingerprint
from red_owner import _parse_pytest_nodes,_budget_scope
from base_samples import measure_nodes,load_measurement


def assess(context,attempt,*,budget_s):
    require_driver(context);selected=read_dispatch(context)
    original=driver_authority(context).get('iteration_deadline_monotonic')
    if (isinstance(budget_s,bool) or not isinstance(budget_s,(int,float)) or not math.isfinite(budget_s) or budget_s<=0
            or isinstance(original,bool) or not isinstance(original,(int,float)) or not math.isfinite(original)):
        raise ContextError('progress assessment original allowance unavailable')
    frozen=context.get('iteration_deadline_monotonic')
    if isinstance(frozen,bool) or not isinstance(frozen,(int,float)) or not math.isfinite(frozen):
        raise ContextError('progress frozen original deadline unavailable')
    started=monotonic()
    attribution_deadline=min(original,frozen,started+budget_s)
    if attribution_deadline<=monotonic():raise ContextError('progress assessment original deadline reached')
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
    with _budget_scope(max(0,attribution_deadline-monotonic())):
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
                receipt=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                sample_started_monotonic=receipt['started_monotonic'],sample_ended_monotonic=receipt['ended_monotonic']))
    require_driver(context)
    if working_tree(root)!=attempt['working_tree'] or gate_input_fingerprint(context,selected)!=fingerprint:
        raise ContextError('progress inputs changed during base measurements')
    if any(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256'] for row in head_captures):
        raise ContextError('head output changed during base measurements')
    return dict(rejected,progress_eligible=True,reason='measured pre-existing red; green proof remains absent',
        **identity,tree=attempt['working_tree'],attempt_id=identifier,input_fingerprint=fingerprint,
        head_captures=head_captures,observations=observations,attribution_deadline_monotonic=attribution_deadline,attribution_started_monotonic=started,attribution_ended_monotonic=monotonic(),attribution_allowance=budget_s)


def verify(context,attempt,decision):
    """Recheck the complete decision inventory without rerunning any gate.

    This transfers/recoveries one exact fresh transaction's measured evidence;
    it cannot turn a diagnostic receipt or a caller's eligibility flag into
    progress permission. It returns no green acceptance.
    """
    require_driver(context)
    try:_verify(context,attempt,decision)
    except (OSError,KeyError,TypeError,ValueError) as error:
        raise ContextError('progress observation verification refused: '+str(error)) from error


def _verify(context,attempt,decision):
    started=decision['attribution_started_monotonic'];allowance=decision['attribution_allowance'];expires=decision['attribution_deadline_monotonic']
    if (any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) for value in (started,allowance,expires))
            or allowance<=0 or expires<=started or expires>min(context['iteration_deadline_monotonic'],started+allowance)):
        raise ContextError('progress original attribution allowance changed')
    checks=attempt['check_observations'];verdicts=attempt['check_verdicts']
    keys=[(row['scope'],row['command']) for row in checks]
    if (not checks or len(set(keys))!=len(keys)
            or {(row['scope'],row['command']) for row in verdicts}!=set(keys)
            or any(row['verdict'] not in {'red','green'} for row in verdicts)):
        raise ContextError('progress all-attempt inventory incomplete or flaky')
    failed={};head_captures=[]
    directory=(Path(context['runtime_dir'])/'launcher/driver-step'/attempt['attempt_id']).resolve()
    for check in checks:
        if check.get('output_complete') is not True or check.get('error') or check['outcome'] not in {'pass','fail'}:
            raise ContextError('progress head output incomplete')
        output=[]
        for channel in ('stdout','stderr'):
            path=Path(check[channel+'_path']).resolve(strict=True)
            if path.parent!=directory:raise ContextError('progress head capture outside exact attempt')
            data=path.read_bytes();output.append(data.decode('utf-8'))
            head_captures.append(dict(path=str(path),sha256=hashlib.sha256(data).hexdigest()))
        if check['outcome']=='pass':
            if check['exit_code']!=0:raise ContextError('progress pass lacks actual zero exit')
            continue
        blob=re.sub(r'\x1b\[[0-9;]*m','',''.join(output))
        nodes=_parse_pytest_nodes(blob)
        actual='\n'.join(line for line in blob.splitlines() if line.startswith('FAILED '))
        if check['exit_code']!=1 or not nodes or nodes!=_parse_pytest_nodes(actual):
            raise ContextError('progress head failure inventory unknown')
        failed[(check['scope'],check['command'])]=nodes
    observations=decision['observations']
    if (not failed or decision['head_captures']!=head_captures or len(observations)!=len(failed)
            or {(row['scope'],row['command']) for row in observations}!=set(failed)):
        raise ContextError('progress decision does not cover complete head failures')
    for observation in observations:
        nodes=failed[(observation['scope'],observation['command'])]
        if observation['node_ids']!=nodes:raise ContextError('progress failed selectors changed')
        path=Path(observation['receipt']).resolve(strict=True)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=observation['sha256']:
            raise ContextError('progress baseline receipt changed')
        receipt=load_measurement(context,path);bases=set(receipt['bases'].values())
        if (receipt['command']!=observation['command'] or receipt['node_ids']!=nodes or receipt.get('capture_error')
                or set(receipt['measurements'])!=bases
                or any(results!={node:'failed' for node in nodes} for results in receipt['measurements'].values())):
            raise ContextError('progress nodes not measured red at both exact bases')
        captures=receipt['captures']
        if len(captures)!=len(bases)*len(nodes) or {(row['base_commit'],row['node']) for row in captures}!={(base,node) for base in bases for node in nodes}:
            raise ContextError('progress base capture inventory incomplete')
        for row in captures:
            blob=re.sub(r'\x1b\[[0-9;]*m','', ''.join(Path(row[channel]['path']).read_text() for channel in ('stdout','stderr')))
            actual='\n'.join(line for line in blob.splitlines() if line.startswith('FAILED '))
            if (row.get('sample_head')!=row['base_commit'] or row.get('sample_unchanged') is not True
                    or row.get('exit_code')!=1 or row.get('timed_out') is not False or row.get('outcome')!='failed'
                    or _parse_pytest_nodes(blob)!=[row['node']] or _parse_pytest_nodes(actual)!=[row['node']]):
                raise ContextError('progress raw base failure evidence unknown')
