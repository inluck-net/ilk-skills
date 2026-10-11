#!/usr/bin/env python3
"""Candidate driver acceptance transaction; not enabled in installed runners.

Gates run against a captured working-tree object. Only the driver commits and
CAS-advances one step. Changed commit-hook output is gated again; an unchanged
commit does not rerun the same gates. Unknown/red/mutating gates never advance.
Existing dirty entry, baseline-red exceptions and multi-repo dispatch require
their migration adapters before rollout; this candidate refuses them.
"""
from __future__ import annotations
from dataclasses import asdict
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from iteration_context import read_dispatch,plan_snapshot,root_for_plan,ContextError
from plan_status import parse_frontmatter
from plan_state import transition
_deadline=ContextVar('driver_step_deadline',default=None)


def remaining():
    deadline=_deadline.get()
    if deadline is None:return None
    value=deadline-time.monotonic()
    if value<=0:raise ContextError('iteration deadline reached')
    return value


def git(root,*args,env=None):
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
    if context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        raise ContextError('enforced worker adapter required')
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


def gate(context,root,path,selected,*,default_timeout):
    from run_local_checks import (split_frontmatter,parse_local_checks_block,extract_step_local_checks,
        count_local_checks_items,count_step_local_checks_items,step_gate_fence,
        build_mention_gate_inventory,run_one,_read_declared_reds)
    tree=working_tree(root);fm,body=split_frontmatter(path.read_text())
    sub=parse_local_checks_block(fm);step=extract_step_local_checks(body,selected['step'])
    if count_local_checks_items(fm)>len(sub) or count_step_local_checks_items(body,selected['step'])>len(step):
        raise ContextError('malformed declared gate')
    if step_gate_fence(body,selected['step']).declares_local_checks and not step:
        raise ContextError('unreadable step gate')
    if not sub and not step:raise ContextError('a runnable declared gate is required')
    if _read_declared_reds(root):raise ContextError('baseline-red attribution adapter not built')
    changed=git(root,'diff','--name-only',context['base_commit'],tree).splitlines()
    commands={item.get('command','') for item in sub+step}
    mention=build_mention_gate_inventory(root,changed,commands)
    identifier=uuid.uuid4().hex
    directory=Path(context['runtime_dir'])/'launcher/driver-step'/identifier
    directory.mkdir(parents=True,exist_ok=False)
    previous=os.environ.get('ILK_GATE_OUTPUT_DIR');os.environ['ILK_GATE_OUTPUT_DIR']=str(directory)
    started=time.monotonic()
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
        working_tree=tree,started_monotonic=started,ended_monotonic=time.monotonic(),
        check_observations=[asdict(result) for result in results],gate_tree_unchanged=unchanged,
        all_passed=bool(results) and all(result.passed for result in results) and unchanged,
        diagnostic_only=True)
    (directory/'attempt.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def accept_step(context,*,default_timeout,deadline):
    """Use the caller's existing iteration deadline, including commit hooks."""
    token=_deadline.set(deadline)
    try:
        remaining()
        return _accept_step(context,default_timeout=default_timeout)
    finally:_deadline.reset(token)


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
    tree=attempt['working_tree'];commits=[];commit_started=time.monotonic()
    if tree!=git(root,'rev-parse','HEAD^{tree}'):
        kernel=context.get('kernel_list') or {}
        if kernel.get('loop_kernel_policy')=='owner-only':
            from safety_kernel import touches_kernel
            if any(touches_kernel(name,kernel=kernel) for name in git(root,'diff','--name-only',context['base_commit'],tree).splitlines()):
                raise ContextError('worker changed frozen owner kernel')
        git(root,'add','-A','--','.')
        if git(root,'write-tree')!=tree:raise ContextError('tree changed between gate and commit')
        git(root,'commit','-m',f"{selected.get('slug','step')}: driver accepted step {selected['step']} [plan:{selected.get('slug','step')}#step-{selected['step']}]")
        commits=[git(root,'rev-parse','HEAD')]
    commit_ended=time.monotonic();attempts=[attempt]
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
    transition(path,{'current_step':state['current_step'],'status':state.get('status')},
               {'current_step':selected['step']+1,'status':'in-progress'},actor='driver',reason='driver gated exact accepted tree',guards=guards)
    return dict(accepted=True,commits=commits,attempts=attempts,head_commit=git(root,'rev-parse','HEAD'),
                tree=committed_tree,commit_started_monotonic=commit_started,commit_ended_monotonic=commit_ended,
                proof_provisional=True) # Integrity, landing and close-record projection remain caller-owned.
