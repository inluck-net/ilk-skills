#!/usr/bin/env python3
"""Persist resolved iteration inputs, without selecting plans or proving gates."""
from __future__ import annotations
import argparse
import hashlib
import re
import json
import os
from pathlib import Path
import subprocess
import tempfile
from contextvars import ContextVar

_preparation_owner = ContextVar("iteration_preparation_owner", default=None)


class ContextError(ValueError):
    pass


def read_context(path: Path | None = None) -> dict | None:
    raw=path or os.environ.get('ILK_ITER_CONTEXT')
    if not raw: return None
    try:
        data=json.loads(Path(raw).read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict) or data.get('schema_version')!=1:
            raise ValueError('unsupported context schema')
        for name in ('clone','exec_tree','plans_dir','runtime_dir','data_root','code_root'):
            if not isinstance(data.get(name),str) or not Path(data[name]).is_absolute():
                raise ValueError(f'invalid context {name}')
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise ContextError(f'iteration context invalid: {exc}') from exc


def read_dispatch(context: dict) -> dict:
    selected=context.get('dispatch')
    if not isinstance(selected,dict): raise ContextError('frozen dispatch is absent')
    step=selected.get('step')
    if not isinstance(step,int) or isinstance(step,bool) or step<0:
        raise ContextError('frozen step is invalid')
    raw=selected.get('subplan_path')
    if not isinstance(raw,str) or not Path(raw).is_absolute():
        raise ContextError('frozen subplan path is invalid')
    path=Path(raw).resolve()
    row=context.get('plans',{}).get(path.name)
    if path.parent!=Path(context['plans_dir']) or not row or Path(row['path']).resolve()!=path:
        raise ContextError('dispatch is outside frozen registry')
    return dict(selected,exec_tree=context['exec_tree'],code_root=context['code_root'],
                run_id=context['run_id'],iteration=context['iteration'])


def root_for_plan(context: dict, subplan: Path) -> Path:
    from plan_status import parse_frontmatter
    subplan=subplan.resolve()
    if subplan.parent!=Path(context['plans_dir']) or subplan.name not in context.get('plans',{}):
        raise ContextError('plan is outside the frozen iteration registry')
    member=context['plans'][subplan.name].get('repo')
    if member:
        key=str((Path(context['clone'])/member).resolve())
        if key not in context['observe_tree']:
            raise ContextError('plan repo is outside frozen observation map')
        return Path(context['observe_tree'][key])
    return Path(context['exec_tree'])


def red_inputs(context: dict, slug: str, step: int) -> dict:
    """Attribution roots and bases come from this dispatch, never a new queue."""
    matches=[row for row in context.get('plans',{}).values() if row.get('slug')==slug]
    if len(matches)!=1:raise ContextError('red target is not unique in frozen registry')
    row=matches[0]
    if int(row.get('step') or 0)!=step:raise ContextError('red step differs from frozen dispatch')
    root=root_for_plan(context,Path(row['path']))
    base=context.get('repository_bases',{}).get(str(root))
    if base is None and root==Path(context['exec_tree']):base=context.get('base_commit')
    if not base:raise ContextError('red execution base was not frozen')
    selected=context.get('dispatch') or {}
    return dict(exec_tree=str(root),iteration_base=base,
                batch_base=selected.get('batch_base_sha'),master_path=selected.get('master_path'))


def validate_work_tree_binding(context):
    if context.get('mode')!='work_tree':return
    contract=context.get('work_tree_contract')
    if not isinstance(contract,dict):raise ContextError('declared worktree binding unavailable')
    master=(context.get('dispatch') or {}).get('master_path')
    if master!=contract.get('master_path') or context['exec_tree']!=contract.get('exec_tree'):
        raise ContextError('declared worktree identity changed')
    from plan_status import parse_frontmatter
    declared=parse_frontmatter(Path(master).read_text()).get('work_tree','')
    if not declared or not Path(declared).is_absolute() or str(Path(declared).resolve())!=context['exec_tree']:
        raise ContextError('declared worktree master changed')
    marker=Path(context['exec_tree'])/'.git';metadata=marker.stat()
    if [metadata.st_dev,metadata.st_ino,metadata.st_ctime_ns]!=contract.get('git_indirection_identity'):
        raise ContextError('declared worktree was replaced')
    from driver_step import git
    for root in (Path(context['clone']),Path(context['exec_tree'])):
        raw=Path(git(root,'rev-parse','--git-common-dir'))
        common=raw.resolve() if raw.is_absolute() else (root/raw).resolve()
        if str(common)!=contract.get('common_dir'):
            raise ContextError('declared worktree Git store changed')


def _git_result(root, *args):
    owner = _preparation_owner.get()
    argv = ['git', '-C', str(root), *args]
    if not owner:
        return subprocess.run(argv, capture_output=True, text=True)
    from iteration_clock import monotonic
    from run_registry import require_driver
    from owned_command import wrap_command
    from bounded_run import run
    require_driver(owner)
    remaining = owner['iteration_deadline_monotonic'] - monotonic()
    if remaining <= 0:
        raise ContextError('context preparation reached original iteration deadline')
    owned = argv if read_context() else wrap_command(argv, owner)
    code, stdout, stderr, timed_out = run(owned, cwd=str(root), timeout=remaining)
    if timed_out:
        raise ContextError('context preparation Git reached original iteration deadline')
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def git_value(root: Path, name: str) -> str | None:
    result = _git_result(root, 'rev-parse', name)
    return result.stdout.strip() if result.returncode == 0 else None


def _git_status(root):
    result = _git_result(root, 'status', '--porcelain')
    if result.returncode:
        raise ContextError('context preparation Git status failed')
    return bool(result.stdout)


def _common_dir(root):
    result = _git_result(root, 'rev-parse', '--git-common-dir')
    return str((Path(root) / result.stdout.strip()).resolve()) if result.returncode == 0 else None


def plan_snapshot(path: Path) -> dict:
    from plan_status import parse_frontmatter
    from plan_slug import strip_date_prefix
    from plan_fingerprint import surface
    try:
        text=path.read_text(encoding='utf-8-sig')
    except OSError as exc:
        return {'path':str(path),'error':type(exc).__name__}
    fm=parse_frontmatter(text)
    immutable=surface(text,master=path.name.startswith('MASTER-'))
    return dict(path=str(path),slug=strip_date_prefix(path.stem),
                status=fm.get('status'),step=fm.get('current_step'),repo=fm.get('repo'),estimated_steps=fm.get('estimated_steps'),
                owned_state={key:fm.get(key) for key in ('status','current_step','last_updated','auto_block_fails','pre_park_status','hold','yield','parked_at','parked_reason')},
                fingerprint=hashlib.sha256(immutable.encode()).hexdigest())


def plan_status_from_context(context: dict) -> dict:
    subplans=[]
    for name,row in context.get('plans',{}).items():
        current=plan_snapshot(Path(row['path']))
        subplans.append(dict(fname=name,slug=row.get('slug'),status=current.get('status'),
                             current_step=current.get('step'),estimated_steps=current.get('estimated_steps')))
    return {'master':(context.get('dispatch') or {}).get('master_path'),'subplans':subplans}


def capture_execution(context: dict) -> dict:
    from run_registry import RegistryError
    owner=context if context.get('iteration_deadline_monotonic') is not None else None
    token=_preparation_owner.set(owner)
    try:
        return _capture_execution(context)
    except (ContextError, RegistryError) as error:
        return dict(head_commit=None,head_tree=None,commits=None,step_to={},
                    capture_error='owned execution capture unavailable: '+str(error))
    finally:
        _preparation_owner.reset(token)


def _capture_execution(context: dict) -> dict:
    root=Path(context['exec_tree'])
    head=git_value(root,'HEAD')
    result={'head_commit':head,'head_tree':git_value(root,'HEAD^{tree}'),
            'commits':None,'step_to':{name:plan_snapshot(Path(row['path']))
                                    for name,row in context.get('plans',{}).items()}}
    base=context.get('base_commit')
    if not base or not head:
        result['capture_error']='execution tree has no available git identity'
        return result
    ancestor=_git_result(root,'merge-base','--is-ancestor',base,head)
    if ancestor.returncode:
        result['capture_error']='iteration base is not an ancestor of final execution HEAD'
        return result
    commits=_git_result(root,'rev-list','--reverse',f'{base}..{head}')
    if commits.returncode:
        result['capture_error']='execution commit range unavailable'
        return result
    result['commits']=commits.stdout.splitlines()
    return result


def resolve_context(*, operation_context=None, **kwargs):
    token = _preparation_owner.set(operation_context)
    try:
        if operation_context:
            from iteration_clock import monotonic
            from run_registry import require_driver
            require_driver(operation_context)
            if monotonic() >= operation_context['iteration_deadline_monotonic']:
                raise ContextError('context preparation reached original iteration deadline')
        return _resolve_context(**kwargs)
    finally:
        _preparation_owner.reset(token)


def _resolve_context(*, clone, exec_tree, observe_tree, code_root, plans_dir,
                    runtime_dir, data_root, dispatch, run_id, iteration, mode):
    # Empty paths must not resolve to cwd and create control metadata there.
    # Validate all supplied roots before any journal lock or state recovery.
    for label,raw in (('clone',clone),('exec_tree',exec_tree),('code_root',code_root),
                      ('plans_dir',plans_dir),('runtime_dir',runtime_dir),('data_root',data_root)):
        if not isinstance(raw,(str,os.PathLike)) or not str(raw).strip():
            raise ValueError(f'explicit non-empty {label} required')
    if mode not in {'none','selfmod','work_tree'}:
        raise ValueError('unknown execution mode')
    def directory(raw):
        p=Path(raw).resolve(strict=True)
        if not p.is_dir(): raise ValueError(f'not a directory: {p}')
        return str(p)
    clone=directory(clone);exec_tree=directory(exec_tree)
    code_root=directory(code_root);plans_dir=directory(plans_dir)
    observed={directory(k):directory(v) for k,v in observe_tree.items()}
    if clone not in observed:
        observed[clone]=exec_tree
    if observed[clone]!=exec_tree:
        raise ValueError('clone observation differs from execution tree')
    if mode=='selfmod' and clone==exec_tree:
        raise ValueError('selfmod worker would run in live clone')
    mention_gate_policy='bounded-existing-cap'
    if dispatch:
        selected=Path(dispatch['subplan_path']).resolve(strict=True)
        if selected.parent!=Path(plans_dir):
            raise ValueError('dispatch belongs to another plans root')
        from plan_status import parse_frontmatter
        mention_gate_policy=parse_frontmatter(selected.read_text()).get('mention_gate_policy','bounded-existing-cap')
        if mention_gate_policy not in {'bounded-existing-cap','complete'}:
            raise ValueError('unknown mention_gate_policy')
        from safety_kernel import check_plan_admission
        violations = check_plan_admission(selected.read_text(encoding='utf-8-sig'), Path(exec_tree))
        if violations:
            raise ValueError('; '.join(violations))
        master=dispatch.get('master_path')
        if master and Path(master).resolve(strict=True).parent!=Path(plans_dir):
            raise ValueError('master belongs to another plans root')
    work_tree_contract=None
    if mode=='work_tree':
        master=(dispatch or {}).get('master_path')
        if not master:raise ValueError('exact master required for declared worktree')
        from plan_status import parse_frontmatter
        declared=parse_frontmatter(Path(master).read_text()).get('work_tree','')
        if not declared or not Path(declared).is_absolute() or str(Path(declared).resolve())!=exec_tree:
            raise ValueError('execution root differs from exact master work_tree')
        common=_common_dir(Path(clone))
        if not common or common!=_common_dir(Path(exec_tree)):
            raise ValueError('declared worktree belongs to another Git store')
        metadata=(Path(exec_tree)/'.git').stat()
        work_tree_contract=dict(master_path=master,exec_tree=exec_tree,common_dir=common,
            git_indirection_identity=[metadata.st_dev,metadata.st_ino,metadata.st_ctime_ns])
    from plan_state import _lock,journal_cursor,_recover
    owner = _preparation_owner.get()
    lock_options = {}
    if owner:
        from run_registry import require_driver
        lock_options = dict(deadline=owner['iteration_deadline_monotonic'],guard=lambda:require_driver(owner))
    with _lock(Path(plans_dir)/'.state-transitions.lock', **lock_options):
        snapshots={}
        master_snapshot=None
        if dispatch:
            from plan_status import extract_subplan_files
            master_path=dispatch.get('master_path')
            if master_path:
                mp=Path(master_path)
                master_snapshot=plan_snapshot(mp)
                for name in extract_subplan_files(mp.read_text(encoding='utf-8-sig')):
                    path=(Path(plans_dir)/name).resolve()
                    if path.parent!=Path(plans_dir): raise ValueError('registered path escapes plans root')
                    snapshots[name]=plan_snapshot(path)
            else:
                snapshots[selected.name]=plan_snapshot(selected)
        for snapshot in list(snapshots.values())+([master_snapshot] if master_snapshot else []):
            snapshot_path=Path(snapshot['path'])
            if snapshot_path.exists():_recover(Path(plans_dir)/'state-transitions.jsonl',snapshot_path,snapshot_path.read_bytes())
        state_journal_cursor=journal_cursor(plans_dir)
    runtime=Path(runtime_dir).resolve()
    recovery_identity={'master':(dispatch or {}).get('master_path'),
                       'base':(dispatch or {}).get('batch_base_sha'),
                       'master_fingerprint':(master_snapshot or {}).get('fingerprint'),
                       'plan_fingerprint':snapshots.get(Path((dispatch or {}).get('subplan_path','')).name,{}).get('fingerprint')}
    recovery_key=hashlib.sha256(json.dumps(recovery_identity,sort_keys=True).encode()).hexdigest()
    kernel_list=None
    if (Path(exec_tree)/'skills/ilk-loop/safety-kernel.json').exists():
        from safety_kernel import load
        kernel_list=load(Path(exec_tree))
    clone_ref=_git_result(clone,'symbolic-ref','-q','HEAD')
    clone_base=git_value(Path(clone),'HEAD')
    exec_base=clone_base if clone==exec_tree else git_value(Path(exec_tree),'HEAD')
    clone_dirty=_git_status(clone) if clone_base else None
    exec_dirty=clone_dirty if clone==exec_tree else (_git_status(exec_tree) if exec_base else None)
    bases={exec_tree:exec_base}
    for root in sorted(set(observed.values())):
        if root not in bases:bases[root]=clone_base if root==clone else git_value(Path(root),'HEAD')
    return dict(schema_version=1,run_id=run_id,iteration=int(iteration),
                clone_base_commit=clone_base,
                clone_ref=clone_ref.stdout.strip() if clone_ref.returncode==0 else None,
                clone_entry_dirty=clone_dirty,
                clone=clone,exec_tree=exec_tree,observe_tree=observed,repository_bases=bases,mode=mode,
                plans_dir=plans_dir,runtime_dir=str(runtime),
                ledger_path=str(runtime/'launcher/ship-proof.jsonl'),
                registry_path=str(runtime/'launcher'), # Owning roles are attached before dispatch freeze.
                recovery_dir=str(runtime/'launcher/recovery'/recovery_key),
                data_root=str(Path(data_root).resolve()),code_root=code_root,
                toolkit_sha=git_value(Path(code_root),'HEAD') or os.environ.get('ILK_TOOLKIT_SHA'),
                base_commit=exec_base,
                base_tree=git_value(Path(exec_tree),'HEAD^{tree}'),
                entry_dirty=exec_dirty,
                dispatch=dispatch or None, plans=snapshots, master_snapshot=master_snapshot,
                mention_gate_policy=mention_gate_policy,kernel_list=kernel_list,work_tree_contract=work_tree_contract,state_journal_cursor=state_journal_cursor)


def write_context(path: Path, context: dict):
    # Never silently replace an iteration's previously frozen identity.
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False,encoding='utf-8') as f:
        tmp=Path(f.name)
        json.dump(context,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    try:
        tmp.chmod(0o400)
        os.link(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sources=p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--print-dispatch',action='store_true')
    sources.add_argument('--print-red-inputs',action='store_true')
    sources.add_argument('--input',type=Path)
    sources.add_argument('--capture',type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--slug')
    p.add_argument('--step',type=int)
    a=p.parse_args()
    if a.print_red_inputs:
        context=read_context()
        if not context or not a.slug or a.step is None:p.error('frozen context, slug and step required')
        print(json.dumps(red_inputs(context,a.slug,a.step)))
    elif a.print_dispatch:
        context=read_context()
        if not context: p.error('frozen iteration context required')
        print(json.dumps(read_dispatch(context)))
    elif a.capture:
        print(json.dumps(capture_execution(json.loads(a.capture.read_text(encoding='utf-8-sig')))))
    else:
        if not a.out: p.error('--out required with --input')
        write_context(a.out,resolve_context(**json.loads(a.input.read_text(encoding='utf-8-sig'))))

if __name__=='__main__': main()
