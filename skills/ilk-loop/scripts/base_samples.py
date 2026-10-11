"""Durable exact-node base observations; this API grants no progress/ship proof.

New owned Mac adapter prerequisite. Not wired into step acceptance yet.
"""
from __future__ import annotations
import hashlib,json,math,os,re,shlex,uuid
from pathlib import Path
from iteration_context import ContextError,read_context,read_dispatch
from iteration_clock import monotonic
from run_registry import require_driver,driver_authority
from red_owner import _budget_scope,_run_at_base_worktree


def _write(path,payload):
    with path.open('x',encoding='utf-8') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    path.chmod(0o600)
    from plan_state import _sync_parent
    _sync_parent(path)
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def measure_nodes(context,command,nodes,*,budget_s):
    """Capture requested node outputs at the two frozen bases, under one cap.

    A later transaction must bind these observations to complete failed HEAD
    evidence and input/tree fingerprints. This receipt alone never advances.
    Unknown runner, deadline, output or cleanup remains diagnostic unknown.
    """
    require_driver(context)
    if read_context()!=context:raise ContextError('base observation context differs from frozen dispatch')
    if context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        raise ContextError('base observation host adapter unavailable')
    selected=read_dispatch(context)
    if not isinstance(command,str):raise ContextError('base observation command unavailable')
    tokens=shlex.split(command)
    # Deliberately narrow eligibility: no compound/env/custom shell runner.
    # This is an API restriction, not a claim to whole-repository hermeticity.
    if (len(tokens)<3 or not re.fullmatch(r'python(?:3(?:[.][0-9]+)?)?',Path(tokens[0]).name)
            or tokens[1:3]!=['-m','pytest'] or any(re.search(r'[;&|<>]',token) for token in tokens)):
        raise ContextError('exact-node base observation requires a simple Python pytest command')
    if (not isinstance(nodes,list) or not nodes
            or any(not isinstance(node,str) or '::' not in node or '\n' in node for node in nodes)
            or len(set(nodes))!=len(nodes)):
        raise ContextError('complete unique pytest node inventory required')
    if isinstance(budget_s,bool) or not isinstance(budget_s,(int,float)) or not math.isfinite(budget_s) or budget_s<=0:
        raise ContextError('positive existing attribution allowance required')
    deadline=driver_authority(context).get('iteration_deadline_monotonic')
    if isinstance(deadline,bool) or not isinstance(deadline,(int,float)) or not math.isfinite(deadline):
        raise ContextError('original base observation deadline unavailable')
    allowance=min(budget_s,deadline-monotonic())
    if allowance<=0:raise ContextError('original base observation deadline reached')
    bases=dict(iteration=context.get('base_commit'),batch=selected.get('batch_base_sha'))
    if any(not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',sha) for sha in bases.values()):
        raise ContextError('both exact frozen bases required')
    directory=Path(context['runtime_dir'])/'launcher/driver-step'/('base-'+uuid.uuid4().hex)
    directory.mkdir(parents=True,exist_ok=False)
    measurements={};captures=[];error=None;started=monotonic()
    try:
        with _budget_scope(allowance):
            # Equal bases share one fresh observation in this invocation only.
            # There is no lookup of prior receipts or reusable gate outcome.
            for sha in dict.fromkeys(bases.values()):
                measurements[sha]=_run_at_base_worktree(Path(context['exec_tree']),sha,command,nodes,allowance,captures=captures)
    except Exception as exception:
        # Persist completed outputs even when later metadata/cleanup refuses.
        error=type(exception).__name__+': '+str(exception)
    rows=[]
    for index,capture in enumerate(captures):
        row={key:value for key,value in capture.items() if key not in {'stdout','stderr'}}
        for channel in ('stdout','stderr'):
            payload=capture.get(channel)
            row[channel]=_write(directory/f'{index}.{channel}.log',payload or '')
            if not isinstance(payload,str):row['outcome']='unmeasured'
        rows.append(row)
    receipt=dict(schema_version=1,diagnostic_only=True,accepted=False,
        run_id=context['run_id'],iteration=context['iteration'],slug=selected.get('slug'),step=selected['step'],
        context_sha256=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
        command=command,node_ids=nodes,bases=bases,measurements=measurements,captures=rows,
        started_monotonic=started,ended_monotonic=monotonic(),capture_error=error)
    receipt['receipt']=str(directory/'measurement.json')
    _write(Path(receipt['receipt']),json.dumps(receipt,sort_keys=True))
    return receipt


def load_measurement(context,path):
    """Validate diagnostic artifacts for recovery; never return acceptance."""
    require_driver(context)
    path=Path(path).resolve(strict=True)
    root=(Path(context['runtime_dir'])/'launcher/driver-step').resolve()
    if path.parent.parent!=root or not path.parent.name.startswith('base-') or path.name!='measurement.json':
        raise ContextError('base measurement outside owned diagnostic root')
    try:row=json.loads(path.read_text())
    except (ValueError,OSError) as error:raise ContextError('base measurement unavailable') from error
    selected=read_dispatch(context)
    fingerprint=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
    if (not isinstance(row,dict) or row.get('schema_version')!=1 or row.get('diagnostic_only') is not True
            or row.get('accepted') is not False or row.get('context_sha256')!=fingerprint
            or row.get('run_id')!=context['run_id'] or row.get('iteration')!=context['iteration']
            or row.get('step')!=selected['step'] or row.get('slug')!=selected.get('slug')
            or row.get('bases')!=dict(iteration=context.get('base_commit'),batch=selected.get('batch_base_sha'))):
        raise ContextError('base measurement frozen identity changed')
    for capture in row.get('captures',[]):
        for channel in ('stdout','stderr'):
            artifact=capture[channel];target=Path(artifact['path']).resolve(strict=True)
            if target.parent!=path.parent or hashlib.sha256(target.read_bytes()).hexdigest()!=artifact.get('sha256'):
                raise ContextError('base measurement output changed')
    return row
