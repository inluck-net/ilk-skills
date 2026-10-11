"""Candidate measured-red commit/CAS transaction; no runtime caller enabled.

Progress acceptance is separate from green acceptance. A close/landing/result
adapter must carry this distinction before the loop may use this protocol.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib,json,os,tempfile,math
from pathlib import Path
import driver_step as driver
from iteration_clock import monotonic
from iteration_context import ContextError,read_context,read_dispatch,plan_snapshot
from plan_status import parse_frontmatter
from plan_state import transition,_sync_parent,audit_context
from run_registry import require_driver,driver_authority
from base_samples import load_measurement
from measured_progress import assess,verify


@contextmanager
def _authority(context,deadline):
    require_driver(context)
    if read_context()!=context:raise ContextError('progress transaction context differs from frozen dispatch')
    original=driver_authority(context).get('iteration_deadline_monotonic')
    frozen=context.get('iteration_deadline_monotonic')
    if any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) for value in (deadline,original,frozen)):
        raise ContextError('progress transaction finite original deadline missing')
    original=min(original,frozen)
    token=driver._deadline.set(min(deadline,original));owner=driver._operation_context.set(context)
    try:driver.remaining();yield
    finally:driver._operation_context.reset(owner);driver._deadline.reset(token)


def _path(context):
    key=hashlib.sha256(json.dumps([context['run_id'],context['iteration']],separators=(',',':')).encode()).hexdigest()
    return Path(context['runtime_dir'])/'launcher/driver-step'/('progress-acceptance-'+key+'.json')


def _captures(context,decision):
    captures=list(decision['head_captures'])
    for observation in decision['observations']:
        path=Path(observation['receipt']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=observation['sha256']:
            raise ContextError('progress base receipt changed')
        row=load_measurement(context,path)
        if row.get('capture_error'):raise ContextError('progress base receipt incomplete')
        captures.append(dict(path=str(path),sha256=observation['sha256']))
        for capture in row['captures']:
            captures.extend(capture[channel] for channel in ('stdout','stderr'))
    for capture in captures:
        if hashlib.sha256(Path(capture['path']).read_bytes()).hexdigest()!=capture['sha256']:
            raise ContextError('progress output changed')
    return captures


def _decision(context,attempt,decision):
    selected=read_dispatch(context)
    identity=dict(run_id=context['run_id'],iteration=context['iteration'],slug=selected.get('slug'),step=selected['step'],base_commit=context['base_commit'])
    if (decision.get('protocol')!='measured-red-progress-v1' or decision.get('progress_eligible') is not True
            or decision.get('accepted') is not False or decision.get('green_accepted') is not False
            or any(decision.get(key)!=value or attempt.get(key)!=value for key,value in identity.items())
            or decision.get('attempt_id')!=attempt.get('attempt_id') or decision.get('tree')!=attempt.get('working_tree')
            or attempt.get('all_passed') is not False or attempt.get('raw_all_passed') is not False
            or attempt.get('gate_tree_unchanged') is not True or attempt.get('verdict')!='red'
            or decision.get('input_fingerprint')!=driver.gate_input_fingerprint(context,selected)):
        raise ContextError('progress decision does not bind exact measured-red attempt')
    artifact=Path(context['runtime_dir'])/'launcher/driver-step'/attempt['attempt_id']/'attempt.json'
    if json.loads(artifact.read_text())!=attempt:raise ContextError('progress head attempt changed')
    verify(context,attempt,decision)
    return _captures(context,decision)


def _save(context,result,phase):
    captures=_decision(context,result['attempts'][-1],result['progress_observation'])
    row=dict(schema_version=1,protocol='measured-red-progress-v1',phase=phase,
        context_sha256=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
        result=result,captures=captures)
    target=_path(context);target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(row,stream);stream.flush();os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600);os.replace(temporary,target);_sync_parent(target)
    finally:temporary.unlink(missing_ok=True)


def _kernel(context,root,tree):
    kernel=context.get('kernel_list') or {}
    if kernel.get('loop_kernel_policy')=='owner-only':
        from safety_kernel import touches_kernel
        if any(touches_kernel(name,kernel=kernel) for name in driver.git(root,'diff','--name-only',context['base_commit'],tree).splitlines()):
            raise ContextError('progress transaction changed frozen owner kernel')


def _finish_commit(context,result,*,gate_timeout,budget_s):
    root=Path(context['exec_tree']);selected=read_dispatch(context)
    head=driver.git(root,'rev-parse','HEAD');tree=driver.git(root,'rev-parse','HEAD^{tree}')
    if driver.git(root,'rev-parse','HEAD^')!=context['base_commit'] or driver.git(root,'log','-1','--format=%B')!=driver._commit_message(selected):
        raise ContextError('progress commit is outside exact intent')
    _kernel(context,root,tree)
    if driver.working_tree(root)!=tree:raise ContextError('progress commit hook left uncommitted changes')
    if tree!=result['tree']:
        # Fresh changed shipping tree: diagnostic history is not gate proof.
        attempt=driver.gate(context,root,Path(selected['subplan_path']),selected,default_timeout=gate_timeout)
        allowance=min(budget_s,result['progress_observation']['attribution_deadline_monotonic']-monotonic())
        if allowance<=0:raise ContextError('original progress attribution allowance exhausted after hook')
        decision=assess(context,attempt,budget_s=allowance)
        if decision.get('progress_eligible') is not True:
            raise ContextError('changed progress commit tree needs a different verified transaction')
        result=dict(result,attempts=result['attempts']+[attempt],tree=tree,progress_observation=decision)
    result=dict(result,commits=[head],head_commit=head,commit_ended_monotonic=monotonic(),progress_accepted=True)
    _save(context,result,'prepared')
    return result


def _advance(context,result):
    selected,path,root,state=driver.validate(context)
    if driver.git(root,'rev-parse','HEAD')!=result['head_commit'] or driver.working_tree(root)!=result['tree']:
        raise ContextError('progress tree changed before CAS')
    _decision(context,result['attempts'][-1],result['progress_observation'])
    guards={Path(context['master_snapshot']['path']):context['master_snapshot']['owned_state']} if context.get('master_snapshot') else {}
    transition(path,{'current_step':state['current_step'],'status':state.get('status')},
        {'current_step':selected['step']+1,'status':'in-progress'},actor='driver',
        reason='exact measured pre-existing-red progress; no green proof',guards=guards)
    _save(context,result,'advanced')
    return result


def accept(context,attempt,decision,*,deadline,gate_timeout,budget_s):
    with _authority(context,deadline):
        selected,path,root,state=driver.validate(context)
        _decision(context,attempt,decision)
        if driver.git(root,'rev-parse','HEAD')!=context['base_commit'] or driver.git(root,'diff','--cached','--name-only'):
            raise ContextError('progress worker changed Git state')
        if driver.working_tree(root)!=decision['tree']:raise ContextError('progress worker tree changed')
        _kernel(context,root,decision['tree'])
        if decision['tree']==driver.git(root,'rev-parse','HEAD^{tree}'):
            raise ContextError('measured-red progress needs a changed code tree')
        result=dict(accepted=False,green_accepted=False,progress_accepted=False,
            protocol='measured-red-progress-v1',run_id=context['run_id'],iteration=context['iteration'],
            attempts=[attempt],progress_observation=decision,tree=decision['tree'],commits=[],
            head_commit=context['base_commit'],proof_provisional=True,
            commit_started_monotonic=monotonic(),gate_timeout=gate_timeout,attribution_allowance=budget_s)
        _save(context,result,'commit-intent')
        driver.git(root,'add','-A','--','.')
        if driver.git(root,'write-tree')!=result['tree']:raise ContextError('progress index changed before commit')
        driver.git(root,'commit','-m',driver._commit_message(selected))
        result=_finish_commit(context,result,gate_timeout=gate_timeout,budget_s=budget_s)
        return _advance(context,result)


def recover(context,*,deadline):
    with _authority(context,deadline):
        target=_path(context)
        if not target.exists():return None
        row=json.loads(target.read_text());result=row.get('result') or {};selected=read_dispatch(context)
        if (row.get('schema_version')!=1 or row.get('protocol')!='measured-red-progress-v1'
                or row.get('phase') not in {'commit-intent','prepared','advanced'}
                or row.get('context_sha256')!=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
                or result.get('accepted') is not False or result.get('green_accepted') is not False):
            raise ContextError('progress recovery receipt identity changed')
        for snapshot in list(context['plans'].values())+([context['master_snapshot']] if context.get('master_snapshot') else []):
            if plan_snapshot(Path(snapshot['path'])).get('fingerprint')!=snapshot.get('fingerprint'):
                raise ContextError('progress recovery contract changed')
        if context.get('master_snapshot'):
            snapshot=context['master_snapshot'];state=parse_frontmatter(Path(snapshot['path']).read_text())
            if any(state.get(key)!=value for key,value in snapshot['owned_state'].items()):
                raise ContextError('progress recovery master state changed')
        _decision(context,result['attempts'][-1],result['progress_observation'])
        for capture in row['captures']:
            if hashlib.sha256(Path(capture['path']).read_bytes()).hexdigest()!=capture['sha256']:
                raise ContextError('progress recovery capture changed')
        root=Path(context['exec_tree']);head=driver.git(root,'rev-parse','HEAD')
        if row['phase']=='commit-intent':
            if head==context['base_commit']:
                driver.validate(context)
                if driver.working_tree(root)!=result['tree']:raise ContextError('progress intent tree changed')
                staged=driver.git(root,'diff','--cached','--name-only')
                if not staged:driver.git(root,'add','-A','--','.')
                if driver.git(root,'write-tree')!=result['tree']:raise ContextError('progress recovered index changed')
                driver.git(root,'commit','-m',driver._commit_message(selected))
            result=_finish_commit(context,result,gate_timeout=result['gate_timeout'],budget_s=result['attribution_allowance'])
        if (result.get('progress_accepted') is not True or head not in {context['base_commit'],result['head_commit']}
                or driver.git(root,'rev-parse','HEAD')!=result['head_commit'] or driver.working_tree(root)!=result['tree']):
            raise ContextError('progress recovery committed tree changed')
        state=parse_frontmatter(Path(selected['subplan_path']).read_text())
        if state.get('current_step')==str(selected['step']):result=_advance(context,result)
        elif state.get('current_step')!=str(selected['step']+1) or state.get('status')!='in-progress' or state.get('hold')=='human':
            raise ContextError('progress recovery pointer or hold changed')
        if audit_context(context).get('explained') is not True:raise ContextError('progress recovery state unexplained')
        _save(context,result,'advanced')
        return dict(result,recovered_progress=True)
