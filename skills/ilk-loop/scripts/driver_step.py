#!/usr/bin/env python3
"""Candidate driver acceptance transaction; not enabled in installed runners.

Gates run against a captured working-tree object. Only the driver commits and
CAS-advances one step. Changed commit-hook output is gated again; an unchanged
commit does not rerun the same gates. Unknown/red/mutating gates never advance.
Dirty entry, baseline-red waivers and multi-repo dispatch still require migration
adapters. Baseline declarations may exist; this path grants no waiver.
"""
from __future__ import annotations
from dataclasses import asdict
from contextvars import ContextVar
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
from iteration_clock import monotonic
import uuid
from iteration_context import read_dispatch,plan_snapshot,root_for_plan,ContextError
from plan_status import parse_frontmatter
from plan_state import transition
_deadline=ContextVar('driver_step_deadline',default=None)
_operation_context=ContextVar('driver_step_context',default=None)


def remaining():
    deadline=_deadline.get()
    if deadline is None:return None
    value=deadline-monotonic()
    if value<=0:raise ContextError('iteration deadline reached')
    return value


def git(root,*args,env=None):
    context=_operation_context.get()
    if context:
        from run_registry import require_driver
        require_driver(context)
    timeout=remaining()
    if timeout is None:
        cp=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,env=env)
        code,stdout,stderr,timed_out=cp.returncode,cp.stdout,cp.stderr,False
    else:
        from bounded_run import run
        code,stdout,stderr,timed_out=run(['git','-C',str(root),*args],cwd=str(root),timeout=timeout,env=env)
    if timed_out:raise ContextError('driver Git operation reached iteration deadline')
    if code!=0:raise ContextError('driver Git operation failed: '+stderr[-1000:])
    return stdout.strip()


def working_tree(root: Path) -> str:
    # Alternate index captures accepted tracked/untracked files without
    # changing the real index. Objects remain available for attempt evidence.
    with tempfile.TemporaryDirectory(prefix='ilk-driver-index-') as temporary:
        env=dict(os.environ,GIT_INDEX_FILE=str(Path(temporary)/'index'))
        git(root,'read-tree','HEAD',env=env)
        git(root,'add','-A','--','.',env=env)
        return git(root,'write-tree',env=env)


def validate(context):
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise ContextError('worker cannot accept a step')
    from run_registry import require_driver
    require_driver(context)
    if context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        raise ContextError('enforced worker adapter required')
    from iteration_context import validate_work_tree_binding
    validate_work_tree_binding(context)
    if context.get('entry_dirty') is not False:raise ContextError('clean frozen entry required')
    selected=read_dispatch(context);path=Path(selected['subplan_path']);root=root_for_plan(context,path)
    if root!=Path(context['exec_tree']):raise ContextError('member-repo acceptance adapter not built')
    for row in list(context['plans'].values())+([context['master_snapshot']] if context.get('master_snapshot') else []):
        if plan_snapshot(Path(row['path'])).get('fingerprint')!=row.get('fingerprint'):
            raise ContextError('frozen plan contract changed')
    state=parse_frontmatter(path.read_text())
    if state.get('current_step')!=str(selected['step']):raise ContextError('step changed since dispatch')
    if state.get('status') in {'draft','blocked','parked','shipped'} or state.get('hold')=='human':
        raise ContextError('selected plan is held or already shipped')
    master=selected.get('master_path')
    if master:
        fm=parse_frontmatter(Path(master).read_text())
        if fm.get('status') in {'draft','blocked','parked','shipped'} or fm.get('hold')=='human':
            raise ContextError('master is held or already shipped')
    return selected,path,root,state


def gate_contract(root,path,selected):
    from run_local_checks import (split_frontmatter,parse_local_checks_block,extract_step_local_checks,
        count_local_checks_items,count_step_local_checks_items,step_gate_fence)
    fm,body=split_frontmatter(path.read_text())
    sub=parse_local_checks_block(fm);step=extract_step_local_checks(body,selected['step'])
    if count_local_checks_items(fm)>len(sub) or count_step_local_checks_items(body,selected['step'])>len(step):
        raise ContextError('malformed declared gate')
    if step_gate_fence(body,selected['step']).declares_local_checks and not step:
        raise ContextError('unreadable step gate')
    if not sub and not step:raise ContextError('a runnable declared gate is required')
    # Strict code-only acceptance does not import suite baseline waivers.
    # Every selected subplan/step/mention check must pass on its raw exit.
    # A declared unrelated suite red does not require running that suite.
    return sub,step


def gate(context,root,path,selected,*,default_timeout):
    from run_local_checks import build_mention_gate_inventory,run_one,_read_declared_reds
    tree=working_tree(root);sub,step=gate_contract(root,path,selected)
    changed=git(root,'diff','--name-only',context['base_commit'],tree).splitlines()
    commands={item.get('command','') for item in sub+step}
    policy=context.get('mention_gate_policy','complete') # Descriptors without an explicit policy require complete coverage on a fresh gate.
    if policy not in {'bounded-existing-cap','complete'}:raise ContextError('unknown frozen mention policy')
    mention_observation={}
    try:mention=build_mention_gate_inventory(root,changed,commands,
        require_complete=policy=='complete',observation=mention_observation)
    except ValueError as error:raise ContextError(str(error)) from error
    identifier=uuid.uuid4().hex
    directory=Path(context['runtime_dir'])/'launcher/driver-step'/identifier
    directory.mkdir(parents=True,exist_ok=False)
    previous=os.environ.get('ILK_GATE_OUTPUT_DIR');os.environ['ILK_GATE_OUTPUT_DIR']=str(directory)
    started=monotonic()
    try:
        results=[]
        for scope,items in (('subplan',sub),('step',step),('mention',mention)):
            for item in items:
                limit=remaining()
                check=dict(item)
                if limit is not None:
                    # Existing run_one timeout parser has whole-second units.
                    if int(limit)<=0:raise ContextError('iteration deadline reached at whole-second gate boundary')
                    check['timeout']=min(int(check.get('timeout',default_timeout)),int(limit))
                results.append(run_one(check,scope,root,default_timeout=default_timeout))
    finally:
        if previous is None:os.environ.pop('ILK_GATE_OUTPUT_DIR',None)
        else:os.environ['ILK_GATE_OUTPUT_DIR']=previous
    unchanged=working_tree(root)==tree
    record=dict(attempt_id=identifier,run_id=context['run_id'],iteration=context['iteration'],
        slug=selected.get('slug'),step=selected['step'],base_commit=context['base_commit'],
        working_tree=tree,started_monotonic=started,ended_monotonic=monotonic(),
        mention_gate_policy=policy,mention_inventory=mention_observation,
        baseline_red_policy='strict-raw-exit-no-waivers',
        diagnostic_baseline_red_declarations=_read_declared_reds(root),
        declared_contract_commands=[check['command'] for check in sub+step],
        check_observations=[asdict(result) for result in results],gate_tree_unchanged=unchanged,
        all_passed=bool(results) and all(result.passed for result in results) and unchanged,
        diagnostic_only=True)
    artifact=directory/'attempt.json'
    with artifact.open('x') as stream:
        json.dump(record,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    artifact.chmod(0o600)
    from plan_state import _sync_parent
    _sync_parent(artifact);_sync_parent(directory)
    return record


def accept_step(context,*,default_timeout,deadline):
    """Use the caller's existing iteration deadline, including commit hooks."""
    token=_deadline.set(deadline)
    owner_token=_operation_context.set(context)
    try:
        remaining()
        return _accept_step(context,default_timeout=default_timeout)
    finally:
        _operation_context.reset(owner_token);_deadline.reset(token)


def _acceptance_receipt(context):
    identity=json.dumps([context['run_id'],context['iteration']],separators=(',',':'))
    return Path(context['runtime_dir'])/'launcher/driver-step'/('acceptance-'+hashlib.sha256(identity.encode()).hexdigest()+'.json')


def _save_acceptance(context,result,phase):
    """Write ahead of pointer mutation; this is provisional, never ship proof."""
    target=_acceptance_receipt(context);target.parent.mkdir(parents=True,exist_ok=True)
    captures=[]
    for attempt in result['attempts']:
        for check in attempt['check_observations']:
            for channel in ('stdout','stderr'):
                path=Path(check[channel+'_path'])
                captures.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    row=dict(schema_version=1,phase=phase,context_sha256=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
             result=result,captures=captures,context=context,context_path=os.environ.get('ILK_ITER_CONTEXT'))
    with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(row,stream);stream.flush();os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600);os.replace(temporary,target)
        from plan_state import _sync_parent
        _sync_parent(target)
    finally:temporary.unlink(missing_ok=True)


def recover_acceptance(context,*,deadline):
    token=_deadline.set(deadline)
    owner_token=_operation_context.set(context)
    try:
        remaining()
        return _recover_acceptance(context)
    finally:
        _operation_context.reset(owner_token);_deadline.reset(token)


def _recover_acceptance(context):
    """Finish only this frozen transaction's commit-to-CAS/receipt crash gap.

    No provider dispatch, different tree, fresh context or general gate reuse.
    Ordinary consumer startup selects this original frozen transaction;
    selfmod/member landing adapters remain separate.
    """
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise ContextError('worker cannot recover acceptance')
    from run_registry import require_driver
    require_driver(context)
    from iteration_context import validate_work_tree_binding
    validate_work_tree_binding(context)
    target=_acceptance_receipt(context)
    if not target.exists():return None
    row=json.loads(target.read_text())
    fingerprint=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
    if row.get('schema_version')!=1 or row.get('phase') not in {'commit-intent','prepared','advanced'} or row.get('context_sha256')!=fingerprint:
        raise ContextError('acceptance recovery identity changed')
    result=row['result'];selected=read_dispatch(context);path=Path(selected['subplan_path']);root=root_for_plan(context,path)
    for snapshot in list(context['plans'].values())+([context['master_snapshot']] if context.get('master_snapshot') else []):
        if plan_snapshot(Path(snapshot['path'])).get('fingerprint')!=snapshot.get('fingerprint'):
            raise ContextError('acceptance recovery contract changed')
    if context.get('master_snapshot'):
        snapshot=context['master_snapshot'];observed=parse_frontmatter(Path(snapshot['path']).read_text())
        if any(observed.get(key)!=value for key,value in snapshot['owned_state'].items()):
            raise ContextError('acceptance recovery master state changed')
    attempt=result['attempts'][-1]
    intended=row['phase']=='commit-intent'
    if (result.get('accepted') is not (not intended) or not result.get('proof_provisional') or not attempt.get('all_passed')
            or not attempt.get('gate_tree_unchanged') or result.get('tree')!=attempt.get('working_tree')):
        raise ContextError('acceptance recovery has no exact green attempt')
    for capture in row['captures']:
        if hashlib.sha256(Path(capture['path']).read_bytes()).hexdigest()!=capture['sha256']:
            raise ContextError('acceptance recovery capture changed')
    if intended:result=_recover_commit_intent(context,result)
    _transaction_head(context,result)
    state=parse_frontmatter(path.read_text())
    if state.get('current_step')==str(selected['step']):
        validate(context)
        guards={Path(context['master_snapshot']['path']):context['master_snapshot']['owned_state']} if context.get('master_snapshot') else {}
        transition(path,{'current_step':state['current_step'],'status':state.get('status')},
            {'current_step':selected['step']+1,'status':'in-progress'},actor='driver',
            reason='recover exact gated acceptance transaction',guards=guards)
    elif state.get('current_step')!=str(selected['step']+1) or state.get('status') not in {'in-progress','shipped'} or state.get('hold')=='human':
        raise ContextError('acceptance recovery pointer or hold changed')
    from plan_state import audit_context
    if not audit_context(context)['explained']:raise ContextError('acceptance recovery state is unexplained')
    _save_acceptance(context,result,'advanced')
    return dict(result,recovered_acceptance=True)


def _commit_message(selected):
    return f"{selected.get('slug','step')}: driver accepted step {selected['step']} [plan:{selected.get('slug','step')}#step-{selected['step']}]"


def _recover_commit_intent(context,result):
    """Complete the original staged transaction, never adopt another commit."""
    selected,path,root,_=validate(context)
    head=git(root,'rev-parse','HEAD');base=context['base_commit'];tree=result['tree']
    if head==base:
        if working_tree(root)!=tree:raise ContextError('commit intent working tree changed')
        index=git(root,'write-tree')
        if index not in {context['base_tree'],tree}:raise ContextError('commit intent index changed')
        git(root,'add','-A','--','.')
        if git(root,'write-tree')!=tree:raise ContextError('commit intent staging tree changed')
        git(root,'commit','-m',_commit_message(selected))
        head=git(root,'rev-parse','HEAD')
    elif (git(root,'rev-list','--parents','-n','1',head).split()!=[head,base]
            or git(root,'show','-s','--format=%B',head)!=_commit_message(selected)):
        raise ContextError('commit intent has unrelated HEAD')
    committed_tree=git(root,'rev-parse','HEAD^{tree}')
    if working_tree(root)!=committed_tree:raise ContextError('commit intent hook left uncommitted changes')
    kernel=context.get('kernel_list') or {}
    if kernel.get('loop_kernel_policy')=='owner-only':
        from safety_kernel import touches_kernel
        if any(touches_kernel(name,kernel=kernel) for name in git(root,'diff','--name-only',base,committed_tree).splitlines()):
            raise ContextError('commit intent changed frozen owner kernel')
    attempts=result['attempts']
    if committed_tree!=tree:
        # A hook-modified tree needs a fresh authoritative gate; the previous
        # output remains diagnostic and cannot cover this new shipping tree.
        attempt=gate(context,root,path,selected,default_timeout=result['gate_timeout'])
        attempts=attempts+[attempt]
        if not attempt['all_passed']:raise ContextError('committed intent fresh gate is red or incomplete')
    validate(context)
    recovered=dict(result,accepted=True,commits=[head],head_commit=head,tree=committed_tree,
                   attempts=attempts,commit_ended_monotonic=monotonic())
    _save_acceptance(context,recovered,'prepared')
    return recovered


def _transaction_head(context,result):
    """Only this transaction's empty ship markers may follow accepted code."""
    root=Path(context['exec_tree']);head=git(root,'rev-parse','HEAD')
    if git(root,'rev-parse','HEAD^{tree}')!=result['tree'] or working_tree(root)!=result['tree']:
        raise ContextError('acceptance recovery tree changed')
    if head==result['head_commit']:return head
    git(root,'merge-base','--is-ancestor',result['head_commit'],head)
    selected=read_dispatch(context);needle=f"[plan:{selected['slug']}#ship]"
    for commit in git(root,'rev-list',result['head_commit']+'..'+head).splitlines():
        parents=git(root,'rev-list','--parents','-n','1',commit).split()
        if (len(parents)!=2 or needle not in git(root,'show','-s','--format=%B',commit)
                or git(root,'rev-parse',commit+'^{tree}')!=result['tree']):
            raise ContextError('acceptance recovery has unrelated follow-on commits')
    return head


def _accept_step(context,*,default_timeout):
    selected,path,root,state=validate(context)
    if git(root,'rev-parse','HEAD')!=context['base_commit']:raise ContextError('worker changed HEAD')
    if git(root,'diff','--cached','--name-only'):raise ContextError('worker changed real index')
    attempt=gate(context,root,path,selected,default_timeout=default_timeout)
    if not attempt['all_passed']:return dict(accepted=False,attempts=[attempt])
    # Revalidate holds/contracts/pointer after gates, before accepting code.
    validate(context)
    if git(root,'rev-parse','HEAD')!=context['base_commit'] or git(root,'diff','--cached','--name-only'):
        raise ContextError('gate changed Git state')
    tree=attempt['working_tree'];commits=[];commit_started=monotonic()
    if tree!=git(root,'rev-parse','HEAD^{tree}'):
        kernel=context.get('kernel_list') or {}
        if kernel.get('loop_kernel_policy')=='owner-only':
            from safety_kernel import touches_kernel
            if any(touches_kernel(name,kernel=kernel) for name in git(root,'diff','--name-only',context['base_commit'],tree).splitlines()):
                raise ContextError('worker changed frozen owner kernel')
        _save_acceptance(context,dict(accepted=False,commits=[],attempts=[attempt],
            head_commit=context['base_commit'],tree=tree,gate_timeout=default_timeout,
            commit_started_monotonic=commit_started,proof_provisional=True),'commit-intent')
        git(root,'add','-A','--','.')
        if git(root,'write-tree')!=tree:raise ContextError('tree changed between gate and commit')
        git(root,'commit','-m',_commit_message(selected))
        commits=[git(root,'rev-parse','HEAD')]
    commit_ended=monotonic();attempts=[attempt]
    committed_tree=git(root,'rev-parse','HEAD^{tree}')
    kernel=context.get('kernel_list') or {}
    if kernel.get('loop_kernel_policy')=='owner-only':
        from safety_kernel import touches_kernel
        if any(touches_kernel(name,kernel=kernel) for name in git(root,'diff','--name-only',context['base_commit'],committed_tree).splitlines()):
            raise ContextError('commit hook changed frozen owner kernel')
    if working_tree(root)!=committed_tree:raise ContextError('commit hook left uncommitted changes')
    if committed_tree!=tree:
        # Hooks changed the actual shipping tree. A prior green cannot cover it.
        attempt=gate(context,root,path,selected,default_timeout=default_timeout);attempts.append(attempt)
        if not attempt['all_passed']:return dict(accepted=False,commits=commits,attempts=attempts)
    validate(context)
    guards={}
    if context.get('master_snapshot'):
        row=context['master_snapshot'];guards[Path(row['path'])]=row['owned_state']
    result=dict(accepted=True,commits=commits,attempts=attempts,head_commit=git(root,'rev-parse','HEAD'),
                tree=committed_tree,commit_started_monotonic=commit_started,commit_ended_monotonic=commit_ended,
                proof_provisional=True)
    _save_acceptance(context,result,'prepared')
    transition(path,{'current_step':state['current_step'],'status':state.get('status')},
               {'current_step':selected['step']+1,'status':'in-progress'},actor='driver',reason='driver gated exact accepted tree',guards=guards)
    _save_acceptance(context,result,'advanced')
    return result # Integrity, landing and close-record projection remain caller-owned.


def project_result(result,context,*,allow_transaction_marker=False):
    """Adapt this transaction to existing gate readers without rerunning gates."""
    from emit_jsonl_record import build_record,extract_failing_check
    selected=read_dispatch(context)
    if result.get('run_id')!=context['run_id'] or result.get('iteration')!=context['iteration']:
        raise ContextError('driver result belongs to another iteration')
    attempts=result.get('attempts') or []
    accepted=result.get('accepted') is True
    if accepted:
        if not attempts or not attempts[-1].get('all_passed') or not attempts[-1].get('gate_tree_unchanged'):
            raise ContextError('accepted result lacks an exact green gate')
        root=Path(context['exec_tree'])
        if allow_transaction_marker:
            _transaction_head(context,result)
        if ((not allow_transaction_marker and result.get('head_commit')!=git(root,'rev-parse','HEAD')) or result.get('tree')!=git(root,'rev-parse','HEAD^{tree}')
                or result['tree']!=attempts[-1]['working_tree'] or working_tree(root)!=result['tree']):
            raise ContextError('accepted tree changed before projection')
    checks=attempts[-1]['check_observations'] if attempts else []
    data=dict(results=checks,head_sha=result.get('head_commit') if accepted else None)
    record=build_record(selected['slug'],selected['step'],'pass' if accepted else 'error',
                        0 if accepted else 1,extract_failing_check(data),data)
    record.update(driver_owned=True,driver_attempts=attempts,accepted_tree=result.get('tree') if accepted else None,
                  run_id=context['run_id'],iteration=context['iteration'],
                  reason=None if accepted else result.get('stop_reason','code_only_acceptance_failed'))
    return record


def main():
    import argparse
    from iteration_context import read_context
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-result',type=Path,required=True)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();context=read_context(args.context)
    result=json.loads(args.project_result.read_text());record=project_result(result,context)
    with args.out.open('x') as stream:
        stream.write(json.dumps(record)+'\n');stream.flush();os.fsync(stream.fileno())
    from emit_jsonl_record import append_gate_history
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).isoformat()
    append_gate_history(Path(context['runtime_dir'])/'launcher/gate-history.jsonl',record,
                        context['run_id'],context['iteration'],stamp)


if __name__=='__main__':main()
